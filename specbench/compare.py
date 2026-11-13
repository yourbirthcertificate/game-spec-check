"""Compare a machine against one analysed requirement level.

Row statuses: pass, close, fail, warn (storage only) and unknown. An unknown
row says why in `unknown_reason`:

  not_listed     the listing does not name this part
  unreadable     the listing names it, but it could not be matched to anything
  local_unknown  the listing is clear, but this PC's value is not known

Level verdicts: pass, close, fail, partial and unknown. A definite failure
always wins. A requirement that was published but could not be checked turns
what would be a pass or close into "partial", so an unchecked processor or
graphics card can never produce an unconditional "meets the specs".
"""
import re

from . import matcher

PASS, CLOSE, FAIL, UNKNOWN, WARN, PARTIAL = "pass", "close", "fail", "unknown", "warn", "partial"
NOT_LISTED, UNREADABLE, LOCAL_UNKNOWN = "not_listed", "unreadable", "local_unknown"

# Scores are approximate, so "within 5%" counts as meeting the bar and
# "within 20%" is flagged as borderline instead of a hard fail.
_PASS_RATIO = 0.95
_CLOSE_RATIO = 0.80
# Memory and video memory. A game that asks for 8 GB usually still starts
# with 6 GB, with stutter or lower texture settings, so up to about a quarter
# short is "close". A bigger shortfall is reported as not met: games can
# refuse to start, crash or swap constantly at that point.
_MEM_CLOSE_RATIO = 0.74

CORE = ("cpu", "gpu", "ram", "vram", "os", "directx")
DECISIVE = ("cpu", "gpu", "ram")

_WIN_NAMES = {5.0: "2000", 5.1: "XP", 6.0: "Vista", 7.0: "7", 8.0: "8",
              8.1: "8.1", 10.0: "10", 11.0: "11"}

# Things listings mention that the speed table cannot check.
_MENTIONS = [
    (r"\bavx\s*-?\s*512\b", "AVX-512 instructions"),
    (r"\bavx\s*2\b", "AVX2 instructions"),
    (r"\bavx\b(?!\s*-?\s*(2|512))", "AVX instructions"),
    (r"\bsse\s*4(\.[12])?\b", "SSE4 instructions"),
    (r"\bssd\b|solid[- ]state", "an SSD"),
    (r"ray[- ]?tracing|\bdxr\b", "ray tracing support"),
    (r"\btpm\b", "TPM"),
    (r"secure boot", "Secure Boot"),
    (r"\bvulkan\b", "Vulkan support"),
    (r"opengl\s*\d", "an OpenGL version"),
    (r"shader model\s*\d", "a shader model"),
    (r"\bfps\b|frames per second", "a frame rate target"),
    (r"\b(720|1080|1440|2160)p\b|\b4k\b", "a screen resolution target"),
    (r"\bvr\b|virtual reality|headset", "VR hardware"),
]


def _status(ratio):
    if ratio >= _PASS_RATIO:
        return PASS
    return CLOSE if ratio >= _CLOSE_RATIO else FAIL


def _mem_status(ratio):
    if ratio >= _PASS_RATIO:
        return PASS
    return CLOSE if ratio >= _MEM_CLOSE_RATIO else FAIL


def _gb(v):
    if v is None:
        return "?"
    if v < 1:
        return "%d MB" % round(v * 1024)
    return ("%.1f" % v).rstrip("0").rstrip(".") + " GB"


def _unknown(row, reason, note):
    row.update(status=UNKNOWN, unknown_reason=reason, note=note)
    return row


def mentions(req):
    text = " ".join([req.get("raw") or ""] + list((req.get("fields") or {}).values()))
    found = []
    for pattern, label in _MENTIONS:
        if re.search(pattern, text, re.I) and label not in found:
            found.append(label)
    return found


