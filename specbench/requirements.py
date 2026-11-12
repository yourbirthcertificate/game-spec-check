"""Parse published system requirements into comparable numbers."""
import html
import re

from . import matcher

_TAG = re.compile(r"<[^>]+>")
_FIELD = re.compile(
    r"<strong>\s*([^<]{1,40}?)\s*\*?\s*:\s*\*?\s*</strong>(.*?)(?=<strong>|</li>|</ul>|$)",
    re.S | re.I)

_LABELS = {
    "os": "os", "operating system": "os", "supported os": "os",
    "processor": "cpu", "cpu": "cpu",
    "memory": "ram", "ram": "ram", "system memory": "ram",
    "graphics": "gpu", "video card": "gpu", "video": "gpu",
    "graphics card": "gpu", "gpu": "gpu",
    "directx": "directx", "directx version": "directx", "directx®": "directx",
    "storage": "storage", "hard drive": "storage", "hard disk space": "storage",
    "hard disk": "storage", "hdd": "storage", "disk space": "storage",
    "additional notes": "notes", "additional": "notes", "notes": "notes",
    "sound card": "sound", "sound": "sound", "network": "network",
    "vr support": "vr",
}


def _text(fragment):
    fragment = re.sub(r"<br\s*/?>", "\n", fragment or "", flags=re.I)
    fragment = html.unescape(_TAG.sub("", fragment))
    return re.sub(r"[ \t\r\f\v]+", " ", fragment).strip(" \n:")


def parse_steam(block):
    """One Steam requirements blob (minimum or recommended) -> fields."""
    if not block or not isinstance(block, str):
        return None
    fields = {}
    for label, value in _FIELD.findall(block):
        key = _LABELS.get(_text(label).lower().strip(" *"))
        value = re.sub(r"\s*\n\s*", " ", _text(value)).strip()
        if key and value and key not in fields:
            fields[key] = value
    raw = re.sub(r"\n{2,}", "\n", _text(block))
    raw = re.sub(r"^(minimum|recommended)\s*:?\s*", "", raw, flags=re.I).strip()
    if not fields:
        # Older listings: plain "Label: value" lines without bold labels.
        for line in raw.split("\n"):
            m = _LINE.match(line.strip())
            key = _LABELS.get(m.group(1).lower().strip()) if m else None
            if key and key not in fields:
                fields[key] = m.group(2).strip()
    if not fields and not raw:
        return None
    return {"fields": fields, "raw": raw}


_LINE = re.compile(r"^(?:required|minimum|recommended)?\s*"
                   r"([A-Za-z][A-Za-z ]{1,24}?)\s*[®™]?\s*:\s*(\S.*)$", re.I)
_CORE_FIELDS = ("os", "cpu", "ram", "gpu", "directx", "storage")


def has_substance(analysis):
    """False for a level that says nothing comparable ("Requires 64-bit")."""
    f = analysis["fields"]
    placeholder = re.compile(r"^\W*(tbd|tba|n/?a|none|-)?\W*$", re.I)
    if any(f.get(k) and not placeholder.match(f[k]) for k in _CORE_FIELDS):
        return True
    return bool(analysis["cpu"] or analysis["gpu"] or analysis["ram_gb"])


# ----------------------------------------------------------- PCGamingWiki

def _wiki_clean(v):
    v = re.sub(r"<ref[^>]*/>", "", v)
    v = re.sub(r"<ref[^>]*>.*?</ref>", "", v, flags=re.S)
    v = re.sub(r"\{\{[^{}]*\}\}", "", v)
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)
    v = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", v)
    v = v.replace("'''", "").replace("''", "")
    return html.unescape(_TAG.sub("", v)).strip()


_PCGW_BLOCK = re.compile(r"\{\{System requirements(.*?)\n\}\}", re.S | re.I)


def pcgw_blocks(wikitext):
    """Just the system-requirement templates of a page (what gets cached)."""
    return [m.group(0) for m in _PCGW_BLOCK.finditer(wikitext or "")]


def parse_pcgw(wikitext):
    """Windows block of a PCGamingWiki page -> {minimum, recommended}."""
    for m in _PCGW_BLOCK.finditer(wikitext or ""):
        params = {}
        for line in re.split(r"\n\s*\|", "\n" + m.group(1)):
            if "=" in line:
                k, v = line.split("=", 1)
                params[k.strip()] = _wiki_clean(v)
        if "windows" not in params.get("OSfamily", "").lower():
            continue
        out = {}
        for prefix, level in (("min", "minimum"), ("rec", "recommended")):
            def alts(name):
                vals = [params.get(prefix + name + n, "") for n in ("", "2", "3")]
                return " / ".join(v for v in vals if v)
            f = {}
            os_ = params.get(prefix + "OS", "")
            if os_:
                f["os"] = os_ if "win" in os_.lower() else "Windows " + os_
            for name, key in (("CPU", "cpu"), ("RAM", "ram"), ("HD", "storage"),
                              ("GPU", "gpu"), ("DX", "directx")):
                if alts(name):
                    f[key] = alts(name)
            vram = params.get(prefix + "VRAM", "")
            if vram:
                f["vram"] = vram
            if f:
                out[level] = {"fields": f, "raw": ""}
        if out:
            return out
    return {}


