"""Turn free-text hardware names into entries of the score database.

Both sides (database names and the text being searched) are reduced to the
same token stream, e.g. "Intel(R) Core(TM) i7-12700H" -> core i 7 12700 h,
and a database entry matches when its tokens appear contiguously. The longest
entry wins, so "RTX 3060 Ti" is never mistaken for "RTX 3060".
"""
import re

from .hardware_db import CPUS, GPUS, GPU_GENERIC

_TOKEN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
_MARKS = re.compile(r"\((?:r|tm|c)\)|[®™©]", re.I)

_GPU_STOP = {"graphics", "gpu", "tm"}
_CPU_STOP = {"processor", "cpu", "pro", "gold", "silver", "tm", "apu"}


def tokens(text, stop=()):
    text = _MARKS.sub(" ", text or "").lower()
    return [t for t in _TOKEN.findall(text) if t not in stop]


# ---------------------------------------------------------------- GPU index

_NV_FAMILY = {"gtx", "rtx", "gt", "gts", "mx", "titan"}
_AMD_FAMILY = {"rx", "r", "hd", "vega"}

# Database entries that only ever existed as laptop chips or integrated
# graphics: never apply the laptop slowdown to these.
_NO_LAPTOP_FACTOR = re.compile(
    r"\bMX\d|\d{3}MX|RTX 4050|RTX 3050 Ti|RTX 2050|GTX 1650 Ti|"
    r"Radeon \d{3}M|Vega (3|6|8|10|11)$|Iris|UHD|^HD |Arc 1\d\d|GMA", re.I)


def _gpu_vendor(name):
    if name.startswith(("GeForce", "Titan")):
        return "nvidia"
    if name.startswith("Radeon"):
        return "amd"
    return "intel"


def _gpu_keys(name):
    t = tokens(name, _GPU_STOP)
    keys = []
    if t[0] == "geforce" and len(t) > 1 and t[1] in _NV_FAMILY:
        t = t[1:]
    elif t[0] == "radeon" and len(t) > 1 and t[1] in _AMD_FAMILY:
        t = t[1:]
    keys.append(tuple(t))
    if t[:2] == ["rx", "vega"]:
        keys.append(tuple(t[1:]))          # "Radeon Vega 56"
    if t[:2] == ["gtx", "titan"] or t[0] == "titan":
        pass
    return keys


def _build(entries, keyfn, vendorfn):
    index, exact = {}, {}
    for name, score in entries:
        entry = {"name": name, "score": score, "vendor": vendorfn(name)}
        for key in keyfn(name):
            index.setdefault(key[0], []).append((key, entry))
            exact[key] = entry
    for lst in index.values():
        lst.sort(key=lambda ke: -len(ke[0]))
    return index, exact


_GPU_INDEX, _GPU_EXACT = _build(GPUS, _gpu_keys, _gpu_vendor)


def _scan(toks, index):
    """Left-to-right, longest-first scan. Returns [(start, end, entry)]."""
    out, i = [], 0
    while i < len(toks):
        hit = None
        for key, entry in index.get(toks[i], ()):
            if tuple(toks[i:i + len(key)]) == key:
                hit = (i, i + len(key), entry)
                break
        if hit:
            out.append(hit)
            i = hit[1]
        else:
            i += 1
    return out


def _laptop_factor(entry):
    name = entry["name"]
    m = re.search(r"\d{3,4}", name)
    num = int(m.group()) if m else 0
    if entry["vendor"] == "nvidia":
        if "RTX" in name:
            return {90: 0.72, 80: 0.77, 70: 0.80, 60: 0.87}.get(num % 100, 0.85)
        return 0.9 if num >= 1000 else 0.6
    return 0.88


_LAPTOP_TOKS = {"laptop", "mobile", "notebook", "maxq"}
# Words that end the description of one card in a list of alternatives.
_ALT_BREAK = {"or", "and", "desktop", "vs", "versus"}


