"""Where files live, both from source and inside the packaged exe."""
import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))
_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Bundled read-only files. PyInstaller unpacks them to a temp folder
# (sys._MEIPASS) that is deleted when the exe exits.
RESOURCE_DIR = getattr(sys, "_MEIPASS", _SOURCE_ROOT)
WEB_DIR = os.path.join(RESOURCE_DIR, "web")

# The lookup cache has to outlive the exe, so it goes under the user's
# local app data instead of the temp folder.
if FROZEN:
    _base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    CACHE_DIR = os.path.join(_base, "GameSpecCheck", "cache")
else:
    CACHE_DIR = os.path.join(_SOURCE_ROOT, ".cache")
