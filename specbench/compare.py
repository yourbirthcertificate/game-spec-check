"""Compare a machine against one analysed requirement level."""
from . import matcher

PASS, CLOSE, FAIL, UNKNOWN, WARN = "pass", "close", "fail", "unknown", "warn"

# Scores are approximate, so "within 5%" counts as meeting the bar and
# "within 20%" is flagged as borderline instead of a hard fail.
_PASS_RATIO = 0.95
_CLOSE_RATIO = 0.80

_WIN_NAMES = {5.0: "2000", 5.1: "XP", 6.0: "Vista", 7.0: "7", 8.0: "8",
              8.1: "8.1", 10.0: "10", 11.0: "11"}


def _status(ratio):
    if ratio >= _PASS_RATIO:
        return PASS
    return CLOSE if ratio >= _CLOSE_RATIO else FAIL


def _gb(v):
    if v is None:
        return "?"
    if v < 1:
        return "%d MB" % round(v * 1024)
    return ("%.1f" % v).rstrip("0").rstrip(".") + " GB"


def _part_row(key, label, req_text, cands, mine):
    row = {"key": key, "label": label, "required": req_text or "Not specified",
           "yours": mine["name"] if mine else "Not detected",
           "status": UNKNOWN, "ratio": None, "note": ""}
    if not req_text:
        row["note"] = "The listing does not name a %s." % label.lower()
        return row
    if not mine or not mine.get("score"):
        row["note"] = "Your %s is not in the score table." % label.lower()
        return row
    target = matcher.pick_requirement(cands, mine.get("vendor"))
    if not target:
        row["note"] = "Could not recognise a specific part in this requirement."
        return row
    ratio = mine["score"] / target["score"]
    row.update(status=_status(ratio), ratio=round(ratio, 2),
               target=target["name"], target_score=target["score"],
               target_kind=target["kind"],
               your_score=mine["score"],
               estimated=target["kind"] in ("generic", "estimate")
               or mine.get("kind") in ("generic", "estimate"))
    if target["kind"] == "generic":
        row["note"] = "No exact model listed; compared against a typical %s." \
            % target["name"]
    elif target["kind"] == "estimate":
        row["note"] = "%s is not in the score table; its score is estimated." \
            % target["name"]
    return row


def compare(specs, req):
    """rows + verdict for one level (output of requirements.analyze)."""
    f = req["fields"]
    rows = []

    rows.append(_part_row("cpu", "Processor", f.get("cpu"), req["cpu"],
                          specs.get("cpu")))
    rows.append(_part_row("gpu", "Graphics", f.get("gpu"), req["gpu"],
                          specs.get("gpu")))

    # Memory
    have, need = specs.get("ram_gb"), req.get("ram_gb")
    row = {"key": "ram", "label": "Memory", "status": UNKNOWN, "ratio": None,
           "required": f.get("ram") or "Not specified",
           "yours": _gb(have) + " RAM", "note": ""}
    if have and need:
        ratio = have / need
        row.update(ratio=round(ratio, 2), need_text=_gb(need), have_text=_gb(have),
                   status=PASS if ratio >= 0.95 else
                   CLOSE if ratio >= 0.74 else FAIL)
    rows.append(row)

    # Video memory (only when the listing asks for it explicitly)
    need = req.get("vram_gb")
    have = (specs.get("gpu") or {}).get("vram_gb")
    if need and f.get("vram") and have:
        ratio = have / need
        rows.append({"key": "vram", "label": "Video memory",
                     "need_text": _gb(need), "have_text": _gb(have),
                     "required": f["vram"], "yours": _gb(have) + " VRAM",
                     "ratio": round(ratio, 2), "note": "",
                     "status": PASS if ratio >= 0.95 else CLOSE})

    # Operating system
    osinfo = specs.get("os") or {}
    need = req.get("os_version")
    row = {"key": "os", "label": "Operating system", "status": UNKNOWN,
           "ratio": None, "required": f.get("os") or "Not specified",
           "yours": osinfo.get("name") or "Unknown", "note": ""}
    if need and osinfo.get("version"):
        ok = osinfo["version"] >= need
        if ok and req.get("needs_64bit") and not osinfo.get("is_64bit", True):
            ok = False
            row["note"] = "Needs 64-bit Windows."
        row["status"] = PASS if ok else FAIL
        if not ok and not row["note"]:
            row["note"] = "Needs Windows %s or newer." % _WIN_NAMES.get(need, need)
    rows.append(row)

    # DirectX
    need, have = req.get("directx"), specs.get("directx")
    if need:
        rows.append({"key": "directx", "label": "DirectX", "ratio": None,
                     "need_text": "DirectX %d" % need,
                     "required": f.get("directx") or "DirectX %d" % need,
                     "yours": "DirectX %s" % have if have else "Unknown",
                     "note": "",
                     "status": UNKNOWN if not have else
                     PASS if have >= need else FAIL})

    # Storage: a shortfall is fixable, so it warns instead of failing.
    need = req.get("storage_gb")
    drive = specs.get("best_drive")
    if need or f.get("storage"):
        row = {"key": "storage", "label": "Storage", "status": UNKNOWN,
               "ratio": None, "required": f.get("storage") or "Not specified",
               "yours": "%s free on %s" % (_gb(drive["free_gb"]), drive["id"])
               if drive else "Unknown", "note": ""}
        if need and drive:
            row["ratio"] = round(drive["free_gb"] / need, 2)
            row["status"] = PASS if drive["free_gb"] >= need else WARN
            if row["status"] == WARN:
                row["note"] = "Free up %s to install." % _gb(need - drive["free_gb"])
        rows.append(row)

    core = [r for r in rows if r["key"] != "storage"]
    known = [r for r in core if r["status"] != UNKNOWN]
    decisive = [r for r in known if r["key"] in ("cpu", "gpu", "ram")]
    if not decisive:
        verdict = UNKNOWN
    elif any(r["status"] == FAIL for r in known):
        verdict = FAIL
    elif any(r["status"] == CLOSE for r in known):
        verdict = CLOSE
    else:
        verdict = PASS
    return {
        "rows": rows, "verdict": verdict,
        "failing": [r["label"] for r in known if r["status"] == FAIL],
        "close": [r["label"] for r in known if r["status"] == CLOSE],
        "unknown": [r["label"] for r in core if r["status"] == UNKNOWN
                    and r["key"] in ("cpu", "gpu", "ram")],
        "raw": req.get("raw", ""),
    }


