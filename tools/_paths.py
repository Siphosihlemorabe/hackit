"""Repo paths, and putting src/ on sys.path.

Import this first in every tool: `import _paths` before any engine import.
Deliberately no package install -- `python tools/run.py` must work in a
clean checkout with nothing but the stdlib.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DOCS = ROOT / "docs"
LEVELS = ROOT / "levels"
SUBMISSIONS = ROOT / "submissions"
BEST = ROOT / "best"
LOG = ROOT / "log"
TRACES = ROOT / "traces"

BEST_KNOWN = ROOT / "best-known.json"
HISTORY = ROOT / "best-known.history.jsonl"
FORMULA = ROOT / "formula.json"