def _part_row(key, label, req_text, cands, mine):
    part = {"cpu": "processor", "gpu": "graphics card"}.get(key, label.lower())
    row = {"key": key, "label": label, "required": req_text or "Not specified",
           "yours": mine["name"] if mine else "Not detected",
           "status": UNKNOWN, "ratio": None, "note": "", "evidence": ""}
    if not req_text:
        return _unknown(row, NOT_LISTED, "The listing does not name a %s." % part)
    target = matcher.pick_requirement(cands, (mine or {}).get("vendor"))
    if not target:
        return _unknown(row, UNREADABLE,
                        "Could not match this requirement to a known %s, so it was not checked." % part)
    row.update(target=target["name"], target_score=target["score"], target_kind=target["kind"])
    if not mine or not mine.get("score"):
        return _unknown(row, LOCAL_UNKNOWN,
                        "This PC's %s is not in the speed table, so it was not checked." % part)
    ratio = mine["score"] / target["score"]
    row.update(status=_status(ratio), ratio=round(ratio, 2), your_score=mine["score"],
               estimated=target["kind"] in ("generic", "estimate")
               or mine.get("kind") in ("generic", "estimate"))
    if target["kind"] == "generic":
        row["note"] = "No exact model listed; compared against a typical %s." % target["name"]
        req_ev = "No exact model in the listing, so a typical %s is used." % target["name"]
    elif target["kind"] == "estimate":
        row["note"] = "%s is not in the score table; its score is estimated." % target["name"]
        req_ev = "%s is not in the speed table; its score is estimated from its generation and tier." % target["name"]
    elif target["kind"] == "variant":
        req_ev = "Matched %s, scored as a variant of the listed model." % target["name"]
    else:
        req_ev = "Matched %s in the speed table." % target["name"]
    if mine.get("kind") == "exact" and mine.get("matched"):
        mine_ev = "Yours matched %s." % mine["matched"]
    elif mine.get("kind") == "variant":
        mine_ev = "Yours is scored from %s, adjusted for the laptop version." % (
            (mine.get("matched") or "the desktop model").replace(" (laptop)", ""))
    else:
        mine_ev = "Your %s's score is an estimate." % part
    row["evidence"] = "%s %s" % (req_ev, mine_ev)
    return row


def _mem_row(key, label, need, have, field_text, have_note=None):
    row = {"key": key, "label": label, "status": UNKNOWN, "ratio": None,
           "required": field_text or (_gb(need) if need else "Not specified"),
           "yours": _gb(have) if have else "Unknown", "note": "", "evidence": ""}
    if not need:
        if field_text:
            return _unknown(row, UNREADABLE, "Could not read an amount from this requirement.")
        return _unknown(row, NOT_LISTED, "The listing does not say how much %s it needs." % label.lower())
    row["need_text"] = _gb(need)
    if not have:
        return _unknown(row, LOCAL_UNKNOWN, have_note or "This PC's %s is not known." % label.lower())
    ratio = have / need
    row.update(ratio=round(ratio, 2), have_text=_gb(have), status=_mem_status(ratio),
               evidence="%s asked for, %s here." % (_gb(need), _gb(have)))
    return row