# ---------------------------------------------------------------- analysis

_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(tb|gb|mb|kb|g|m)\b", re.I)
_UNIT = {"tb": 1024.0, "gb": 1.0, "g": 1.0, "mb": 1 / 1024, "m": 1 / 1024,
         "kb": 1 / 1048576}


def _size_gb(text):
    m = _SIZE.search(text or "")
    if not m:
        return None
    return round(float(m.group(1).replace(",", ".")) * _UNIT[m.group(2).lower()], 3)


def _windows_version(text):
    low = (text or "").lower()
    found = []
    if re.search(r"\bxp\b", low):
        found.append(5.1)
    if "vista" in low:
        found.append(6.0)
    if re.search(r"\b(2000|98|95|me)\b", low) and "win" in low:
        found.append(5.0)
    for v in re.findall(r"(?<![\d.])(11|10|8\.1|8|7)(?![\d.])(?!\s*-?\s*bit)", low):
        found.append(float(v))
    return min(found) if found else None


_VRAM_HINT = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(gb|mb)\s*(?:of\s+)?(?:dedicated\s+)?"
    r"(?:v-?ram|video\s*(?:memory|ram)|vram|graphics\s*memory)", re.I)
# VRAM -> the kind of card that shipped with that much memory.
_VRAM_CLASS = [(0.064, 30), (0.128, 60), (0.256, 150), (0.512, 400),
               (1, 900), (2, 2500), (3, 4500), (4, 6500), (6, 10000),
               (8, 14000), (12, 20000), (16, 26000)]
_DX_CLASS = {8: 20, 9: 100, 10: 400, 11: 800, 12: 2500}


def _gpu_from_vram(gb):
    score = _VRAM_CLASS[0][1]
    for size, s in _VRAM_CLASS:
        if gb >= size * 0.95:
            score = s
    label = ("%g GB" % gb) if gb >= 1 else ("%d MB" % round(gb * 1024))
    return {"name": "%s graphics card class" % label, "score": score,
            "vendor": None, "kind": "generic"}


def analyze(level):
    """Fields of one requirement level -> numbers the comparer understands."""
    f = dict(level.get("fields") or {})
    raw = level.get("raw") or ""
    whole = " ".join([raw] + list(f.values()))
    out = {"fields": f, "raw": raw}

    out["os_version"] = _windows_version(f.get("os") or
                                         (raw if not f else ""))
    out["needs_64bit"] = bool(re.search(r"64\s*-?\s*bit|\bx64\b", whole, re.I))

    cpu_text = f.get("cpu") or (raw if not f else "")
    out["cpu"] = matcher.match_cpus(cpu_text) if cpu_text else []

    ram = _size_gb(f.get("ram", ""))
    if ram is None and not f:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(gb|mb)\s*(?:of\s+)?(?:system\s+)?"
                      r"(?:ram|memory)", raw, re.I)
        ram = _size_gb(m.group(0)) if m else None
    out["ram_gb"] = ram

    gpu_text = f.get("gpu") or (raw if not f else "")
    out["gpu"] = matcher.match_gpus(gpu_text) if gpu_text else []

    vram = _size_gb(f.get("vram", ""))
    if vram is None:
        m = _VRAM_HINT.search(gpu_text)
        vram = _size_gb(m.group(0)) if m else None
    out["vram_gb"] = vram

    dx = None
    m = re.search(r"\d{1,2}", f.get("directx", ""))
    if not m:
        m = re.search(r"(?:directx|dx)\W{0,3}(\d{1,2})", gpu_text, re.I)
    if m and 7 <= int(m.group(m.lastindex or 0)) <= 12:
        dx = int(m.group(m.lastindex or 0))
    out["directx"] = dx

    if all(c["kind"] == "generic" for c in out["gpu"]):
        # No card named: fall back to the memory size, then the DirectX level.
        size = vram if vram is not None else (
            _size_gb(gpu_text) if f.get("gpu") else None)
        if size is not None and size <= 32:
            out["gpu"] = [_gpu_from_vram(size)]
            out["vram_gb"] = size
        elif not out["gpu"] and dx in _DX_CLASS and gpu_text:
            out["gpu"] = [{"name": "DirectX %d class graphics card" % dx,
                           "score": _DX_CLASS[dx], "vendor": None,
                           "kind": "generic"}]

    out["storage_gb"] = _size_gb(f.get("storage", ""))
    return out
