"""Find installed games across launchers, read-only and without running anything.

PROVIDERS lists every source in scan order. A scan runs them one after the
other, reports progress after each record, can be cancelled between records,
and gives each provider its own status so "not installed" and "failed" are
never confused with "no games".
"""
import threading
import time

from . import generic, launchers
from .env import Env

# (id, display name, scan function taking (env, ctx, settings))
PROVIDERS = [
    ("steam", "Steam", lambda env, ctx, s: launchers.scan_steam(env, ctx)),
    ("epic", "Epic Games", lambda env, ctx, s: launchers.scan_epic(env, ctx)),
    ("gog", "GOG", lambda env, ctx, s: launchers.scan_gog(env, ctx)),
    ("xbox", "Xbox app", lambda env, ctx, s: launchers.scan_xbox(env, ctx)),
    ("ea", "EA app", lambda env, ctx, s: launchers.scan_ea(env, ctx)),
    ("ubisoft", "Ubisoft Connect", lambda env, ctx, s: launchers.scan_ubisoft(env, ctx)),
    ("battlenet", "Battle.net", lambda env, ctx, s: launchers.scan_battlenet(env, ctx)),
    ("itch", "itch.io", lambda env, ctx, s: launchers.scan_itch(env, ctx)),
    ("programs", "Installed programs", lambda env, ctx, s: generic.scan_programs(env, ctx)),
    ("shortcuts", "Start menu", lambda env, ctx, s: generic.scan_shortcuts(env, ctx)),
    ("folders", "Your folders", lambda env, ctx, s: generic.scan_folders(s.get("folders", []), ctx)),
]
PROVIDER_NAMES = dict((p[0], p[1]) for p in PROVIDERS)
PROVIDER_NAMES["manual"] = "Added by you"


class Cancelled(Exception):
    pass


class ScanContext:
    def __init__(self):
        self._cancel = threading.Event()
        self.items = 0

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self):
        return self._cancel.is_set()

    def check(self):
        self.items += 1
        if self._cancel.is_set():
            raise Cancelled()


def run_scan(env=None, settings=None, ctx=None, on_progress=None, only=None):
    """Run every provider. Returns {provider_id: status dict with installs}."""
    env = env or Env()
    settings = settings or {}
    ctx = ctx or ScanContext()
    out = {}
    todo = [p for p in PROVIDERS if not only or p[0] in only]
    for n, (pid, name, fn) in enumerate(todo):
        if on_progress:
            on_progress(pid, n, len(todo))
        started = time.monotonic()
        if ctx.cancelled:
            out[pid] = {"status": "cancelled", "message": "Not scanned: the scan was cancelled.",
                        "installs": []}
            continue
        try:
            res = fn(env, ctx, settings)
        except Cancelled:
            res = {"status": "cancelled", "message": "Not finished: the scan was cancelled.",
                   "installs": []}
        except Exception as e:      # one broken provider must not end the scan
            res = {"status": "error", "message": "%s: %s" % (type(e).__name__, e), "installs": []}
        res["name"] = name
        res["count"] = len(res.get("installs", []))
        res["seconds"] = round(time.monotonic() - started, 2)
        out[pid] = res
    if on_progress:
        on_progress(None, len(todo), len(todo))
    return out
