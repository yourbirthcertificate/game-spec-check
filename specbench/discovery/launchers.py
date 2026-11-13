"""Providers that read a launcher's own install records.

Each provider is a function scan(env, ctx) returning
    {"status": "ok" | "not_installed" | "error", "message": str, "installs": [...]}
and calls ctx.check() between items so a scan can be cancelled. Records that
cannot be read are skipped and counted in the message; one bad manifest never
stops the rest.

Launchers lose track of games: after a reinstall, a reset or a moved library
the files are still there but the records are gone. So every provider also
looks, one level deep, in that store's library folders (where its known games
live, its default folder and the usual folder names at the top of each fixed
drive) for game folders that carry the store's own install files. Those are
added as "medium" confidence with a note saying the launcher has no record of
them. Nothing is searched recursively and nothing is run.
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
from .model import NOT_GAME_STRICT, make_install, pick_exe

_PAIR = re.compile(r'"([^"]+)"\s+"([^"]*)"')
_MAX_FILE = 4 * 2 ** 20
_MAX_ENTRIES = 300


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


def _json_file(path):
    try:
        data = json.loads(_read_text(path) or "")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _key(path):
    return os.path.normcase(os.path.normpath(path))


def library_roots(env, known=(), program_files=(), drive_dirs=()):
    """Existing folders to look in: parents of known installs, folders under
    Program Files and Program Files (x86), and folders at each drive root.
    An empty name means the base folder itself."""
    roots = [os.path.dirname(os.path.normpath(p)) for p in known if p]
    for var, default in (("ProgramFiles", r"C:\Program Files"),
                         ("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        base = env.env(var, default)
        if base:
            roots += [os.path.join(base, p) if p else base for p in program_files]
    for drive in env.drives():
        roots += [os.path.join(drive, p) if p else drive for p in drive_dirs]
    out, seen = [], set()
    for r in roots:
        k = _key(r)
        if k not in seen and os.path.isdir(r):
            seen.add(k)
            out.append(os.path.normpath(r))
    return out


def unlisted(roots, test, seen, ctx, skip=()):
    """(folder, name, info) for each subfolder of `roots` that is not in
    `seen` and for which test(folder) returns something truthy."""
    for root in roots:
        try:
            names = sorted(os.listdir(root))[:_MAX_ENTRIES]
        except OSError:
            continue
        for name in names:
            ctx.check()
            folder = os.path.normpath(os.path.join(root, name))
            if _key(folder) in seen or name.lower() in skip or NOT_GAME_STRICT.search(name):
                continue
            try:
                if not os.path.isdir(folder):
                    continue
                info = test(folder)
            except OSError:
                continue
            if info:
                seen.add(_key(folder))
                yield folder, name, info


def unlisted_note(count, launcher, advice):
    if not count:
        return ""
    one = count == 1
    return ("%d game folder%s %s %s's install files, but %s has no record of %s. %s" % (
        count, "" if one else "s", "has" if one else "have", launcher, launcher,
        "it" if one else "them", advice))


def _join(*parts):
    return " ".join(p for p in parts if p)


# ------------------------------------------------------------------ Steam

def steam_root(env):
    for hive, key, value in ((HKCU, r"Software\Valve\Steam", "SteamPath"),
                             (HKLM, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                             (HKLM, r"SOFTWARE\Valve\Steam", "InstallPath")):
        path = env.reg_values(hive, key).get(value)
        if path and os.path.isdir(path):
            return os.path.normpath(path)
    return None


_STEAM_NOT_GAMES = {"steamworks shared", "steam controller configs", "steamvr"}


def _steam_folder(folder):
    """A game folder under steamapps\\common: it has a game exe. Folders left
    behind by an uninstall usually hold only saves or settings."""
    if not pick_exe(folder):
        return None
    appid = (_read_text(os.path.join(folder, "steam_appid.txt"), 64) or "").strip()
    return {"appid": appid if appid.isdigit() else None}


def _steam_game_path(library, installdir):
    """Folder of a game from its manifest's installdir. Usually a folder name
    under steamapps\\common, but older manifests store an escaped path
    relative to the library ("SteamApps\\\\common\\\\Game\\\\") or a full path."""
    d = (installdir or "").replace("\\\\", "\\").strip().rstrip("\\/")
    if not d:
        return None
    if os.path.isabs(d):
        return os.path.normpath(d)
    if re.match(r"steamapps[\\/]", d, re.I):
        return os.path.normpath(os.path.join(library, d))
    return os.path.normpath(os.path.join(library, "steamapps", "common", d))


def scan_steam(env, ctx):
    root = steam_root(env)
    folders = {}
    if root:
        folders[_key(root)] = root
        vdf = _read_text(os.path.join(root, "steamapps", "libraryfolders.vdf")) or ""
        for k, v in _PAIR.findall(vdf):
            if k == "path":
                p = os.path.normpath(v.replace("\\\\", "\\"))
                folders.setdefault(_key(p), p)
    # Usual library folders, for a Steam that lost track of them.
    for lib in library_roots(env, program_files=["Steam"], drive_dirs=["SteamLibrary", "Steam"]):
        folders.setdefault(_key(lib), lib)
    installs, skipped, seen_ids, seen = [], 0, set(), set()
    for folder in folders.values():
        for manifest in glob.glob(os.path.join(folder, "steamapps", "appmanifest_*.acf")):
            ctx.check()
            info = dict(_PAIR.findall(_read_text(manifest) or ""))
            appid, name = info.get("appid"), info.get("name")
            if not appid or not name or not appid.isdigit():
                skipped += 1
                continue
            path = _steam_game_path(folder, info.get("installdir"))
            if path:
                seen.add(_key(path))      # also tools, so they are not "found" below
            if NOT_GAME_STRICT.search(name) or appid in seen_ids:
                continue
            seen_ids.add(appid)
            installs.append(make_install(
                "steam", name, provider_id=appid, path=path,
                launch={"kind": "uri", "target": "steam://rungameid/%s" % appid},
                size_gb=_gb(info.get("SizeOnDisk")), evidence="Steam app manifest.",
                ids={"steam": appid}))
    commons = [os.path.join(f, "steamapps", "common") for f in folders.values()]
    found = 0
    for folder, name, info in unlisted([c for c in commons if os.path.isdir(c)], _steam_folder,
                                       seen, ctx, _STEAM_NOT_GAMES):
        if info["appid"] in seen_ids:
            continue
        installs.append(make_install(
            "steam", name, provider_id=info["appid"], path=folder, confidence="medium",
            evidence="Game folder in a Steam library that Steam has no record of.",
            ids={"steam": info["appid"]}))
        found += 1
    if not root and not installs:
        return not_installed("Steam is not installed.")
    return result(installs, skipped=skipped, message=unlisted_note(
        found, "Steam", "In Steam, install the game to the same library folder and Steam will "
                        "find the files instead of downloading them again."))


# ------------------------------------------------------------------- Epic

_EPIC_KEY = r"SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher"
# Folders the launcher keeps next to games in its library folder.
_EPIC_NOT_GAMES = {"launcher", "directxredist", "epic online services", "epiconlineservices"}


def _epic_uri(ns, cat, app):
    if not (ns and cat and app):
        return None
    return {"kind": "uri", "target": "com.epicgames.launcher://apps/%s%%3A%s%%3A%s"
            "?action=launch&silent=true" % (ns, cat, app)}


def _epic_folder(folder):
    store = os.path.join(folder, ".egstore")
    if not os.path.isdir(store):
        return None
    meta = next((m for m in (_json_file(p) for p in glob.glob(os.path.join(store, "*.mancpn")))
                 if m and m.get("AppName")), {})
    return {"meta": meta}


def scan_epic(env, ctx):
    """Epic games, from three places, most trusted first:

    1. the launcher's manifests (Data\\Manifests\\*.item),
    2. its install list (UnrealEngineLauncher\\LauncherInstalled.dat),
    3. game folders in an Epic library folder that carry Epic's own
       ".egstore" install folder. These can exist after the launcher lost
       its records (a reinstall or a reset); the launcher may need to verify
       them before they start, so they get no Play button of their own.
    """
    program_data = env.env("ProgramData", r"C:\ProgramData")
    data_dir = env.reg_values(HKLM, _EPIC_KEY).get("AppDataPath") or \
        os.path.join(program_data, "Epic", "EpicGamesLauncher", "Data")
    launcher = env.reg_exists(HKLM, _EPIC_KEY) or os.path.isdir(data_dir)
    installs, skipped, seen = [], 0, set()

    def add(inst):
        key = _key(inst["path"]) if inst["path"] else inst["install_id"]
        if key in seen:
            return
        seen.add(key)
        installs.append(inst)

    for item in glob.glob(os.path.join(data_dir, "Manifests", "*.item")):
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
        add(make_install(
            "epic", name, provider_id=app, path=m.get("InstallLocation"),
            launch=_epic_uri(m.get("CatalogNamespace"), m.get("CatalogItemId"), app),
            size_gb=_gb(m.get("InstallSize")), evidence="Epic Games Launcher manifest.",
            ids={"epic": app}))

    dat_path = os.path.join(program_data, "Epic", "UnrealEngineLauncher", "LauncherInstalled.dat")
    if os.path.isfile(dat_path):
        launcher = True
        dat = _json_file(dat_path)
        if dat is None:
            skipped += 1
        for e in (dat or {}).get("InstallationList") or []:
            ctx.check()
            if not isinstance(e, dict) or not e.get("InstallLocation") or not e.get("AppName"):
                continue
            path = os.path.normpath(e["InstallLocation"])
            name = os.path.basename(path)
            if _key(path) in seen or NOT_GAME_STRICT.search(name) or name.lower() in _EPIC_NOT_GAMES:
                continue
            add(make_install(
                "epic", name, provider_id=e["AppName"], path=path,
                launch=_epic_uri(e.get("NamespaceId"), e.get("ItemId"), e["AppName"]),
                evidence="Epic Games Launcher install list.", ids={"epic": e["AppName"]}))

    roots = library_roots(env, known=[i["path"] for i in installs],
                          program_files=["Epic Games"], drive_dirs=["Epic Games"])
    found = 0
    for folder, name, info in unlisted(roots, _epic_folder, seen, ctx, _EPIC_NOT_GAMES):
        meta, app = info["meta"], info["meta"].get("AppName")
        installs.append(make_install(
            "epic", name, provider_id=app, path=folder, confidence="medium",
            launch=_epic_uri(meta.get("CatalogNamespace"), meta.get("CatalogItemId"), app),
            evidence="Epic install folder (.egstore) that the Epic launcher has no record of.",
            ids={"epic": app}))
        found += 1

    if not installs and not launcher:
        return not_installed("The Epic Games Launcher is not installed.")
    message = unlisted_note(found, "the Epic Games Launcher",
                            "Open the launcher and use Install, pointing it at the existing "
                            "folder, so it verifies the files instead of downloading them again.")
    if not installs:
        message = "The Epic Games Launcher is installed but lists no installed games."
    return result(installs, message=message, skipped=skipped)


# -------------------------------------------------------------------- GOG

_GOG_KEY = r"SOFTWARE\WOW6432Node\GOG.com\Games"


def _gog_folder(folder):
    """GOG puts goggame-<id>.info (JSON) in every game folder; DLC adds more
    of them next to the base game's, so the one whose id is its own root wins."""
    infos = [i for i in (_json_file(p) for p in glob.glob(os.path.join(folder, "goggame-*.info"))) if i]
    base = [i for i in infos if str(i.get("rootGameId") or i.get("gameId")) == str(i.get("gameId"))]
    info = (base or infos or [None])[0]
    if not info or not info.get("name") or not info.get("gameId"):
        return None
    exe = None
    for task in info.get("playTasks") or []:
        if isinstance(task, dict) and task.get("type") == "FileTask" and task.get("path") and \
                (task.get("isPrimary") or exe is None):
            candidate = os.path.normpath(os.path.join(folder, task["path"]))
            if candidate.lower().endswith(".exe") and os.path.isfile(candidate) and \
                    _key(candidate).startswith(_key(folder) + os.sep):
                exe = candidate
    return {"id": str(info["gameId"]), "name": info["name"], "exe": exe}