def _explain(r):
    """One plain sentence about a row that is short of the requirement."""
    key = r["key"]
    if key in ("cpu", "gpu"):
        part = "processor" if key == "cpu" else "graphics card"
        article = "a" if r.get("target_kind") == "generic" else "the"
        target = "%s %s" % (article, r.get("target") or "part it asks for")
        pct = round((r.get("ratio") or 0) * 100)
        if r["status"] == CLOSE:
            return "Your %s is a little slower than %s it asks for (about %d%% as fast)." % (
                part, target, pct)
        return "Your %s is too slow: about %d%% as fast as %s it asks for." % (
            part, pct, target)
    if key == "ram":
        return "It asks for %s of memory and this PC has %s." % (
            r.get("need_text", "more"), r.get("have_text", "less"))
    if key == "vram":
        return "It asks for %s of video memory and your graphics card has %s." % (
            r.get("need_text", "more"), r.get("have_text", "less"))
    if key == "directx":
        return "It needs %s, which this PC does not provide." % r.get("need_text", "a newer DirectX")
    return r.get("note") or "It needs a newer version of Windows."


def _issues(level):
    short = [r for r in level["rows"] if r["status"] in (FAIL, CLOSE)]
    short.sort(key=lambda r: r["status"] != FAIL)
    return [{"label": r["label"], "status": r["status"], "text": _explain(r)}
            for r in short]


def summarize(levels):
    """The answer a person wants: can I run it, and if not, why not."""
    mn, rec = levels.get("minimum"), levels.get("recommended")
    out = {"issues": [], "issues_title": "", "storage_note": ""}
    base = mn or rec
    if not base:
        out.update(tier="unknown", headline="No PC specs published",
                   detail="This source has not published Windows requirements "
                          "for this game.")
        return out
    for r in base["rows"]:
        if r["key"] == "storage" and r["status"] == WARN:
            out["storage_note"] = "You will need to clear some space first. " + r["note"]
    which = "minimum" if mn else "recommended"

    if rec and rec["verdict"] == PASS and (not mn or mn["verdict"] != FAIL):
        out.update(tier="great", headline="Yes, it runs well",
                   detail="This PC meets the recommended specs, so expect "
                          "smooth play at good settings.")
    elif base["verdict"] == PASS:
        margins = [r["ratio"] for r in base["rows"]
                   if r["key"] in ("cpu", "gpu") and r["ratio"]]
        if not rec and len(margins) == 2 and min(margins) >= 1.5:
            out.update(tier="great", headline="Yes, it runs well",
                       detail="Only %s specs are published, and this PC is "
                              "comfortably beyond them." % which)
        elif not rec:
            out.update(tier="ok", headline="Yes, it runs",
                       detail="This PC meets the %s specs, the only ones "
                              "published for this game." % which)
        elif rec["verdict"] in (CLOSE, FAIL):
            out.update(tier="ok", headline="Yes, it runs",
                       detail="It meets the minimum specs, so it will play, but "
                              "you may need to turn some settings down.",
                       issues=_issues(rec),
                       issues_title="What it would take to reach recommended")
        else:
            out.update(tier="ok", headline="Yes, it runs",
                       detail="It meets the minimum specs. The recommended specs "
                              "are too vague to check automatically.")
    elif base["verdict"] == CLOSE:
        out.update(tier="close", headline="Just about",
                   detail="This PC sits right at the edge of the %s specs. It "
                          "should start, but may feel choppy." % which,
                   issues=_issues(base), issues_title="Where it falls short")
    elif base["verdict"] == FAIL:
        out.update(tier="fail", headline="Probably not",
                   detail="This PC falls short of the %s specs." % which,
                   issues=_issues(base), issues_title="Where it falls short")
    else:
        out.update(tier="unknown", headline="Not sure",
                   detail="The published specs are too vague to compare "
                          "automatically. The original wording is shown below.")
    return out
