"""The installed-game library: background scans, saved choices and launching.

Saved in DATA_DIR/library.json:
    scan        last scan: time, per-provider status, installations found
    folders     folders the user added for scanning
    manual      exes the user added by hand
    exe         {install_id: exe} set by the user for a copy with no launcher action
    decisions   {install_id: "accept" | "ignore"} for uncertain finds
    hidden      game ids the user hid
"""
import os
import subprocess
import threading
import time
import uuid

from . import discovery, identity, store
from .discovery import generic
from .discovery.model import clean_title

DEFAULT = {"scan": None, "folders": [], "manual": [], "exe": {}, "decisions": {}, "hidden": []}
MAX_FOLDERS, MAX_MANUAL = 50, 500

# URI launches the app knows. Anything else stored in a record is refused.
_URI_PREFIXES = ("steam://rungameid/", "com.epicgames.launcher://apps/", "uplay://launch/",
                 "shell:AppsFolder\\")


class LibraryError(Exception):
    pass


def load():
    return _normalize(store.load("library", DEFAULT))


def _update(fn):
    """Load, normalise, change and save library.json in one locked step."""
    return store.update("library", DEFAULT, lambda lib: fn(_normalize(lib)))


def _normalize(lib):
    for k, v in DEFAULT.items():
        if k == "scan":
            if not isinstance(lib.get(k), dict):
                lib[k] = None
        elif not isinstance(lib.get(k), type(v)):
            lib[k] = store._copy(v)
    return lib


# ------------------------------------------------------------------ scans

class Job:
    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.ctx = discovery.ScanContext()
        self.provider = None
        self.done = 0
        self.total = len(discovery.PROVIDERS)
        self.finished = False
        self.cancelled = False
        self.error = None
        self.started = time.time()

    def status(self):
        return {"job": self.id, "running": not self.finished, "done": self.done,
                "total": self.total, "items": self.ctx.items,
                "provider": discovery.PROVIDER_NAMES.get(self.provider) if self.provider else None,
                "cancelled": self.cancelled, "error": self.error}


_jobs = {}
_jobs_lock = threading.Lock()


def start_scan(env=None, wait=False):
    with _jobs_lock:
        running = next((j for j in _jobs.values() if not j.finished), None)
        if running:
            return running.status()
        job = Job()
        _jobs.clear()
        _jobs[job.id] = job
    t = threading.Thread(target=_run, args=(job, env), daemon=True)
    t.start()
    if wait:
        t.join()
    return job.status()


def job_status(job_id):
    job = _jobs.get(job_id)
    if not job:
        raise LibraryError("No scan with that id.")
    return job.status()


def cancel_scan(job_id):
    job = _jobs.get(job_id)
    if job and not job.finished:
        job.cancelled = True
        job.ctx.cancel()
    return job.status() if job else {"running": False}


def _run(job, env):
    def progress(pid, done, total):
        job.provider, job.done, job.total = pid, done, total
    try:
        folders = load()["folders"]
        results = discovery.run_scan(env, {"folders": folders}, job.ctx, progress)
        _update(lambda lib: apply_scan(lib, results))
    except Exception as e:          # report it instead of leaving the job running
        job.error = "%s: %s" % (type(e).__name__, e)
    finally:
        job.finished = True


def apply_scan(lib, results, now=None):
    """Store a scan. A provider that failed or was cancelled keeps the
    installations it reported last time, so one bad scan does not empty the
    library. Installations missing from a provider that did finish are gone
    (uninstalled); a copy whose folder moved keeps its id with the new path."""
    prev = [i for i in ((lib.get("scan") or {}).get("installs") or []) if isinstance(i, dict)]
    providers, installs = {}, []
    for pid, res in results.items():
        info = {k: res.get(k) for k in ("name", "status", "message", "count", "seconds")}
        if res["status"] in ("ok", "not_installed"):
            installs += res["installs"]
        else:
            kept = [i for i in prev if i.get("provider") == pid]
            installs += kept
            info["kept"] = len(kept)
        providers[pid] = info
    lib["scan"] = {"time": now or time.time(), "providers": providers, "installs": installs}
    return lib


# ------------------------------------------------------------------- view

def _all_installs(lib):
    installs = list(((lib.get("scan") or {}).get("installs")) or [])
    installs += generic.manual_installs(lib["manual"])
    for inst in installs:
        exe = lib["exe"].get(inst["install_id"])
        if exe:
            inst["launch"] = {"kind": "exe", "target": exe, "cwd": os.path.dirname(exe)}
            inst["exe_set_by_you"] = True
    return installs