def scan_gog(env, ctx):
    launcher = env.reg_exists(HKLM, r"SOFTWARE\WOW6432Node\GOG.com\GalaxyClient")
    installs, seen, seen_ids = [], set(), set()
    for sub in env.reg_subkeys(HKLM, _GOG_KEY):
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
        seen_ids.add(gid)
        if path:
            seen.add(_key(path))
    roots = library_roots(env, known=[i["path"] for i in installs],
                          program_files=[os.path.join("GOG Galaxy", "Games"), "GOG Games"],
                          drive_dirs=["GOG Games", os.path.join("GOG Galaxy", "Games")])
    found = 0
    for folder, _, info in unlisted(roots, _gog_folder, seen, ctx):
        if info["id"] in seen_ids:
            continue
        seen_ids.add(info["id"])
        exe = info["exe"]
        installs.append(make_install(
            "gog", info["name"], provider_id=info["id"], path=folder, confidence="medium",
            launch={"kind": "exe", "target": exe, "cwd": os.path.dirname(exe)} if exe else None,
            evidence="GOG game folder (goggame .info file) that is not registered with Windows.",
            ids={"gog": info["id"]}))
        found += 1
    if not installs:
        if launcher:
            return result([], message="GOG Galaxy is installed but lists no installed games.")
        return not_installed("GOG Galaxy is not installed and no GOG games are registered.")
    return result(installs, message=unlisted_note(
        found, "GOG", "GOG games run without the launcher, so Play still works. To manage "
                      "them in GOG Galaxy, add them with Add games, Scan and import folders."))


