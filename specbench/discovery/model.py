"""The shared installation record and the helpers every provider uses.

An installation is one copy of a game on disk, found by one provider:

    install_id    stable id: provider + provider id, or provider + folder
    provider      "steam", "epic", "gog", ... (see PROVIDERS in __init__)
    provider_id   the launcher's own id (Steam app id, Epic AppName, ...)
    title         display title as the provider gives it (untrusted text)
    edition       edition words split off the title, if any
    path          install folder (stays on this PC)
    launch        {"kind": "uri"|"exe"|"shortcut", "target", "cwd"} or None
    size_gb       size on disk when the provider records it
    confidence    "high" (launcher record), "medium" (game files in a folder
                  you added), "low" (heuristic; shown for review first)
    evidence      one sentence on how it was found
    ids           identifiers useful for finding requirements ({"steam": ..})
    present       False when the record exists but the folder is gone
"""
import hashlib
import os
import re
import struct

MAX_TITLE = 160


def install_id(provider, provider_id=None, path=None):
    if provider_id:
        basis = "%s:%s" % (provider, provider_id)
    else:
        basis = "%s:path:%s" % (provider, os.path.normcase(os.path.normpath(path or "")))
    return "i_" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


_MARKS = re.compile(r"[™®©]|\((?:tm|r|c)\)", re.I)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_title(title):
    t = _CONTROL.sub(" ", str(title or ""))
    t = _MARKS.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" -:")
    return t[:MAX_TITLE]


# Edition words that usually mean a different build with its own requirements.
_MATERIAL = re.compile(r"definitive|special|remaster|enhanced|director'?s cut|anniversary|"
                       r"legendary|redux|reforged|\bhd\b|\bvr\b|next[- ]gen|"
                       r"classic|legacy|reloaded", re.I)
_END = r"(?:edition|goty|remastered|remaster|director'?s cut)"
# After a separator ("Game - Whatever Edition") any short phrase counts.
_EDITION_SEP = re.compile(r"\s*(?:[-:–—]\s*|\(\s*)"
                          r"(?P<ed>(?:[\w'&.]+\s+){0,5}" + _END + r")\s*\)?\s*$", re.I)
# Without one ("Game Deluxe Edition") only known edition words do.
_QUAL = (r"digital|deluxe|super|gold|ultimate|premium|standard|complete|collector'?s|launch|"
         r"champions?'?|game of the year|goty|definitive|special|enhanced|anniversary|legendary|"
         r"platinum|limited|day one|bonus|classic|legacy|reloaded|next[- ]gen|hd|vr|royal|"
         r"deluxe|expanded|extended|final|director'?s|\d+(?:th|st|nd|rd)")
_EDITION_BARE = re.compile(r"\s+(?P<ed>(?:(?:" + _QUAL + r")\s+){0,4}" + _END + r")\s*$", re.I)


def split_edition(title):
    """"Witcher 3: Wild Hunt - Game of the Year Edition" ->
    ("Witcher 3: Wild Hunt", "Game of the Year Edition", material=False)."""
    t = clean_title(title)
    m = _EDITION_SEP.search(t) or _EDITION_BARE.search(t)
    if not m or m.start() == 0:
        return t, None, False
    base = t[:m.start()].strip(" -:(–—")
    if not base:
        return t, None, False
    ed = m.group("ed").strip()
    return base, ed, bool(_MATERIAL.search(ed))


def normalize(text):
    t = clean_title(text).lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def title_key(title):
    """Key two titles of the same game share. Cosmetic editions (Deluxe,
    Gold, GOTY) collapse into the base game; material ones (Remastered,
    Definitive, Special) stay separate."""
    base, ed, material = split_edition(title)
    key = normalize(base)
    if ed and material:
        key += " | " + normalize(ed)
    return key


# ------------------------------------------------------------ not-a-game

# For launcher records: things launchers install that are not games.
NOT_GAME_STRICT = re.compile(
    r"soundtrack|\bost\b|redistributable|redist\b|dedicated server|\bsdk\b|steamworks common|"
    r"\bproton\b|steam linux runtime|steamvr|artbook|art book|wallpaper engine|"
    r"\bbenchmark\b|\beditor\b$|\bmod tools?\b|\bplaytest\b$", re.I)

