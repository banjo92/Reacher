from __future__ import annotations

import logging
import typing
from collections.abc import Mapping, MutableMapping
from copy import copy
from http.cookiejar import CookieJar
from pprint import pprint  # pylint: disable=unused-import
from typing import Any, cast

from .base import BaseClient, BaseTransport
from .document import Document
from .extensions import RedirectExtension
from .request import HttpRequest
from .transport import Urllib3Transport
from .types import resolve_entity, resolve_transport_entity

__all__ = ["HttpClient", "request"]
logger = logging.getLogger(__name__)


def copy_config(config: Mapping[str, Any]) -> MutableMapping[str, Any]:
    """Copy grab config with correct handling of mutable config values."""
    return {x: copy(y) for x, y in config.items()}


class Retry:
    def __init__(self) -> None:
        self.state: MutableMapping[str, int] = {}


class HttpClient(BaseClient[HttpRequest, Document]):
    document_class: type[Document] = Document
    transport_class = Urllib3Transport
    extension = RedirectExtension()

    def __init__(
        self,
        transport: None
        | BaseTransport[HttpRequest, Document]
        | type[BaseTransport[HttpRequest, Document]] = None,
    ) -> None:
        self.config: MutableMapping[str, Any] = {}
        self.transport = resolve_transport_entity(transport, self.transport_class)
        super().__init__()

    def get_request_cookies(self, req: HttpRequest) -> CookieJar:
        jar = CookieJar()
        for func in self.ext_handlers["request_cookies"]:
            func(req, jar)
        return jar

    def request(
        self, req: None | str | HttpRequest = None, **request_kwargs: Any
    ) -> Document:
        if not isinstance(req, HttpRequest):
            if req is not None:
                assert isinstance(req, str)
                request_kwargs["url"] = req
            req = HttpRequest.create_from_mapping(request_kwargs)
        retry = Retry()
        all(x(retry) for x in self.ext_handlers["init-retry"])
        while True:
            for func in self.ext_handlers["request:pre"]:
                func(req)
            self.transport.reset()
            self.transport.request(req, self.get_request_cookies(req))
            with self.transport.wrap_transport_error():
                doc = self.process_request_result(req)
            if any(
                (
                    (item := func(retry, req, doc)) != (None, None)
                    for func in self.ext_handlers["retry"]
                )
            ):
                # pylint: disable=deprecated-typing-alias
                retry, req = cast(typing.Tuple[Retry, HttpRequest], item)
                continue
            return doc

    def process_request_result(self, req: HttpRequest) -> Document:
        """Process result of real request performed via transport extension."""
        doc = self.transport.prepare_response(req, document_class=self.document_class)
        for func in self.ext_handlers["response:post"]:
            func(req, doc)
        return doc


def request(
    url: None | str | HttpRequest = None,
    client: None | HttpClient | type[HttpClient] = None,
    **request_kwargs: Any,
) -> Document:
    client = resolve_entity(HttpClient, client, default=HttpClient)
    return client.request(url, **request_kwargs)
