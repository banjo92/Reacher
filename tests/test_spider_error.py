from pprint import pprint  # pylint: disable=unused-import

from test_server import Response

from grab import Grab
from grab.spider import Spider, Task
from tests.util import BaseGrabTestCase, build_spider

# That URLs breaks Grab's URL normalization process
# with error "label empty or too long"
INVALID_URL = (
    "http://13354&altProductId=6423589&productId=6423589"
    "&altProductStoreId=13713&catalogId=10001"
    "&categoryId=28678&productStoreId=13713"
    "http://www.textbooksnow.com/webapp/wcs/stores"
    "/servlet/ProductDisplay?langId=-1&storeId="
)


class SpiderErrorTestCase(BaseGrabTestCase):
    def setUp(self):
        self.server.reset()

    def test_generator_with_invalid_url(self):
        class SomeSpider(Spider):
            def task_generator(self):
                yield Task("page", url=INVALID_URL)

        bot = build_spider(SomeSpider)
        bot.run()

    def test_redirect_with_invalid_url(self):

        server = self.server

        class TestSpider(Spider):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.done_counter = 0

            def task_generator(self):
                self.done_counter = 0
                yield Task("page", url=server.get_url())

            def task_page(self, grab, task):
                pass

        self.server.add_response(
            Response(
                status=301,
                headers=[("Location", INVALID_URL)],
            )
        )
        bot = build_spider(TestSpider, network_try_limit=1)
        bot.run()

    def test_stat_error_name_threaded_urllib3(self):

        server = self.server
        server.add_response(Response(sleep=2))

        class SimpleSpider(Spider):
            def prepare(self):
                self.network_try_limit = 1

            def task_generator(self):
                grab = Grab(url=server.get_url(), timeout=1)
                yield Task("page", grab=grab)

            def task_page(self, grab, unused_task):
                pass

        bot = build_spider(SimpleSpider)
        bot.run()
        self.assertTrue("error:ReadTimeoutError" in bot.stat.counters)