# ---------------------------------------------------------------- Ubisoft

_UBI = r"SOFTWARE\WOW6432Node\Ubisoft\Launcher"


_UBI_FILES = ("uplay_install.state", "uplay_r1_loader64.dll", "uplay_r1_loader.dll",
              "upc_r2_loader64.dll", "upc_r1_loader64.dll")


def _ubisoft_folder(folder):
    return any(os.path.isfile(os.path.join(folder, f)) for f in _UBI_FILES)


def scan_ubisoft(env, ctx):
    ids = env.reg_subkeys(HKLM, _UBI + r"\Installs")
    launcher = env.reg_exists(HKLM, _UBI)
    names = {}
    for e in uninstall_entries(env):
        m = re.match(r"Uplay Install (\d+)$", e.get("_key", ""))
        if m and e.get("DisplayName"):
            names[m.group(1)] = e["DisplayName"]
    installs, seen = [], set()
    for gid in ids:
        ctx.check()
        path = env.reg_values(HKLM, _UBI + r"\Installs\%s" % gid).get("InstallDir")
        if not path:
            continue
        seen.add(_key(path))
        name = names.get(gid) or os.path.basename(os.path.normpath(path))
        if NOT_GAME_STRICT.search(name):
            continue
        installs.append(make_install(
            "ubisoft", name, provider_id=gid, path=path,
            launch={"kind": "uri", "target": "uplay://launch/%s/0" % gid},
            evidence="Ubisoft Connect install record.", ids={"ubisoft": gid}))
    games_dir = os.path.join("Ubisoft", "Ubisoft Game Launcher", "games")
    roots = library_roots(env, known=[i["path"] for i in installs], program_files=[games_dir],
                          drive_dirs=["Ubisoft Games", games_dir])
    found = 0
    for folder, name, _ in unlisted(roots, _ubisoft_folder, seen, ctx):
        installs.append(make_install(
            "ubisoft", name, path=folder, confidence="medium",
            evidence="Ubisoft game folder that Ubisoft Connect has no record of."))
        found += 1
    if not installs and not launcher:
        return not_installed("Ubisoft Connect is not installed.")
    return result(installs, message=unlisted_note(
        found, "Ubisoft Connect", "In Ubisoft Connect, choose Locate installed game on the "
                                  "game's page and point it at the folder."))


