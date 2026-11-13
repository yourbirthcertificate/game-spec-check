"""License files are present, served, and cannot be used to read other files."""
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from specbench import paths, server  # noqa: E402


class LegalFiles(unittest.TestCase):
    def read(self, name):
        path = paths.legal_file(name)
        self.assertIsNotNone(path, name)
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_license_is_polyform_noncommercial(self):
        self.assertTrue(self.read("LICENSE").startswith("# PolyForm Noncommercial License 1.0.0"))

    def test_notice_has_required_notice_line_and_statements(self):
        text = self.read("NOTICE")
        self.assertRegex(text, r"(?m)^Required Notice: Copyright ")
        for phrase in ("Claude", "Commercial use is not allowed", "not accepting donations"):
            self.assertIn(phrase, text)

    def test_credits_and_python_license_exist(self):
        self.assertIn("PCGamingWiki", self.read("CREDITS.md"))
        self.assertIn("PYTHON SOFTWARE FOUNDATION LICENSE", self.read("PSF-LICENSE.txt").upper())

    def test_unknown_names_are_refused(self):
        for name in ("../run.py", "run.py", "..\\run.py", "licenses/PSF-LICENSE.txt", ""):
            self.assertIsNone(paths.legal_file(name), name)


class LegalRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.make_server(0, tries=1)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, r.headers.get("Content-Type"), r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, None, ""

    def test_license_and_about_are_served(self):
        status, ctype, body = self.get("/legal/LICENSE")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/plain"))
        self.assertIn("PolyForm Noncommercial", body)
        status, ctype, body = self.get("/about.html")
        self.assertEqual((status, ctype.split(";")[0]), (200, "text/html"))
        self.assertIn("No donations", body)

    def test_other_paths_under_legal_are_404(self):
        for path in ("/legal/run.py", "/legal/..%2Frun.py", "/legal/", "/legal/%2e%2e/run.py"):
            self.assertEqual(self.get(path)[0], 404, path)


if __name__ == "__main__":
    unittest.main()
