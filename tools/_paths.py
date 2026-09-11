"""Repo paths, and putting src/ on sys.path.

Import this first in every tool: `import _paths` before any engine import.
Deliberately no package install -- `python tools/run.py` must work in a
clean checkout with nothing but the stdlib.

The paths themselves live in src/workspace.py so that library code and
tools cannot disagree about where best/ is.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from workspace import (  # noqa: E402
    BEST,
    BEST_KNOWN,
    DOCS,
    FORMULA,
    HISTORY,
    LEVELS,
    LOG,
    SUBMISSIONS,
    TRACES,
    best_plan_path,
    submission_path,
)

__all__ = [
    "BEST",
    "BEST_KNOWN",
    "DOCS",
    "FORMULA",
    "HISTORY",
    "LEVELS",
    "LOG",
    "ROOT",
    "SRC",
    "SUBMISSIONS",
    "TRACES",
    "best_plan_path",
    "submission_path",
]
