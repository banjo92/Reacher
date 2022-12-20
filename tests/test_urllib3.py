from urllib3 import PoolManager
from urllib3.exceptions import LocationParseError

from grab.error import GrabInvalidResponse
from tests.util import BaseGrabTestCase, build_grab


class GrabApiTestCase(BaseGrabTestCase):
    def setUp(self):
        self.server.reset()

    def test_urllib3_idna_error(self):
        # It was failed with UnicodeError on previuos
        # versions of python or ullib3 library
        # I do not remember clearly.
        # Now it produces valid exception

        invalid_url = (
            "http://13354&altProductId=6423589&productId=6423589"
            "&altProductStoreId=13713&catalogId=10001"
            "&categoryId=28678&productStoreId=13713"
            "http://www.textbooksnow.com/webapp/wcs/stores"
            "/servlet/ProductDisplay?langId=-1&storeId="
        )
        pool = PoolManager()
        self.assertRaises(
            LocationParseError, pool.request, "GET", invalid_url, retries=False
        )

    def test_invalid_url(self):
        invalid_url = (
            "http://13354&altProductId=6423589&productId=6423589"
            "&altProductStoreId=13713&catalogId=10001"
            "&categoryId=28678&productStoreId=13713"
            "http://www.textbooksnow.com/webapp/wcs/stores"
            "/servlet/ProductDisplay?langId=-1&storeId="
        )
        grab = build_grab()
        with self.assertRaises(GrabInvalidResponse):
            grab.go(invalid_url)