"""Access to the registry, environment and drives, behind one small object.

Providers read through an Env so the tests can hand them a fake registry and
temporary folders instead of a real Windows install with every launcher.
Everything here only reads.
"""
import os
import string
import sys

try:
    import winreg
except ImportError:          # not Windows
    winreg = None

HKLM, HKCU = "HKLM", "HKCU"


class Env:
    def __init__(self, registry=None, environ=None, drives=None):
        # {"HKLM\\path": {value: data}} for tests; matched case-insensitively.
        self._fake = None if registry is None else {k.lower(): v for k, v in registry.items()}
        self._fake_names = list(registry or {})
        self._environ = environ
        self._drives = drives

    # --------------------------------------------------------- environment
    def env(self, name, default=None):
        source = self._environ if self._environ is not None else os.environ
        return source.get(name, default)

    def drives(self):
        """Root folders of fixed drives, e.g. ["C:\\", "D:\\"]."""
        if self._drives is not None:
            return list(self._drives)
        if sys.platform != "win32":
            return []
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            roots = ["%s:\\" % letter for i, letter in enumerate(string.ascii_uppercase)
                     if mask >> i & 1]
            # DRIVE_FIXED == 3; skip network, optical and removable drives.
            return [r for r in roots if ctypes.windll.kernel32.GetDriveTypeW(r) == 3]
        except (OSError, AttributeError):
            return []

    # ------------------------------------------------------------ registry
    def reg_values(self, hive, path):
        """{name: data} of one key, {} if it does not exist."""
        if self._fake is not None:
            return dict(self._fake.get(_k(hive, path), {}))
        if not winreg:
            return {}
        try:
            with winreg.OpenKey(_HIVES[hive], path) as key:
                out, i = {}, 0
                while True:
                    try:
                        name, data, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    out[name] = data
                    i += 1
                return out
        except OSError:
            return {}

    def reg_subkeys(self, hive, path):
        if self._fake is not None:
            prefix = _k(hive, path) + "\\"
            names = []
            for full in self._fake_names:
                if full.lower().startswith(prefix):
                    name = full[len(prefix):].split("\\", 1)[0]
                    if name not in names:
                        names.append(name)
            return names
        if not winreg:
            return []
        try:
            with winreg.OpenKey(_HIVES[hive], path) as key:
                out, i = [], 0
                while True:
                    try:
                        out.append(winreg.EnumKey(key, i))
                    except OSError:
                        break
                    i += 1
                return out
        except OSError:
            return []

    def reg_exists(self, hive, path):
        if self._fake is not None:
            k = _k(hive, path)
            return any(full == k or full.startswith(k + "\\") for full in self._fake)
        if not winreg:
            return False
        try:
            with winreg.OpenKey(_HIVES[hive], path):
                return True
        except OSError:
            return False


def _k(hive, path):
    return (hive + "\\" + path).lower()


_HIVES = {}
if winreg:
    _HIVES = {HKLM: winreg.HKEY_LOCAL_MACHINE, HKCU: winreg.HKEY_CURRENT_USER}


UNINSTALL_KEYS = [
    (HKLM, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (HKLM, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (HKCU, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def uninstall_entries(env):
    """Installed-program records from the registry, as dicts with "_key"."""
    out = []
    for hive, path in UNINSTALL_KEYS:
        for sub in env.reg_subkeys(hive, path):
            vals = env.reg_values(hive, path + "\\" + sub)
            if vals:
                vals["_key"] = sub
                out.append(vals)
    return out
