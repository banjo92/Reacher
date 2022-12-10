# Copyright: 2015, Grigoriy Petukhov
# Author: Grigoriy Petukhov (http://getdata.pro)
# License: MIT
from __future__ import annotations

import logging
import os
import random
import ssl
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from http.client import HTTPResponse
from http.cookiejar import CookieJar
from pprint import pprint  # pylint: disable=unused-import
from typing import Any, Generator, Optional, Sequence, Union, cast
from urllib.parse import urlsplit

import certifi
from urllib3 import PoolManager, ProxyManager, exceptions, make_headers
from urllib3.contrib.socks import SOCKSProxyManager
from urllib3.exceptions import LocationParseError
from urllib3.fields import RequestField
from urllib3.filepost import encode_multipart_formdata
from urllib3.response import HTTPResponse as Urllib3HTTPResponse
from urllib3.util.retry import Retry
from urllib3.util.timeout import Timeout
from user_agent import generate_user_agent

from grab import error
from grab.base import Grab
from grab.cookie import CookieManager, MockRequest, MockResponse
from grab.document import Document
from grab.error import GrabMisuseError, GrabTimeoutError
from grab.upload import UploadContent, UploadFile
from grab.util.encoding import decode_pairs, make_str
from grab.util.http import normalize_http_values, normalize_post_data, normalize_url


class BaseTransport:
    def __init__(self) -> None:
        pass
        # these assignments makes pylint happy
        # self.body_file = None
        # self.body_path = None

    def reset(self) -> None:
        # self.body_file = None
        # self.body_path = None
        pass

    def setup_body_file(
        self,
        storage_dir: str,
        storage_filename: Optional[str],
        create_dir: bool = False,
    ) -> str:
        if create_dir and not os.path.exists(storage_dir):
            os.makedirs(storage_dir)
        if storage_filename is None:
            file, file_path = tempfile.mkstemp(dir=storage_dir)
            os.close(file)
            # body_file = os.fdopen(handle, "wb")
        else:
            file_path = os.path.join(storage_dir, storage_filename)
            # body_file = open(path, "wb")
        return file_path  # noqa: R504


def process_upload_items(
    items: Sequence[tuple[str, Any]]
) -> list[Union[RequestField, tuple[str, Any]]]:
    result = []
    for key, val in items:
        if isinstance(val, UploadContent):
            headers = {"Content-Type": val.content_type}
            field = RequestField(
                name=key, data=val.content, filename=val.filename, headers=headers
            )
            field.make_multipart(content_type=val.content_type)
            result.append(field)
        elif isinstance(val, UploadFile):
            with open(val.path, "rb") as inp:
                data = inp.read()
            headers = {"Content-Type": val.content_type}
            field = RequestField(
                name=key, data=data, filename=val.filename, headers=headers
            )
            field.make_multipart(content_type=val.content_type)
            result.append(field)
        else:
            result.append((key, val))
    return result


class Request:  # pylint: disable=too-many-instance-attributes
    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        url: str,
        method: str,
        headers: dict[str, Any],
        config_nobody: bool,
        config_body_maxsize: int,
        timeout: int,
        connect_timeout: int,
        data: Optional[bytes] = None,
        body_maxsize: Optional[int] = None,
        response_path: Optional[str] = None,
        proxy_type: Optional[str] = None,
        proxy: Optional[str] = None,
        proxy_userpwd: Optional[str] = None,
    ) -> None:
        self.url = url
        self.method = method
        self.data = data
        self.proxy = proxy
        self.proxy_userpwd = proxy_userpwd
        self.proxy_type = proxy_type
        self.headers = headers
        self.body_maxsize = body_maxsize
        self.op_started: Optional[float] = None
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.config_nobody = config_nobody
        self.config_body_maxsize = config_body_maxsize
        self.response_path: Optional[str] = response_path

    def get_full_url(self) -> str:
        return self.url


