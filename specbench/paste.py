"""Requirements pasted from anywhere: a store page, a forum post, a box.

The text is split into minimum and recommended when it has those headings,
then read line by line. A field found under a label ("Processor: ...") is
taken as is. A field guessed from an unlabelled line is marked uncertain, so
the person can check it before anything is compared. Nothing is invented: a
field that cannot be found stays empty.
"""
import re

from . import matcher, requirements

MAX_TEXT = 6000
FIELDS = ("os", "cpu", "gpu", "ram", "vram", "directx", "storage")
_HEAD = re.compile(r"^\s*(minimum|min\.?|recommended|rec\.?)\b(?:\s+(?:system\s+)?requirements?)?"
                   r"\s*[:\-]?\s*", re.I | re.M)
_BULLET = re.compile(r"^[\s•·*\-–—>]+")
_LABEL = re.compile(r"^([A-Za-z][A-Za-z /]{1,30}?)\s*[®™]?\s*[:\-–]\s*(\S.*)$")


def split_levels(text):
    """{"minimum": str, "recommended": str} using headings when present."""
    text = (text or "")[:MAX_TEXT].replace("\r\n", "\n")
    marks = list(_HEAD.finditer(text))
    if not marks:
        return {"minimum": text.strip()} if text.strip() else {}
    out = {}
    for n, m in enumerate(marks):
        end = marks[n + 1].start() if n + 1 < len(marks) else len(text)
        level = "minimum" if m.group(1).lower().startswith("min") else "recommended"
        chunk = text[m.end():end].strip()
        if chunk and level not in out:
            out[level] = chunk
    before = text[:marks[0].start()].strip()
    if before and "minimum" not in out:
        out["minimum"] = before
    return out


_GUESS = [
    ("ram", re.compile(r"\d+(?:[.,]\d+)?\s*(?:gb|mb)\s*(?:of\s+)?(?:system\s+)?(?:ram|memory)\b", re.I)),
    ("vram", re.compile(r"\d+(?:[.,]\d+)?\s*(?:gb|mb)\s*(?:of\s+)?(?:dedicated\s+)?(?:vram|video\s*memory)", re.I)),
    ("storage", re.compile(r"\d+(?:[.,]\d+)?\s*(?:tb|gb|mb)\b.*\b(?:storage|disk|space|ssd|hdd|drive)\b|"
                           r"\b(?:storage|disk|space|ssd|hdd)\b.*\d+\s*(?:tb|gb|mb)\b", re.I)),
    ("directx", re.compile(r"\bdirect\s*x\s*\d{1,2}|\bdx\s*1[0-2]\b", re.I)),
    ("os", re.compile(r"\bwindows\b|\bwin\s*(?:7|8|10|11)\b", re.I)),
]


def parse_level(text):
    """One level of pasted text -> {fields, raw, uncertain, unlabelled}."""
    text = (text or "")[:MAX_TEXT]
    fields, uncertain, unlabelled = {}, [], []
    lines = [_BULLET.sub("", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    for ln in lines:
        m = _LABEL.match(ln)
        key = requirements._LABELS.get(m.group(1).lower().strip()) if m else None
        if key == "gpu" and m and "memory" in m.group(1).lower():
            key = "vram"
        if key in FIELDS and key not in fields:
            fields[key] = m.group(2).strip()
        else:
            unlabelled.append(ln)
    for ln in list(unlabelled):
        for key, rx in _GUESS:
            if key not in fields and rx.search(ln):
                fields[key] = ln
                uncertain.append(key)
                break
        else:
            if "cpu" not in fields and matcher.match_cpus(ln) and not matcher.match_gpus(ln):
                fields["cpu"] = ln
                uncertain.append("cpu")
            elif "gpu" not in fields and matcher.match_gpus(ln):
                fields["gpu"] = ln
                uncertain.append("gpu")
    # A labelled field that only matched loosely is worth a second look too.
    if fields.get("cpu") and "cpu" not in uncertain:
        if all(c["kind"] == "generic" for c in matcher.match_cpus(fields["cpu"])):
            uncertain.append("cpu")
    if fields.get("gpu") and "gpu" not in uncertain:
        if all(c["kind"] == "generic" for c in matcher.match_gpus(fields["gpu"])):
            uncertain.append("gpu")
    used = set(fields.values())
    return {"fields": fields, "raw": text.strip(), "uncertain": uncertain,
            "unlabelled": [ln for ln in unlabelled if ln not in used][:20]}


def parse(text_min, text_rec=None):
    """Both levels. With one box holding both, its headings split it."""
    if text_rec and text_rec.strip():
        chunks = {"minimum": (text_min or "").strip(), "recommended": text_rec.strip()}
    else:
        chunks = split_levels(text_min)
    return {lv: parse_level(t) for lv, t in chunks.items() if t}
