"""Providers that read a launcher's own install records.

Each provider is a function scan(env, ctx) returning
    {"status": "ok" | "not_installed" | "error", "message": str, "installs": [...]}
and calls ctx.check() between items so a scan can be cancelled. Records that
cannot be read are skipped and counted in the message; one bad manifest never
stops the rest.
"""
import glob
import gzip
import hashlib
import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET

from .env import HKCU, HKLM, uninstall_entries
from .model import NOT_GAME_STRICT, make_install

_PAIR = re.compile(r'"([^"]+)"\s+"([^"]*)"')
_MAX_FILE = 4 * 2 ** 20


def _read_text(path, limit=_MAX_FILE):
    try:
        if os.path.getsize(path) > limit:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def result(installs, status="ok", message="", skipped=0):
    if skipped:
        message = (message + " " if message else "") + \
            "%d record%s could not be read and %s skipped." % (
                skipped, "" if skipped == 1 else "s", "was" if skipped == 1 else "were")
    return {"status": status, "message": message, "installs": installs}


def not_installed(message):
    return {"status": "not_installed", "message": message, "installs": []}


def _gb(n):
    try:
        return int(n) / 2 ** 30
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ Steam

def steam_root(env):
    for hive, key, value in ((HKCU, r"Software\Valve\Steam", "SteamPath"),
                             (HKLM, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                             (HKLM, r"SOFTWARE\Valve\Steam", "InstallPath")):
        path = env.reg_values(hive, key).get(value)
        if path and os.path.isdir(path):
            return os.path.normpath(path)
    return None


def scan_steam(env, ctx):
    root = steam_root(env)
    if not root:
        return not_installed("Steam is not installed.")
    folders = {os.path.normcase(root): root}
    vdf = _read_text(os.path.join(root, "steamapps", "libraryfolders.vdf")) or ""
    for k, v in _PAIR.findall(vdf):
        if k == "path":
            p = os.path.normpath(v.replace("\\\\", "\\"))
            folders.setdefault(os.path.normcase(p), p)
    installs, skipped, seen = [], 0, set()
    for folder in folders.values():
        for manifest in glob.glob(os.path.join(folder, "steamapps", "appmanifest_*.acf")):
            ctx.check()
            info = dict(_PAIR.findall(_read_text(manifest) or ""))
            appid, name = info.get("appid"), info.get("name")
            if not appid or not name or not appid.isdigit():
                skipped += 1
                continue
            if NOT_GAME_STRICT.search(name) or appid in seen:
                continue
            seen.add(appid)
            path = os.path.join(folder, "steamapps", "common", info["installdir"]) \
                if info.get("installdir") else None
            installs.append(make_install(
                "steam", name, provider_id=appid, path=path,
                launch={"kind": "uri", "target": "steam://rungameid/%s" % appid},
                size_gb=_gb(info.get("SizeOnDisk")), evidence="Steam app manifest.",
                ids={"steam": appid}))
    return result(installs, skipped=skipped)


# ------------------------------------------------------------------- Epic

def scan_epic(env, ctx):
    data_dir = env.reg_values(HKLM, r"SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher").get("AppDataPath")
    if not data_dir:
        data_dir = os.path.join(env.env("ProgramData", r"C:\ProgramData"),
                                "Epic", "EpicGamesLauncher", "Data")
    folder = os.path.join(data_dir, "Manifests")
    if not os.path.isdir(folder):
        return not_installed("The Epic Games Launcher is not installed.")
    installs, skipped = [], 0
    for item in glob.glob(os.path.join(folder, "*.item")):
        ctx.check()
        try:
            m = json.loads(_read_text(item) or "")
            if not isinstance(m, dict):
                raise ValueError
        except ValueError:
            skipped += 1
            continue
        name, app = m.get("DisplayName"), m.get("AppName")
        if not name or not app or m.get("bIsIncompleteInstall"):
            continue
        cats = m.get("AppCategories")
        if isinstance(cats, list) and cats and "games" not in cats:
            continue
        main = m.get("MainGameAppName")
        if main and main != app:          # DLC installed into a game
            continue
        if NOT_GAME_STRICT.search(name):
            continue
        ns, cat = m.get("CatalogNamespace"), m.get("CatalogItemId")
        launch = None
        if ns and cat:
            launch = {"kind": "uri", "target": "com.epicgames.launcher://apps/%s%%3A%s%%3A%s"
                      "?action=launch&silent=true" % (ns, cat, app)}
        installs.append(make_install(
            "epic", name, provider_id=app, path=m.get("InstallLocation"), launch=launch,
            size_gb=_gb(m.get("InstallSize")), evidence="Epic Games Launcher manifest.",
            ids={"epic": app}))
    return result(installs, skipped=skipped)


# -------------------------------------------------------------------- GOG

_GOG_KEY = r"SOFTWARE\WOW6432Node\GOG.com\Games"


def scan_gog(env, ctx):
    subs = env.reg_subkeys(HKLM, _GOG_KEY)
    if not subs:
        if env.reg_exists(HKLM, r"SOFTWARE\WOW6432Node\GOG.com\GalaxyClient"):
            return result([])
        return not_installed("GOG Galaxy is not installed and no GOG games are registered.")
    installs = []
    for sub in subs:
        ctx.check()
        v = env.reg_values(HKLM, _GOG_KEY + "\\" + sub)
        name, path = v.get("gameName") or v.get("GAMENAME"), v.get("path") or v.get("PATH")
        gid = str(v.get("gameID") or sub)
        if not name or v.get("dependsOn") or NOT_GAME_STRICT.search(name):
            continue
        exe = v.get("exe") or v.get("EXE")
        launch = {"kind": "exe", "target": exe, "cwd": v.get("workingDir") or path} if exe else None
        installs.append(make_install("gog", name, provider_id=gid, path=path, launch=launch,
                                     evidence="GOG registry entry.", ids={"gog": gid}))
    return result(installs)


# ---------------------------------------------------------------- Ubisoft

_UBI = r"SOFTWARE\WOW6432Node\Ubisoft\Launcher"


def scan_ubisoft(env, ctx):
    ids = env.reg_subkeys(HKLM, _UBI + r"\Installs")
    if not ids and not env.reg_exists(HKLM, _UBI):
        return not_installed("Ubisoft Connect is not installed.")
    names = {}
    for e in uninstall_entries(env):
        m = re.match(r"Uplay Install (\d+)$", e.get("_key", ""))
        if m and e.get("DisplayName"):
            names[m.group(1)] = e["DisplayName"]
    installs = []
    for gid in ids:
        ctx.check()
        path = env.reg_values(HKLM, _UBI + r"\Installs\%s" % gid).get("InstallDir")
        if not path:
            continue
        name = names.get(gid) or os.path.basename(os.path.normpath(path))
        if NOT_GAME_STRICT.search(name):
            continue
        installs.append(make_install(
            "ubisoft", name, provider_id=gid, path=path,
            launch={"kind": "uri", "target": "uplay://launch/%s/0" % gid},
            evidence="Ubisoft Connect install record.", ids={"ubisoft": gid}))
    return result(installs)


# --------------------------------------------------------------------- EA

def scan_ea(env, ctx):
    launcher = any(env.reg_exists(HKLM, k) for k in (
        r"SOFTWARE\WOW6432Node\Electronic Arts\EA Desktop", r"SOFTWARE\Electronic Arts\EA Desktop",
        r"SOFTWARE\WOW6432Node\Origin"))
    installs = []
    for e in uninstall_entries(env):
        ctx.check()
        name, loc = e.get("DisplayName"), e.get("InstallLocation")
        if not name or not loc:
            continue
        uninstall = str(e.get("UninstallString") or "")
        has_data = os.path.isfile(os.path.join(loc, "__Installer", "installerdata.xml"))
        ea = "electronic arts" in str(e.get("Publisher") or "").lower() and \
            re.search(r"EAInstaller|EA Desktop|Origin", uninstall, re.I)
        if not (has_data or ea) or NOT_GAME_STRICT.search(name) or \
                re.search(r"^(EA app|EA Desktop|Origin)$", name, re.I):
            continue
        installs.append(make_install(
            "ea", name, provider_id=e["_key"], path=loc, confidence="high" if has_data else "medium",
            evidence="EA app install data." if has_data else "Installed-program record from EA."))
    if not installs and not launcher:
        return not_installed("The EA app is not installed.")
    return result(installs, message="EA games can be checked but not started from here.")


# -------------------------------------------------------------- Battle.net

def scan_battlenet(env, ctx):
    launcher, installs = False, []
    for e in uninstall_entries(env):
        ctx.check()
        name, uninstall = e.get("DisplayName") or "", str(e.get("UninstallString") or "")
        if name == "Battle.net":
            launcher = True
            continue
        m = re.search(r"--uid=([\w-]+)", uninstall)
        if "battle.net" not in uninstall.lower() or not m or not name:
            continue
        installs.append(make_install(
            "battlenet", name, provider_id=m.group(1), path=e.get("InstallLocation"),
            evidence="Battle.net installed-program record.", ids={"battlenet": m.group(1)}))
    if not installs and not launcher:
        return not_installed("Battle.net is not installed.")
    return result(installs, message="Battle.net games can be checked but not started from here.")


# ----------------------------------------------------------------- itch.io

def _itch_db(path):
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, timeout=2)
    try:
        rows = con.execute(
            "SELECT caves.id, games.id, games.title, games.classification, "
            "caves.install_folder_name, install_locations.path FROM caves "
            "JOIN games ON games.id = caves.game_id "
            "LEFT JOIN install_locations ON install_locations.id = caves.install_location_id"
        ).fetchall()
    finally:
        con.close()
    return rows