# For installed programs, shortcuts and folders: also launchers, runtimes,
# drivers, tools and uninstallers.
NOT_GAME = re.compile(
    NOT_GAME_STRICT.pattern + r"|runtime|directx|visual c\+\+|\.net|framework|driver|"
    r"launcher|uninstall|setup|installer|updater|\bupdate\b|helper|service|crash ?report|"
    r"anti-?cheat|battleye|overlay|vcredist|physx|nvidia|geforce experience|error report|"
    r"^steam$|social club|"
    r"amd software|radeon software|intel|epic games|epic online|ubisoft connect|uplay|"
    r"\bea app\b|ea desktop|\borigin\b|gog galaxy|battle\.net|\bitch\b|xbox|game bar|"
    r"discord|\bobs\b|afterburner|rivatuner|\bmanual\b|readme|documentation|"
    r"\bconfig(uration)?\b|\bsettings\b|\bhelp\b|website|support|register", re.I)

_BAD_EXE = re.compile(r"^(unins\d*|uninstall|setup|install|vc_?redist|dxsetup|dotnet|"
                      r"crashreport\w*|crashpad\w*|ue4prereq\w*|\w*launcher\w*|"
                      r"\w*updater\w*|\w*helper\w*|easyanticheat\w*|be(service|launcher)\w*|"
                      r"unitycrashhandler\w*|notification_helper|quicksfv|"
                      r"cefsharp\w*|\w*webhelper\w*)\.exe$", re.I)

# Files that ship with games and rarely with anything else.
_MARKER_FILES = {
    "unityplayer.dll", "gameassembly.dll", "steam_api.dll", "steam_api64.dll",
    "eossdk-win64-shipping.dll", "eossdk-win32-shipping.dll", "galaxy.dll", "galaxy64.dll",
    "binkw32.dll", "binkw64.dll", "bink2w32.dll", "bink2w64.dll", "fmod.dll", "fmod64.dll",
    "fmodex.dll", "fmodex64.dll", "fmodstudio.dll", "fmodstudio64.dll", "physxloader.dll",
    "physx3_x64.dll", "physx3_x86.dll", "nvngx_dlss.dll", "libxess.dll",
    "amd_fidelityfx_dx12.dll", "discord_game_sdk.dll",
    "renpy.exe", "data.win", "game.rgss3a", "game.rgssad", "uplay_r1_loader64.dll",
    "steam_appid.txt", "goggame.dll",
}
# Engine files strong enough to count even next to a browser engine.
_ENGINE_FILES = {"unityplayer.dll", "gameassembly.dll", "engine/binaries"}
# Electron and CEF apps (launchers, peripheral tools) ship these at the top
# level; a folder with them is an app unless it also has engine files.
_BROWSER_APP = {"chrome_100_percent.pak", "resources.pak", "cef.pak", "libcef.dll",
                "icudtl.dat", "snapshot_blob.bin", "v8_context_snapshot.bin"}
_MARKER_SUFFIX = (".pck", ".upk", ".bsa", ".ba2", ".vpk", ".forge", ".rpa", ".wad")
_BIN_DIRS = {"bin", "bin64", "binaries", "win64", "x64", "game", "engine"}


def _entries(path, limit=400):
    try:
        with os.scandir(path) as it:
            out = []
            for e in it:
                out.append(e)
                if len(out) >= limit:
                    break
            return out
    except OSError:
        return []


