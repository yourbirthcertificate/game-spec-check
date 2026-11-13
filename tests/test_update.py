"""The update check: versions, GitHub's answers, rate limits, the digest check and the exe swap."""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from specbench import net, paths, server, sources, update  # noqa: E402

EXE = b"MZ new exe bytes"
DIGEST = "sha256:" + hashlib.sha256(EXE).hexdigest()
ASSET_URL = update.DOWNLOAD_PREFIX + "v9.0.0/GameSpecCheck.exe"


def release(tag="v9.0.0", digest=DIGEST, **extra):
    data = {"tag_name": tag, "name": "Game Spec Check 9.0.0", "body": "Fixes.",
            "draft": False, "prerelease": False, "html_url": "https://evil.example/",
            "assets": [{"name": "GameSpecCheck.exe", "browser_download_url": ASSET_URL,
                        "size": len(EXE), "digest": digest}]}
    data.update(extra)
    return data


def http_error(code, headers=None):
    return urllib.error.HTTPError(update.API_URL, code, "err", headers or {}, io.BytesIO(b""))


class Versions(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(update.parse_version("v1.1.0"), (1, 1, 0, 0))
        self.assertEqual(update.parse_version("1.2"), (1, 2, 0, 0))
        self.assertEqual(update.parse_version(" v2.0.1.3 "), (2, 0, 1, 3))
        for bad in ("", "v", "latest", "v1.2.0-rc1", "1.2.3.4.5", "v1..2", None, 1.2, ["v1"]):
            with self.subTest(bad=bad):
                self.assertIsNone(update.parse_version(bad))

    def test_compare_numerically(self):
        self.assertTrue(update.is_newer("v1.10.0", "1.9.9"))
        self.assertTrue(update.is_newer("v1.1.1", "1.1.0"))
        self.assertTrue(update.is_newer("v2", "1.99.99"))
        self.assertFalse(update.is_newer("v1.1.0", "1.1.0"))
        self.assertFalse(update.is_newer("v1.1", "1.1.0"))
        self.assertFalse(update.is_newer("v1.0.9", "1.1.0"))
        self.assertFalse(update.is_newer("v9.0.0-beta", "1.1.0"))
        self.assertFalse(update.is_newer("v9.0.0", "dev"))

    def test_version_text(self):
        self.assertEqual(update.version_text("v1.2"), "1.2.0")
        self.assertEqual(update.version_text("v1.2.3.4"), "1.2.3.4")
        self.assertIsNone(update.version_text("nope"))


class ParseRelease(unittest.TestCase):
    def test_good_release(self):
        r = update.parse_release(update._trim(release()))
        self.assertEqual(r["version"], "9.0.0")
        self.assertEqual(r["asset"], {"url": ASSET_URL, "size": len(EXE),
                                      "sha256": DIGEST.split(":")[1]})
        # The page link is built from the tag, never taken from the answer.
        self.assertEqual(r["url"], update.RELEASES_URL + "/tag/v9.0.0")

    def test_unusable_answers(self):
        for raw in (None, [], "x", {}, {"tag_name": None}, {"tag_name": "latest"},
                    release(draft=True), release(prerelease=True), release(tag="v9.0.0-rc1")):
            with self.subTest(raw=raw):
                self.assertIsNone(update.parse_release(raw))
        with self.assertRaises(sources.SourceError):
            update._trim(["not", "a", "dict"])

    def test_bad_assets_are_dropped(self):
        def asset_of(**a):
            data = release()
            data["assets"][0].update(a)
            return update.parse_release(update._trim(data))["asset"]
        self.assertIsNone(asset_of(browser_download_url="https://evil.example/GameSpecCheck.exe"))
        self.assertIsNone(asset_of(browser_download_url=None))
        self.assertIsNone(asset_of(name="Other.exe"))
        self.assertIsNone(asset_of(digest=None)["sha256"])
        self.assertIsNone(asset_of(digest="md5:abc")["sha256"])
        self.assertIsNone(asset_of(digest="sha256:" + "A" * 64)["sha256"])
        self.assertIsNone(asset_of(size=True)["size"])
        self.assertIsNone(asset_of(size="12")["size"])
        self.assertIsNone(update.parse_release(update._trim(release(assets="x")))["asset"])
        self.assertIsNone(update.parse_release(update._trim(release(assets=[1, None])))["asset"])

    def test_text_is_trimmed(self):
        r = update.parse_release(update._trim(release(name={"x": 1}, body="a\x00b\x1b" + "c" * 9000)))
        self.assertEqual(r["name"], "Game Spec Check 9.0.0")
        self.assertTrue(r["notes"].startswith("abc"))
        self.assertLessEqual(len(r["notes"]), update.MAX_NOTES + 1)

    def test_install_note(self):
        r = update.parse_release(update._trim(release()))
        with mock.patch.object(paths, "FROZEN", False):
            self.assertIn("git pull", update.install_note(r))
        with mock.patch.object(paths, "FROZEN", True):
            self.assertIsNone(update.install_note(r))
            r["asset"]["sha256"] = None
            self.assertIn("no checksum", update.install_note(r))
            r["asset"] = None
            self.assertIn("no GameSpecCheck.exe", update.install_note(r))


class Isolated(unittest.TestCase):
    """A temporary cache and data folder, a reset GitHub budget and no waiting."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gsc-update-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = [1000.0]
        patches = [mock.patch.object(sources, "CACHE_DIR", os.path.join(self.tmp, "cache")),
                   mock.patch.dict(os.environ, {"GSC_DATA_DIR": os.path.join(self.tmp, "data")}),
                   mock.patch.object(net, "_clock", lambda: self.now[0]),
                   mock.patch.object(net, "_sleep", lambda s: None),
                   mock.patch.object(update, "off_for_session", False),
                   mock.patch.object(update, "_last_failure", None),
                   mock.patch.object(update, "restart_requested", None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        net.GITHUB.reset()
        self.addCleanup(net.GITHUB.reset)
        self.calls = []

    def transport(self, *answers):
        answers = list(answers)

        def fake(url, timeout, follow):
            self.calls.append(url)
            a = answers.pop(0) if len(answers) > 1 else answers[0]
            if isinstance(a, Exception):
                raise a
            return a
        return mock.patch.object(net, "_transport", fake)


def ok(data):
    body = data if isinstance(data, bytes) else json.dumps(data).encode()
    return 200, {}, body


class Checking(Isolated):
    def test_newer_release_is_reported_and_cached(self):
        with self.transport(ok(release())):
            s = update.status()
            self.assertTrue(s["checked"])
            self.assertTrue(s["newer"])
            self.assertEqual(s["latest"], "9.0.0")
            self.assertEqual(s["url"], update.RELEASES_URL + "/tag/v9.0.0")
            update.status()                                  # from the cache
            self.assertEqual(self.calls, [update.API_URL])
            update.status(force=True)                        # "Check for updates"
            self.assertEqual(len(self.calls), 2)

    def test_same_version_is_not_newer(self):
        with self.transport(ok(release(tag="v" + update.__version__))):
            s = update.status()
        self.assertTrue(s["checked"])
        self.assertFalse(s["newer"])

    def test_only_the_generic_request_is_sent(self):
        with self.transport(ok(release())):
            update.status()
        self.assertEqual(self.calls, ["https://api.github.com/repos/yourbirthcertificate/game-spec-check/releases/latest"])
        self.assertNotIn("?", self.calls[0])

    def test_malformed_answers(self):
        for body in (b"not json", b"[1, 2]", b'"text"'):
            with self.subTest(body=body):
                update._last_failure = None
                with self.transport(ok(body)):
                    s = update.status(force=True)
                self.assertFalse(s["checked"])
                self.assertIn("unreadable", s["error"])
        with self.transport(ok({"tag_name": 5, "assets": "x"})):
            s = update.status(force=True)
        self.assertTrue(s["checked"])
        self.assertFalse(s["newer"])

    def test_rate_limit_403_pauses_without_more_requests(self):
        reset = str(int(time.time()) + 1800)
        err = http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset})
        with self.transport(err):
            s = update.status()
            self.assertIn("GitHub is limiting requests", s["error"])
            self.assertIn("about 30 minutes", s["error"])
            self.assertGreater(net.GITHUB.remaining_pause(), 1700)
            again = update.status(force=True)
        self.assertIn("limiting", again["error"])
        self.assertEqual(len(self.calls), 1)

    def test_rate_limit_429_with_retry_after(self):
        with self.transport(http_error(429, {"Retry-After": "90"})):
            s = update.status()
        self.assertIn("about 90 seconds", s["error"])
        self.assertEqual(len(self.calls), 1)                 # no retry for GitHub
        self.now[0] += 91
        self.assertEqual(net.GITHUB.remaining_pause(), 0)

    def test_plain_403_is_an_error_not_a_pause(self):
        with self.transport(http_error(403)):
            s = update.status()
        self.assertIn("HTTP 403", s["error"])
        self.assertEqual(net.GITHUB.remaining_pause(), 0)

    def test_failure_waits_before_the_next_automatic_check(self):
        with self.transport(urllib.error.URLError("offline"), ok(release())):
            self.assertIn("Could not reach GitHub", update.status()["error"])
            self.assertIn("Could not reach GitHub", update.status()["error"])
            self.assertEqual(len(self.calls), 1)
            self.assertTrue(update.status(force=True)["newer"])
            self.assertEqual(len(self.calls), 2)

    def test_setting_turns_the_automatic_check_off(self):
        update.set_enabled(False)
        with self.transport(ok(release())):
            s = update.status()
            self.assertFalse(s["enabled"])
            self.assertFalse(s["checked"])
            self.assertEqual(self.calls, [])
            self.assertTrue(update.status(force=True)["newer"])  # a click still checks
        update.set_enabled(True)
        self.assertTrue(update.enabled())
        with self.assertRaises(update.UpdateError):
            update.set_enabled("yes")

    def test_flag_turns_every_request_off(self):
        update.off_for_session = True
        with self.transport(ok(release())):
            self.assertFalse(update.status(force=True)["checked"])
            with self.assertRaises(update.UpdateError):
                update.latest(force=True)
        self.assertEqual(self.calls, [])


class Digest(unittest.TestCase):
    ASSET = {"sha256": DIGEST.split(":")[1], "size": len(EXE)}

    def test_match(self):
        update.verify(EXE, self.ASSET)

    def test_refusals(self):
        with self.assertRaisesRegex(update.UpdateError, "checksum"):
            update.verify(EXE + b"x", dict(self.ASSET, size=None))
        with self.assertRaisesRegex(update.UpdateError, "size"):
            update.verify(EXE + b"x", self.ASSET)
        with self.assertRaisesRegex(update.UpdateError, "no checksum"):
            update.verify(EXE, dict(self.ASSET, sha256=None))
        with self.assertRaisesRegex(update.UpdateError, "no checksum"):
            update.verify(EXE, None)


class Swap(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gsc-swap-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.cur = self.write("GameSpecCheck.exe", b"old")
        self.new = self.write("download.exe", b"new")

    def write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def read(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    def names(self):
        return sorted(os.listdir(self.dir))

    def test_swap(self):
        old = update.swap_in(self.cur, self.new)
        self.assertEqual(old, self.cur + ".old")
        self.assertEqual(self.read(self.cur), b"new")
        self.assertEqual(self.read(old), b"old")
        self.assertEqual(self.names(), ["GameSpecCheck.exe", "GameSpecCheck.exe.old", "download.exe"])

    def test_failed_move_in_keeps_the_old_exe(self):
        real = os.replace

        def flaky(src, dst):
            if src.endswith(".new"):
                raise PermissionError("locked")
            return real(src, dst)
        with mock.patch.object(update.os, "replace", flaky):
            with self.assertRaises(OSError):
                update.swap_in(self.cur, self.new)
        self.assertEqual(self.read(self.cur), b"old")
        self.assertEqual(self.names(), ["GameSpecCheck.exe", "download.exe"])

    def test_failed_rename_keeps_the_old_exe(self):
        real = os.replace

        def flaky(src, dst):
            if src == self.cur:
                raise PermissionError("locked")
            return real(src, dst)
        with mock.patch.object(update.os, "replace", flaky):
            with self.assertRaises(OSError):
                update.swap_in(self.cur, self.new)
        self.assertEqual(self.read(self.cur), b"old")
        self.assertEqual(self.names(), ["GameSpecCheck.exe", "download.exe"])

    def test_failed_copy_changes_nothing(self):
        with self.assertRaises(OSError):
            update.swap_in(self.cur, os.path.join(self.dir, "missing.exe"))
        self.assertEqual(self.read(self.cur), b"old")
        self.assertEqual(self.names(), ["GameSpecCheck.exe", "download.exe"])

    def test_locked_old_file_gets_another_name(self):
        self.write("GameSpecCheck.exe.old", b"older")
        real = update._remove
        with mock.patch.object(update, "_remove", lambda p: False if p.endswith(".old") else real(p)):
            old = update.swap_in(self.cur, self.new)
        self.assertRegex(os.path.basename(old), r"^GameSpecCheck\.exe\.old-\d+$")
        self.assertEqual(self.read(self.cur), b"new")

    def test_roll_back(self):
        old = update.swap_in(self.cur, self.new)
        update.roll_back(self.cur, old)
        self.assertEqual(self.read(self.cur), b"old")
        self.assertEqual(self.read(self.cur + ".failed"), b"new")

    def test_clean_up_removes_only_leftovers(self):
        for n in ("GameSpecCheck.exe.old", "GameSpecCheck.exe.old-123", "GameSpecCheck.exe.new",
                  "GameSpecCheck.exe.failed", "GameSpecCheck.exe.older", "Other.exe.old"):
            self.write(n, b"x")
        self.assertTrue(update.clean_up(self.cur, tries=1))
        self.assertEqual(self.names(), ["GameSpecCheck.exe", "GameSpecCheck.exe.older",
                                        "Other.exe.old", "download.exe"])

    def test_clean_up_from_source_does_nothing(self):
        with mock.patch.object(paths, "FROZEN", False):
            self.assertTrue(update.clean_up())


class Install(Isolated):
    def setUp(self):
        super().setUp()
        self.exe = os.path.join(self.tmp, "app", "GameSpecCheck.exe")
        os.makedirs(os.path.dirname(self.exe))
        with open(self.exe, "wb") as fh:
            fh.write(b"old exe")
        for p in (mock.patch.object(paths, "FROZEN", True),
                  mock.patch.object(paths, "UPDATE_DIR", os.path.join(self.tmp, "update"), create=True),
                  mock.patch.object(sys, "executable", self.exe),
                  mock.patch.object(update, "_runs", lambda exe, version: None)):
            p.start()
            self.addCleanup(p.stop)

    def exe_bytes(self):
        with open(self.exe, "rb") as fh:
            return fh.read()

    def test_from_source_says_git_pull(self):
        with mock.patch.object(paths, "FROZEN", False):
            with self.assertRaisesRegex(update.UpdateError, "git pull"):
                update.install()

    def test_digest_mismatch_is_refused_and_nothing_changes(self):
        tampered = b"MZ bad exe bytes"
        self.assertEqual(len(tampered), len(EXE))            # same size, so only the digest differs
        with self.transport(ok(release()), ok(tampered)):
            with self.assertRaisesRegex(update.UpdateError, "checksum"):
                update.install()
        self.assertEqual(self.exe_bytes(), b"old exe")
        self.assertEqual(os.listdir(os.path.dirname(self.exe)), ["GameSpecCheck.exe"])
        self.assertIsNone(update.restart_requested)

    def test_missing_digest_is_refused_before_downloading(self):
        with self.transport(ok(release(digest=None))):
            with self.assertRaisesRegex(update.UpdateError, "no checksum"):
                update.install()
        self.assertEqual(self.calls, [update.API_URL])
        self.assertEqual(self.exe_bytes(), b"old exe")

    def test_not_newer_is_refused(self):
        with self.transport(ok(release(tag="v1.0.0"))):
            with self.assertRaisesRegex(update.UpdateError, "no newer"):
                update.install()

    def test_good_download_is_swapped_in(self):
        with self.transport(ok(release()), ok(EXE)):
            done = update.install()
        self.assertEqual(done, {"ok": True, "version": "9.0.0", "restarting": True})
        self.assertEqual(self.calls, [update.API_URL, ASSET_URL])
        self.assertEqual(self.exe_bytes(), EXE)
        self.assertTrue(os.path.isfile(self.exe + ".old"))
        self.assertEqual(update.restart_requested, self.exe)
        self.assertEqual(os.listdir(paths.UPDATE_DIR), [])

    def test_exe_that_does_not_run_is_not_swapped_in(self):
        def broken(exe, version):
            raise update.UpdateError("The new version did not start correctly, so it was not installed.")
        with mock.patch.object(update, "_runs", broken), self.transport(ok(release()), ok(EXE)):
            with self.assertRaisesRegex(update.UpdateError, "did not start"):
                update.install()
        self.assertEqual(self.exe_bytes(), b"old exe")
        self.assertIsNone(update.restart_requested)


class Redirects(unittest.TestCase):
    def test_https_never_redirects_to_http(self):
        h = net._NoDowngrade()
        req = urllib.request.Request("https://github.com/x")
        self.assertIsNone(h.redirect_request(req, None, 302, "Found", {}, "http://example.com/x"))
        self.assertIsNotNone(h.redirect_request(req, None, 302, "Found", {}, "https://example.com/x"))

    def test_wait_words(self):
        self.assertEqual(net._wait_words(0.2), "about 1 second")
        self.assertEqual(net._wait_words(90), "about 90 seconds")
        self.assertEqual(net._wait_words(1800), "about 30 minutes")


class Api(Isolated):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.make_server(0, tries=1)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, path, body, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return json.loads(r.read())

    def test_status_when_turned_off(self):
        update.off_for_session = True
        s = self.get("/api/update?force=1")
        self.assertTrue(s["off_for_session"])
        self.assertFalse(s["checked"])

    def test_settings_are_a_checked_post(self):
        self.assertEqual(self.post("/api/settings", {"update_check": False},
                                   {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.post("/api/settings", {"update_check": False},
                                   {"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertTrue(update.enabled())
        status, data = self.post("/api/settings", {"update_check": False}, {"Origin": self.base})
        self.assertEqual(status, 200, data)
        self.assertFalse(data["update_check"])
        self.assertFalse(update.enabled())
        self.assertEqual(self.post("/api/settings", {"update_check": "no"})[0], 400)

    def test_install_is_post_only_and_refused_from_source(self):
        try:
            urllib.request.urlopen(self.base + "/api/update/install", timeout=5)
            self.fail("GET was accepted")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 405)
        self.assertEqual(self.post("/api/update/install", {}, {"Origin": "http://evil.example"})[0], 403)
        status, data = self.post("/api/update/install", {})
        self.assertEqual(status, 400)
        self.assertIn("git pull", data["error"])


if __name__ == "__main__":
    unittest.main()
