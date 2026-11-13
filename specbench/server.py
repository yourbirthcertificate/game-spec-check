"""Local web server: serves the interface and a small JSON API.

Listens on 127.0.0.1 only. Nothing about this PC leaves the machine; the
only outbound requests are game lookups to Steam and PCGamingWiki.
"""
import json
import mimetypes
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, check_game, matcher, sources, specs, steam_library
from .paths import WEB_DIR, legal_file

# Windows takes these from the registry, where .js is sometimes text/plain.
_TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
          ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
APP_ID = "game-spec-check"


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _effective_specs(q):
    ram = q.get("ram")
    try:
        ram = float(ram) if ram else None
    except ValueError:
        raise ApiError(400, "ram must be a number of gigabytes.")
    if ram is not None and not 0.25 <= ram <= 2048:
        raise ApiError(400, "ram is out of range.")
    return specs.with_overrides(specs.detect(), q.get("cpu"), q.get("gpu"), ram)


def _search(q):
    term = (q.get("q") or "").strip()
    if len(term) < 2:
        return {"results": []}
    found = {"steam": [], "pcgw": []}
    errors = {}

    def run(name, fn):
        try:
            found[name] = fn(term)
        except sources.SourceError as e:
            errors[name] = str(e)

    threads = [threading.Thread(target=run, args=(n, f)) for n, f in
               (("steam", sources.search_steam), ("pcgw", sources.search_pcgw))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors.get("steam") and not found["pcgw"]:
        raise ApiError(502, errors["steam"])

    def norm(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())

    seen = {norm(r["name"]) for r in found["steam"]}
    extra = [r for r in found["pcgw"] if norm(r["name"]) not in seen]
    return {"results": found["steam"] + extra, "warning": errors.get("steam")}


def _game(q):
    source, ident = q.get("source", "steam"), (q.get("id") or "").strip()
    if source not in ("steam", "pcgw") or not ident:
        raise ApiError(400, "Expected source=steam|pcgw and an id.")
    if source == "steam" and not ident.isdigit():
        raise ApiError(400, "A Steam id is a number.")
    return check_game(source, ident, _effective_specs(q))


def _pcgw_for(q):
    """Second opinion: the PCGamingWiki page for a Steam app id, if any."""
    appid = (q.get("appid") or "").strip()
    if not appid.isdigit():
        raise ApiError(400, "appid must be a number.")
    title = sources.pcgw_title_for_appid(appid)
    if not title:
        return {"found": False}
    game = check_game("pcgw", title, _effective_specs(q))
    game["found"] = bool(game["levels"])
    return game


def _hardware(q):
    kind = q.get("kind")
    if kind not in ("cpu", "gpu"):
        raise ApiError(400, "kind must be cpu or gpu.")
    text = (q.get("q") or "").strip()
    out = {"results": matcher.search_hardware(kind, text) if text else []}
    if text:
        d = specs.describe_gpu(text) if kind == "gpu" else specs.describe_cpu(text)
        out["reading"] = d if d["score"] else None
    return out


ROUTES = {
    "/api/ping": lambda q: {"app": APP_ID, "version": __version__},
    "/api/specs": lambda q: specs.detect(refresh=q.get("refresh") == "1"),
    "/api/search": _search,
    "/api/game": _game,
    "/api/pcgw-for": _pcgw_for,
    "/api/library": lambda q: steam_library.installed_games(),
    "/api/hardware": _hardware,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "GameSpecCheck"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        # Only answer requests addressed to this machine by its loopback
        # name, so another website cannot reach the API through DNS tricks.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            return self._json(403, {"error": "Local access only."})
        url = urlparse(self.path)
        if url.path.startswith("/legal/"):
            path = legal_file(url.path[len("/legal/"):])
            if not path:
                return self._send(404, b"Not found", "text/plain; charset=utf-8")
            with open(path, "rb") as fh:
                return self._send(200, fh.read(), "text/plain; charset=utf-8")
        if url.path in ROUTES:
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                return self._json(200, ROUTES[url.path](q))
            except ApiError as e:
                return self._json(e.status, {"error": str(e)})
            except sources.SourceError as e:
                return self._json(502, {"error": str(e)})
            except Exception as e:      # keep the server alive, show the cause
                return self._json(500, {"error": "%s: %s" % (type(e).__name__, e)})
        self._static(url.path)

    def _static(self, path):
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, name))
        if not full.startswith(WEB_DIR + os.sep) or not os.path.isfile(full):
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        ctype = (_TYPES.get(os.path.splitext(full)[1].lower())
                 or mimetypes.guess_type(full)[0] or "application/octet-stream")
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype)


def make_server(port=8765, verbose=False, tries=10):
    last = None
    for p in range(port, port + tries):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
        except OSError as e:
            last = e
            continue
        httpd.daemon_threads = True
        httpd.verbose = verbose
        return httpd
    raise last
