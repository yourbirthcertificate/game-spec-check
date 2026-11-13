"""Checks GitHub for a newer release, and replaces the exe when asked to.

The check is one GET request to GitHub's API for the latest release of this
project. It carries the generic User-Agent from net.py and nothing about the
PC or its games. The answer is cached for a day, and failures are not retried
on their own for an hour, so opening the page many times sends few requests.

Installing only happens when the person clicks, only in the packaged exe, and
only when GitHub lists a SHA-256 digest for the exe. The download must match
that digest. A match proves the file is the one GitHub serves for the
release; the exe is unsigned, so it does not prove who built it.

Replacing a running exe on Windows: the file cannot be deleted or overwritten
while it runs, but it can be renamed. So the new exe is copied next to the old
one as "<name>.new", the running exe is renamed to "<name>.old", and the new
one takes its name. If the last step fails the old name is put back, so a
failed swap leaves the old exe working. The ".old" file is still locked until
the old copy exits, so the next start deletes it.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

from . import __version__, net, paths, sources, store
from .net import RateLimited, SourceError

REPO = "yourbirthcertificate/game-spec-check"
API_URL = "https://api.github.com/repos/%s/releases/latest" % REPO
RELEASES_URL = "https://github.com/%s/releases" % REPO
DOWNLOAD_PREFIX = "https://github.com/%s/releases/download/" % REPO
ASSET_NAME = "GameSpecCheck.exe"
MAX_ASSET_BYTES = 100 * 1024 * 1024
MAX_NOTES = 4000
CACHE_TTL = 86400               # one automatic check a day
FAILURE_PAUSE = 3600            # after a failed check, wait an hour before the next automatic one

_VERSION = re.compile(r"v?(\d{1,6})((?:\.\d{1,6}){0,3})")
_DIGEST = re.compile(r"sha256:([0-9a-f]{64})")

off_for_session = False         # set by run.py --no-update-check
restart_requested = None        # path of the new exe once one is in place
_swapped_old = None             # where the previous exe was moved to
_last_failure = None            # (time, message) of the last failed check
_install_lock = threading.Lock()


class UpdateError(Exception):
    pass


# ------------------------------------------------------------- versions

def parse_version(text):
    """"v1.2.3" -> (1, 2, 3, 0). Anything else, such as "1.2.0-rc1", -> None."""
    if not isinstance(text, str):
        return None
    m = _VERSION.fullmatch(text.strip())
    if not m:
        return None
    parts = [int(m.group(1))] + [int(p) for p in m.group(2).split(".")[1:]]
    return tuple(parts + [0] * (4 - len(parts)))


def is_newer(tag, current=__version__):
    new, cur = parse_version(tag), parse_version(current)
    return bool(new and cur and new > cur)


def version_text(tag):
    v = parse_version(tag)
    if not v:
        return None
    parts = list(v)
    while len(parts) > 3 and parts[-1] == 0:
        parts.pop()
    return ".".join(str(p) for p in parts)


# -------------------------------------------------------------- setting

def enabled():
    """Whether the page may check for a new version when it opens."""
    if off_for_session:
        return False
    return store.load("settings", {}).get("update_check", True) is not False


def set_enabled(value):
    if not isinstance(value, bool):
        raise UpdateError("update_check must be true or false.")

    def change(s):
        s["update_check"] = value
        return s
    store.update("settings", {}, change)
    return settings()


def settings():
    return {"update_check": store.load("settings", {}).get("update_check", True) is not False,
            "off_for_session": off_for_session}


# -------------------------------------------------------------- release

def _trim(data):
    """The fields of GitHub's answer that are used, as GitHub sent them.

    The cache keeps these raw and parse_release reads them on every use, so
    a fix to the parsing applies to an answer that is already cached.
    """
    if not isinstance(data, dict):
        raise SourceError("GitHub returned an unreadable answer.")
    assets = data.get("assets")
    keep = ("name", "browser_download_url", "size", "digest")
    return {
        "tag_name": data.get("tag_name"), "name": data.get("name"),
        "body": data.get("body"), "draft": data.get("draft"),
        "prerelease": data.get("prerelease"),
        "assets": [{k: a.get(k) for k in keep} for a in assets if isinstance(a, dict)]
        if isinstance(assets, list) else [],
    }


def _fetch_release():
    _, _, body = net.fetch(net.GITHUB, API_URL)
    return _trim(sources._json(body, "GitHub"))


def _text(value, limit):
    if not isinstance(value, str):
        return ""
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value).strip()
    return value[:limit].rstrip() + ("…" if len(value) > limit else "")


def parse_release(raw):
    """Checked fields of a release, or None when it cannot be used.

    Every field is treated as untrusted. The release page link is built from
    the tag rather than taken from the answer.
    """
    if not isinstance(raw, dict) or raw.get("draft") or raw.get("prerelease"):
        return None
    tag = raw.get("tag_name")
    version = version_text(tag)
    if not version:
        return None
    asset = None
    for a in raw.get("assets") or []:
        if not isinstance(a, dict) or a.get("name") != ASSET_NAME:
            continue
        url, size = a.get("browser_download_url"), a.get("size")
        if not (isinstance(url, str) and url.startswith(DOWNLOAD_PREFIX)):
            continue
        m = _DIGEST.fullmatch(a.get("digest") or "") if isinstance(a.get("digest"), str) else None
        asset = {"url": url,
                 "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
                 "sha256": m.group(1) if m else None}
        break
    return {"tag": tag, "version": version,
            "name": _text(raw.get("name"), 120) or "Game Spec Check " + version,
            "notes": _text(raw.get("body"), MAX_NOTES),
            "url": RELEASES_URL + "/tag/" + urllib.parse.quote(tag, safe=""),
            "asset": asset}


def latest(force=False):
    """The latest release from GitHub (cached for a day), or None."""
    global _last_failure
    if off_for_session:
        raise UpdateError("Update checks are turned off for this session (--no-update-check).")
    wait = net.GITHUB.remaining_pause()
    if wait > 0:
        raise RateLimited("GitHub is limiting requests right now. Try again in %s."
                          % net._wait_words(wait), wait)
    if not force and _last_failure and time.time() - _last_failure[0] < FAILURE_PAUSE:
        raise SourceError(_last_failure[1])
    try:
        raw = sources._cached("github-release:" + REPO, CACHE_TTL, _fetch_release, refresh=force)
    except SourceError as e:
        _last_failure = (time.time(), str(e))
        raise
    _last_failure = None
    return parse_release(raw)


def install_note(release):
    """Why one-click install is unavailable, or None when it is available."""
    if not paths.FROZEN:
        return "You are running from source. Run git pull to update."
    asset = release.get("asset")
    if not asset:
        return "This release has no %s file. Download it from the release page." % ASSET_NAME
    if not asset["sha256"]:
        return ("GitHub lists no checksum for this file, so the app will not install it. "
                "Download it from the release page.")
    if not asset["size"] or asset["size"] > MAX_ASSET_BYTES:
        return "GitHub lists an unexpected size for this file. Download it from the release page."
    return None


def status(force=False):
    """What the page shows. Without `force`, nothing is fetched when the check is off."""
    out = {"current": __version__, "enabled": enabled(), "off_for_session": off_for_session,
           "frozen": paths.FROZEN, "checked": False, "newer": False,
           "releases_url": RELEASES_URL + "/latest"}
    if off_for_session or not (force or out["enabled"]):
        return out
    try:
        release = latest(force)
    except (SourceError, UpdateError) as e:
        out["error"] = str(e)
        return out
    out["checked"] = True
    if not release:
        return out
    out.update(latest=release["version"], name=release["name"], notes=release["notes"],
               url=release["url"], newer=is_newer(release["tag"]))
    if out["newer"]:
        note = install_note(release)
        out["can_install"] = note is None
        out["install_note"] = note
    return out


# -------------------------------------------------------------- install

def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


def verify(data, asset):
    """Refuse a download whose digest or size differs from what GitHub lists."""
    expected = (asset or {}).get("sha256")
    if not expected:
        raise UpdateError("GitHub lists no checksum for this file, so it was not installed.")
    if asset.get("size") is not None and len(data) != asset["size"]:
        raise UpdateError("The download is not the size GitHub lists, so it was not installed.")
    if sha256_of(data) != expected:
        raise UpdateError("The download does not match the checksum GitHub lists, "
                          "so it was not installed.")


def _remove(path):
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def swap_in(current, new_file):
    """Put `new_file` in place of `current`, keeping the old one as ".old".

    Returns the path the old exe was moved to. Raises OSError and leaves
    `current` as it was if any step fails.
    """
    staged, old = current + ".new", current + ".old"
    _remove(staged)
    shutil.copyfile(new_file, staged)           # same folder, so the renames below cannot cross drives
    if not _remove(old):                        # an earlier .old that is still locked
        old = "%s.old-%d" % (current, int(time.time()))
    try:
        os.replace(current, old)                # a running exe can be renamed, not deleted
    except OSError:
        _remove(staged)
        raise
    try:
        os.replace(staged, current)
    except OSError:
        os.replace(old, current)
        _remove(staged)
        raise
    return old


def roll_back(current, old):
    """Undo swap_in: the old exe gets its name back."""
    failed = current + ".failed"
    _remove(failed)
    os.replace(current, failed)
    os.replace(old, current)


def leftovers(current):
    folder, name = os.path.split(current)
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return []
    pick = re.compile(re.escape(name) + r"\.(old|old-\d+|new|failed)", re.I)
    return [os.path.join(folder, n) for n in names if pick.fullmatch(n)]


def clean_up(current=None, tries=60, delay=1.0):
    """Delete files left by an earlier update. The old exe stays locked until
    the copy that ran it has exited, so this keeps trying for a while."""
    if current is None:
        if not paths.FROZEN:
            return True
        current = sys.executable
    for attempt in range(tries):
        left = [p for p in leftovers(current) if not _remove(p)]
        if not left:
            return True
        if attempt + 1 < tries:
            time.sleep(delay)
    return False


def _child_env():
    env = dict(os.environ)
    # Lets a onefile exe start another onefile exe without the child reusing
    # the parent's unpacked files (supported since PyInstaller 6.9).
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _runs(exe, version):
    """Start the downloaded exe with --version and check that it answers."""
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=120,
                             env=_child_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        raise UpdateError("The new version could not be started (%s), so it was not installed." % e)
    if out.returncode != 0 or version not in out.stdout:
        raise UpdateError("The new version did not start correctly, so it was not installed.")


def install():
    """Download, verify and swap in the newer exe. Only runs on a click."""
    global restart_requested, _swapped_old
    if not paths.FROZEN:
        raise UpdateError("You are running from source. Run git pull to update.")
    if not _install_lock.acquire(blocking=False):
        raise UpdateError("An update is already being installed.")
    try:
        release = latest()
        if not release or not is_newer(release["tag"]):
            raise UpdateError("There is no newer version to install.")
        note = install_note(release)
        if note:
            raise UpdateError(note)
        asset = release["asset"]
        _, _, data = net.fetch(net.GITHUB, asset["url"], timeout=60)
        verify(data, asset)

        os.makedirs(paths.UPDATE_DIR, exist_ok=True)
        download = os.path.join(paths.UPDATE_DIR, "GameSpecCheck-%s.exe" % release["version"])
        with open(download + ".part", "wb") as fh:
            fh.write(data)
        os.replace(download + ".part", download)
        try:
            _runs(download, release["version"])
            try:
                _swapped_old = swap_in(sys.executable, download)
            except OSError as e:
                raise UpdateError("The exe could not be replaced (%s). The current version still "
                                  "works. Download the new one from the release page instead." % e)
        finally:
            _remove(download)
        restart_requested = sys.executable
        return {"ok": True, "version": release["version"], "restarting": True}
    finally:
        _install_lock.release()


def start_new_copy(port):
    """Start the swapped-in exe after this server has stopped. On failure the
    old exe gets its name back. Returns True when the new copy started."""
    exe = restart_requested
    try:
        subprocess.Popen([exe, "--no-browser", "--port", str(port)], env=_child_env(),
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0), close_fds=True)
        return True
    except OSError as e:
        print("The new version could not be started: %s" % e)
        try:
            roll_back(exe, _swapped_old)
            print("The previous version was put back. Start Game Spec Check again.")
        except OSError as e2:
            print("The previous version is at %s (%s)." % (_swapped_old, e2))
        return False