class Urllib3Transport(BaseTransport):
    """Grab network transport based on urllib3 library."""

    def __init__(self) -> None:
        super().__init__()
        # http://urllib3.readthedocs.io/en/latest/user-guide.html#certificate-verification
        self.pool = PoolManager(10, cert_reqs="CERT_REQUIRED", ca_certs=certifi.where())

        logger = logging.getLogger("urllib3.connectionpool")
        logger.setLevel(logging.WARNING)

        self.request_head = b""
        self.request_body = b""
        self.request_log = b""

        self._response: Optional[Urllib3HTTPResponse] = None
        self._request: Optional[Request] = None

    def reset(self) -> None:
        # self.response_header_chunks = []
        # self.response_body_chunks = []
        # self.response_body_bytes_read = 0
        # self.verbose_logging = False
        # Maybe move to super-class???
        self.request_head = b""
        self.request_body = b""
        self.request_log = b""

        self._response = None
        self._request = None

    def process_config_post(
        self, grab: Grab, method: str
    ) -> tuple[dict[str, Any], Optional[bytes]]:
        if method in ("POST", "PUT") and (
            grab.config["post"] is None and grab.config["multipart_post"] is None
        ):
            raise GrabMisuseError(
                "Neither `post` or `multipart_post`"
                " options was specified for the %s"
                " request" % method
            )
        extra_headers = {}
        post_data: Optional[bytes] = None
        if grab.config["multipart_post"] is not None:
            post_data = grab.config["multipart_post"]
            if isinstance(post_data, bytes):
                pass
            elif isinstance(post_data, str):  # noqa: R506
                raise GrabMisuseError(
                    "Option multipart_post data does not accept unicode."
                )
            else:
                # WTF: why I encode things into bytes and then decode them back?
                post_items: Sequence[tuple[bytes, Any]] = normalize_http_values(
                    grab.config["multipart_post"],
                    charset=grab.config["charset"],
                    ignore_classes=(UploadFile, UploadContent),
                )
                post_items2: Sequence[tuple[str, Any]] = decode_pairs(
                    post_items, grab.config["charset"]
                )
                post_items3 = process_upload_items(post_items2)
                post_data, content_type = encode_multipart_formdata(
                    post_items3
                )  # type: ignore
                extra_headers["Content-Type"] = content_type
            extra_headers["Content-Length"] = len(post_data)
        elif grab.config["post"] is not None:
            post_data = normalize_post_data(grab.config["post"], grab.config["charset"])
            extra_headers["Content-Length"] = len(post_data)
        return extra_headers, post_data

    def process_config(  # noqa: C901
        self, grab: Grab
    ) -> None:  # noqa: C901 pylint: disable=too-many-branches
        # Init
        extra_headers: dict[str, str] = {}
        # URL
        try:
            request_url = normalize_url(grab.config["url"])
        except Exception as ex:
            raise error.GrabInvalidUrl(
                "%s: %s" % (str(ex), make_str(grab.config["url"], errors="ignore"))
            )
        # Method
        method = grab.detect_request_method()
        # Body storage/memory storing
        if grab.config["body_inmemory"]:
            response_path = None
        else:
            if not grab.config["body_storage_dir"]:
                raise GrabMisuseError("Option body_storage_dir is not defined")
            response_path = self.setup_body_file(
                grab.config["body_storage_dir"],
                grab.config["body_storage_filename"],
                create_dir=grab.config["body_storage_create_dir"],
            )
        # POST data
        post_headers, req_data = self.process_config_post(grab, method)
        extra_headers.update(post_headers)
        # Proxy
        req_proxy = None
        if grab.config["proxy"]:
            req_proxy = grab.config["proxy"]
        req_proxy_userpwd = None
        if grab.config["proxy_userpwd"]:
            req_proxy_userpwd = grab.config["proxy_userpwd"]
        req_proxy_type = None
        if grab.config["proxy_type"]:
            req_proxy_type = grab.config["proxy_type"]
        # User-Agent
        if grab.config["user_agent"] is None:
            if grab.config["user_agent_file"] is not None:
                with open(grab.config["user_agent_file"], encoding="utf-8") as inf:
                    lines = inf.read().splitlines()
                grab.config["user_agent"] = random.choice(lines)
            else:
                grab.config["user_agent"] = generate_user_agent()
        extra_headers["User-Agent"] = cast(str, grab.config["user_agent"])
        # Headers
        extra_headers.update(grab.config["common_headers"])
        if grab.config["headers"]:
            extra_headers.update(grab.config["headers"])
        cookie_hdr = self.process_cookie_options(grab, request_url, extra_headers)
        if cookie_hdr:
            extra_headers["Cookie"] = cookie_hdr

        self._request = Request(
            url=request_url,
            method=method,
            config_body_maxsize=grab.config["body_maxsize"],
            config_nobody=grab.config["nobody"],
            timeout=grab.config["timeout"],
            connect_timeout=grab.config["connect_timeout"],
            response_path=response_path,
            proxy=req_proxy,
            proxy_type=req_proxy_type,
            proxy_userpwd=req_proxy_userpwd,
            headers=extra_headers,
            data=req_data,
        )

    @contextmanager
    def wrap_transport_error(self) -> Generator[None, None, None]:
        try:
            yield
        except exceptions.ReadTimeoutError as ex:
            raise error.GrabTimeoutError("ReadTimeoutError", ex)
        except exceptions.ConnectTimeoutError as ex:
            raise error.GrabConnectionError("ConnectTimeoutError", ex)
        except exceptions.ProtocolError as ex:
            # TODO:
            # the code
            # raise error.GrabConnectionError(ex.args[1][0], ex.args[1][1])
            # fails
            # with error TypeError: 'OSError' object is not subscriptable
            raise error.GrabConnectionError("ProtocolError", ex)
        except exceptions.SSLError as ex:
            raise error.GrabConnectionError("SSLError", ex)
        except ssl.SSLError as ex:
            raise error.GrabConnectionError("SSLError", ex)

    def request(self) -> None:
        req = cast(Request, self._request)

        pool: Union[PoolManager, SOCKSProxyManager]
        if req.proxy:
            if req.proxy_userpwd:
                headers = make_headers(
                    proxy_basic_auth=req.proxy_userpwd
                )  # type: ignore
            else:
                headers = None
            proxy_url = "%s://%s" % (req.proxy_type, req.proxy)
            if req.proxy_type == "socks5":
                pool = SOCKSProxyManager(
                    proxy_url, cert_reqs="CERT_REQUIRED", ca_certs=certifi.where()
                )  # , proxy_headers=headers)
            else:
                pool = ProxyManager(
                    proxy_url,
                    proxy_headers=headers,
                    cert_reqs="CERT_REQUIRED",
                    ca_certs=certifi.where(),
                )
        else:
            pool = self.pool
        with self.wrap_transport_error():
            # Retries can be disabled by passing False:
            # http://urllib3.readthedocs.io/en/latest/reference/urllib3.util.html#module-urllib3.util.retry
            # Do not use False because of warning:
            # Converted retries value: False -> Retry(total=False,
            # connect=None, read=None, redirect=0, status=None)
            retry = Retry(
                total=False,
                connect=False,
                read=False,
                redirect=0,
                status=None,
            )
            # The read timeout is not total response time timeout
            # It is the timeout on read of next data chunk from the server
            # Total response timeout is handled by Grab
            timeout = Timeout(connect=req.connect_timeout, read=req.timeout)
            # req_headers = dict((make_str(x), make_str(y))
            #                   for (x, y) in req.headers.items())
            req_url = req.url
            req_method = req.method
            req.op_started = time.time()
            try:
                res = pool.urlopen(  # type: ignore
                    req_method,
                    req_url,
                    body=req.data,
                    timeout=timeout,
                    retries=retry,
                    headers=req.headers,
                    preload_content=False,
                )
            except LocationParseError as ex:
                raise error.GrabInvalidResponse(str(ex), ex)
        # except exceptions.ReadTimeoutError as ex:
        #    raise error.GrabTimeoutError('ReadTimeoutError', ex)
        # except exceptions.ConnectTimeoutError as ex:
        #    raise error.GrabConnectionError('ConnectTimeoutError', ex)
        # except exceptions.ProtocolError as ex:
        #    # TODO:
        #    # the code
        #    # raise error.GrabConnectionError(ex.args[1][0], ex.args[1][1])
        #    # fails
        #    # with error TypeError: 'OSError' object is not subscriptable
        #    raise error.GrabConnectionError('ProtocolError', ex)
        # except exceptions.SSLError as ex:
        #    raise error.GrabConnectionError('SSLError', ex)

        # WTF?
        self.request_head = b""
        self.request_body = b""
        self.request_log = b""

        self._response = res
        # raise error.GrabNetworkError(ex.args[0], ex.args[1])
        # raise error.GrabTimeoutError(ex.args[0], ex.args[1])
        # raise error.GrabConnectionError(ex.args[0], ex.args[1])
        # raise error.GrabAuthError(ex.args[0], ex.args[1])
        # raise error.GrabTooManyRedirectsError(ex.args[0],
        #                                      ex.args[1])
        # raise error.GrabCouldNotResolveHostError(ex.args[0],
        #                                         ex.args[1])
        # raise error.GrabNetworkError(ex.args[0], ex.args[1])

    def read_with_timeout(self) -> bytes:
        if cast(Request, self._request).config_nobody:
            return b""
        maxsize = cast(Request, self._request).config_body_maxsize
        chunks = []
        default_chunk_size = 10000
        if maxsize:
            chunk_size = min(default_chunk_size, maxsize + 1)
        else:
            chunk_size = default_chunk_size
        bytes_read = 0
        while True:
            chunk = cast(HTTPResponse, self._response).read(chunk_size)
            if chunk:
                bytes_read += len(chunk)
                chunks.append(chunk)
                if maxsize and bytes_read > maxsize:
                    # reached limit on bytes to read
                    break
            else:
                break
            if cast(Request, self._request).timeout and (
                time.time() - cast(float, cast(Request, self._request).op_started)
                > cast(float, cast(Request, self._request).timeout)
            ):
                raise GrabTimeoutError
        data = b"".join(chunks)
        if maxsize:
            return data[:maxsize]
        return data

    def get_response_header_items(self) -> list[tuple[str, Any]]:
        """
        Return current response headers as items.

        This funciton is required to isolated smalles part of untyped code
        and hide it from mypy

        WTF: why "type: ignore" is not required here by mypy
        """
        headers = cast(HTTPResponse, self._response).headers
        return headers.items()

    def prepare_response(self, grab_config: dict[str, Any]) -> Optional[Document]:
        """
        Prepare response, duh.

        This methed is called after network request is completed
        hence the "self._request" is not None.
        Update: maybe not.

        Good to know: on python3 urllib3 headers are converted to str type
        using latin encoding.
        """
        if not self._response:
            return None
        try:
            response = Document()
            head = ""
            for key, val in self.get_response_header_items():
                key = key.encode("latin").decode("utf-8", errors="ignore")
                val = val.encode("latin").decode("utf-8", errors="ignore")
                head += "%s: %s\r\n" % (key, val)
            head += "\r\n"
            response.head = head.encode("utf-8", errors="strict")

            if cast(Request, self._request).response_path:
                # FIXME: Read/write by chunks.
                # Now the whole content is read at once.
                response.body_path = cast(
                    str, cast(Request, self._request).response_path
                )
                with open(response.body_path, "wb") as out:
                    out.write(self.read_with_timeout())
            else:
                response.body_path = None
                response.body = self.read_with_timeout()

            # Clear memory
            # self.response_header_chunks = []

            response.code = self._response.status
            # response.total_time =
            # response.connect_time =
            # response.name_lookup_time =
            # response.download_size =
            # response.upload_size =
            # response.download_speed =
            # response.remote_ip =

            response.url = (
                self._response.get_redirect_location()
                or cast(Request, self._request).url
            )

            # WTF: it is imported here?
            import email.message  # pylint: disable=import-outside-toplevel

            hdr = email.message.Message()
            for key, val in self.get_response_header_items():
                key = key.encode("latin").decode("utf-8", errors="ignore")
                val = val.encode("latin").decode("utf-8", errors="ignore")
                # if key == 'Location':
                #    import pdb; pdb.set_trace()
                hdr[key] = val
            response.parse(charset=grab_config["document_charset"], headers=hdr)

            jar = self.extract_cookiejar()  # self._response, self._request)
            response.cookies = CookieManager(jar)

            return response
        finally:
            self._response.release_conn()

    def extract_cookiejar(self) -> CookieJar:
        jar = CookieJar()
        # self._respose could be None
        # if this method is called from custom prepare response
        if self._response and self._request:
            jar.extract_cookies(
                # pylint: disable=protected-access
                cast(
                    HTTPResponse,
                    # MockResponse(self._response._original_response.headers),
                    MockResponse(self._response.headers),
                ),
                # pylint: enable=protected-access
                cast(
                    urllib.request.Request,
                    MockRequest(self._request.url, self._request.headers),
                ),
            )
        return jar

    def process_cookie_options(
        self, grab: Grab, request_url: str, request_headers: dict[str, Any]
    ) -> Optional[str]:
        # `cookiefile` option should be processed before `cookies` option
        # because `load_cookies` updates `cookies` option
        if grab.config["cookiefile"]:
            # Do not raise exception if cookie file does not exist
            try:
                grab.cookies.load_from_file(grab.config["cookiefile"])
            except IOError as ex:
                logging.error(ex)

        request_host = urlsplit(request_url).hostname
        if request_host:
            if request_host.startswith("www."):
                request_host_no_www = request_host[4:]
            else:
                request_host_no_www = request_host

            # Process `cookies` option that is simple dict i.e.
            # it provides only `name` and `value` attributes of cookie
            # No domain, no path, no expires, etc
            # I pass these no-domain cookies to *each* requested domain
            # by setting these cookies with corresponding domain attribute
            # Trying to guess better domain name by removing leading "www."
            if grab.config["cookies"]:
                if not isinstance(grab.config["cookies"], dict):
                    raise error.GrabMisuseError("cookies option should be a dict")
                for name, value in grab.config["cookies"].items():
                    grab.cookies.set(name=name, value=value, domain=request_host_no_www)

        cookie_hdr = grab.cookies.get_cookie_header(request_url, request_headers)
        return cookie_hdr if cookie_hdr else None