def game_markers(path):
    """Names of game-only files in `path` or one level down in a bin folder.

    Reads directory listings only; nothing is opened or run.
    """
    found = []
    top = _entries(path)
    dirs = []
    names = {e.name.lower() for e in top}
    if names & _BROWSER_APP and not names & _ENGINE_FILES and \
            not os.path.isdir(os.path.join(path, "Engine", "Binaries")):
        return []
    for e in top:
        low = e.name.lower()
        try:
            is_dir = e.is_dir()
        except OSError:
            continue
        if is_dir:
            if low.endswith("_data") or low in _BIN_DIRS:
                dirs.append(e.path)
            if low.endswith("_data") and os.path.exists(os.path.join(e.path, "globalgamemanagers")):
                found.append(e.name + "/globalgamemanagers")
            if low == "engine" and os.path.isdir(os.path.join(e.path, "Binaries")):
                found.append("Engine/Binaries")
        elif low in _MARKER_FILES or low.endswith(_MARKER_SUFFIX) or \
                (low.startswith("goggame-") and low.endswith(".info")):
            found.append(e.name)
    for d in dirs[:6]:
        for e in _entries(d, 200):
            low = e.name.lower()
            if low in _MARKER_FILES or low.endswith(_MARKER_SUFFIX):
                found.append(os.path.basename(d) + "/" + e.name)
    return sorted(set(found))[:8]


def pick_exe(path, hint=""):
    """Most likely game executable in a folder (or its bin folders), or None."""
    cands = []
    for root in [path] + [e.path for e in _entries(path) if e.name.lower() in _BIN_DIRS
                          and _safe_isdir(e)]:
        for e in _entries(root):
            if e.name.lower().endswith(".exe") and not _BAD_EXE.match(e.name):
                try:
                    cands.append((e.path, e.stat().st_size))
                except OSError:
                    continue
    if not cands:
        return None
    h = normalize(hint).replace(" ", "")

    def rank(c):
        name = normalize(os.path.splitext(os.path.basename(c[0]))[0]).replace(" ", "")
        return (bool(h) and (name in h or h in name), c[1])
    return max(cands, key=rank)[0]


def _safe_isdir(e):
    try:
        return e.is_dir()
    except OSError:
        return False


def make_install(provider, title, provider_id=None, path=None, launch=None, size_gb=None,
                 confidence="high", evidence="", ids=None, markers=None):
    base, ed, _ = split_edition(title)
    path = os.path.normpath(path) if path else None
    return {
        "install_id": install_id(provider, provider_id, path),
        "provider": provider, "provider_id": str(provider_id) if provider_id else None,
        "title": clean_title(title), "title_base": base, "edition": ed,
        "path": path, "launch": launch,
        "size_gb": round(size_gb, 1) if isinstance(size_gb, (int, float)) and size_gb > 0 else None,
        "confidence": confidence, "evidence": evidence,
        "ids": {k: str(v) for k, v in (ids or {}).items() if v},
        "markers": markers or [],
        "present": bool(path and os.path.isdir(path)) if path else True,
    }


# ------------------------------------------------------------ shortcuts

def lnk_target(path):
    """Local target path of a Windows .lnk shortcut, or None.

    Parses the Shell Link binary format far enough to read LinkInfo's local
    base path. The shortcut is read as data and never opened through the shell.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read(65536)
    except OSError:
        return None
    try:
        if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
            return None
        flags = struct.unpack_from("<I", data, 0x14)[0]
        pos = 0x4C
        if flags & 0x01:                       # HasLinkTargetIDList
            pos += 2 + struct.unpack_from("<H", data, pos)[0]
        if not flags & 0x02:                   # HasLinkInfo
            return None
        li = pos
        header = struct.unpack_from("<I", data, li + 4)[0]
        li_flags = struct.unpack_from("<I", data, li + 8)[0]
        if not li_flags & 0x01:                # VolumeIDAndLocalBasePath
            return None
        suffix_off = struct.unpack_from("<I", data, li + 24)[0]
        if header >= 0x24:
            off = struct.unpack_from("<I", data, li + 28)[0]
            if off:
                end = data.find(b"\x00\x00", li + off)
                while end != -1 and (end - (li + off)) % 2:
                    end = data.find(b"\x00\x00", end + 1)
                return data[li + off:end].decode("utf-16-le", "replace") or None
        off = struct.unpack_from("<I", data, li + 16)[0]
        base = data[li + off:data.index(b"\x00", li + off)].decode("mbcs" if os.name == "nt" else "latin-1", "replace")
        suffix = ""
        if suffix_off:
            suffix = data[li + suffix_off:data.index(b"\x00", li + suffix_off)].decode("latin-1", "replace")
        return (base + suffix) or None
    except (struct.error, ValueError, IndexError, LookupError):
        return None