def scan_itch(env, ctx):
    appdata = env.env("APPDATA") or ""
    base = os.path.join(appdata, "itch")
    if not appdata or not os.path.isdir(base):
        return not_installed("The itch app is not installed.")
    installs, skipped, used_db = [], 0, False
    db = os.path.join(base, "db", "butler.db")
    if os.path.isfile(db):
        try:
            for cave, gid, title, cls, folder, loc in _itch_db(db):
                ctx.check()
                if cls not in (None, "game") or not title:
                    continue
                path = os.path.join(loc or os.path.join(base, "apps"), folder or "")
                installs.append(make_install("itch", title, provider_id=str(gid), path=path,
                                             evidence="itch app database.", ids={"itch": gid}))
            used_db = True
        except sqlite3.Error:
            installs = []
    if not used_db:
        for receipt in glob.glob(os.path.join(base, "apps", "*", ".itch", "receipt.json.gz")):
            ctx.check()
            try:
                with gzip.open(receipt, "rb") as fh:
                    data = json.loads(fh.read(_MAX_FILE).decode("utf-8", "replace"))
                game = data.get("game") or {}
            except (OSError, ValueError, AttributeError, EOFError):
                skipped += 1
                continue
            if game.get("classification") not in (None, "game") or not game.get("title"):
                continue
            path = os.path.dirname(os.path.dirname(receipt))
            installs.append(make_install("itch", game["title"], provider_id=str(game.get("id") or ""),
                                         path=path, evidence="itch install receipt.",
                                         ids={"itch": game.get("id")}))
    return result(installs, skipped=skipped,
                  message="itch.io games can be started once you set their exe.")