# --------------------------------------------------------------------- EA

def scan_ea(env, ctx):
    launcher = any(env.reg_exists(HKLM, k) for k in (
        r"SOFTWARE\WOW6432Node\Electronic Arts\EA Desktop", r"SOFTWARE\Electronic Arts\EA Desktop",
        r"SOFTWARE\WOW6432Node\Origin"))
    installs, seen = [], set()
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
        seen.add(_key(loc))
        installs.append(make_install(
            "ea", name, provider_id=e["_key"], path=loc, confidence="high" if has_data else "medium",
            evidence="EA app install data." if has_data else "Installed-program record from EA."))
    roots = library_roots(env, known=[i["path"] for i in installs],
                          program_files=["EA Games", "Origin Games"],
                          drive_dirs=["EA Games", "Origin Games"])
    found = 0
    for folder, name, title in unlisted(roots, _ea_folder, seen, ctx):
        installs.append(make_install(
            "ea", title if isinstance(title, str) else name, path=folder, confidence="medium",
            evidence="EA game folder (EA install data) that is not registered with Windows."))
        found += 1
    if not installs and not launcher:
        return not_installed("The EA app is not installed.")
    return result(installs, message=_join(
        "EA games can be checked but not started from here.",
        unlisted_note(found, "the EA app", "In the EA app, use Locate game on the game's page.")))


def _ea_folder(folder):
    """EA's install data. Returns the English title from it, or True."""
    path = os.path.join(folder, "__Installer", "installerdata.xml")
    if not os.path.isfile(path):
        return None
    try:
        root = ET.fromstring(_read_text(path, 2 ** 20) or "")
    except ET.ParseError:
        return True
    titles = {t.get("locale", "").lower(): (t.text or "").strip() for t in root.iter("gameTitle")}
    return titles.get("en_us") or next((t for t in titles.values() if t), True)


