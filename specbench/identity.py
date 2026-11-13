"""From installations to games, and from games to their requirements.

Discovery says "this folder holds a copy of X". Identity decides which copies
are the same game and which store page or wiki page has that game's
requirements. The two stay separate: a game found through Epic or GOG can
still use Steam's or PCGamingWiki's requirements, and a Steam app id is one
possible identifier, not the only one.
"""
import hashlib
import os

from . import sources
from .discovery.model import split_edition, title_key

_RANK = {"high": 0, "medium": 1, "low": 2}


def game_id(key):
    return "g_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _inside(child, parent):
    c = os.path.normcase(os.path.normpath(child)) + os.sep
    p = os.path.normcase(os.path.normpath(parent)) + os.sep
    return c.startswith(p)


def visible_installs(installs, decisions):
    """Split into (library installs, installs waiting for review).

    Low-confidence finds join the library only once accepted. Anything the
    user ignored is dropped. A heuristic find inside a folder that a launcher
    already reported is the same copy and is dropped too.
    """
    trusted = [i for i in installs if i["confidence"] != "low" and i.get("path")]
    lib, review = [], []
    for i in installs:
        d = decisions.get(i["install_id"])
        if d == "ignore":
            continue
        if i["confidence"] == "low":
            if i.get("path") and any(_inside(i["path"], t["path"]) or _inside(t["path"], i["path"])
                                     for t in trusted):
                continue
            if d != "accept":
                review.append(i)
                continue
        lib.append(i)
    # Shortcuts and installed-program entries often point at the same folder.
    seen, unique = set(), []
    for i in sorted(review, key=lambda x: x["provider"] != "programs"):
        k = os.path.normcase(i.get("path") or i["install_id"])
        if k not in seen:
            seen.add(k)
            unique.append(i)
    return lib, unique


def merge(installs, hidden=()):
    """Installations -> games, one per game, each keeping all its copies.

    Copies with the same Steam app id are the same game. Otherwise copies
    whose titles match after dropping cosmetic edition words (Deluxe, GOTY)
    are merged, while material editions (Remastered, Definitive) stay apart.
    """
    groups, steam_by_title = {}, {}
    for inst in installs:
        tkey = title_key(inst["title"])
        steam = inst["ids"].get("steam")
        key = "steam:%s" % steam if steam else "title:%s" % tkey
        groups.setdefault(key, []).append(inst)
        if steam:
            steam_by_title.setdefault(tkey, key)
    for key in [k for k in groups if k.startswith("title:")]:
        target = steam_by_title.get(key[len("title:"):])
        if target:
            groups[target].extend(groups.pop(key))
    games = []
    hidden = set(hidden)
    for key, insts in groups.items():
        insts.sort(key=lambda i: (_RANK.get(i["confidence"], 3), i["provider"] != "steam"))
        best = insts[0]
        ids = {}
        for i in insts:
            for k, v in i["ids"].items():
                ids.setdefault(k, v)
        base, edition, _ = split_edition(best["title"])
        gid = game_id(key)
        games.append({
            "game_id": gid, "key": key, "title": best["title"], "title_base": base,
            "edition": edition, "title_key": title_key(best["title"]),
            "installs": insts, "providers": sorted({i["provider"] for i in insts}),
            "ids": ids, "size_gb": max([i["size_gb"] or 0 for i in insts]) or None,
            "present": any(i["present"] for i in insts),
            "confidence": best["confidence"], "hidden": gid in hidden,
        })
    games.sort(key=lambda g: g["title"].lower())
    return games


# ------------------------------------------------------- requirements

def resolve(game, mappings, search_steam=None, search_pcgw=None, gog_lookup=None):
    """Where this game's requirements come from.

    Order: a match the user confirmed, then verified ids (Steam app id, GOG
    id through PCGamingWiki), then an exact title match. Anything less
    certain asks the user to choose.
    """
    search_steam = search_steam or sources.search_steam
    search_pcgw = search_pcgw or sources.search_pcgw
    gog_lookup = gog_lookup or sources.pcgw_title_for_gog
    m = mappings.get(game["key"])
    if isinstance(m, dict) and m.get("source") in ("steam", "pcgw") and m.get("id"):
        return {"source": m["source"], "id": str(m["id"]), "name": m.get("name"),
                "confidence": "confirmed", "how": "You chose this match."}
    ids = game.get("ids") or {}
    if ids.get("steam"):
        return {"source": "steam", "id": ids["steam"], "name": game["title"], "confidence": "verified",
                "how": "Steam app id from the Steam library."}
    errors = []
    if ids.get("gog"):
        try:
            title = gog_lookup(ids["gog"])
        except sources.SourceError as e:
            errors.append(str(e))
            title = None
        if title:
            return {"source": "pcgw", "id": title, "name": title, "confidence": "verified",
                    "how": "GOG product id matched on PCGamingWiki."}
    want = game.get("title_key") or title_key(game["title"])
    candidates = []
    for source, fn in (("steam", search_steam), ("pcgw", search_pcgw)):
        try:
            results = fn(game.get("title_base") or game["title"])
        except sources.SourceError as e:
            errors.append(str(e))
            continue
        exact = [r for r in results if title_key(r["name"]) == want]
        if len(exact) == 1:
            return {"source": source, "id": exact[0]["id"], "name": exact[0]["name"],
                    "confidence": "title",
                    "how": "Matched by title on %s. If it is the wrong game, choose the right one."
                           % ("Steam" if source == "steam" else "PCGamingWiki")}
        candidates += results[:5]
    return {"needs_choice": True, "candidates": candidates[:8], "errors": errors,
            "how": "No single exact title match was found." if not errors and candidates
            else "Could not search for this game." if errors and not candidates
            else "No match was found by title."}
