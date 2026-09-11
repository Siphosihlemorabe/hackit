"""Exact linear algebra over the rationals. No floats, no least squares.

The whole point: a linear system either has a unique solution, or it does
not. Floating point turns "does not" into "here is something close",
which is exactly the failure we cannot afford -- a formula that is 0.3%
wrong will rank two plans in the wrong order and you will not find out
until the leaderboard does.

So: Fraction throughout, Gauss-Jordan, and three honest answers --
UNIQUE, UNDERDETERMINED, INCONSISTENT.

Provenance: the augmented matrix carries an identity block, so when rows
contradict each other we can name the observations responsible rather
than just saying "no solution".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

UNIQUE = "unique"
UNDERDETERMINED = "underdetermined"
INCONSISTENT = "inconsistent"


@dataclass
class Solution:
    status: str
    names: list[str]
    solution: dict[str, Fraction] = field(default_factory=dict)
    determined: dict[str, Fraction] = field(default_factory=dict)
    free: list[str] = field(default_factory=list)
    nullspace: list[dict[str, Fraction]] = field(default_factory=list)
    conflicts: list[dict[str, object]] = field(default_factory=list)
    rank: int = 0


def _rref(rows: list[list[Fraction]], ncols: int) -> list[list[Fraction]]:
    """Gauss-Jordan over the first `ncols` columns. Trailing columns
    (rhs, provenance block) come along for the ride."""
    rows = [r[:] for r in rows]
    pivot_row = 0
    for col in range(ncols):
        pick = None
        for r in range(pivot_row, len(rows)):
            if rows[r][col] != 0:
                pick = r
                break
        if pick is None:
            continue
        rows[pivot_row], rows[pick] = rows[pick], rows[pivot_row]
        pv = rows[pivot_row][col]
        rows[pivot_row] = [x / pv for x in rows[pivot_row]]
        for r in range(len(rows)):
            if r != pivot_row and rows[r][col] != 0:
                f = rows[r][col]
                rows[r] = [a - f * b for a, b in zip(rows[r], rows[pivot_row])]
        pivot_row += 1
        if pivot_row == len(rows):
            break
    return rows


def solve(
    names: list[str],
    observations: list[tuple[list[Fraction], Fraction]],
    labels: list[str] | None = None,
) -> Solution:
    """names: coefficient names, in column order (include a constant term
    as a column of 1s if the formula has one).
    observations: (row, rhs) pairs. labels: a name per observation, used
    to attribute contradictions.
    """
    n = len(names)
    m = len(observations)
    labels = labels or [f"obs[{i}]" for i in range(m)]

    # [ A | b | I ]
    aug = []
    for i, (row, rhs) in enumerate(observations):
        prov = [Fraction(1) if j == i else Fraction(0) for j in range(m)]
        aug.append(list(row) + [rhs] + prov)

    if not aug:
        return Solution(status=UNDERDETERMINED, names=list(names), free=list(names), rank=0)

    red = _rref(aug, n)

    # Contradictions: 0 ... 0 | nonzero
    conflicts = []
    for r in red:
        if all(x == 0 for x in r[:n]) and r[n] != 0:
            combo = {labels[j]: r[n + 1 + j] for j in range(m) if r[n + 1 + j] != 0}
            conflicts.append({"residual": r[n], "combination": combo})
    if conflicts:
        return Solution(status=INCONSISTENT, names=list(names), conflicts=conflicts)

    pivot_col: dict[int, int] = {}
    for ri, r in enumerate(red):
        for cidx in range(n):
            if r[cidx] != 0:
                if cidx not in pivot_col.values():
                    pivot_col[ri] = cidx
                break
    rank = len(pivot_col)
    pivots = set(pivot_col.values())
    free_cols = [j for j in range(n) if j not in pivots]

    if not free_cols:
        sol = {}
        for ri, cidx in pivot_col.items():
            sol[names[cidx]] = red[ri][n]
        return Solution(status=UNIQUE, names=list(names), solution=sol,
                        determined=dict(sol), rank=rank)

    # Null space basis: one vector per free column.
    basis = []
    for fc in free_cols:
        vec = [Fraction(0)] * n
        vec[fc] = Fraction(1)
        for ri, cidx in pivot_col.items():
            vec[cidx] = -red[ri][fc]
        basis.append({names[j]: vec[j] for j in range(n)})

    # A coefficient is pinned iff every null-space vector is zero there.
    particular = [Fraction(0)] * n
    for ri, cidx in pivot_col.items():
        particular[cidx] = red[ri][n]
    determined = {
        names[j]: particular[j]
        for j in range(n)
        if all(v[names[j]] == 0 for v in basis)
    }

    return Solution(
        status=UNDERDETERMINED,
        names=list(names),
        determined=determined,
        free=[names[j] for j in range(n) if names[j] not in determined],
        nullspace=basis,
        rank=rank,
    )


def most_informative_probe(
    names: list[str],
    observations: list[tuple[list[Fraction], Fraction]],
) -> list[tuple[str, int]]:
    """Which single component should the next probe isolate?

    For each unresolved name, pretend we ran a probe that moves only that
    component by 1, and count how many coefficients that would newly pin
    down. Returns (name, coefficients_gained) best first. Ties break
    alphabetically so the advice is reproducible.
    """
    base = solve(names, observations)
    if base.status != UNDERDETERMINED:
        return []
    known = len(base.determined)
    scored = []
    for j, name in enumerate(names):
        if name in base.determined:
            continue
        probe_row = [Fraction(1) if k == j else Fraction(0) for k in range(len(names))]
        hypo = solve(names, observations + [(probe_row, Fraction(0))])
        gain = (len(names) if hypo.status == UNIQUE else len(hypo.determined)) - known
        scored.append((name, gain))
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored


NICE_DENOMS = {1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 25, 32, 50, 60, 64, 100, 1000}


def is_round(value: Fraction) -> bool:
    """A recovered constant that is not round usually means the derivation
    is not finished -- a missing component, or a unit you have not spotted.
    Advisory only.
    """
    if value.denominator not in NICE_DENOMS:
        return False
    if value.denominator != 1:
        return True
    n = abs(value.numerator)
    if n == 0:
        return True
    while n % 10 == 0:  # strip trailing zeros: 1_500_000 -> 15
        n //= 10
    return n <= 1000


def show(value: Fraction) -> str:
    if value.denominator == 1:
        return f"{value.numerator:,}"
    return f"{value} ({float(value):.10g})"
