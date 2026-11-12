"""List the Steam games installed on this PC (read-only, local files only)."""
import glob
import os
import re

try:
    import winreg
except ImportError:          # not Windows
    winreg = None

_PAIR = re.compile(r'"([^"]+)"\s+"([^"]*)"')
# Runtimes and tools that live in the library but are not games.
_NOT_GAMES = re.compile(r"steamworks common|proton|steam linux runtime|"
                        r"steamvr|redistributable|dedicated server", re.I)


def steam_path():
    if not winreg:
        return None
    for root, key, value in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(root, key) as k:
                path = winreg.QueryValueEx(k, value)[0]
                if path and os.path.isdir(path):
                    return os.path.normpath(path)
        except OSError:
            continue
    return None


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def installed_games():
    root = steam_path()
    if not root:
        return {"found": False, "games": []}
    folders = {os.path.normcase(root): root}
    vdf = _read(os.path.join(root, "steamapps", "libraryfolders.vdf"))
    for k, v in _PAIR.findall(vdf):
        if k == "path":
            p = os.path.normpath(v.replace("\\\\", "\\"))
            folders.setdefault(os.path.normcase(p), p)
    games = {}
    for folder in folders.values():
        for manifest in glob.glob(os.path.join(folder, "steamapps", "appmanifest_*.acf")):
            info = dict(_PAIR.findall(_read(manifest)))
            appid, name = info.get("appid"), info.get("name")
            if not appid or not name or _NOT_GAMES.search(name):
                continue
            size = int(info.get("SizeOnDisk") or 0)
            games[appid] = {"appid": appid, "name": name,
                            "size_gb": round(size / 2 ** 30, 1),
                            "drive": os.path.splitdrive(folder)[0].upper()}
    return {"found": True,
            "games": sorted(games.values(), key=lambda g: g["name"].lower())}