# -------------------------------------------------------------- Battle.net

def _battlenet_folder(folder):
    # Every Battle.net game folder has both of these at its top level.
    return os.path.isfile(os.path.join(folder, ".build.info")) and \
        os.path.isfile(os.path.join(folder, ".product.db"))


def scan_battlenet(env, ctx):
    launcher, installs, seen = False, [], set()
    for e in uninstall_entries(env):
        ctx.check()
        name, uninstall = e.get("DisplayName") or "", str(e.get("UninstallString") or "")
        if name == "Battle.net":
            launcher = True
            continue
        m = re.search(r"--uid=([\w-]+)", uninstall)
        if "battle.net" not in uninstall.lower() or not m or not name:
            continue
        if e.get("InstallLocation"):
            seen.add(_key(e["InstallLocation"]))
        installs.append(make_install(
            "battlenet", name, provider_id=m.group(1), path=e.get("InstallLocation"),
            evidence="Battle.net installed-program record.", ids={"battlenet": m.group(1)}))
    # Battle.net installs each game in its own folder straight under the
    # chosen location, by default Program Files (x86).
    roots = library_roots(env, known=[i["path"] for i in installs], program_files=[""],
                          drive_dirs=["", "Games", "Battle.net Games"])
    found = 0
    for folder, name, _ in unlisted(roots, _battlenet_folder, seen, ctx, {"battle.net"}):
        installs.append(make_install(
            "battlenet", name, path=folder, confidence="medium",
            evidence="Battle.net game folder that is not registered with Windows."))
        found += 1
    if not installs and not launcher:
        return not_installed("Battle.net is not installed.")
    return result(installs, message=_join(
        "Battle.net games can be checked but not started from here.",
        unlisted_note(found, "Battle.net", "In Battle.net, choose Locate the game on the game's page.")))


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
    installs, skipped, seen, locations = [], 0, set(), [os.path.join(base, "apps")]
    db_ok = False
    db = os.path.join(base, "db", "butler.db")
    if os.path.isfile(db):
        try:
            rows = _itch_db(db)
            db_ok = True
            for cave, gid, title, cls, folder, loc in rows:
                ctx.check()
                if loc and loc not in locations:
                    locations.append(loc)
                path = os.path.join(loc or os.path.join(base, "apps"), folder or "")
                seen.add(_key(path))
                if cls not in (None, "game") or not title:
                    continue
                installs.append(make_install("itch", title, provider_id=str(gid), path=path,
                                             evidence="itch app database.", ids={"itch": gid}))
        except sqlite3.Error:
            installs, seen, db_ok = [], set(), False
    # Install receipts in every install location: all games when the
    # database cannot be read, and any game it does not list.
    for loc in locations:
        for receipt in glob.glob(os.path.join(loc, "*", ".itch", "receipt.json.gz")):
            ctx.check()
            path = os.path.dirname(os.path.dirname(receipt))
            if _key(path) in seen:
                continue
            try:
                with gzip.open(receipt, "rb") as fh:
                    data = json.loads(fh.read(_MAX_FILE).decode("utf-8", "replace"))
                game = data.get("game") or {}
            except (OSError, ValueError, AttributeError, EOFError):
                skipped += 1
                continue
            if game.get("classification") not in (None, "game") or not game.get("title"):
                continue
            seen.add(_key(path))
            installs.append(make_install(
                "itch", game["title"], provider_id=str(game.get("id") or ""), path=path,
                confidence="medium" if db_ok else "high",
                evidence="itch install receipt the itch app does not list." if db_ok
                else "itch install receipt.", ids={"itch": game.get("id")}))
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
    # Older Game Pass installs went to ModifiableWindowsApps.
    roots += library_roots(env, program_files=["ModifiableWindowsApps"])
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
            cfg_path = next((p for p in (os.path.join(folder, "Content", "MicrosoftGame.config"),
                                         os.path.join(folder, "MicrosoftGame.config"))
                             if os.path.isfile(p)), None)
            if not cfg_path:
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
                "xbox", title, provider_id=cfg["name"] or name, path=os.path.dirname(cfg_path),
                launch=launch, evidence="Xbox app game folder (MicrosoftGame.config).",
                ids={"xbox": cfg["name"]}))
    return result(installs, skipped=skipped,
                  message="Older Microsoft Store games kept in the protected WindowsApps "
                          "folder cannot be listed.")
