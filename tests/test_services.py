"""Rate limiting, saved data, pasted requirements, reports, the planner and startup."""
import email.utils
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run  # noqa: E402
from specbench import checks, net, paste, planner, report, server, sources, store, userdata  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.now += s


def http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("http://x", code, "err", headers, io.BytesIO(b""))


class RateLimits(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.patches = [mock.patch.object(net, "_clock", self.clock.clock),
                        mock.patch.object(net, "_sleep", self.clock.sleep)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_requests_are_spaced_with_a_monotonic_clock(self):
        host = net.Host("T", min_interval=1.5)
        with mock.patch.object(net, "_transport", lambda *a: (200, {}, b"ok")):
            for _ in range(3):
                net.fetch(host, "http://x")
        self.assertEqual(self.clock.slept, [1.5, 1.5])

    def test_429_honours_retry_after_then_succeeds(self):
        host = net.Host("T", min_interval=0.1)
        answers = [http_error(429, "7"), (200, {}, b"ok")]

        def transport(*a):
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        with mock.patch.object(net, "_transport", transport):
            self.assertEqual(net.fetch(host, "http://x")[2], b"ok")
        self.assertIn(7.0, self.clock.slept)

    def test_backoff_without_retry_after_and_give_up(self):
        host = net.Host("T", min_interval=0, backoff=2, max_retries=2)
        with mock.patch.object(net, "_transport", mock.Mock(side_effect=http_error(429))):
            with self.assertRaises(net.RateLimited) as cm:
                net.fetch(host, "http://x")
        self.assertEqual(self.clock.slept, [2.0, 4.0])
        self.assertEqual(cm.exception.retry_after, 8.0)

    def test_long_retry_after_fails_fast(self):
        host = net.Host("T", min_interval=0, max_wait=60)
        with mock.patch.object(net, "_transport", mock.Mock(side_effect=http_error(429, "600"))):
            with self.assertRaises(net.RateLimited):
                net.fetch(host, "http://x")
        self.assertEqual(self.clock.slept, [])

    def test_retry_after_http_date(self):
        when = email.utils.formatdate(1030, usegmt=True)
        self.assertAlmostEqual(net.retry_after_seconds(when, now=1000), 30, delta=1)
        self.assertIsNone(net.retry_after_seconds("soon"))

    def test_other_errors_are_source_errors(self):
        host = net.Host("T", min_interval=0)
        with mock.patch.object(net, "_transport", mock.Mock(side_effect=http_error(500))):
            with self.assertRaises(net.SourceError):
                net.fetch(host, "http://x")
        with mock.patch.object(net, "_transport", mock.Mock(side_effect=urllib.error.URLError("dns"))):
            with self.assertRaises(net.SourceError):
                net.fetch(host, "http://x")

    def test_concurrency_is_bounded(self):
        host = net.Host("T", min_interval=0, concurrency=1)
        active, peak, lock = [0], [0], threading.Lock()
        gate = threading.Event()

        def transport(*a):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            gate.wait(0.05)
            with lock:
                active[0] -= 1
            return 200, {}, b""
        with mock.patch.object(net, "_transport", transport):
            ts = [threading.Thread(target=net.fetch, args=(host, "u")) for _ in range(4)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(peak[0], 1)


class TempData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"GSC_DATA_DIR": self.tmp})
        self.env.start()
        store.recovered.clear()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class SavedData(TempData):
    def test_malformed_file_is_set_aside(self):
        with open(os.path.join(self.tmp, "profiles.json"), "w") as fh:
            fh.write("{broken")
        self.assertEqual(userdata.profiles(), [])
        self.assertEqual(store.recovered, ["profiles"])
        self.assertTrue(any(n.startswith("profiles.bad-") for n in os.listdir(self.tmp)))
        with open(os.path.join(self.tmp, "mappings.json"), "w") as fh:
            fh.write("[]")          # valid JSON, wrong shape
        self.assertEqual(userdata.mappings(), {})

    def test_profiles_round_trip_and_validation(self):
        p = userdata.save_profile({"name": "Planned build", "kind": "planned", "gpu": "RTX 4070", "ram": "32"})
        self.assertEqual(userdata.get_profile(p["id"])["ram"], 32.0)
        p2 = userdata.save_profile(dict(p, name="Planned build 2"))
        self.assertEqual(len(userdata.profiles()), 1)
        self.assertEqual(p2["id"], p["id"])
        for bad in ({"name": ""}, {"name": "x"}, {"name": "x", "ram": "lots"}, {"name": "x", "ram": 99999},
                    {"name": "x", "gpu": "a", "id": "../../etc"}):
            with self.assertRaises(userdata.InvalidData):
                userdata.save_profile(bad)
        userdata.delete_profile(p["id"])
        self.assertEqual(userdata.profiles(), [])

    def test_profile_specs_are_simulated(self):
        p = userdata.save_profile({"name": "Laptop", "kind": "laptop", "gpu": "GeForce RTX 3050", "vram": 4})
        detected = {"cpu": {"name": "x"}, "gpu": {"name": "Real GPU", "vram_gb": 8}, "adapters": [], "ram_gb": 16,
                    "simulated": False}
        sp = checks.specs_for(p["id"], detected=detected)
        self.assertTrue(sp["simulated"])
        self.assertEqual(sp["gpu"]["vram_gb"], 4)
        self.assertEqual(sp["label"], "Laptop")
        self.assertFalse(checks.specs_for("real", detected=detected)["simulated"])
        with self.assertRaises(checks.NotFound):
            checks.specs_for("0123456789ab", detected=detected)

    def test_wanted_games(self):
        w = userdata.add_wanted({"source": "steam", "ref": "1245620", "title": "Elden Ring"})
        userdata.add_wanted({"source": "steam", "ref": "1245620", "title": "Elden Ring"})
        self.assertEqual(len(userdata.wanted()), 1)
        p = userdata.add_wanted({"source": "pasted", "title": "Box game", "attribution": "Back of the box",
                                 "levels": {"minimum": {"fields": {"cpu": "Core i5-8400", "evil": "x"},
                                                        "raw": "Processor: Core i5-8400"}}})
        self.assertEqual(set(p["levels"]["minimum"]["fields"]), {"cpu"})
        with self.assertRaises(userdata.InvalidData):
            userdata.add_wanted({"source": "steam", "ref": "abc", "title": "x"})
        userdata.remove_wanted(w["id"])
        self.assertEqual([x["source"] for x in userdata.wanted()], ["pasted"])

    def test_mappings(self):
        userdata.set_mapping("title:hades", "steam", "1145360", "Hades")
        self.assertEqual(userdata.mappings()["title:hades"]["id"], "1145360")
        with self.assertRaises(userdata.InvalidData):
            userdata.set_mapping("title:x", "steam", "not-a-number")
        userdata.clear_mapping("title:hades")
        self.assertEqual(userdata.mappings(), {})


class LibraryStore(TempData):
    def test_saved_scan_survives_reload_and_choices(self):
        from specbench import library
        from specbench.discovery import model
        game = model.make_install("epic", "Hades", provider_id="h")
        odd = model.make_install("programs", "Odd Tool", path=self.tmp, confidence="low")
        lib = library.apply_scan(store._copy(library.DEFAULT),
                                 {"epic": {"status": "ok", "installs": [game]},
                                  "programs": {"status": "ok", "installs": [odd]}}, now=5)
        store.save("library", lib)
        v = library.view()
        self.assertTrue(v["scanned"])
        self.assertEqual([g["title"] for g in v["games"]], ["Hades"])
        self.assertEqual([i["title"] for i in v["review"]], ["Odd Tool"])
        library.decide(odd["install_id"], "accept")
        self.assertEqual(len(library.view()["games"]), 2)
        gid = library.view()["games"][0]["game_id"]
        library.set_hidden(gid, True)
        self.assertEqual(len(library.view()["hidden"]), 1)
        with self.assertRaises(library.LibraryError):
            library.launch("i_missing")
        with self.assertRaises(library.LibraryError):
            library.edit_folders("add", "relative\\path")

    def test_launch_uses_stored_action_only(self):
        from specbench import library
        from specbench.discovery import model
        game = model.make_install("steam", "Game", provider_id="10",
                                  launch={"kind": "uri", "target": "steam://rungameid/10"})
        bad = model.make_install("epic", "Bad", provider_id="b",
                                 launch={"kind": "uri", "target": "https://example.com/x"})
        store.save("library", library.apply_scan(store._copy(library.DEFAULT),
                                                 {"steam": {"status": "ok", "installs": [game, bad]}}))
        opened = []
        library.launch(game["install_id"], opener=opened.append)
        self.assertEqual(opened, ["steam://rungameid/10"])
        with self.assertRaises(library.LibraryError):
            library.launch(bad["install_id"], opener=opened.append)
        self.assertEqual(len(opened), 1)


class Pasted(unittest.TestCase):
    TEXT = """MINIMUM:
    OS: Windows 10 64-bit
    Processor: Intel Core i5-8400 / AMD Ryzen 5 2600
    Memory: 8 GB RAM
    Graphics: NVIDIA GeForce GTX 1060 6GB
    Storage: 60 GB available space
    RECOMMENDED:
    \u2022 OS: Windows 11
    \u2022 Processor: Intel Core i7-10700K
    \u2022 Memory: 16 GB RAM
    \u2022 Graphics: RTX 3070
    """

    def test_split_and_labelled_fields(self):
        levels = paste.parse(self.TEXT)
        self.assertEqual(set(levels), {"minimum", "recommended"})
        mn = levels["minimum"]
        self.assertEqual(mn["fields"]["ram"], "8 GB RAM")
        self.assertEqual(mn["uncertain"], [])
        self.assertEqual(levels["recommended"]["fields"]["gpu"], "RTX 3070")
        self.assertIn("Memory: 8 GB RAM", mn["raw"])

    def test_unlabelled_lines_are_marked_uncertain(self):
        lv = paste.parse("Needs a GTX 970 or better\n8 GB of RAM\nWindows 10\nruns on a toaster")["minimum"]
        self.assertEqual(lv["fields"]["gpu"], "Needs a GTX 970 or better")
        self.assertEqual(sorted(lv["uncertain"]), ["gpu", "os", "ram"])
        self.assertNotIn("cpu", lv["fields"])
        self.assertIn("runs on a toaster", lv["unlabelled"])

    def test_two_boxes(self):
        levels = paste.parse("Memory: 4 GB", "Memory: 8 GB")
        self.assertEqual(levels["recommended"]["fields"]["ram"], "8 GB")

    def test_vague_labelled_field_is_uncertain(self):
        lv = paste.parse_level("Processor: a fast one, 3 GHz")
        self.assertIn("cpu", lv["uncertain"])


class Reports(unittest.TestCase):
    DATA = {
        "name": "Test <Game> & Co", "source_name": "Steam", "profile_label": "C:\\Users\\alice\\laptop",
        "summary": {"tier": "fail", "headline": "Below the minimum specs", "detail": "Installed at D:\\Games\\x",
                    "issues": [{"text": "Your graphics card is too slow.", "status": "fail"}],
                    "not_checked": ["an SSD"]},
        "levels": {"minimum": {"verdict": "fail", "rows": [
            {"key": "gpu", "label": "Graphics", "required": "GTX 1060", "yours": "HD 4000", "status": "fail"}]}},
    }

    def test_escaped_and_private(self):
        svg = report.build_svg(self.DATA)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("Test &lt;Game&gt; &amp; Co", svg)
        self.assertNotIn("<Game>", svg)
        self.assertNotIn("D:\\Games", svg)
        self.assertNotIn("C:\\Users", svg)
        self.assertIn("Requirements from Steam", svg)
        self.assertIn("an SSD", svg)

    def test_user_name_is_removed(self):
        self.assertEqual(report.scrub("made by Alice on alice-pc", {"alice"}), "made by [user] on [user]-pc")

    def test_bad_input_does_not_crash(self):
        svg = report.build_svg({"levels": {"minimum": {"rows": ["x", None]}}, "summary": {"issues": [1]}})
        self.assertIn("</svg>", svg)


class Planner(unittest.TestCase):
    def st(self, base, rec=None, group=None):
        g = group or {"pass": "met", "close": "borderline", "fail": "failing"}.get(base, "incomplete")
        return {"minimum": base, "recommended": rec, "base": base, "group": g, "limits": [], "unchecked": []}

    def test_categories(self):
        d = planner.diff
        self.assertEqual(d(self.st("fail", "fail"), self.st("pass", "fail")), "newly_minimum")
        self.assertEqual(d(self.st("pass", "fail"), self.st("pass", "pass")), "newly_recommended")
        self.assertEqual(d(self.st("fail"), self.st("fail")), "still_below")
        self.assertEqual(d(self.st("fail"), self.st("partial")), "incomplete")
        self.assertEqual(d(self.st("partial"), self.st("pass")), "now_met")
        self.assertEqual(d(self.st("pass", "pass"), self.st("pass", "close")), "worse")
        self.assertEqual(d(self.st("pass"), self.st("pass")), "no_change")
        self.assertEqual(d(self.st("fail"), self.st("close")), "borderline")
        self.assertEqual(d(self.st("pass"), self.st("fail")), "worse")


class Server(TempData):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.make_server(0, tries=1)
        cls.port = cls.httpd.server_address[1]
        cls.base = "http://127.0.0.1:%d" % cls.port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, path, body, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_cross_site_posts_are_refused(self):
        body = {"name": "x", "gpu": "y"}
        self.assertEqual(self.post("/api/profiles/save", body, {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.post("/api/profiles/save", body, {"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.post("/api/profiles/save", body, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.post("/api/profiles/save", body, {"Host": "evil.example"})[0], 403)
        status, data = self.post("/api/profiles/save", body, {"Origin": self.base})
        self.assertEqual(status, 200, data)

    def test_launch_only_known_installs(self):
        status, data = self.post("/api/launch", {"install_id": "i_nope"})
        self.assertEqual(status, 400)
        with urllib.request.urlopen(self.base + "/api/state", timeout=5) as r:
            self.assertIn("profiles", json.loads(r.read()))

    def test_get_on_post_route(self):
        try:
            urllib.request.urlopen(self.base + "/api/launch", timeout=5)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 405)

    def test_paste_endpoints(self):
        status, data = self.post("/api/paste/parse", {"minimum": "Memory: 8 GB\nGraphics: GTX 1060"})
        self.assertEqual(status, 200)
        self.assertEqual(data["levels"]["minimum"]["fields"]["ram"], "8 GB")

    def test_report_endpoint(self):
        status, data = self.post("/api/report", Reports.DATA)
        self.assertEqual(status, 200)
        self.assertIn("<svg", data["svg"])


class SourcesFallback(unittest.TestCase):
    def test_steam_failure_falls_back_to_pcgw(self):
        sp = {"cpu": None, "gpu": None, "ram_gb": 16, "os": {}, "best_drive": None}
        with mock.patch.object(sources, "steam_game", side_effect=sources.SourceError("Steam is down")), \
                mock.patch.object(sources, "pcgw_title_for_appid", return_value="Some Game"), \
                mock.patch.object(sources, "pcgw_game", return_value={
                    "name": "Some Game", "levels": {"minimum": {"fields": {"ram": "8 GB"}, "raw": ""}}}):
            res = checks.check_identity({"source": "steam", "id": "1"}, sp)
        self.assertEqual(res["source"], "pcgw")
        self.assertEqual(res["errors"], {"steam": "Steam is down"})
        self.assertIn("PCGamingWiki", res["fallback_note"])

    def test_both_failing_raises(self):
        with mock.patch.object(sources, "steam_game", side_effect=sources.SourceError("a")), \
                mock.patch.object(sources, "pcgw_title_for_appid", side_effect=sources.SourceError("b")):
            with self.assertRaises(sources.SourceError):
                checks.check_identity({"source": "steam", "id": "1"}, {})


class Startup(unittest.TestCase):
    def serve(self, payload):
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = payload.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        httpd = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def test_unrelated_service_answers(self):
        for payload in ("[]", "null", '"x"', "not json", '{"app": "other"}'):
            with self.subTest(payload=payload):
                self.assertFalse(run.already_running(self.serve(payload)))
        self.assertTrue(run.already_running(self.serve('{"app": "game-spec-check"}')))

    def test_finds_copy_on_fallback_port(self):
        other = self.serve("[]")
        httpd = server.make_server(other, tries=3)        # skips the busy port
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            self.assertNotEqual(httpd.server_address[1], other)
            self.assertEqual(run.find_running(other, tries=3), httpd.server_address[1])
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
