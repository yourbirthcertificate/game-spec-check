"""Game Spec Check: compare this PC with published game requirements."""
from . import compare, requirements, sources

__version__ = "1.0.1"


def check_game(source, ident, specs):
    """Fetch one game's requirements and compare them with `specs`."""
    raw = sources.steam_game(ident) if source == "steam" else sources.pcgw_game(ident)
    levels = {}
    for level, data in (raw.get("levels") or {}).items():
        analysis = requirements.analyze(data)
        if requirements.has_substance(analysis):
            levels[level] = compare.compare(specs, analysis)
    return {
        "source": source, "id": str(ident), "name": raw.get("name") or str(ident),
        "image": raw.get("image"), "release": raw.get("release"),
        "windows": raw.get("windows", True),
        "links": sources.links(source, str(ident)),
        "levels": levels, "summary": compare.summarize(levels),
    }