def compare(specs, req, installed=False):
    """Rows and a verdict for one level (output of requirements.analyze).

    `installed` drops the free-space check, which means nothing for a game
    that is already on the disk.
    """
    f = req.get("fields") or {}
    rows = []

    rows.append(_part_row("cpu", "Processor", f.get("cpu"), req.get("cpu") or [], specs.get("cpu")))
    rows.append(_part_row("gpu", "Graphics", f.get("gpu"), req.get("gpu") or [], specs.get("gpu")))

    ram = _mem_row("ram", "Memory", req.get("ram_gb"), specs.get("ram_gb"), f.get("ram"))
    ram["yours"] = _gb(specs.get("ram_gb")) + " RAM" if specs.get("ram_gb") else "Unknown"
    rows.append(ram)

    # Video memory: checked whenever an amount was read, whichever field it
    # came from (Steam puts it in the graphics line, PCGamingWiki in its own).
    need = req.get("vram_gb")
    if need:
        gpu = specs.get("gpu") or {}
        have, note = gpu.get("vram_gb"), None
        shared = False
        if gpu.get("integrated"):
            # Integrated graphics borrow system memory; Windows lets them use
            # up to about half of it.
            shared = True
            have = gpu.get("shared_gb") or ((specs.get("ram_gb") or 0) / 2) or None
            note = "Integrated graphics use system memory, and how much is not known."
        elif not have:
            note = "The amount of video memory on this graphics card is not known."
        row = _mem_row("vram", "Video memory", need, have,
                       f.get("vram") or "%s video memory" % _gb(need), note)
        row["yours"] = (("Up to %s shared" % _gb(have)) if shared else _gb(have) + " VRAM") if have else "Unknown"
        if shared and row["status"] != UNKNOWN:
            row["estimated"] = True
            row["note"] = "Integrated graphics share system memory; counted as up to %s." % _gb(have)
        rows.append(row)

    # Operating system
    osinfo = specs.get("os") or {}
    need = req.get("os_version")
    os_text = f.get("os") or ""
    row = {"key": "os", "label": "Operating system", "status": UNKNOWN, "ratio": None,
           "required": os_text or "Not specified",
           "yours": osinfo.get("name") or "Unknown", "note": "", "evidence": ""}
    if not need and not os_text:
        _unknown(row, NOT_LISTED, "")
    elif not osinfo.get("version"):
        _unknown(row, LOCAL_UNKNOWN, "The Windows version of this PC is not known.")
    elif not need and "win" not in os_text.lower():
        _unknown(row, UNREADABLE, "Could not read a Windows version from this requirement.")
    else:
        ok = need is None or osinfo["version"] >= need
        if ok and req.get("needs_64bit") and not osinfo.get("is_64bit", True):
            ok = False
            row["note"] = "Needs 64-bit Windows."
        row["status"] = PASS if ok else FAIL
        if not ok and not row["note"]:
            row["note"] = "Needs Windows %s or newer." % _WIN_NAMES.get(need, need)
    rows.append(row)

    # DirectX: what the graphics hardware supports, not the Windows version.
    need = req.get("directx")
    if need:
        cap = (specs.get("gpu") or {}).get("directx") or {}
        have = cap.get("directx")
        row = {"key": "directx", "label": "DirectX", "ratio": None, "need_text": "DirectX %d" % need,
               "required": f.get("directx") or "DirectX %d" % need, "note": "", "evidence": "",
               "yours": "Unknown", "status": UNKNOWN}
        if not have:
            _unknown(row, LOCAL_UNKNOWN,
                     "Could not tell which DirectX version this graphics card supports.")
        else:
            fl = cap.get("feature_level")
            row["yours"] = "DirectX %d" % have + (" (feature level %s)" % fl.replace("_", ".") if fl else "")
            row["status"] = PASS if have >= need else FAIL
            if cap.get("source") == "model":
                row["estimated"] = True
                row["evidence"] = "Estimated from the graphics card model, not tested on this PC."
                row["note"] = "Estimated from the card model."
            else:
                row["evidence"] = "Reported by Direct3D for this graphics card."
        rows.append(row)

    # Storage: a shortfall is fixable, so it warns instead of failing.
    need = req.get("storage_gb")
    drive = specs.get("best_drive")
    if not installed and (need or f.get("storage")):
        row = {"key": "storage", "label": "Storage", "status": UNKNOWN, "ratio": None,
               "required": f.get("storage") or "Not specified",
               "yours": "%s free on %s" % (_gb(drive["free_gb"]), drive["id"]) if drive else "Unknown",
               "note": "", "evidence": ""}
        if need and drive:
            row["ratio"] = round(drive["free_gb"] / need, 2)
            row["status"] = PASS if drive["free_gb"] >= need else WARN
            if row["status"] == WARN:
                row["note"] = "Free up %s to install." % _gb(need - drive["free_gb"])
        rows.append(row)

    core = [r for r in rows if r["key"] in CORE]
    known = [r for r in core if r["status"] != UNKNOWN]
    unchecked = [r for r in core if r["status"] == UNKNOWN and r.get("unknown_reason") != NOT_LISTED]
    decisive = [r for r in known if r["key"] in DECISIVE]
    if any(r["status"] == FAIL for r in known):
        verdict = FAIL
    elif not decisive:
        verdict = UNKNOWN
    elif unchecked:
        verdict = PARTIAL
    elif any(r["status"] == CLOSE for r in known):
        verdict = CLOSE
    else:
        verdict = PASS
    return {
        "rows": rows, "verdict": verdict,
        "failing": [r["label"] for r in known if r["status"] == FAIL],
        "close": [r["label"] for r in known if r["status"] == CLOSE],
        "unchecked": [r["label"] for r in unchecked],
        "missing": [r["label"] for r in core if r.get("unknown_reason") == NOT_LISTED
                    and r["key"] in DECISIVE],
        "estimated": [r["label"] for r in known if r.get("estimated")],
        # Kept for older callers: decisive parts that could not be compared.
        "unknown": [r["label"] for r in core if r["status"] == UNKNOWN and r["key"] in DECISIVE],
        "not_checked": mentions(req),
        "limits": [r["key"] for r in known if r["status"] in (FAIL, CLOSE)],
        "raw": req.get("raw", ""),
    }