# --------------------------------------------------------- Xbox / MS Store

_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"


def publisher_id(publisher):
    """The 13-character publisher id in a package family name."""
    digest = hashlib.sha256(publisher.encode("utf-16-le")).digest()[:8]
    bits = "".join("{:08b}".format(b) for b in digest) + "0"
    return "".join(_CROCKFORD[int(bits[i:i + 5], 2)] for i in range(0, 65, 5))


def gaming_roots(drive):
    """Folders listed in a drive's .GamingRoot file (where the Xbox app installs)."""
    try:
        with open(os.path.join(drive, ".GamingRoot"), "rb") as fh:
            data = fh.read(65536)
    except OSError:
        data = b""
    roots = []
    if data[:4] == b"RGBX":
        for part in data[8:].decode("utf-16-le", "replace").split("\x00"):
            part = part.strip()
            if part and not part.startswith("\ufffd"):
                roots.append(os.path.join(drive, part.lstrip("\\/")))
    default = os.path.join(drive, "XboxGames")
    if not roots and os.path.isdir(default):
        roots.append(default)
    return roots


def _ms_config(path):
    text = _read_text(path, 2 ** 20)
    if not text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    ident = root.find("Identity")
    shell = root.find("ShellVisuals")
    exe = root.find("ExecutableList/Executable")
    return {"name": ident.get("Name") if ident is not None else None,
            "publisher": ident.get("Publisher") if ident is not None else None,
            "title": shell.get("DefaultDisplayName") if shell is not None else None,
            "app_id": exe.get("Id") if exe is not None else None}


def scan_xbox(env, ctx):
    roots = []
    for drive in env.drives():
        roots += gaming_roots(drive)
    if not roots:
        return not_installed("No Xbox app game folders were found.")
    installs, skipped = [], 0
    for root in roots:
        try:
            entries = sorted(os.listdir(root))[:500]
        except OSError:
            continue
        for name in entries:
            ctx.check()
            folder = os.path.join(root, name)
            cfg_path = os.path.join(folder, "Content", "MicrosoftGame.config")
            if not os.path.isfile(cfg_path):
                continue
            cfg = _ms_config(cfg_path)
            if not cfg:
                skipped += 1
                continue
            title = cfg["title"] if cfg["title"] and not cfg["title"].startswith("ms-resource") else name
            launch = None
            if cfg["name"] and cfg["publisher"] and cfg["app_id"]:
                pfn = "%s_%s" % (cfg["name"], publisher_id(cfg["publisher"]))
                launch = {"kind": "uri", "target": "shell:AppsFolder\\%s!%s" % (pfn, cfg["app_id"])}
            installs.append(make_install(
                "xbox", title, provider_id=cfg["name"] or name, path=os.path.join(folder, "Content"),
                launch=launch, evidence="Xbox app game folder (MicrosoftGame.config).",
                ids={"xbox": cfg["name"]}))
    return result(installs, skipped=skipped,
                  message="Older Microsoft Store games kept in the protected WindowsApps "
                          "folder cannot be listed.")
