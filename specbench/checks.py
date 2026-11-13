"""Checks that combine a game identity, a PC profile and the sources.

When one source fails, the other is tried and the failure is reported next
to the result instead of replacing it.
"""
import threading

from . import build_result, compare, identity, library, planner, sources, specs, userdata

_resolved = {}
_resolved_lock = threading.Lock()


class NotFound(Exception):
    pass


def forget_resolutions(key=None):
    with _resolved_lock:
        if key:
            _resolved.pop(key, None)
        else:
            _resolved.clear()


# ------------------------------------------------------------- PC profiles

def specs_for(profile_id=None, cpu=None, gpu=None, ram=None, vram=None, detected=None):
    """Detected hardware, a saved profile on top of it, then ad-hoc changes."""
    base = detected or specs.detect()
    if profile_id and profile_id != "real":
        p = userdata.get_profile(profile_id)
        if not p:
            raise NotFound("That profile no longer exists.")
        base = specs.with_overrides(base, p.get("cpu"), p.get("gpu"), p.get("ram"), p.get("vram"),
                                    label=p["name"])
        base["profile"] = {"id": p["id"], "name": p["name"], "kind": p["kind"]}
    if cpu or gpu or ram or vram:
        base = specs.with_overrides(base, cpu, gpu, ram, vram)
    return base


# ------------------------------------------------------------ fetching raw

def raw_for(ident):
    """(source, id, raw, errors) for an identity, falling back to
    PCGamingWiki when Steam fails or publishes nothing usable."""
    errors = {}
    source, ident_id = ident["source"], str(ident["id"])
    raw = None
    try:
        raw = sources.steam_game(ident_id) if source == "steam" else sources.pcgw_game(ident_id)
    except sources.SourceError as e:
        errors[source] = str(e)
    if source == "steam" and (raw is None or not raw.get("levels")):
        try:
            title = sources.pcgw_title_for_appid(ident_id)
            if title:
                alt = sources.pcgw_game(title)
                if alt.get("levels") or raw is None:
                    alt.setdefault("image", (raw or {}).get("image"))
                    return "pcgw", title, alt, errors
        except sources.SourceError as e:
            errors["pcgw"] = str(e)
    if raw is None:
        raise sources.SourceError(" ".join(errors.values()) or "No source answered.")
    return source, ident_id, raw, errors


def check_identity(ident, sp, installed=False):
    source, ident_id, raw, errors = raw_for(ident)
    res = build_result(source, ident_id, raw, sp, installed)
    res["errors"] = errors
    if source != ident["source"]:
        res["fallback_note"] = "Steam had no usable requirements%s, so PCGamingWiki's are shown." % (
            " (%s)" % errors["steam"] if errors.get("steam") else "")
    return res


def second_opinion(appid, sp, installed=False):
    """PCGamingWiki's comparison for a Steam app, plus where it disagrees with Steam."""
    title = sources.pcgw_title_for_appid(appid)
    if not title:
        return {"found": False}
    game = build_result("pcgw", title, sources.pcgw_game(title), sp, installed)
    game["found"] = bool(game["levels"])
    try:
        steam = build_result("steam", appid, sources.steam_game(appid), sp, installed)
        game["disagreements"] = compare.disagreements(steam["levels"], game["levels"])
    except sources.SourceError:
        game["disagreements"] = []
    return game


# ----------------------------------------------------------------- targets

def _identity_for_game(game):
    with _resolved_lock:
        cached = _resolved.get(game["key"])
    if cached:
        return cached
    ident = identity.resolve(game, userdata.mappings())
    if not ident.get("needs_choice"):
        with _resolved_lock:
            _resolved[game["key"]] = ident
    return ident


def resolve_target(target):
    """("g_..." library game | "w_..." wanted game) -> (title, ident or pasted, installed, meta)."""
    if target.startswith("g_"):
        game = library.find_game(target)
        if not game:
            raise NotFound("That game is no longer in the library. Scan again.")
        return game["title"], _identity_for_game(game), True, {"game_id": game["game_id"], "key": game["key"]}
    if target.startswith("w_"):
        w = userdata.get_wanted(target[2:])
        if not w:
            raise NotFound("That wanted game was removed.")
        if w["source"] == "pasted":
            ident = {"pasted": True, "levels": w["levels"], "attribution": w.get("attribution", "")}
        else:
            ident = {"source": w["source"], "id": w["ref"], "name": w["title"],
                     "confidence": "verified", "how": "Saved from a search result."}
        return w["title"], ident, False, {"wanted_id": w["id"]}
    raise NotFound("Unknown target.")


def pasted_result(title, levels, attribution, sp, ident_key="pasted"):
    raw = {"name": title, "levels": levels}
    res = build_result("pasted", ident_key, raw, sp)
    res["attribution"] = attribution
    if attribution.startswith(("http://", "https://")):
        res["links"] = [{"label": "Source you gave", "url": attribution}]
    return res


def check_target(target, sp):
    title, ident, installed, meta = resolve_target(target)
    out = dict(meta, target=target, title=title)
    if ident.get("needs_choice"):
        out.update(needs_choice=True, candidates=ident["candidates"], how=ident["how"],
                   errors=ident.get("errors", []))
        return out
    if ident.get("pasted"):
        res = pasted_result(title, ident["levels"], ident["attribution"], sp, target)
    else:
        res = check_identity(ident, sp, installed)
        res["identity"] = ident
    out.update(result=res)
    return out


def plan_target(target, profile_ids, detected=None):
    """One game against the real PC and each profile, with what changed."""
    title, ident, installed, meta = resolve_target(target)
    out = dict(meta, target=target, title=title)
    if ident.get("needs_choice"):
        out.update(needs_choice=True)
        return out
    if ident.get("pasted"):
        def result(sp):
            return pasted_result(title, ident["levels"], ident["attribution"], sp, target)
    else:
        source, ident_id, raw, errors = raw_for(ident)
        out["errors"] = errors

        def result(sp):
            return build_result(source, ident_id, raw, sp, installed)
    detected = detected or specs.detect()
    before = planner.states(result(detected))
    out["states"] = {"real": before}
    out["changes"] = {}
    for pid in profile_ids:
        if pid == "real":
            continue
        after = planner.states(result(specs_for(pid, detected=detected)))
        out["states"][pid] = after
        out["changes"][pid] = planner.diff(before, after)
    return out
