"""Saved PC profiles, wanted games and confirmed requirement matches.

All of it is local JSON (see store.py) and every field is checked on the way
in, since the values come from the browser.
"""
import re
import time
import uuid

from . import store
from .discovery.model import clean_title

PROFILE_KINDS = ("desktop", "laptop", "planned", "upgrade")
MAX_PROFILES, MAX_WANTED, MAX_MAPPINGS = 50, 500, 5000


class InvalidData(Exception):
    pass


def _text(v, limit, what, required=False):
    v = re.sub(r"[\x00-\x1f\x7f]", " ", str(v or "")).strip()
    if required and not v:
        raise InvalidData("%s is required." % what)
    if len(v) > limit:
        raise InvalidData("%s is too long." % what)
    return v


def _num(v, lo, hi, what):
    if v in (None, ""):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise InvalidData("%s must be a number." % what)
    if not lo <= n <= hi:
        raise InvalidData("%s is out of range." % what)
    return n


# --------------------------------------------------------------- profiles

def profiles():
    data = store.load("profiles", [])
    return [p for p in data if isinstance(p, dict) and p.get("id") and p.get("name")]


def get_profile(pid):
    return next((p for p in profiles() if p["id"] == pid), None)


def save_profile(data):
    p = {
        "id": data.get("id") or uuid.uuid4().hex[:12],
        "name": _text(data.get("name"), 60, "Name", required=True),
        "kind": data.get("kind") if data.get("kind") in PROFILE_KINDS else "planned",
        "cpu": _text(data.get("cpu"), 120, "Processor"),
        "gpu": _text(data.get("gpu"), 120, "Graphics card"),
        "ram": _num(data.get("ram"), 0.25, 2048, "Memory"),
        "vram": _num(data.get("vram"), 0.0625, 256, "Video memory"),
        "updated": int(time.time()),
    }
    if not re.fullmatch(r"[0-9a-f]{12}", p["id"]):
        raise InvalidData("Bad profile id.")
    if not any(p[k] for k in ("cpu", "gpu", "ram", "vram")):
        raise InvalidData("Set at least one part. Blank parts use this PC's hardware.")

    def fn(lst):
        lst = [x for x in lst if isinstance(x, dict) and x.get("id") != p["id"]]
        if len(lst) >= MAX_PROFILES:
            raise InvalidData("You can save up to %d profiles." % MAX_PROFILES)
        return lst + [p]
    store.update("profiles", [], fn)
    return p


def delete_profile(pid):
    store.update("profiles", [], lambda lst: [x for x in lst if isinstance(x, dict) and x.get("id") != pid])


# ------------------------------------------------------------ wanted games

def wanted():
    data = store.load("wanted", [])
    return [w for w in data if isinstance(w, dict) and w.get("id") and w.get("title")]


def get_wanted(wid):
    return next((w for w in wanted() if w["id"] == wid), None)


def _levels(raw):
    out = {}
    for lv in ("minimum", "recommended"):
        level = (raw or {}).get(lv)
        if not isinstance(level, dict):
            continue
        fields = {}
        for k, v in (level.get("fields") or {}).items():
            if k in ("os", "cpu", "gpu", "ram", "vram", "directx", "storage", "notes"):
                v = _text(v, 400, "A requirement field")
                if v:
                    fields[k] = v
        if fields or level.get("raw"):
            out[lv] = {"fields": fields, "raw": _text(level.get("raw"), 6000, "The pasted text")}
    return out


def add_wanted(data):
    source = data.get("source")
    w = {"id": uuid.uuid4().hex[:12], "title": clean_title(_text(data.get("title"), 160, "Title", True)),
         "source": source, "added": int(time.time())}
    if source == "steam":
        ref = str(data.get("ref") or "")
        if not ref.isdigit():
            raise InvalidData("A Steam id is a number.")
        w["ref"] = ref
    elif source == "pcgw":
        w["ref"] = _text(data.get("ref"), 200, "Page title", required=True)
    elif source == "pasted":
        w["levels"] = _levels(data.get("levels"))
        if not w["levels"]:
            raise InvalidData("There are no requirements to save.")
        w["attribution"] = _text(data.get("attribution"), 300, "Source")
    else:
        raise InvalidData("source must be steam, pcgw or pasted.")

    def fn(lst):
        lst = [x for x in lst if isinstance(x, dict)]
        if source != "pasted" and any(x.get("source") == source and x.get("ref") == w["ref"] for x in lst):
            return lst
        if len(lst) >= MAX_WANTED:
            raise InvalidData("You can save up to %d wanted games." % MAX_WANTED)
        return lst + [w]
    store.update("wanted", [], fn)
    return w


def remove_wanted(wid):
    store.update("wanted", [], lambda lst: [x for x in lst if isinstance(x, dict) and x.get("id") != wid])


# ---------------------------------------------------------------- mappings

def mappings():
    return store.load("mappings", {})


def set_mapping(key, source, ident, name=None):
    key = _text(key, 300, "Game key", required=True)
    if source not in ("steam", "pcgw"):
        raise InvalidData("source must be steam or pcgw.")
    ident = _text(ident, 200, "Id", required=True)
    if source == "steam" and not ident.isdigit():
        raise InvalidData("A Steam id is a number.")

    def fn(m):
        if len(m) >= MAX_MAPPINGS and key not in m:
            raise InvalidData("Too many saved matches.")
        m[key] = {"source": source, "id": ident, "name": clean_title(name or ""),
                  "confirmed": int(time.time())}
        return m
    store.update("mappings", {}, fn)


def clear_mapping(key):
    def fn(m):
        m.pop(key, None)
        return m
    store.update("mappings", {}, fn)