def _laptop_near(toks, s, e, prev_end, next_start):
    """True when a laptop word belongs to the card at toks[s:e].

    "RTX 3060 Laptop GPU / GTX 1070" is a laptop chip and a desktop card, so
    the word only counts right after the card (before the next card or an
    "or") or right before it ("Mobile GTX 1050").
    """
    for j in range(e, min(next_start, e + 4)):
        t = toks[j]
        if t in _ALT_BREAK:
            break
        if t in _LAPTOP_TOKS or (t == "max" and j + 1 < len(toks) and toks[j + 1] == "q"):
            return True
    for j in range(s - 1, max(prev_end, s - 2) - 1, -1):
        t = toks[j]
        if t in _ALT_BREAK:
            break
        if t in _LAPTOP_TOKS:
            return True
    return False


def _is_num(tok, lo=3, hi=4):
    return tok.isdigit() and lo <= len(tok) <= hi


def _series_hit(toks):
    """"GTX 400 series or better": use the entry-level gaming card of it."""
    for i, tok in enumerate(toks):
        if not re.fullmatch(r"[1-9]0{2,3}", tok):
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        prev = toks[i - 1] if i else ""
        if nxt != "series" and prev not in ("gtx", "rtx", "gt", "rx", "hd"):
            continue
        lo, span = int(tok), 10 ** (len(tok) - 1)
        prefixes = [prev] if prev in ("gtx", "rtx", "gt", "gts", "rx", "hd") \
            else ["gtx", "rtx", "rx", "hd", "gt"]
        for p in prefixes:
            pool = [e for k, e in _GPU_EXACT.items()
                    if len(k) == 2 and k[0] == p and k[1].isdigit()
                    and lo <= int(k[1]) < lo + span]
            gaming = [e for e in pool if int(re.search(r"\d+", e["name"]).group()) % 100 >= 50]
            if pool:
                e = min(gaming or pool, key=lambda x: x["score"])
                return {"name": "%s (entry card of the %s %s series)" % (e["name"], p.upper(), tok),
                        "score": e["score"], "vendor": e["vendor"], "kind": "generic"}
    return None


def match_gpus(text):
    """Every graphics card named in `text`, as candidate dicts."""
    toks = tokens(text, _GPU_STOP)
    hits = _scan(toks, _GPU_INDEX)
    covered = set()
    for s, e, _ in hits:
        covered.update(range(s, e))

    # "GeForce 1060" / "Radeon 580": a bare model number after a vendor word.
    # Also catches a wrong family word, e.g. "GTX 2060 Super" for an RTX card.
    vendor = None
    for i, tok in enumerate(toks):
        if tok in ("nvidia", "geforce", "gtx", "rtx"):
            vendor = "nvidia"
        elif tok in ("amd", "radeon", "ati", "rx"):
            vendor = "amd"
        if i in covered or not vendor or not _is_num(tok):
            continue
        # Not a model number: "1080p", "1920x1080", "1024 MB", "144 Hz".
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        prev = toks[i - 1] if i else ""
        after = toks[i + 2] if i + 2 < len(toks) else ""
        if nxt in ("p", "mb", "gb", "hz", "fps", "mhz") or \
                (nxt == "x" and after.isdigit()) or \
                (prev == "x" and i > 1 and toks[i - 2].isdigit()):
            continue
        prefixes = ([("gtx",), ("rtx",), ("gt",), ("gts",)] if vendor == "nvidia"
                    else [("rx",), ("hd",), ("r", "9"), ("r", "7")])
        tail = toks[i + 1:i + 3]
        found = None
        for n in (2, 1, 0):
            for p in prefixes:
                found = _GPU_EXACT.get(p + (tok,) + tuple(tail[:n]))
                if found:
                    hits.append((i, i + 1 + n, found))
                    break
            if found:
                break

    hits.sort(key=lambda h: h[0])
    out = []
    for n, (s, e, entry) in enumerate(hits):
        cand = dict(entry, kind="exact")
        prev_end = hits[n - 1][1] if n else 0
        next_start = hits[n + 1][0] if n + 1 < len(hits) else len(toks)
        suffix_m = e < len(toks) and toks[e] == "m"
        laptop = suffix_m or _laptop_near(toks, s, e, prev_end, next_start)
        if laptop and not _NO_LAPTOP_FACTOR.search(entry["name"]):
            cand["score"] = round(entry["score"] * _laptop_factor(entry))
            cand["name"] = entry["name"] + " (laptop)"
            cand["kind"] = "variant"
        out.append(cand)
    if out:
        return out

    series = _series_hit(toks)
    if series:
        return [series]

    low = _MARKS.sub("", text or "").lower()
    for pattern, label, score in GPU_GENERIC:
        if re.search(pattern, low):
            vendor = ("intel" if "intel" in label.lower() else
                      "amd" if "amd" in label.lower() else None)
            return [{"name": label, "score": score, "vendor": vendor,
                     "kind": "generic"}]
    return []


