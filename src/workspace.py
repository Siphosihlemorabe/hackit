"""Where things live. One source of truth for repo paths.

`tools/_paths.py` re-exports these so tools and library code cannot drift
apart on where `best/` is.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = ROOT / "docs"
LEVELS = ROOT / "levels"
SUBMISSIONS = ROOT / "submissions"
BEST = ROOT / "best"
LOG = ROOT / "log"
TRACES = ROOT / "traces"

BEST_KNOWN = ROOT / "best-known.json"
HISTORY = ROOT / "best-known.history.jsonl"
FORMULA = ROOT / "formula.json"


def best_plan_path(level_id: str) -> Path:
    return BEST / f"level{level_id}.json"


def submission_path(level_id: str) -> Path:
    return SUBMISSIONS / f"level{level_id}.txt"