def _explain(r):
    """One plain sentence about a row that is short of the requirement or unchecked."""
    key = r["key"]
    if r["status"] == UNKNOWN:
        return "%s: %s" % (r["label"], r.get("note") or "not checked.")
    if key in ("cpu", "gpu"):
        part = "processor" if key == "cpu" else "graphics card"
        article = "a" if r.get("target_kind") == "generic" else "the"
        target = "%s %s" % (article, r.get("target") or "part it asks for")
        pct = round((r.get("ratio") or 0) * 100)
        if r["status"] == CLOSE:
            return "Your %s is a little slower than %s it asks for (about %d%% as fast)." % (
                part, target, pct)
        return "Your %s is too slow: about %d%% as fast as %s it asks for." % (part, pct, target)
    if key == "ram":
        return "It asks for %s of memory and this PC has %s." % (
            r.get("need_text", "more"), r.get("have_text", "less"))
    if key == "vram":
        return "It asks for %s of video memory and this graphics card has %s." % (
            r.get("need_text", "more"), r.get("have_text", "less"))
    if key == "directx":
        return "It needs %s, and this graphics card supports %s." % (
            r.get("need_text", "a newer DirectX"), r.get("yours", "less"))
    return r.get("note") or "It needs a newer version of Windows."


def _issues(level, include_unknown=False):
    short = [r for r in level["rows"] if r["status"] in (FAIL, CLOSE)]
    short.sort(key=lambda r: r["status"] != FAIL)
    if include_unknown:
        short += [r for r in level["rows"] if r["key"] in CORE and r["status"] == UNKNOWN
                  and r.get("unknown_reason") != NOT_LISTED]
    return [{"label": r["label"], "status": r["status"], "text": _explain(r)} for r in short]


def _list(labels):
    labels = [x.lower() for x in labels]
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


TIER_GROUP = {"great": "met", "ok": "met", "close": "borderline", "fail": "failing",
              "partial": "incomplete", "unknown": "incomplete"}