# ---------------------------------------------------------------- CPU index

def _cpu_vendor(name):
    if name.startswith(("Ryzen", "FX", "Athlon", "Phenom", "A1", "A4", "A6", "A8")):
        return "amd"
    if name.startswith("Snapdragon"):
        return "qualcomm"
    return "intel"


def _cpu_keys(name):
    t = tokens(name, _CPU_STOP | {"intel"})
    if t[0] == "core" and len(t) > 1 and t[1] in ("i", "ultra"):
        t = t[1:]
    return [tuple(t)]


_CPU_INDEX, _CPU_EXACT = _build(CPUS, _cpu_keys, _cpu_vendor)

# Typical mainstream desktop Core i5 of each generation; other tiers and
# laptop chips are derived from it when the exact model is not in the table.
_I5_BASE = {1: 2500, 2: 3800, 3: 4500, 4: 5000, 5: 5300, 6: 5600, 7: 6100,
            8: 9300, 9: 9600, 10: 12300, 11: 17000, 12: 19500, 13: 25500,
            14: 26000}
_INTEL_TIER = {
    "3": lambda g: 0.6 if g <= 7 else 0.7,
    "5": lambda g: 1.0,
    "7": lambda g: 1.2 if g == 11 else (1.4 if g <= 10 else 1.55),
    "9": lambda g: {9: 1.9, 10: 1.75, 11: 1.4}.get(g, 2.2 if g >= 12 else 1.8),
}
_R5_BASE = {1: 12000, 2: 13200, 3: 17800, 4: 16000, 5: 21500, 6: 21500,
            7: 27500, 8: 25000, 9: 30000}
_RYZEN_TIER = {"3": 0.6, "5": 1.0, "7": 1.3, "9": 1.9}

_DESKTOP_SUFFIX = {"k": 1.04, "kf": 1.04, "ks": 1.06, "f": 1.0, "s": 0.93,
                   "t": 0.8, "te": 0.75, "e": 0.9, "c": 1.0, "x": 1.03,
                   "xt": 1.04}
_MOBILE_SUFFIX = {"u": 0.6, "y": 0.35, "m": 0.55, "p": 0.7, "g": 0.6,
                  "h": 0.8, "hs": 0.8, "hq": 0.78, "hk": 0.82, "mq": 0.78,
                  "qm": 0.78, "mx": 0.8, "xm": 0.8, "hx": 0.9}
_GENERIC_TIER = {
    ("i", "3"): 2500, ("i", "5"): 4000, ("i", "7"): 5500, ("i", "9"): 18000,
    ("ryzen", "3"): 6500, ("ryzen", "5"): 12000, ("ryzen", "7"): 15000,
    ("ryzen", "9"): 32000,
    ("ultra", "5"): 20000, ("ultra", "7"): 25000, ("ultra", "9"): 30000,
}


def _intel_gen(num):
    if len(num) == 3:
        return 1
    if len(num) == 4:
        return int(num[:2]) if num[0] == "1" else int(num[0])
    return int(num[:2])


