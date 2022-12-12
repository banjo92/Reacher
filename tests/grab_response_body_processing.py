import os

from test_server import Response

from grab import GrabMisuseError
from tests.util import TEST_DIR, BaseGrabTestCase, build_grab, temp_dir

# class CustomGrab(Grab):
#    def get_cached_bytes_body(self):
#        return self._bytes_body


class GrabSimpleTestCase(BaseGrabTestCase):
    def setUp(self):
        self.server.reset()

    def test_body_inmemory_false(self):
        with temp_dir() as tmp_dir:
            grab = build_grab()
            grab.setup(body_inmemory=False)
            with self.assertRaises(GrabMisuseError):
                grab.go(self.server.get_url())

            self.server.add_response(Response(data=b"foo"))
            grab = build_grab()
            grab.setup(body_inmemory=False)
            grab.setup(body_storage_dir=tmp_dir)
            grab.go(self.server.get_url())
            self.assertTrue(os.path.exists(grab.doc.body_path))
            self.assertTrue(tmp_dir in grab.doc.body_path)
            with open(grab.doc.body_path, "rb") as inp:
                self.assertEqual(b"foo", inp.read())
            # pylint: disable=protected-access
            self.assertEqual(grab.doc._bytes_body, None)
            # pylint: enable=protected-access
            old_path = grab.doc.body_path

            grab.go(self.server.get_url())
            self.assertTrue(old_path != grab.doc.body_path)

        with temp_dir() as tmp_dir:
            self.server.add_response(Response(data=b"foo"))
            grab = build_grab()
            grab.setup(body_inmemory=False)
            grab.setup(body_storage_dir=tmp_dir)
            grab.setup(body_storage_filename="music.mp3")
            grab.go(self.server.get_url())
            self.assertTrue(os.path.exists(grab.doc.body_path))
            self.assertTrue(tmp_dir in grab.doc.body_path)
            with open(grab.doc.body_path, "rb") as inp:
                self.assertEqual(b"foo", inp.read())
            self.assertEqual(os.path.join(tmp_dir, "music.mp3"), grab.doc.body_path)
            self.assertEqual(grab.doc.body, b"foo")
            # pylint: disable=protected-access
            self.assertEqual(grab.doc._bytes_body, None)
            # pylint: enable=protected-access

    def test_body_inmemory_true(self):
        grab = build_grab()
        self.server.add_response(Response(data=b"bar"))
        grab.go(self.server.get_url())
        # pylint: disable=protected-access
        self.assertEqual(grab.doc._bytes_body, b"bar")
        # pylint: enable=protected-access

    def test_assign_unicode_to_body(self):
        grab = build_grab()
        grab.doc.body = b"abc"
        grab.doc.body = b"def"

        with self.assertRaises(GrabMisuseError):
            grab.doc.body = "Спутник"

    def test_empty_response(self):
        self.server.add_response(Response(data=b""))
        grab = build_grab()
        grab.go(self.server.get_url())
        self.assertTrue(grab.doc.tree is not None)  # should not raise exception

    # def test_emoji_processing(self):
    #    #html = u'''
    #    #<html><body>
    #    #    <span class="a-color-base"> 👍🏻 </span>
    #    #</body></html>
    #    #'''.encode('utf-8')
    #    grab = build_grab()
    #    #print('>>',grab.doc('//span').text(),'<<')
    #    #import grab
    #    #grab = grab.Grab()
    #    #grab.go('https://github.com/lorien/grab/issues/199'
    #            '#issuecomment-269854859')
    #    grab.go('https://en.wikipedia.org/wiki/Emoji')
    #    grab.doc.select("//*")

    def test_doc_tree_notags_document(self):
        data = b"test"
        grab = build_grab(data)
        self.assertEqual(grab.doc("//html").text(), "test")

    def test_github_html_processing(self):
        # This test is for osx and py3.5
        # See: https://github.com/lorien/grab/issues/199

        # It should works with recent lxml builds on any platform.
        # In the past it failed on previous lxml releases on macos platform
        path = os.path.join(TEST_DIR, "files/github_showcases.html")
        with open(path, "rb") as inp:
            data = inp.read()
        self.server.add_response(Response(data=data))
        grab = build_grab()
        grab.go(self.server.get_url())
        items = []
        for elem in grab.doc('//a[contains(@class, "exploregrid-item")]'):
            items.append(grab.make_url_absolute(elem.attr("href")))
        self.assertTrue("tools-for-open-source" in items[2])

    def test_explicit_custom_charset(self):
        grab = build_grab(
            "<html><head></head><body><h1>привет</h1></body></html".encode("cp1251"),
            document_charset="cp1251",
        )
        self.assertEqual("привет", grab.doc.select("//h1").text())

    def test_json(self):
        self.server.add_response(Response(data=b'{"foo": "bar"}'))
        grab = build_grab()
        grab.go(self.server.get_url())
        self.assertEqual({"foo": "bar"}, grab.doc.json)
