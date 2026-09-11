"""The recovered scoring formula: components -> predicted score.

THIS FILE APPLIES WEIGHTS. It never measures anything and never decides
anything. The coefficients live in formula.json and are written by
tools/fit.py, which solves for them exactly. Do not hand-edit them to make
a number match -- that is fitting by eye, and it will cost you the
afternoon.

Arithmetic is exact (Fraction) so that a nine-figure total is never off by
a float rounding step when you are diffing against the portal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FORMULA_PATH = ROOT / "formula.json"


class UnknownComponent(Exception):
    """The engine measured something the formula does not price.

    Loud on purpose: a silently-ignored component is a wrong score that
    looks right.
    """


@dataclass
class Formula:
    coefficients: dict[str, Fraction] = field(default_factory=dict)
    constant: Fraction = Fraction(0)
    verified: bool = False
    source: str = "placeholder"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "coefficients": {k: str(v) for k, v in sorted(self.coefficients.items())},
            "constant": str(self.constant),
            "verified": self.verified,
            "source": self.source,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Formula:
        return cls(
            coefficients={k: Fraction(str(v)) for k, v in d.get("coefficients", {}).items()},
            constant=Fraction(str(d.get("constant", 0))),
            verified=bool(d.get("verified", False)),
            source=str(d.get("source", "unknown")),
            notes=str(d.get("notes", "")),
        )


# Used only until tools/fit.py writes a real formula.json. Prices the
# placeholder simulator's components so the pipeline runs end to end.
PLACEHOLDER = Formula(
    coefficients={
        "actions": Fraction(1_000_000),
        "distinct": Fraction(10_000),
        "weight": Fraction(1),
    },
    constant=Fraction(0),
    verified=False,
    source="placeholder",
    notes="Invented. Replace by running tools/fit.py once logs exist.",
)


def load_formula(path: Path | None = None) -> Formula:
    p = Path(path) if path else FORMULA_PATH
    if not p.is_file():
        return PLACEHOLDER
    return Formula.from_dict(json.loads(p.read_text()))


def score_exact(components: dict[str, float], formula: Formula | None = None) -> Fraction:
    f = formula if formula is not None else load_formula()
    unknown = sorted(set(components) - set(f.coefficients))
    if unknown:
        raise UnknownComponent(
            f"formula has no coefficient for: {', '.join(unknown)}. "
            "Either the engine invented a component or the formula is incomplete -- "
            "run tools/fit.py, do not guess."
        )
    total = f.constant
    for name in sorted(components):  # sorted: no dict-order dependence
        total += f.coefficients[name] * Fraction(str(components[name]))
    return total


def score(components: dict[str, float], formula: Formula | None = None) -> float:
    return float(score_exact(components, formula))
