"""Parsing the portal's logs.

=========================================================================
THIS IS THE FILE YOU EDIT FIRST once you have seen a real log. Both
fit.py and diff_trace.py go through it, so the format lives in exactly
one place. Paste a real log into docs/ and adjust the patterns below.
=========================================================================

Until then the defaults are a generic guess: `Name: 123`, `Name = 123`,
and a total on a line mentioning score/total.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# --- patterns to adjust on the day --------------------------------------

# A component figure printed by the portal. Group 1 = name, group 2 = value.
COMPONENT_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _-]*?)\s*[:=]\s*(-?[\d_]+(?:\.\d+)?)\s*$")

# The final score.
TOTAL_RE = re.compile(r"^\s*(?:total|final|score|total score)\s*[:=]\s*(-?[\d_]+(?:\.\d+)?)\s*$",
                      re.IGNORECASE)

# Which level the log belongs to.
LEVEL_RE = re.compile(r"level\s*[:=#]?\s*(\d+)", re.IGNORECASE)

# A per-step line, if the portal prints one. Group 1 = step index.
STEP_RE = re.compile(r"^\s*step\s*[:=#]?\s*(\d+)\b", re.IGNORECASE)

# Names that are not scoring components even though they parse like one.
NOT_COMPONENTS = {"level", "seed", "total", "score", "final", "total score", "time", "runtime"}

# ------------------------------------------------------------------------


def _num(s: str) -> float:
    return float(s.replace("_", ""))


@dataclass
class Observation:
    """One scored submission as the portal reported it."""

    path: Path | None = None
    level: str | None = None
    total: float | None = None
    components: dict[str, float] = field(default_factory=dict)
    steps: list[dict[str, float]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.path.name if self.path else "<inline>"

    @property
    def usable(self) -> bool:
        return self.total is not None and bool(self.components)


def parse_text(text: str, path: Path | None = None) -> Observation:
    obs = Observation(path=path)
    current_step: dict[str, float] | None = None

    for line in text.splitlines():
        if obs.level is None:
            m = LEVEL_RE.search(line)
            if m:
                obs.level = m.group(1)

        m = TOTAL_RE.match(line)
        if m:
            obs.total = _num(m.group(1))
            continue

        m = STEP_RE.match(line)
        if m:
            current_step = {}
            obs.steps.append(current_step)
            continue

        m = COMPONENT_RE.match(line)
        if m:
            name = m.group(1).strip()
            if name.lower() in NOT_COMPONENTS:
                continue
            value = _num(m.group(2))
            if current_step is not None:
                current_step[name] = value
            else:
                obs.components[name] = value

    # If the log only prints per-step figures, the last step is the final state.
    if not obs.components and obs.steps:
        obs.components = dict(obs.steps[-1])
    return obs


def parse_file(path: Path) -> Observation:
    return parse_text(Path(path).read_text(encoding="utf-8", errors="replace"), Path(path))


def load_dir(directory: Path) -> list[Observation]:
    """Every log in a directory, sorted by name for reproducibility."""
    d = Path(directory)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in {
            ".txt", ".log", ".json", ".csv", ""
        }:
            out.append(parse_file(p))
    return out
