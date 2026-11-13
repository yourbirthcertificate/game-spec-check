"""Start the app (the built exe or run.py) and check that it works end to end.

    python tools/smoke.py dist\\GameSpecCheck.exe
    python tools/smoke.py run.py [--no-specs]

Uses a free port and a temporary data folder, sends no requests to Steam,
PCGamingWiki or GitHub (the app runs with --no-update-check), and stops the
app afterwards. Exit code 0 means every check passed.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def command(target, *args):
    if target.lower().endswith(".py"):
        return [sys.executable, target] + list(args)
    return [target] + list(args)


def get(base, path, timeout=10):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def post(base, path, body, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(base + path, json.dumps(body).encode(), h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = os.path.abspath(args[0] if args else os.path.join(ROOT, "dist", "GameSpecCheck.exe"))
    check_specs = "--no-specs" not in sys.argv
    failures = []

    def check(ok, what):
        print(("ok    " if ok else "FAIL  ") + what, flush=True)
        if not ok:
            failures.append(what)

    out = subprocess.run(command(target, "--version"), capture_output=True, text=True, timeout=120)
    check(out.returncode == 0 and "Game Spec Check" in out.stdout, "--version prints the version")

    port = free_port()
    env = dict(os.environ, GSC_DATA_DIR=tempfile.mkdtemp(prefix="gsc-smoke-"))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(command(target, "--no-browser", "--no-update-check", "--port", str(port)), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
    base = "http://127.0.0.1:%d" % port
    try:
        deadline = time.time() + 90           # a onefile exe unpacks itself first
        ping = None
        while time.time() < deadline and proc.poll() is None:
            try:
                ping = json.loads(get(base, "/api/ping", 2)[2])
                break
            except (OSError, ValueError):
                time.sleep(0.5)
        check(isinstance(ping, dict) and ping.get("app") == "game-spec-check", "the app answers /api/ping")
        if not ping:
            return report(failures, proc)

        for path, ctype, needle in (("/", "text/html", b"Game Spec Check"),
                                    ("/app.js", "text/javascript", b"openGame"),
                                    ("/logic.js", "text/javascript", b"SearchState"),
                                    ("/update.js", "text/javascript", b"GSCUpdate"),
                                    ("/style.css", "text/css", b".lib-row"),
                                    ("/about.html", "text/html", b"No donations"),
                                    ("/about.html", "text/html", b"does not show who built it"),
                                    ("/legal/LICENSE", "text/plain", b"PolyForm Noncommercial"),
                                    ("/legal/NOTICE", "text/plain", b"Required Notice"),
                                    ("/legal/CREDITS.md", "text/plain", b"PCGamingWiki"),
                                    ("/legal/PSF-LICENSE.txt", "text/plain", b"PYTHON SOFTWARE FOUNDATION")):
            try:
                status, got, body = get(base, path)
                check(status == 200 and got.startswith(ctype) and needle in body, "serves %s" % path)
            except OSError as e:
                check(False, "serves %s (%s)" % (path, e))
        try:
            get(base, "/legal/..%2Frun.py")
            check(False, "refuses paths outside the legal files")
        except urllib.error.HTTPError as e:
            check(e.code == 404, "refuses paths outside the legal files")

        state = json.loads(get(base, "/api/state")[2])
        check(isinstance(state.get("profiles"), list) and len(state.get("providers", [])) >= 10,
              "/api/state lists profiles and discovery providers")
        upd = json.loads(get(base, "/api/update?force=1")[2])
        check(upd.get("off_for_session") is True and upd.get("checked") is False,
              "--no-update-check stops the update check")
        status, body = post(base, "/api/update/install", {})
        check(status == 400, "update install is refused while checks are off")
        status, body = post(base, "/api/paste/parse", {"minimum": "Memory: 8 GB\nGraphics: GTX 1060"})
        check(status == 200 and body["levels"]["minimum"]["fields"].get("ram") == "8 GB", "parses pasted requirements")
        status, body = post(base, "/api/report", {"name": "Smoke", "summary": {"headline": "x"}})
        check(status == 200 and body["svg"].startswith("<svg"), "makes a report image")
        status, _ = post(base, "/api/profiles/save", {"name": "x", "gpu": "y"}, {"Origin": "http://evil.example"})
        check(status == 403, "refuses cross-site requests")
        if check_specs:
            specs = json.loads(get(base, "/api/specs", 120)[2])
            check(bool(specs.get("cpu", {}).get("name")) and "adapters" in specs, "reads this PC's hardware")

        second = subprocess.run(command(target, "--no-browser", "--port", str(port)), env=env,
                                capture_output=True, text=True, timeout=120, creationflags=flags)
        check(second.returncode == 0 and "already running" in second.stdout,
              "a second start finds the running copy")
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return report(failures, proc)


def report(failures, proc):
    if failures:
        try:
            print(proc.stdout.read().decode("utf-8", "replace")[-2000:])
        except (OSError, ValueError, AttributeError):
            pass
        print("%d check(s) failed." % len(failures))
        return 1
    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
