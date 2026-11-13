"""Where files live, both from source and inside the packaged exe."""
import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))
_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Bundled read-only files. PyInstaller unpacks them to a temp folder
# (sys._MEIPASS) that is deleted when the exe exits.
RESOURCE_DIR = getattr(sys, "_MEIPASS", _SOURCE_ROOT)
WEB_DIR = os.path.join(RESOURCE_DIR, "web")

# License and credits files, as {name served at /legal/<name>: path from the
# project root}. The exe carries them in a "legal" folder.
LEGAL_FILES = {
    "LICENSE": "LICENSE",
    "NOTICE": "NOTICE",
    "CREDITS.md": "CREDITS.md",
    "PSF-LICENSE.txt": os.path.join("licenses", "PSF-LICENSE.txt"),
}


def legal_file(name):
    """Full path of a bundled license file, or None for any other name."""
    rel = LEGAL_FILES.get(name)
    if not rel:
        return None
    path = os.path.join(RESOURCE_DIR, "legal", name) if FROZEN \
        else os.path.join(_SOURCE_ROOT, rel)
    return path if os.path.isfile(path) else None


# The lookup cache has to outlive the exe, so it goes under the user's
# local app data instead of the temp folder.
if FROZEN:
    _base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    CACHE_DIR = os.path.join(_base, "GameSpecCheck", "cache")
else:
    CACHE_DIR = os.path.join(_SOURCE_ROOT, ".cache")
