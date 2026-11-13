"""A compact compatibility report as an SVG image, made on this PC.

The browser sends the comparison it is showing; this turns it into an
image. All text is escaped. By default, Windows user names and anything that
looks like a file path are replaced, so a report can be shared without
saying who made it or where games are installed.
"""
import datetime
import getpass
import os
import re
from xml.sax.saxutils import escape

from . import __version__

W = 900
_COLORS = {"pass": "#1f9d6b", "close": "#c98a0b", "warn": "#c98a0b", "fail": "#d04545",
           "unknown": "#7a8594", "partial": "#7a8594"}
_WORD = {"pass": "Met", "close": "Close", "warn": "Space", "fail": "Not met",
         "unknown": "Not checked", "partial": "Partly checked"}
_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|]*|\\\\[^\s\"'<>|]+|/(?:home|users)/[^\s\"'<>|]+", re.I)


def _user_names():
    names = set()
    for get in (getpass.getuser, lambda: os.environ.get("USERNAME"),
                lambda: os.path.basename(os.path.expanduser("~"))):
        try:
            n = get()
        except Exception:
            n = None
        if n and len(n) >= 3:
            names.add(n)
    return names


def scrub(text, names=None):
    """Remove file paths and user names from one string."""
    text = _PATH.sub("[path]", str(text or ""))
    for n in (names if names is not None else _user_names()):
        text = re.sub(re.escape(n), "[user]", text, flags=re.I)
    return text


def _s(v, limit=200):
    v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(v or ""))
    return v[:limit]


def _wrap(text, width):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def build_svg(data, private=True, today=None):
    names = _user_names() if private else set()

    def t(v, limit=200):
        v = _s(v, limit)
        return escape(scrub(v, names) if private else v)

    name = t(data.get("name"), 120) or "Game"
    summary = data.get("summary") or {}
    levels = data.get("levels") or {}
    out, y = [], 0

    def text(x, yy, s, size=14, weight="normal", color="#1b2430", anchor="start"):
        out.append('<text x="%d" y="%d" font-size="%d" font-weight="%s" fill="%s" text-anchor="%s">%s</text>'
                   % (x, yy, size, weight, color, anchor, s))

    y = 44
    text(32, y, name, 24, "bold")
    y += 34
    tier = summary.get("tier") or "unknown"
    color = _COLORS.get({"great": "pass", "ok": "pass"}.get(tier, tier), "#7a8594")
    out.append('<rect x="32" y="%d" width="10" height="26" rx="3" fill="%s"/>' % (y - 19, color))
    text(52, y, t(summary.get("headline"), 120), 20, "bold", color)
    y += 26
    for line in _wrap(_s(summary.get("detail"), 600), 100):
        text(32, y, t(line), 14, color="#3a4554")
        y += 20
    profile = t(data.get("profile_label"), 80) or "This PC"
    sim = " (simulated parts, not detected hardware)" if data.get("simulated") else " (detected hardware)"
    y += 6
    text(32, y, "Checked for: %s%s" % (profile, sim), 13, color="#3a4554")
    y += 26

    # Table
    cols = [("minimum", "Minimum"), ("recommended", "Recommended")]
    cols = [(k, lbl) for k, lbl in cols if isinstance(levels.get(k), dict)]
    by_key, order = {}, []
    for k, _ in cols:
        for r in (levels[k].get("rows") or [])[:10]:
            if not isinstance(r, dict):
                continue
            key = _s(r.get("key"), 20)
            if key not in by_key:
                by_key[key] = {"label": r.get("label"), "yours": r.get("yours")}
                order.append(key)
            by_key[key][k] = r
    if cols:
        x_part, x_mine, x_lv = 32, 190, 430
        lv_w = (W - 32 - x_lv) // len(cols)
        out.append('<line x1="32" y1="%d" x2="%d" y2="%d" stroke="#d5dbe3"/>' % (y - 16, W - 32, y - 16))
        text(x_mine, y, "This PC", 12, "bold", "#5b6676")
        for n, (k, lbl) in enumerate(cols):
            v = _s(levels[k].get("verdict"), 12)
            text(x_lv + n * lv_w, y, "%s: %s" % (lbl, _WORD.get(v, "?")), 12, "bold", _COLORS.get(v, "#5b6676"))
        y += 10
        for key in order:
            row = by_key[key]
            y += 22
            out.append('<line x1="32" y1="%d" x2="%d" y2="%d" stroke="#eef1f5"/>' % (y - 16, W - 32, y - 16))
            text(x_part, y, t(row["label"], 30), 13, "bold")
            mine = _wrap(_s(row["yours"], 90), 30)[:2]
            height = len(mine)
            for i, ln in enumerate(mine):
                text(x_mine, y + i * 17, t(ln), 12, color="#3a4554")
            for n, (k, _) in enumerate(cols):
                r = row.get(k)
                if not r:
                    continue
                st = _s(r.get("status"), 12)
                req = _wrap(_s(r.get("required"), 120), (lv_w - 30) // 7)[:2]
                out.append('<circle cx="%d" cy="%d" r="5" fill="%s"/>' % (x_lv + n * lv_w + 5, y - 4, _COLORS.get(st, "#7a8594")))
                for i, ln in enumerate(req):
                    text(x_lv + n * lv_w + 16, y + i * 17, t(ln), 12)
                height = max(height, len(req))
            y += (height - 1) * 17
        y += 16

    issues = [i for i in (summary.get("issues") or []) if isinstance(i, dict)][:6]
    if issues:
        y += 8
        text(32, y, t(summary.get("issues_title") or "Notes", 60), 14, "bold")
        for i in issues:
            for n, ln in enumerate(_wrap(_s(i.get("text"), 300), 100)[:2]):
                y += 19
                text(44 if n else 32, y, ("- " if not n else "") + t(ln), 13, color="#3a4554")
        y += 8
    not_checked = [_s(x, 40) for x in (summary.get("not_checked") or [])][:8]
    lines = ["Speed scores are estimates. This does not predict frame rates or graphics settings."]
    if not_checked:
        lines.append("Mentioned in the listing but not checked: %s." % ", ".join(not_checked))
    src = _s(data.get("source_name"), 60) or "unknown source"
    attribution = _s(data.get("attribution"), 200)
    lines.append("Requirements from %s%s." % (src, (" (%s)" % attribution) if attribution else ""))
    day = (today or datetime.date.today()).isoformat()
    lines.append("Made with Game Spec Check %s on %s." % (__version__, day))
    y += 10
    for ln in lines:
        for part in _wrap(ln, 110):
            y += 18
            text(32, y, t(part, 300), 12, color="#5b6676")
    y += 28
    head = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'font-family="Segoe UI, Arial, sans-serif"><rect width="100%%" height="100%%" fill="#ffffff"/>'
            % (W, y, W, y))
    return head + "".join(out) + "</svg>"
