"""Read this PC's hardware through PowerShell/CIM and score it."""
import json
import re
import subprocess
import threading

from . import dx, matcher

# Single quotes only: the script is passed as one command-line argument.
_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1 Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed
$gpus = @(Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion,PNPDeviceID,CurrentHorizontalResolution,CurrentVerticalResolution,CurrentRefreshRate)
$reg = @(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*' | ForEach-Object { [pscustomobject]@{ Name = $_.DriverDesc; Vram = [string]$_.'HardwareInformation.qwMemorySize' } })
$os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture
$sys = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,TotalPhysicalMemory
$dimms = (Get-CimInstance Win32_PhysicalMemory | Measure-Object -Property Capacity -Sum).Sum
$disks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID,Size,FreeSpace)
[pscustomobject]@{ cpu = $cpu; gpus = $gpus; reg = $reg; os = $os; sys = $sys; dimms = [string]$dimms; disks = $disks } | ConvertTo-Json -Depth 4 -Compress
"""

# Adapters that draw nothing themselves: remote desktop, streaming, VMs.
_VIRTUAL = re.compile(
    r"basic (display|render)|virtual|remote|mirror|displaylink|parsec|citrix|"
    r"vmware|hyper-v|virtualbox|spacedesk|indirect|idd\b|dummy|sunshine|"
    r"oculus|meta |rdp|teamviewer|anydesk|splashtop", re.I)

_lock = threading.Lock()
_cache = None


def _run_powershell():
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
        capture_output=True, timeout=60, creationflags=flags)
    text = proc.stdout.decode("utf-8", "replace").strip()
    if not text:
        raise RuntimeError("PowerShell returned no hardware data: %s"
                           % proc.stderr.decode("utf-8", "replace")[:300])
    return json.loads(text[text.index("{"):])


def _as_list(v):
    return [] if v is None else v if isinstance(v, list) else [v]


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _windows(os_):
    caption = (os_.get("Caption") or "").replace("Microsoft ", "").strip()
    build = _int(os_.get("BuildNumber"))
    ver = (os_.get("Version") or "")
    if build >= 22000:
        v = 11.0
    elif ver.startswith("10."):
        v = 10.0
    elif ver.startswith("6.3"):
        v = 8.1
    elif ver.startswith("6.2"):
        v = 8.0
    elif ver.startswith("6.1"):
        v = 7.0
    else:
        v = None
    arch = os_.get("OSArchitecture") or ""
    return {"name": ("%s %s" % (caption, arch)).strip(), "version": v,
            "build": build, "is_64bit": "64" in arch or "ARM" in arch.upper()}


_INTEGRATED = re.compile(
    r"\b(uhd|iris|hd graphics|intel\(r\) graphics|intel graphics)\b|"
    r"^(amd )?radeon(\(tm\))? (integrated )?graphics$|radeon(\(tm\))? vega \d+ graphics|"
    r"radeon \d{3}m\b|radeon.*\b\d{3}m graphics", re.I)
_VRAM_IN_NAME = re.compile(r"(\d{1,2})\s*gb\b", re.I)


def is_integrated(name):
    return bool(_INTEGRATED.search((name or "").strip()))


def describe_gpu(name, vram_gb=None):
    """Score one adapter name (also used for manual 'what if' overrides).

    DirectX support here is an estimate from the model; detected adapters get
    the probed value in _build.
    """
    hit = matcher.identify_gpu(name)
    if vram_gb is None:
        m = _VRAM_IN_NAME.search(name or "")
        vram_gb = float(m.group(1)) if m else None
    return {"name": name.strip(), "vram_gb": vram_gb,
            "score": hit["score"] if hit else None,
            "vendor": hit["vendor"] if hit else None,
            "kind": hit["kind"] if hit else None,
            "matched": hit["name"] if hit else None,
            "integrated": is_integrated(name) or bool(hit and is_integrated(hit["name"])),
            "directx": dx.estimate_from_model(hit["name"] if hit else name)
            or dx.estimate_from_model(name)}


def describe_cpu(name, cores=None, threads=None, mhz=None):
    hit = matcher.identify_cpu(name, cores, mhz)
    return {"name": re.sub(r"\s+", " ", name).strip(), "cores": cores,
            "threads": threads, "mhz": mhz,
            "score": hit["score"] if hit else None,
            "vendor": hit["vendor"] if hit else None,
            "kind": hit["kind"] if hit else None,
            "matched": hit["name"] if hit else None}


def choose_gpu(adapters):
    """Pick the adapter games would actually use.

    Laptops and many desktops expose several adapters (integrated graphics,
    a discrete card, virtual displays). Each is scored on its own and the
    fastest real one wins, with video memory as the tie-breaker, so the
    order Windows reports them in never matters.
    """
    real = [a for a in adapters if not a["virtual"]]
    if not real:
        return None, "No physical graphics adapter was found."
    best = max(real, key=lambda a: (a["score"] or 0, a["vram_gb"] or 0))
    others = [a for a in real if a is not best]
    if best["score"] is None:
        reason = "None of this PC's graphics chips is in the score table, so "                  "the one with the most video memory is used."
    elif others:
        reason = "This PC has %d graphics chips. Games use the fastest one, "                  "so that is what gets checked." % len(real)
    else:
        reason = ""
    return best, reason


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\((r|tm)\)", "", (name or "").lower()))


def _apply_probe(adapters, probed):
    """Attach probed DirectX support (and DXGI memory sizes) to each adapter."""
    for a in adapters:
        hit = next((p for p in probed if not p["software"] and _norm(p["name"]) == _norm(a["name"])), None)
        if hit and hit["directx"]:
            a["directx"] = {"directx": hit["directx"], "feature_level": hit["feature_level"],
                            "source": "detected"}
        elif a.get("directx"):
            a["directx"] = dict(a["directx"])
        if hit:
            if not a.get("vram_gb") and hit["dedicated_gb"] >= 0.25:
                a["vram_gb"] = hit["dedicated_gb"]
            a["shared_gb"] = hit["shared_gb"]


def _build(data, probed=None):
    c = data.get("cpu") or {}
    cpu = describe_cpu(c.get("Name") or "Unknown CPU",
                       _int(c.get("NumberOfCores")) or None,
                       _int(c.get("NumberOfLogicalProcessors")) or None,
                       _int(c.get("MaxClockSpeed")) or None)

    reg_vram = {}
    for r in _as_list(data.get("reg")):
        if r.get("Name") and _int(r.get("Vram")):
            reg_vram[r["Name"]] = max(reg_vram.get(r["Name"], 0), _int(r["Vram"]))

    adapters, display = [], None
    for g in _as_list(data.get("gpus")):
        name = (g.get("Name") or "").strip()
        if not name:
            continue
        # AdapterRAM is a 32-bit field that tops out at 4 GB; the registry
        # value is the real figure when the driver publishes it.
        vram = reg_vram.get(name) or _int(g.get("AdapterRAM"))
        a = describe_gpu(name, round(vram / 2 ** 30, 1) if vram else None)
        a["driver"] = g.get("DriverVersion")
        a["virtual"] = bool(_VIRTUAL.search(name)) or \
            not str(g.get("PNPDeviceID") or "PCI").upper().startswith(("PCI", "ACPI", "USB"))
        adapters.append(a)
        w, h = _int(g.get("CurrentHorizontalResolution")), _int(g.get("CurrentVerticalResolution"))
        if w and h and not display:
            display = {"width": w, "height": h,
                       "hz": _int(g.get("CurrentRefreshRate")) or None}

    _apply_probe(adapters, probed or [])
    best, reason = choose_gpu(adapters)
    for a in adapters:
        a["selected"] = a is best

    sys_ = data.get("sys") or {}
    installed = _int(data.get("dimms")) or _int(sys_.get("TotalPhysicalMemory"))
    ram_gb = round(installed / 2 ** 30) if installed else None

    drives = []
    for d in _as_list(data.get("disks")):
        if _int(d.get("Size")):
            drives.append({"id": d.get("DeviceID"),
                           "size_gb": round(_int(d["Size"]) / 2 ** 30, 1),
                           "free_gb": round(_int(d.get("FreeSpace")) / 2 ** 30, 1)})
    os_ = _windows(data.get("os") or {})
    return {
        "cpu": cpu, "gpu": best, "gpu_reason": reason, "adapters": adapters,
        "ram_gb": ram_gb, "os": os_, "drives": drives,
        "best_drive": max(drives, key=lambda d: d["free_gb"]) if drives else None,
        # The runtime ships with Windows; what games can use depends on the
        # graphics hardware, which is per adapter (gpu["directx"]).
        "dx_runtime": 12 if (os_["version"] or 0) >= 10 else 11 if (os_["version"] or 0) >= 7 else None,
        "directx": ((best or {}).get("directx") or {}).get("directx"),
        "display": display,
        "system": " ".join(x for x in (sys_.get("Manufacturer"), sys_.get("Model")) if x),
        "simulated": False,
    }


def detect(refresh=False):
    global _cache
    with _lock:
        if _cache is None or refresh:
            _cache = _build(_run_powershell(), dx.probe())
        return _cache


def with_overrides(specs, cpu=None, gpu=None, ram=None, vram=None, label=None):
    """A copy of `specs` with parts swapped ('what if I upgraded...')."""
    out = dict(specs)
    changed = []
    if cpu:
        out["cpu"] = describe_cpu(cpu)
        changed.append("cpu")
    if gpu:
        known = next((a for a in specs.get("adapters", []) if a["name"] == gpu), None)
        out["gpu"] = dict(known) if known else describe_gpu(gpu)
        if not known:
            changed.append("gpu")
    if vram:
        out["gpu"] = dict(out.get("gpu") or describe_gpu("Unknown graphics card"), vram_gb=vram)
        changed.append("vram")
    if ram:
        out["ram_gb"] = ram
        changed.append("ram")
    out["directx"] = ((out.get("gpu") or {}).get("directx") or {}).get("directx")
    if changed or label:
        out["simulated"] = bool(changed) or bool(specs.get("simulated"))
        out["changed"] = sorted(set(specs.get("changed", [])) | set(changed))
    if label:
        out["label"] = label
    return out
