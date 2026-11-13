"""Providers that look beyond launchers: installed programs, Start menu
shortcuts, folders the user added and exes added by hand.

Installed programs and shortcuts are only kept when their folder holds files
that ship with games (see model.game_markers), and even then they are marked
"low" confidence and shown for review before they join the library. Nothing
is run, and no drive is searched recursively: only folders the registry,
a shortcut or the user points at are listed.
"""
import os

from .env import uninstall_entries
from .launchers import result
from .model import NOT_GAME, _BAD_EXE, clean_title, game_markers, lnk_target, make_install, pick_exe

_SKIP_DIRS = ("\\windows\\", "\\windowsapps\\", "\\system32\\", "\\common files\\",
              "\\windows kits\\", "\\microsoft office\\", "\\microsoft\\edge")


def _skip_path(path):
    low = os.path.normcase(os.path.normpath(path or "")) + "\\"
    return any(s in low for s in _SKIP_DIRS)


def scan_programs(env, ctx):
    installs = []
    for e in uninstall_entries(env):
        ctx.check()
        name = e.get("DisplayName")
        if not name or e.get("SystemComponent") == 1 or e.get("ParentKeyName") or \
                e.get("ReleaseType") in ("Update", "Hotfix", "Security Update"):
            continue
        if NOT_GAME.search(name) or NOT_GAME.search(str(e.get("Publisher") or "")):
            continue
        loc = e.get("InstallLocation")
        if not loc:
            icon = str(e.get("DisplayIcon") or "").split(",")[0].strip('"')
            loc = os.path.dirname(icon) if icon.lower().endswith(".exe") else None
        if not loc or _skip_path(loc) or not os.path.isdir(loc):
            continue
        markers = game_markers(loc)
        if not markers:
            continue
        exe = pick_exe(loc, name)
        installs.append(make_install(
            "programs", name, path=loc, confidence="low", markers=markers,
            launch={"kind": "exe", "target": exe, "cwd": os.path.dirname(exe)} if exe else None,
            evidence="Installed program whose folder has game files (%s)." % ", ".join(markers[:2])))
    return result(installs)


def _start_menu_dirs(env):
    out = []
    for var in ("ProgramData", "APPDATA"):
        base = env.env(var)
        if base:
            out.append(os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs"))
    return [d for d in out if os.path.isdir(d)]


def scan_shortcuts(env, ctx, max_files=3000):
    installs, seen, count = [], set(), 0
    for top in _start_menu_dirs(env):
        top_depth = top.rstrip("\\/").count(os.sep)
        for root, dirs, files in os.walk(top, onerror=lambda e: None):
            if root.count(os.sep) - top_depth >= 3:
                dirs[:] = []
            for f in files:
                if not f.lower().endswith(".lnk"):
                    continue
                count += 1
                if count > max_files:
                    return result(installs, message="Stopped after %d shortcuts." % max_files)
                ctx.check()
                name = clean_title(f[:-4])
                if NOT_GAME.search(name):
                    continue
                lnk = os.path.join(root, f)
                target = lnk_target(lnk)
                if not target or not target.lower().endswith(".exe") or _skip_path(target) \
                        or _BAD_EXE.match(os.path.basename(target)) or not os.path.isfile(target):
                    continue
                folder = os.path.dirname(target)
                key = os.path.normcase(folder)
                if key in seen:
                    continue
                markers = game_markers(folder) or game_markers(os.path.dirname(folder))
                if not markers:
                    continue
                seen.add(key)
                installs.append(make_install(
                    "shortcuts", name, path=folder, confidence="low", markers=markers,
                    launch={"kind": "shortcut", "target": lnk},
                    evidence="Start menu shortcut to a folder with game files (%s)." % ", ".join(markers[:2])))
    return result(installs)


def scan_folders(folders, ctx):
    """Each subfolder of a folder the user added is a possible game."""
    installs, missing = [], 0
    for top in folders:
        if not os.path.isdir(top):
            missing += 1
            continue
        own = game_markers(top)
        candidates = [top] if own and pick_exe(top) else []
        if not candidates:
            try:
                candidates = [os.path.join(top, n) for n in sorted(os.listdir(top))[:300]]
            except OSError:
                missing += 1
                continue
        for folder in candidates:
            ctx.check()
            if not os.path.isdir(folder):
                continue
            name = clean_title(os.path.basename(os.path.normpath(folder)).replace("_", " "))
            exe = pick_exe(folder, name)
            if not exe or NOT_GAME.search(name):
                continue
            markers = game_markers(folder)
            installs.append(make_install(
                "folders", name, path=folder, markers=markers,
                confidence="medium" if markers else "low",
                launch={"kind": "exe", "target": exe, "cwd": os.path.dirname(exe)},
                evidence=("Folder you added, with game files (%s)." % ", ".join(markers[:2])
                          if markers else "Folder you added. No typical game files were found.")))
    msg = "%d added folder%s could not be read." % (missing, "" if missing == 1 else "s") if missing else ""
    return result(installs, message=msg)


def manual_installs(entries):
    """Games added by picking their exe. Missing exes stay listed as missing."""
    out = []
    for m in entries:
        exe = m.get("exe") or ""
        inst = make_install("manual", m.get("title") or os.path.splitext(os.path.basename(exe))[0],
                            provider_id=m.get("id"), path=os.path.dirname(exe),
                            launch={"kind": "exe", "target": exe, "cwd": os.path.dirname(exe)},
                            evidence="Added by you.")
        inst["present"] = os.path.isfile(exe)
        out.append(inst)
    return out
