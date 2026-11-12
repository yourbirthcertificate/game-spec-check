"""Where requirements come from: Steam's store API and PCGamingWiki.

SteamDB has no public API and asks not to be scraped; its requirement data
is Steam's own, which is what the store API below returns, so the app reads
that and links out to the SteamDB page instead.
"""
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import requirements

USER_AGENT = "GameSpecCheck/1.0 (local desktop tool)"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")
_DAY = 86400
_steam_gate = threading.Lock()
_last_steam = [0.0]


class SourceError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _get(url, timeout=20, follow=True):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Language": "en"})
    opener = urllib.request.build_opener() if follow else \
        urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        if not follow and e.code in (301, 302, 303, 307, 308):
            return e.code, e.headers, b""
        if e.code == 429:
            raise SourceError("The source is rate-limiting requests; wait a minute and retry.")
        raise SourceError("The source answered HTTP %d." % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise SourceError("Could not reach the source (%s)." % getattr(e, "reason", e))


def _cached(key, ttl, producer):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")
    try:
        if time.time() - os.path.getmtime(path) < ttl:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    except (OSError, ValueError):
        pass
    value = producer()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(value, fh)
    except OSError:
        pass
    return value


def _steam_json(url):
    # Steam allows roughly 200 store calls per 5 minutes: space them out.
    with _steam_gate:
        wait = 0.35 - (time.time() - _last_steam[0])
        if wait > 0:
            time.sleep(wait)
        try:
            _, _, body = _get(url)
        finally:
            _last_steam[0] = time.time()
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise SourceError("Steam returned an unreadable answer.")


# ------------------------------------------------------------------ search

def search_steam(term):
    def run():
        q = urllib.parse.urlencode({"term": term, "l": "english", "cc": "US"})
        data = _steam_json("https://store.steampowered.com/api/storesearch/?" + q)
        return [{"source": "steam", "id": str(it["id"]), "name": it["name"],
                 "image": it.get("tiny_image")}
                for it in data.get("items", []) if it.get("type") in (None, "app", "game")]
    return _cached("steamsearch:" + term.lower(), _DAY, run)


def search_pcgw(term):
    def run():
        q = urllib.parse.urlencode({"action": "opensearch", "search": term,
                                    "limit": 8, "namespace": 0, "format": "json"})
        _, _, body = _get("https://www.pcgamingwiki.com/w/api.php?" + q)
        data = json.loads(body.decode("utf-8", "replace"))
        return [{"source": "pcgw", "id": t, "name": t, "image": None}
                for t in (data[1] if len(data) > 1 else [])]
    return _cached("pcgwsearch:" + term.lower(), _DAY, run)


# ------------------------------------------------------------------- games

def steam_game(appid):
    """Raw requirement levels for one Steam app id."""
    def run():
        q = urllib.parse.urlencode({"appids": appid, "l": "english", "cc": "US"})
        data = _steam_json("https://store.steampowered.com/api/appdetails?" + q)
        node = data.get(str(appid)) or {}
        if not node.get("success"):
            return {"missing": True}
        d = node["data"]
        pc = d.get("pc_requirements")
        return {"name": d.get("name"), "image": d.get("header_image"),
                "type": d.get("type"),
                "windows": (d.get("platforms") or {}).get("windows", True),
                "release": (d.get("release_date") or {}).get("date"),
                "pc": pc if isinstance(pc, dict) else {}}
    # The cache keeps what the source published; parsing happens on every
    # read so parser improvements apply to games that are already cached.
    out = dict(_cached("steamgame2:%s" % appid, 7 * _DAY, run))
    if out.get("missing"):
        raise SourceError("Steam has no store page for app %s." % appid)
    levels = {}
    for level in ("minimum", "recommended"):
        parsed = requirements.parse_steam(out["pc"].get(level))
        if parsed:
            levels[level] = parsed
    out["levels"] = levels
    return out


def pcgw_title_for_appid(appid):
    def run():
        status, headers, _ = _get(
            "https://www.pcgamingwiki.com/api/appid.php?appid=%s" % appid,
            follow=False)
        loc = headers.get("Location") or ""
        if "/wiki/" not in loc:
            return {"title": None}
        title = urllib.parse.unquote(loc.split("/wiki/", 1)[1]).replace("_", " ")
        return {"title": None if title.lower().startswith("special:") else title}
    return _cached("pcgwappid:%s" % appid, 30 * _DAY, run)["title"]


def pcgw_game(title):
    """Raw requirement levels for one PCGamingWiki page title."""
    def run():
        q = urllib.parse.urlencode({"action": "parse", "page": title,
                                    "prop": "wikitext", "redirects": 1,
                                    "format": "json"})
        _, _, body = _get("https://www.pcgamingwiki.com/w/api.php?" + q)
        data = json.loads(body.decode("utf-8", "replace"))
        if "parse" not in data:
            return {"missing": True}
        text = data["parse"]["wikitext"]["*"]
        return {"name": data["parse"].get("title") or title, "image": None,
                "wikitext": "\n".join(requirements.pcgw_blocks(text))}
    out = dict(_cached("pcgwgame2:" + title.lower(), 7 * _DAY, run))
    if out.get("missing"):
        raise SourceError("PCGamingWiki has no page called \"%s\"." % title)
    out["levels"] = requirements.parse_pcgw(out.pop("wikitext"))
    return out


def links(source, ident, name=None):
    if source == "steam":
        return [
            {"label": "Steam store", "url": "https://store.steampowered.com/app/%s/" % ident},
            {"label": "SteamDB", "url": "https://steamdb.info/app/%s/info/" % ident},
            {"label": "PCGamingWiki", "url": "https://www.pcgamingwiki.com/api/appid.php?appid=%s" % ident},
        ]
    page = urllib.parse.quote(ident.replace(" ", "_"))
    return [{"label": "PCGamingWiki", "url": "https://www.pcgamingwiki.com/wiki/" + page}]