def _estimate_intel(tier, num, suffix):
    gen = _intel_gen(num)
    base = _I5_BASE.get(gen)
    if not base:
        return None
    mult = _INTEL_TIER[tier](gen)
    mult *= _MOBILE_SUFFIX.get(suffix, _DESKTOP_SUFFIX.get(suffix, 1.0))
    return round(base * mult)


def _estimate_ryzen(tier, num, suffix):
    series = int(num[0])
    base = _R5_BASE.get(series)
    if not base:
        return None
    mult = _RYZEN_TIER[tier]
    if suffix in ("g", "ge"):
        mult *= 0.9 if series >= 4 else 0.6
    elif suffix == "u":
        mult *= 0.65 if series >= 4 else 0.45
    else:
        mult *= _MOBILE_SUFFIX.get(suffix, _DESKTOP_SUFFIX.get(suffix, 1.0))
    return round(base * mult)


def _suffix_at(toks, i):
    if i < len(toks) and toks[i] in _DESKTOP_SUFFIX or \
       i < len(toks) and toks[i] in _MOBILE_SUFFIX or \
       i < len(toks) and toks[i] == "ge":
        return toks[i]
    return ""


def _cpu_label(family, tier, num, suffix):
    if family == "i":
        return "Core i%s-%s%s" % (tier, num, suffix.upper())
    if family == "ultra":
        return "Core Ultra %s %s%s" % (tier, num, suffix.upper())
    return "Ryzen %s %s%s" % (tier, num, suffix.upper())


def _family_hits(toks, covered):
    """Core i / Ryzen models that are not in the table: estimate them."""
    out = []
    for i, tok in enumerate(toks):
        if i in covered or tok not in ("i", "ryzen", "ultra"):
            continue
        if i + 1 >= len(toks) or toks[i + 1] not in ("3", "5", "7", "9"):
            continue
        tier = toks[i + 1]
        vendor = "amd" if tok == "ryzen" else "intel"
        num = toks[i + 2] if i + 2 < len(toks) else ""
        # "i5 2.4 GHz" has no model number: the next token is a clock speed.
        has_model = (num.isdigit() and 3 <= len(num) <= 5 and
                     not (i + 3 < len(toks) and toks[i + 3] in ("ghz", "mhz")))
        if has_model and tok != "ultra":
            suffix = _suffix_at(toks, i + 3)
            est = (_estimate_ryzen if tok == "ryzen" else _estimate_intel)(
                tier, num, suffix)
            if est:
                out.append((i, {"name": _cpu_label(tok, tier, num, suffix),
                                "score": est, "vendor": vendor,
                                "kind": "estimate"}))
                continue
        label = {"i": "Core i%s", "ryzen": "Ryzen %s",
                 "ultra": "Core Ultra %s"}[tok] % tier
        out.append((i, {"name": label + " (any model)",
                        "score": _GENERIC_TIER[(tok, tier)],
                        "vendor": vendor, "kind": "generic"}))
    return out


_CORE_WORDS = [("single", 1), ("dual", 2), ("triple", 3), ("quad", 4),
               ("hexa", 6), ("six", 6), ("octa", 8), ("eight", 8)]
_PER_CORE_GHZ = {1: 200, 2: 230, 3: 240, 4: 250, 6: 350, 8: 400}