def view():
    lib = load()
    scan = lib.get("scan") or {}
    installs = _all_installs(lib)
    shown, review = identity.visible_installs(installs, lib["decisions"])
    games = identity.merge(shown, lib["hidden"])
    providers = []
    for pid, name, _ in discovery.PROVIDERS:
        p = (scan.get("providers") or {}).get(pid)
        providers.append(dict(p or {"status": "not_scanned", "message": "", "count": 0},
                              id=pid, name=name))
    if lib["manual"]:
        providers.append({"id": "manual", "name": "Added by you", "status": "ok",
                          "count": len(lib["manual"]), "message": ""})
    return {"scanned": bool(scan), "scanned_at": scan.get("time"),
            "providers": providers,
            "games": [g for g in games if not g["hidden"]],
            "hidden": [{"game_id": g["game_id"], "title": g["title"]} for g in games if g["hidden"]],
            "review": review, "folders": lib["folders"],
            "manual": lib["manual"], "recovered": list(store.recovered)}


def find_game(game_id):
    lib = load()
    shown, _ = identity.visible_installs(_all_installs(lib), lib["decisions"])
    return next((g for g in identity.merge(shown, lib["hidden"]) if g["game_id"] == game_id), None)


# ---------------------------------------------------------------- choices

def decide(install_id, decision):
    if decision not in ("accept", "ignore", "reset"):
        raise LibraryError("decision must be accept, ignore or reset.")

    def fn(lib):
        if decision == "reset":
            lib["decisions"].pop(install_id, None)
        else:
            lib["decisions"][install_id] = decision
        return lib
    _update(fn)


def set_hidden(game_id, hidden):
    def fn(lib):
        ids = [g for g in lib["hidden"] if g != game_id]
        lib["hidden"] = ids + ([game_id] if hidden else [])
        return lib
    _update(fn)


def _abs(path):
    path = (path or "").strip().strip('"')
    if not path or not os.path.isabs(path) or "\x00" in path or len(path) > 500:
        raise LibraryError("Enter a full path, like D:\\Games.")
    return os.path.normpath(path)


def edit_folders(action, path):
    path = _abs(path)
    if action == "add" and not os.path.isdir(path):
        raise LibraryError("That folder does not exist.")

    def fn(lib):
        rest = [f for f in lib["folders"] if os.path.normcase(f) != os.path.normcase(path)]
        if action == "add":
            if len(rest) >= MAX_FOLDERS:
                raise LibraryError("You can add up to %d folders." % MAX_FOLDERS)
            rest.append(path)
        lib["folders"] = rest
        return lib
    _update(fn)


def _exe(path):
    path = _abs(path)
    if not path.lower().endswith(".exe") or not os.path.isfile(path):
        raise LibraryError("That is not an .exe file on this PC.")
    return path


def edit_manual(action, exe=None, title=None, entry_id=None):
    def fn(lib):
        if action == "add":
            path = _exe(exe)
            if len(lib["manual"]) >= MAX_MANUAL:
                raise LibraryError("You can add up to %d games by hand." % MAX_MANUAL)
            name = clean_title(title) or clean_title(os.path.splitext(os.path.basename(path))[0])
            lib["manual"].append({"id": uuid.uuid4().hex[:12], "exe": path, "title": name})
        elif action == "remove":
            lib["manual"] = [m for m in lib["manual"] if m.get("id") != entry_id]
        else:
            raise LibraryError("action must be add or remove.")
        return lib
    _update(fn)


def set_exe(install_id, exe):
    def fn(lib):
        if exe:
            lib["exe"][install_id] = _exe(exe)
        else:
            lib["exe"].pop(install_id, None)
        return lib
    _update(fn)


# ----------------------------------------------------------------- launch

def launch(install_id, opener=None, spawner=None):
    """Start one copy, only through its stored launcher action or exe."""
    lib = load()
    shown, _ = identity.visible_installs(_all_installs(lib), lib["decisions"])
    inst = next((i for i in shown if i["install_id"] == install_id), None)
    if not inst:
        raise LibraryError("That game is not in the library.")
    action = inst.get("launch") or {}
    kind, target = action.get("kind"), action.get("target") or ""
    opener = opener or getattr(os, "startfile", None)
    spawner = spawner or _spawn
    if kind == "uri" and target.startswith(_URI_PREFIXES):
        if not opener:
            raise LibraryError("Launching needs Windows.")
        opener(target)
        return {"ok": True, "how": "Asked %s to start it." % discovery.PROVIDER_NAMES.get(inst["provider"], "the launcher")}
    if kind == "exe" and target.lower().endswith(".exe") and os.path.isfile(target):
        cwd = action.get("cwd")
        spawner(target, cwd if cwd and os.path.isdir(cwd) else os.path.dirname(target))
        return {"ok": True, "how": "Started %s." % os.path.basename(target)}
    if kind == "shortcut" and target.lower().endswith(".lnk") and os.path.isfile(target):
        if not opener:
            raise LibraryError("Launching needs Windows.")
        opener(target)
        return {"ok": True, "how": "Opened its Start menu shortcut."}
    raise LibraryError("There is no known way to start this copy. Set its exe to add one.")


def _spawn(exe, cwd):
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([exe], cwd=cwd, close_fds=True, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
