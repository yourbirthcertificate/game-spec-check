"""Write the Windows version resource (the exe's Details tab) for PyInstaller.

    python tools/make_version_info.py build/version_info.txt
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from specbench import __version__  # noqa: E402

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({a}, {b}, {c}, 0),
    prodvers=({a}, {b}, {c}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('FileDescription', 'Game Spec Check'),
        StringStruct('FileVersion', '{v}'),
        StringStruct('InternalName', 'GameSpecCheck'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 yourbirthcertificate. Noncommercial use only (PolyForm Noncommercial 1.0.0).'),
        StringStruct('OriginalFilename', 'GameSpecCheck.exe'),
        StringStruct('ProductName', 'Game Spec Check'),
        StringStruct('ProductVersion', '{v}'),
        StringStruct('Comments', 'A personal tool written with AI. See the About page for the license and credits.')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main():
    a, b, c = (int(x) for x in re.findall(r"\d+", __version__)[:3])
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join("build", "version_info.txt")
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(TEMPLATE.format(a=a, b=b, c=c, v=__version__))
    print("wrote", target)


if __name__ == "__main__":
    main()