def _generic_cpu(text):
    """"Dual core 2.4 GHz" style requirements: a rough score from the numbers."""
    low = (text or "").lower()
    cores = None
    for word, n in _CORE_WORDS:
        if re.search(r"\b%s[\s-]*core" % word, low):
            cores = n
            break
    if cores is None:
        m = re.search(r"\b(\d{1,2})[\s-]*(?:physical\s*)?cores?\b", low)
        if m:
            cores = int(m.group(1))
        else:
            m = re.search(r"\b(\d{1,2})\s*(?:hardware\s*)?(?:cpu\s*)?threads\b", low)
            if m:
                cores = max(1, int(m.group(1)) // 2)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*\+?\s*ghz", low)
    ghz = float(m.group(1).replace(",", ".")) if m else None
    if ghz is None:
        m = re.search(r"(\d{3,4})\s*mhz", low)
        ghz = int(m.group(1)) / 1000 if m else None
    if cores is None and ghz is None:
        return None
    if cores is None:
        cores = 1
    if ghz is None:
        ghz = 2.4 if cores <= 2 else 3.0
    cores = min(cores, 16)
    per = _PER_CORE_GHZ.get(cores, 400 if cores > 8 else 300)
    label = "%d-core %.1f GHz class CPU" % (cores, ghz) if cores > 1 \
        else "%.1f GHz single-core class CPU" % ghz
    return {"name": label, "score": round(cores * ghz * per), "vendor": None,
            "kind": "generic"}


def match_cpus(text):
    """Every processor named in `text`, as candidate dicts."""
    toks = tokens(text, _CPU_STOP)
    out, covered = [], set()
    for s, e, entry in _scan(toks, _CPU_INDEX):
        covered.update(range(s, e))
        cand = dict(entry, kind="exact")
        nxt = toks[e] if e < len(toks) else ""
        key = tokens(entry["name"], _CPU_STOP)
        is_model = key[-1].isdigit() and len(key[-1]) >= 3
        if is_model and entry["vendor"] != "qualcomm":
            fam = "ryzen" if key[0] == "ryzen" else "i" if "i" in key[:2] else ""
            tier = key[key.index(fam) + 1] if fam else ""
            if nxt in _MOBILE_SUFFIX and tier in _RYZEN_TIER:
                # Same number, laptop chip: a different CPU entirely.
                est = (_estimate_ryzen if fam == "ryzen" else _estimate_intel)(
                    tier, key[-1], nxt)
                if est:
                    cand.update(score=est, kind="estimate",
                                name=entry["name"] + nxt.upper())
            elif nxt in _DESKTOP_SUFFIX and _DESKTOP_SUFFIX[nxt] != 1.0:
                cand.update(score=round(entry["score"] * _DESKTOP_SUFFIX[nxt]),
                            kind="variant", name=entry["name"] + nxt.upper())
        out.append((s, cand))
    out.extend(_family_hits(toks, covered))
    out.sort(key=lambda p: p[0])
    cands = [c for _, c in out]
    if not cands:
        g = _generic_cpu(text)
        if g:
            cands.append(g)
    return cands


# ------------------------------------------------------- picking a candidate

def specific(cands):
    """Model-level candidates if there are any, otherwise everything."""
    named = [c for c in cands if c["kind"] != "generic"]
    return named or cands


def pick_requirement(cands, local_vendor=None):
    """The bar to clear when a requirement lists alternatives ("A or B").

    Compare like with like: an NVIDIA owner is measured against the NVIDIA
    card the developer listed. Otherwise the easiest alternative counts.
    """
    pool = specific(cands)
    if not pool:
        return None
    same = [c for c in pool if local_vendor and c["vendor"] == local_vendor]
    return min(same or pool, key=lambda c: c["score"])


def identify_gpu(name):
    """Best reading of one local adapter name (None if unrecognised)."""
    cands = match_gpus(name)
    return max(cands, key=lambda c: c["score"]) if cands else None


def identify_cpu(name, cores=None, mhz=None):
    cands = [c for c in match_cpus(name)
             if not (c["kind"] == "generic" and c["vendor"] is None)]
    if cands:
        return max(specific(cands), key=lambda c: c["score"])
    if cores and mhz:
        # Unknown chip: assume a mid-2010s-or-later core, flag as rough.
        vendor = ("intel" if "intel" in name.lower() else
                  "amd" if "amd" in name.lower() else None)
        return {"name": name.strip(), "vendor": vendor, "kind": "generic",
                "score": round(cores * (mhz / 1000) * 600)}
    return None


def search_hardware(kind, query, limit=12):
    """Autocomplete for the "what if I had..." picker."""
    q = tokens(query)
    table = GPUS if kind == "gpu" else CPUS
    out = []
    for name, score in table:
        hay = " ".join(tokens(name))
        if all(t in hay for t in q):
            out.append({"name": name, "score": score})
            if len(out) >= limit:
                break
    return out
