"""Where requirements come from: Steam's store API and PCGamingWiki.

SteamDB has no public API and asks not to be scraped; its requirement data
is Steam's own, which is what the store API below returns, so the app reads
that and links out to the SteamDB page instead.

Only game identifiers and search terms are sent. Request pacing and HTTP 429
handling live in net.py.
"""
import hashlib
import json
import os
import re
import time
import urllib.parse

from . import net, requirements
from .net import RateLimited, SourceError  # noqa: F401  (re-exported)
from .paths import CACHE_DIR

_DAY = 86400


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
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(value, fh)
        os.replace(tmp, path)
    except OSError:
        pass
    return value


def _json(body, who):
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise SourceError("%s returned an unreadable answer." % who)


def _steam_json(url):
    _, _, body = net.fetch(net.STEAM, url)
    return _json(body, "Steam")


def _pcgw_json(url):
    _, _, body = net.fetch(net.PCGW, url)
    return _json(body, "PCGamingWiki")


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
        data = _pcgw_json("https://www.pcgamingwiki.com/w/api.php?" + q)
        return [{"source": "pcgw", "id": t, "name": t, "image": None}
                for t in (data[1] if isinstance(data, list) and len(data) > 1 else [])]
    return _cached("pcgwsearch:" + term.lower(), _DAY, run)


# ------------------------------------------------------------------- games

def steam_game(appid):
    """Raw requirement levels for one Steam app id."""
    def run():
        q = urllib.parse.urlencode({"appids": appid, "l": "english", "cc": "US"})
        data = _steam_json("https://store.steampowered.com/api/appdetails?" + q)
        node = (data or {}).get(str(appid)) or {}
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
        parsed = requirements.parse_steam((out.get("pc") or {}).get(level))
        if parsed:
            levels[level] = parsed
    out["levels"] = levels
    return out


def _pcgw_redirect(url, key):
    def run():
        status, headers, _ = net.fetch(net.PCGW, url, follow=False)
        loc = (headers or {}).get("Location") or ""
        if "/wiki/" not in loc:
            return {"title": None}
        title = urllib.parse.unquote(loc.split("/wiki/", 1)[1]).replace("_", " ")
        return {"title": None if title.lower().startswith("special:") else title}
    return _cached(key, 30 * _DAY, run)["title"]


def pcgw_title_for_appid(appid):
    return _pcgw_redirect("https://www.pcgamingwiki.com/api/appid.php?appid=%s" % appid,
                          "pcgwappid:%s" % appid)


def pcgw_title_for_gog(gog_id):
    """PCGamingWiki page for a GOG product id (an exact, verified mapping)."""
    return _pcgw_redirect("https://www.pcgamingwiki.com/api/gog.php?page=%s"
                          % urllib.parse.quote(str(gog_id)), "pcgwgog:%s" % gog_id)


_INFOBOX_APPID = re.compile(r"\|\s*steam appid\s*=\s*([0-9]+)", re.I)


def pcgw_game(title):
    """Raw requirement levels for one PCGamingWiki page title."""
    def run():
        q = urllib.parse.urlencode({"action": "parse", "page": title,
                                    "prop": "wikitext", "redirects": 1,
                                    "format": "json"})
        data = _pcgw_json("https://www.pcgamingwiki.com/w/api.php?" + q)
        if not isinstance(data, dict) or "parse" not in data:
            return {"missing": True}
        text = data["parse"]["wikitext"]["*"]
        m = _INFOBOX_APPID.search(text)
        return {"name": data["parse"].get("title") or title, "image": None,
                "steam_appid": m.group(1) if m else None,
                "wikitext": "\n".join(requirements.pcgw_blocks(text))}
    out = dict(_cached("pcgwgame3:" + title.lower(), 7 * _DAY, run))
    if out.get("missing"):
        raise SourceError("PCGamingWiki has no page called \"%s\"." % title)
    out["levels"] = requirements.parse_pcgw(out.pop("wikitext", ""))
    return out


def links(source, ident, name=None):
    if source == "steam":
        return [
            {"label": "Steam store", "url": "https://store.steampowered.com/app/%s/" % ident},
            {"label": "SteamDB", "url": "https://steamdb.info/app/%s/info/" % ident},
            {"label": "PCGamingWiki", "url": "https://www.pcgamingwiki.com/api/appid.php?appid=%s" % ident},
        ]
    if source == "pcgw":
        page = urllib.parse.quote(ident.replace(" ", "_"))
        return [{"label": "PCGamingWiki", "url": "https://www.pcgamingwiki.com/wiki/" + page}]
    return []