def summarize(levels):
    """The answer a person wants, limited to what was actually checked."""
    mn, rec = levels.get("minimum"), levels.get("recommended")
    out = {"issues": [], "issues_title": "", "storage_note": "", "limits": [],
           "not_checked": [], "estimated": []}
    base = mn or rec
    if not base:
        out.update(tier="unknown", group="incomplete", headline="No PC specs published",
                   detail="This source has not published Windows requirements for this game.")
        return out
    for r in base["rows"]:
        if r["key"] == "storage" and r["status"] == WARN:
            out["storage_note"] = "You will need to clear some space first. " + r["note"]
    which = "minimum" if mn else "recommended"
    out["not_checked"] = base.get("not_checked", [])
    out["estimated"] = base.get("estimated", [])
    missing = base.get("missing", [])
    missing_note = (" The listing does not name a %s, so that was not checked." % _list(missing)
                    if missing else "")
    v = base["verdict"]

    if v == FAIL:
        out.update(tier="fail", headline="Below the %s specs" % which,
                   detail="This PC falls short of the %s specs." % which
                   + ("" if mn else " Only recommended specs are published, so it may still"
                                    " run at lower settings, but that cannot be checked."),
                   issues=_issues(base), issues_title="Where it falls short",
                   limits=base.get("limits", []))
    elif v == PARTIAL:
        state = ("meet the %s specs" % which if not base["close"]
                 else "are at or near the %s specs" % which)
        out.update(tier="partial", headline="Only partly checked",
                   detail="The parts that could be checked %s, but the %s requirement could "
                          "not be checked." % (state, _list(base["unchecked"])),
                   issues=_issues(base, include_unknown=True),
                   issues_title="What could not be checked", limits=base.get("limits", []))
    elif v == UNKNOWN:
        out.update(tier="unknown", headline="Can't tell from the listing",
                   detail="The published specs could not be matched to this PC's processor, "
                          "graphics card or memory. The original wording is shown below.",
                   issues=_issues(base, include_unknown=True), issues_title="What could not be checked")
    elif v == CLOSE:
        out.update(tier="close", headline="Close to the %s specs" % which,
                   detail="This PC is at the edge of the %s specs. Expect to lower settings, "
                          "and it may not run smoothly." % which + missing_note,
                   issues=_issues(base), issues_title="Where it falls short",
                   limits=base.get("limits", []))
    elif rec and mn and rec["verdict"] == PASS:
        out.update(tier="ok" if missing or rec.get("missing") else "great",
                   headline="Meets the recommended specs",
                   detail="Every part that was checked meets the recommended specs." + missing_note)
    elif not mn:
        out.update(tier="ok" if missing else "great", headline="Meets the recommended specs",
                   detail="Only recommended specs are published, and every part that was "
                          "checked meets them." + missing_note)
    else:
        margins = [r["ratio"] for r in base["rows"] if r["key"] in ("cpu", "gpu") and r["ratio"]]
        if not rec and len(margins) == 2 and min(margins) >= 1.5:
            out.update(tier="great", headline="Well above the minimum specs",
                       detail="Only minimum specs are published, and this PC's processor and "
                              "graphics card are at least 1.5 times as fast as the ones listed.")
        elif not rec:
            out.update(tier="ok", headline="Meets the minimum specs",
                       detail="This PC meets the minimum specs, the only ones published for "
                              "this game." + missing_note)
        elif rec["verdict"] in (CLOSE, FAIL):
            out.update(tier="ok", headline="Meets the minimum specs",
                       detail="It meets the minimum specs. You may need lower settings than "
                              "the recommended specs aim for." + missing_note,
                       issues=_issues(rec),
                       issues_title="What it would take to reach recommended",
                       limits=rec.get("limits", []))
        elif rec["verdict"] == PARTIAL:
            out.update(tier="ok", headline="Meets the minimum specs",
                       detail="It meets the minimum specs. The recommended specs could only be "
                              "partly checked (%s)." % _list(rec["unchecked"]) + missing_note,
                       issues=_issues(rec, include_unknown=True),
                       issues_title="Recommended specs")
        else:
            out.update(tier="ok", headline="Meets the minimum specs",
                       detail="It meets the minimum specs. The recommended specs are too vague "
                              "to check automatically." + missing_note)
    out["group"] = TIER_GROUP[out["tier"]]
    return out


def level_status(level):
    return level["verdict"] if level else None


def disagreements(a, b, a_name="Steam", b_name="PCGamingWiki"):
    """Plain sentences where two sources' compared levels differ materially."""
    out = []
    for lv in ("minimum", "recommended"):
        la, lb = (a or {}).get(lv), (b or {}).get(lv)
        if not la or not lb:
            continue
        ra = {r["key"]: r for r in la["rows"]}
        rb = {r["key"]: r for r in lb["rows"]}
        for key, label in (("ram", "memory"), ("vram", "video memory")):
            x, y = ra.get(key, {}).get("need_text"), rb.get(key, {}).get("need_text")
            if x and y and x != y:
                out.append("%s %s: %s asks for %s, %s for %s." % (lv.capitalize(), label, a_name, x, b_name, y))
        for key, label in (("cpu", "processor"), ("gpu", "graphics")):
            x, y = ra.get(key, {}), rb.get(key, {})
            sx, sy = x.get("target_score"), y.get("target_score")
            if sx and sy and max(sx, sy) / min(sx, sy) >= 1.3:
                out.append("%s %s: %s lists %s, %s lists %s." % (
                    lv.capitalize(), label, a_name, x.get("target"), b_name, y.get("target")))
        if la["verdict"] != lb["verdict"] and PARTIAL not in (la["verdict"], lb["verdict"]) \
                and UNKNOWN not in (la["verdict"], lb["verdict"]):
            out.append("The %s check gives a different answer with each source." % lv)
    return out
