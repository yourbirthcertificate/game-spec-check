"""What an upgrade changes, game by game, against the real PC.

Only definite results count. A game that cannot be fully checked with the
new parts is "incomplete", never an upgrade success.
"""

CATEGORIES = [
    ("newly_recommended", "Now meets the recommended specs"),
    ("newly_minimum", "Now meets the minimum specs"),
    ("now_met", "Meets the specs (could not be fully checked before)"),
    ("borderline", "Still borderline"),
    ("still_below", "Below the specs, as on this PC or unclear before"),
    ("worse", "Worse than this PC"),
    ("incomplete", "Could not be fully checked"),
    ("no_change", "No change"),
]


def states(result):
    lv = result.get("levels") or {}
    mn, rec = lv.get("minimum"), lv.get("recommended")
    base = mn or rec or {}
    s = result.get("summary") or {}
    return {"minimum": mn["verdict"] if mn else None,
            "recommended": rec["verdict"] if rec else None,
            "base": base.get("verdict"), "tier": s.get("tier"), "group": s.get("group"),
            "headline": s.get("headline"),
            "limits": base.get("limits") or [], "unchecked": base.get("unchecked") or []}


def diff(before, after):
    """Category for one game moving from `before` to `after` (outputs of states())."""
    met_b, met_a = before["base"] == "pass", after["base"] == "pass"
    if after["group"] == "incomplete":
        return "incomplete"
    if after["base"] == "fail":
        return "worse" if before["base"] in ("pass", "close") else "still_below"
    if after["recommended"] == "pass" and before["recommended"] in ("fail", "close"):
        return "newly_recommended"
    if met_a and before["base"] in ("fail", "close"):
        return "newly_minimum"
    if met_a and not met_b and before["base"] in ("partial", "unknown"):
        return "now_met"
    if after["base"] == "close":
        return "borderline"
    if met_b and not met_a or (before["recommended"] == "pass" and after["recommended"] != "pass"):
        return "worse"
    return "no_change"
