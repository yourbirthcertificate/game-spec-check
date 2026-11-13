"""Local web server: serves the interface and a small JSON API.

Listens on 127.0.0.1 only and answers only requests addressed to it by its
loopback name. Hardware details, install folders and saved data never leave
the machine; the only outbound requests are game lookups (ids and search
terms) to Steam and PCGamingWiki.

Requests that change something or start a program are POSTs with a JSON body.
A web page on another site cannot send those: a cross-site JSON POST needs a
CORS preflight, which this server never approves, and the Origin and
Sec-Fetch-Site headers are checked as well.
"""
import json
import mimetypes
import os
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import (__version__, checks, discovery, library, matcher, paste, planner, report,
               sources, specs, store, userdata)
from .paths import WEB_DIR, legal_file

# Windows takes these from the registry, where .js is sometimes text/plain.
_TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
          ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
APP_ID = "game-spec-check"
PORT_TRIES = 10
MAX_BODY = 256 * 1024


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _num(q, key, lo, hi):
    v = q.get(key)
    if v in (None, ""):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ApiError(400, "%s must be a number." % key)
    if not lo <= n <= hi:
        raise ApiError(400, "%s is out of range." % key)
    return n


def _effective_specs(q):
    return checks.specs_for(q.get("profile"), q.get("cpu"), q.get("gpu"),
                            _num(q, "ram", 0.25, 2048), _num(q, "vram", 0.0625, 256))


def _search(q):
    term = (q.get("q") or "").strip()[:100]
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
    if errors.get("steam") and errors.get("pcgw"):
        raise ApiError(502, errors["steam"])

    def norm(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())

    seen = {norm(r["name"]) for r in found["steam"]}
    extra = [r for r in found["pcgw"] if norm(r["name"]) not in seen]
    warning = " ".join(errors.values()) or None
    return {"results": found["steam"] + extra, "warning": warning, "term": term}


def _game(q):
    source, ident = q.get("source", "steam"), (q.get("id") or "").strip()
    if source not in ("steam", "pcgw") or not ident:
        raise ApiError(400, "Expected source=steam|pcgw and an id.")
    if source == "steam" and not ident.isdigit():
        raise ApiError(400, "A Steam id is a number.")
    return checks.check_identity({"source": source, "id": ident}, _effective_specs(q),
                                 installed=q.get("installed") == "1")


def _pcgw_for(q):
    appid = (q.get("appid") or "").strip()
    if not appid.isdigit():
        raise ApiError(400, "appid must be a number.")
    return checks.second_opinion(appid, _effective_specs(q), installed=q.get("installed") == "1")


def _hardware(q):
    kind = q.get("kind")
    if kind not in ("cpu", "gpu"):
        raise ApiError(400, "kind must be cpu or gpu.")
    text = (q.get("q") or "").strip()[:120]
    out = {"results": matcher.search_hardware(kind, text) if text else []}
    if text:
        d = specs.describe_gpu(text) if kind == "gpu" else specs.describe_cpu(text)
        out["reading"] = d if d["score"] else None
    return out


def _target(q):
    target = (q.get("target") or "").strip()
    if not re.fullmatch(r"[gw]_[0-9a-f]{12}", target):
        raise ApiError(400, "target must be a library or wanted game id.")
    return target


def _plan(q):
    pids = [p for p in (q.get("profiles") or "").split(",") if p][:6]
    if not all(p == "real" or re.fullmatch(r"[0-9a-f]{12}", p) for p in pids):
        raise ApiError(400, "Bad profile list.")
    return checks.plan_target(_target(q), pids)


def _state(q):
    return {"profiles": userdata.profiles(), "wanted": userdata.wanted(),
            "recovered": list(store.recovered),
            "categories": planner.CATEGORIES,
            "providers": [{"id": p[0], "name": p[1]} for p in discovery.PROVIDERS]}


GET_ROUTES = {
    "/api/ping": lambda q: {"app": APP_ID, "version": __version__},
    "/api/specs": lambda q: specs.detect(refresh=q.get("refresh") == "1"),
    "/api/search": _search,
    "/api/game": _game,
    "/api/pcgw-for": _pcgw_for,
    "/api/hardware": _hardware,
    "/api/library": lambda q: library.view(),
    "/api/scan/status": lambda q: library.job_status(q.get("job") or ""),
    "/api/check": lambda q: checks.check_target(_target(q), _effective_specs(q)),
    "/api/plan": _plan,
    "/api/state": _state,
}


# ------------------------------------------------------------------ POSTs

def _p_mapping(b):
    if b.get("clear"):
        userdata.clear_mapping(str(b.get("key") or ""))
    else:
        userdata.set_mapping(b.get("key"), b.get("source"), b.get("id"), b.get("name"))
    checks.forget_resolutions(str(b.get("key") or ""))
    return {"ok": True}


