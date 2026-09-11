"""Loading level files from levels/.

Format is unknown until the briefing, so this reads raw text and hands it
to parse_level(). Until the files land, it synthesises placeholder levels
so the rest of the pipeline can be exercised.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .model import Level

LEVELS_DIR = Path(__file__).resolve().parents[2] / "levels"

# Used only while levels/ is empty.
DEFAULT_LEVEL_IDS = ["1", "2", "3", "4"]


def _id_from_name(name: str) -> str:
    """level3.txt -> '3'; anything without digits keeps its stem."""
    m = re.search(r"\d+", name)
    return m.group(0) if m else Path(name).stem


def level_files() -> dict[str, Path]:
    if not LEVELS_DIR.is_dir():
        return {}
    found = {}
    for p in sorted(LEVELS_DIR.iterdir()):
        if p.name.startswith(".") or not p.is_file():
            continue
        found[_id_from_name(p.name)] = p
    return found


def level_ids() -> list[str]:
    """The levels to work on. Real files if present, else the default four."""
    found = level_files()
    if not found:
        return list(DEFAULT_LEVEL_IDS)
    return sorted(found, key=lambda s: (len(s), s))


def parse_level(raw: str) -> Any:
    """TODO(briefing): parse the level text into whatever the engine needs.

    Keep the return value picklable -- levels cross a process boundary.
    """
    return None


@lru_cache(maxsize=None)
def load_level(level_id: str) -> Level:
    path = level_files().get(level_id)
    if path is None:
        return Level(id=level_id, raw="", data=None, path=None)
    raw = path.read_text(encoding="utf-8", errors="replace")
    return Level(id=level_id, raw=raw, data=parse_level(raw), path=path)
