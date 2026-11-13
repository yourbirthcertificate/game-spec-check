"""Small JSON files for the things a person saves: profiles, wanted games,
requirement mappings, library choices and the last game scan.

Everything stays in DATA_DIR on this PC. Writes go to a temporary file that
replaces the old one, so a crash cannot leave half a file. A file that cannot
be read is renamed to "<name>.bad-<time>.json" and the app starts over with
defaults, and the rename is reported so the UI can say what happened.
"""
import json
import os
import threading
import time

from . import paths

_lock = threading.RLock()
recovered = []          # names of files that were unreadable this session


def _path(name):
    return os.path.join(paths.data_dir(), name + ".json")


def load(name, default):
    """The saved value, or `default` when there is none or it is unreadable.

    `default` also fixes the expected top-level type: a file holding a list
    when a dict is expected counts as unreadable.
    """
    path = _path(name)
    with _lock:
        try:
            with open(path, encoding="utf-8") as fh:
                value = json.load(fh)
        except FileNotFoundError:
            return _copy(default)
        except (OSError, ValueError, UnicodeDecodeError):
            value = None
        if value is not None and isinstance(value, type(default)):
            return value
        try:
            os.replace(path, "%s.bad-%d.json" % (path[:-5], int(time.time())))
        except OSError:
            pass
        if name not in recovered:
            recovered.append(name)
        return _copy(default)


def save(name, value):
    path = _path(name)
    with _lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, path)


def update(name, default, fn):
    """Load, change with fn(value) -> value, save. Returns the new value."""
    with _lock:
        value = fn(load(name, default))
        save(name, value)
        return value


def _copy(v):
    return json.loads(json.dumps(v))
