"""Game Spec Check: compare this PC with published game requirements."""
__version__ = "1.1.1"

from . import compare, requirements, sources  # noqa: E402

SOURCE_NAMES = {"steam": "Steam", "pcgw": "PCGamingWiki", "pasted": "Pasted requirements"}


def compare_levels(raw_levels, specs, installed=False):
    """{level: raw fields} -> {level: comparison}, dropping empty levels."""
    levels = {}
    for level, data in (raw_levels or {}).items():
        if level not in ("minimum", "recommended"):
            continue
        analysis = requirements.analyze(data)
        if requirements.has_substance(analysis):
            levels[level] = compare.compare(specs, analysis, installed=installed)
    return levels


def fetch_raw(source, ident):
    return sources.steam_game(ident) if source == "steam" else sources.pcgw_game(ident)


def build_result(source, ident, raw, specs, installed=False):
    levels = compare_levels(raw.get("levels"), specs, installed)
    return {
        "source": source, "source_name": SOURCE_NAMES.get(source, source),
        "id": str(ident), "name": raw.get("name") or str(ident),
        "image": raw.get("image"), "release": raw.get("release"),
        "windows": raw.get("windows", True),
        "steam_appid": str(ident) if source == "steam" else raw.get("steam_appid"),
        "links": sources.links(source, str(ident)),
        "levels": levels, "summary": compare.summarize(levels),
        "simulated": bool(specs.get("simulated")),
    }


def check_game(source, ident, specs, installed=False):
    """Fetch one game's requirements and compare them with `specs`."""
    return build_result(source, ident, fetch_raw(source, ident), specs, installed)