def _p_paste_parse(b):
    return {"levels": paste.parse(str(b.get("minimum") or ""), str(b.get("recommended") or ""))}


def _p_paste_compare(b):
    title = userdata._text(b.get("title"), 160, "Title") or "Pasted requirements"
    levels = userdata._levels(b.get("levels"))
    if not levels:
        raise ApiError(400, "There are no requirements to compare.")
    attribution = userdata._text(b.get("attribution"), 300, "Source")
    sp = _effective_specs({k: str(v) for k, v in (b.get("pc") or {}).items() if v not in (None, "")})
    return checks.pasted_result(title, levels, attribution, sp)


def _p_report(b):
    return {"svg": report.build_svg(b, private=b.get("private", True) is not False)}


POST_ROUTES = {
    "/api/scan/start": lambda b: library.start_scan(),
    "/api/scan/cancel": lambda b: library.cancel_scan(str(b.get("job") or "")),
    "/api/library/decide": lambda b: library.decide(str(b.get("install_id") or ""), b.get("decision")) or {"ok": True},
    "/api/library/hide": lambda b: library.set_hidden(str(b.get("game_id") or ""), bool(b.get("hidden"))) or {"ok": True},
    "/api/library/folders": lambda b: library.edit_folders(b.get("action"), b.get("path")) or {"ok": True},
    "/api/library/manual": lambda b: library.edit_manual(b.get("action"), b.get("exe"), b.get("title"), b.get("id")) or {"ok": True},
    "/api/library/exe": lambda b: library.set_exe(str(b.get("install_id") or ""), b.get("exe")) or {"ok": True},
    "/api/launch": lambda b: library.launch(str(b.get("install_id") or "")),
    "/api/mapping": _p_mapping,
    "/api/profiles/save": lambda b: userdata.save_profile(b),
    "/api/profiles/delete": lambda b: userdata.delete_profile(str(b.get("id") or "")) or {"ok": True},
    "/api/wanted/add": lambda b: userdata.add_wanted(b),
    "/api/wanted/remove": lambda b: userdata.remove_wanted(str(b.get("id") or "")) or {"ok": True},
    "/api/paste/parse": _p_paste_parse,
    "/api/paste/compare": _p_paste_compare,
    "/api/report": _p_report,
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
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _local(self):
        # Only answer requests addressed to this machine by its loopback
        # name, so another website cannot reach the API through DNS tricks.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ("127.0.0.1", "localhost")

    def _call(self, fn, arg):
        try:
            return self._json(200, fn(arg))
        except ApiError as e:
            return self._json(e.status, {"error": str(e)})
        except (userdata.InvalidData, library.LibraryError) as e:
            return self._json(400, {"error": str(e)})
        except checks.NotFound as e:
            return self._json(404, {"error": str(e)})
        except sources.SourceError as e:
            return self._json(502, {"error": str(e)})
        except Exception as e:      # keep the server alive, show the cause
            return self._json(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def do_GET(self):
        if not self._local():
            return self._json(403, {"error": "Local access only."})
        url = urlparse(self.path)
        if url.path.startswith("/legal/"):
            path = legal_file(url.path[len("/legal/"):])
            if not path:
                return self._send(404, b"Not found", "text/plain; charset=utf-8")
            with open(path, "rb") as fh:
                return self._send(200, fh.read(), "text/plain; charset=utf-8")
        if url.path in GET_ROUTES:
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            return self._call(GET_ROUTES[url.path], q)
        if url.path in POST_ROUTES:
            return self._json(405, {"error": "Use POST."})
        self._static(url.path)

    def do_POST(self):
        # Read the body before any refusal: closing a socket with unread data
        # makes Windows reset the connection before the answer arrives.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self.close_connection = True
            return self._json(413, {"error": "Request too large."})
        raw = self.rfile.read(length)
        if not self._local():
            return self._json(403, {"error": "Local access only."})
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        if origin and origin not in ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port):
            return self._json(403, {"error": "Requests from other sites are refused."})
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            return self._json(403, {"error": "Requests from other sites are refused."})
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return self._json(415, {"error": "Send JSON."})
        url = urlparse(self.path)
        if url.path not in POST_ROUTES:
            return self._json(404, {"error": "Not found."})
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "Request body is not JSON."})
        if not isinstance(body, dict):
            return self._json(400, {"error": "Request body must be a JSON object."})
        return self._call(POST_ROUTES[url.path], body)

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


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second socket bind a port that is already
    # in use, so the "port taken, try the next one" fallback would never run.
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(port=8765, verbose=False, tries=PORT_TRIES):
    last = None
    for p in range(port, port + tries):
        try:
            httpd = _Server(("127.0.0.1", p), Handler)
        except OSError as e:
            last = e
            continue
        httpd.daemon_threads = True
        httpd.verbose = verbose
        return httpd
    raise last
