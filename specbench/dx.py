"""DirectX support of each graphics adapter.

Two separate things matter for a "DirectX 12" requirement: the DirectX
runtime, which ships with Windows (Windows 10 and 11 include DirectX 12), and
what the graphics hardware and driver support, expressed as a Direct3D
feature level. A Windows 11 PC with an old graphics chip has the DirectX 12
runtime but may still be unable to create a Direct3D 12 device.

`probe()` asks Direct3D itself, through DXGI, which feature levels each adapter
supports. It creates no window and no lasting device: D3D12CreateDevice and
D3D11CreateDevice are called with no output pointer, which only tests support.
When that is not possible (or for a hypothetical card in an upgrade check),
`estimate_from_model` gives the DirectX version the card's architecture is
known to support, and says that it is an estimate. Anything else is unknown.
"""
import re
import sys
import uuid

FL = {"9_1": 0x9100, "9_2": 0x9200, "9_3": 0x9300, "10_0": 0xA000,
      "10_1": 0xA100, "11_0": 0xB000, "11_1": 0xB100, "12_0": 0xC000,
      "12_1": 0xC100, "12_2": 0xC200}
_FL_NAME = {v: k for k, v in FL.items()}


def level_name(value):
    return _FL_NAME.get(value)


def directx_from_levels(d3d12_level, d3d11_level):
    """DirectX version a game can use, from the probed feature levels."""
    if d3d12_level and d3d12_level >= FL["11_0"]:
        return 12
    lvl = d3d11_level or 0
    if lvl >= FL["11_0"]:
        return 11
    if lvl >= FL["10_0"]:
        return 10
    if lvl >= FL["9_1"]:
        return 9
    return None


# ------------------------------------------------------------ live probe

def probe():
    """[{name, dedicated_gb, shared_gb, software, d3d12, d3d11, directx, feature_level}].

    Returns [] when Direct3D cannot be queried (not Windows, no DXGI).
    """
    if sys.platform != "win32":
        return []
    try:
        return _probe()
    except (OSError, AttributeError, ValueError):
        return []


def _probe():
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    def guid(text):
        u = uuid.UUID(text)
        g = GUID(u.fields[0], u.fields[1], u.fields[2])
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b
        return g

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class DESC1(ctypes.Structure):
        _fields_ = [("Description", ctypes.c_wchar * 128),
                    ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                    ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint),
                    ("DedicatedVideoMemory", ctypes.c_size_t),
                    ("DedicatedSystemMemory", ctypes.c_size_t),
                    ("SharedSystemMemory", ctypes.c_size_t),
                    ("AdapterLuid", LUID), ("Flags", ctypes.c_uint)]

    def method(ptr, index, *argtypes):
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
        fn = proto(vtbl[index])
        return lambda *a: fn(ptr, *a)

    def release(ptr):
        if ptr:
            vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(ptr)

    dxgi = ctypes.WinDLL("dxgi")
    try:
        d3d12 = ctypes.WinDLL("d3d12")
        create12 = d3d12.D3D12CreateDevice
        create12.restype = ctypes.c_long
        create12.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(GUID), ctypes.c_void_p]
    except (OSError, AttributeError):
        create12 = None
    d3d11 = ctypes.WinDLL("d3d11")
    create11 = d3d11.D3D11CreateDevice
    create11.restype = ctypes.c_long
    create11.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
                         ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_uint,
                         ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.c_void_p]

    iid_factory1 = guid("770aae78-f26f-4dba-a829-253c83d1b387")
    iid_device12 = guid("189819f1-1db6-4b57-be54-1821339b85f7")
    factory = ctypes.c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(iid_factory1), ctypes.byref(factory)) < 0:
        return []
    out = []
    try:
        enum = method(factory.value, 12, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        for i in range(16):
            adapter = ctypes.c_void_p()
            if enum(i, ctypes.byref(adapter)) < 0:
                break
            try:
                desc = DESC1()
                method(adapter.value, 10, ctypes.POINTER(DESC1))(ctypes.byref(desc))
                lvl12 = None
                if create12:
                    for name in ("12_2", "12_1", "12_0", "11_1", "11_0"):
                        if create12(adapter.value, FL[name], ctypes.byref(iid_device12), None) >= 0:
                            lvl12 = FL[name]
                            break
                levels = [FL[n] for n in ("11_1", "11_0", "10_1", "10_0", "9_3", "9_2", "9_1")]
                arr = (ctypes.c_int * len(levels))(*levels)
                got = ctypes.c_int(0)
                hr = create11(adapter.value, 0, None, 0, arr, len(levels), 7, None,
                              ctypes.byref(got), None)
                if hr < 0:      # runtimes without 11_1 reject the whole list
                    arr = (ctypes.c_int * (len(levels) - 1))(*levels[1:])
                    hr = create11(adapter.value, 0, None, 0, arr, len(levels) - 1, 7,
                                  None, ctypes.byref(got), None)
                lvl11 = got.value if hr >= 0 else None
                best = max(x for x in (lvl12 or 0, lvl11 or 0))
                out.append({
                    "name": desc.Description.strip(),
                    "dedicated_gb": round(desc.DedicatedVideoMemory / 2 ** 30, 1),
                    "shared_gb": round(desc.SharedSystemMemory / 2 ** 30, 1),
                    "software": bool(desc.Flags & 2),
                    "d3d12": lvl12 is not None,
                    "directx": directx_from_levels(lvl12, lvl11),
                    "feature_level": level_name(best),
                })
            finally:
                release(adapter.value)
    finally:
        release(factory.value)
    return out


# ------------------------------------------------- estimate from the model

# (pattern on the lower-case card name, DirectX version its architecture
# supports). First match wins. Cards whose support is unclear are left out on
# purpose so they come back as unknown rather than as a guess.
_RULES = [
    (r"\brtx\b|\bgtx 16\d\d|\bgtx 10\d\d|\bgt 10\d\d|\bgtx 9\d\d|\bgtx (titan )?x\b", 12),
    (r"\bgtx 7[5-9]\d|\bgtx 6[5-9]\d|\bgtx titan\b|\bmx ?\d", 12),
    (r"\bgt[sx]? [2-3]\d\d\b|\b[89]\d00 ?g", 10),
    (r"radeon.*\brx\b|radeon.*\br[579] \d{3}|radeon.*\bvega\b|radeon.*\bhd [78]\d\d\d\b|"
     r"radeon \d{3}m\b|^(amd )?radeon (integrated )?graphics$", 12),
    (r"radeon.*\bhd [56]\d\d\d\b", 11),
    (r"radeon.*\bhd [34]\d\d\d\b", 10),
    (r"\barc\b|\biris\b|\buhd\b|\bhd (graphics )?(5[0-9]{2}|6[0-9]{2}|p?530|p?630)\b|"
     r"\bhd (graphics )?(4[2-6]00|5[0-3]00|6000)\b", 12),
    (r"\bhd (graphics )?(2500|4000)\b", 11),
    (r"\bhd (graphics )?(2000|3000)\b", 10),
]


def estimate_from_model(name):
    """{directx, source: 'model'} from a card name, or None when unclear."""
    low = re.sub(r"\((r|tm)\)", "", (name or "").lower())
    low = re.sub(r"\s+", " ", low).strip()
    for pattern, version in _RULES:
        if re.search(pattern, low):
            return {"directx": version, "feature_level": None, "source": "model"}
    return None
