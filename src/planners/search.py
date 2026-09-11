"""Local search: the part that actually earns score.

Everything here is problem-agnostic. Tomorrow you write one function --
how to mutate a plan -- and get restarts, acceptance, budget handling,
warm starts and best-tracking for free.

Two ways in, and you will want both in that order:

  1. `PlanMutationProblem` -- give it `mutate(plan, rng) -> Plan`. Three
     lines to use, re-simulates every candidate. Start here.

  2. A real `SearchProblem` -- keeps mutable state and an incrementally
     maintained score, so a candidate costs O(move) instead of O(plan).
     That is the difference between ~10^3 and ~10^5 evaluations a second,
     and evaluations a second is score.

Move to (2) when the evaluation rate is the bottleneck, which it will be.
When you do, run `tools/check_incremental.py` first: an incremental score
that drifts from the full replay makes the search climb a hill that is
not there, and it fails silently.
"""

from __future__ import annotations

import json
import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import workspace
from engine.model import Plan

NEG_INF = float("-inf")


# --------------------------------------------------------------------------
# the interface a searchable problem presents
# --------------------------------------------------------------------------


class SearchProblem(Protocol):
    """Mutable search state with a cheap score.

    Contract, all of which `tools/check_incremental.py` checks:

      - `apply(move)` then `undo(move)` returns the state *exactly* as it
        was, score included. Not approximately.
      - `score()` after a move equals a full re-simulation of `plan()`.
      - `plan()` returns a snapshot that does not alias internal state --
        the caller keeps it while you keep mutating.
    """

    def propose(self, rng: random.Random) -> Any:
        """A candidate move, or None if there is nothing legal to do."""

    def apply(self, move: Any) -> None: ...

    def undo(self, move: Any) -> None: ...

    def score(self) -> float:
        """Score of the current state. Cheap. -inf if the state is invalid."""

    def plan(self) -> Plan:
        """Snapshot the current state as a Plan."""

    def restore(self, plan: Plan) -> None:
        """Reset state to this plan. Used to jump back to the best found."""


@dataclass
class PlanMutationProblem:
    """Copy-based adapter: the slow path, but trivial to use.

    Each candidate is a whole new Plan, fully re-evaluated. Correct, and
    roughly `len(plan)` times slower than an incremental problem.
    """

    plan_: Plan
    mutate: Callable[[Plan, random.Random], Plan | None]
    evaluate: Callable[[Plan], float]
    score_: float = 0.0
    _prev: tuple[Plan, float] | None = None

    def __post_init__(self) -> None:
        self.score_ = self.evaluate(self.plan_)

    def propose(self, rng: random.Random) -> Plan | None:
        return self.mutate(self.plan_, rng)

    def apply(self, move: Plan) -> None:
        self._prev = (self.plan_, self.score_)
        self.plan_ = move
        self.score_ = self.evaluate(move)

    def undo(self, move: Plan) -> None:
        if self._prev is None:
            raise RuntimeError("undo() with nothing to undo")
        self.plan_, self.score_ = self._prev
        self._prev = None

    def score(self) -> float:
        return self.score_

    def plan(self) -> Plan:
        return self.plan_

    def restore(self, plan: Plan) -> None:
        self.plan_ = plan
        self.score_ = self.evaluate(plan)
        self._prev = None


# --------------------------------------------------------------------------
# acceptance
# --------------------------------------------------------------------------

# An acceptance rule sees `gain` (positive = better, already sign-corrected
# for the objective) and `progress` (0.0 at the start of the budget, 1.0 at
# the end) and says whether to keep the move.
Acceptance = Callable[[float, float, random.Random], bool]


def hill_climb(gain: float, progress: float, rng: random.Random) -> bool:
    """Strictly uphill. Fast, and gets stuck. Try it first anyway -- if it
    beats annealing, the landscape is smooth and you should spend your
    time on better moves rather than a better metaheuristic."""
    return gain > 0


@dataclass
class Anneal:
    """Metropolis acceptance with geometric cooling.

    `t0`/`t1` are in *score units*, which matters here: at nine-figure
    totals a temperature of 1.0 accepts nothing and behaves as a hill
    climber without telling you. Use `Anneal.relative()` unless you know
    the scale.
    """

    t0: float
    t1: float

    @classmethod
    def relative(cls, score: float, frac0: float = 0.02, frac1: float = 1e-5) -> Anneal:
        """Temperatures as a fraction of the starting score magnitude."""
        scale = abs(score) if score not in (0.0, NEG_INF) and math.isfinite(score) else 1.0
        return cls(t0=max(scale * frac0, 1e-9), t1=max(scale * frac1, 1e-12))

    def __call__(self, gain: float, progress: float, rng: random.Random) -> bool:
        if gain > 0:
            return True
        if gain == NEG_INF or not math.isfinite(gain):
            return False  # never wander into an invalid state
        t = self.t0 * (self.t1 / self.t0) ** min(max(progress, 0.0), 1.0)
        if t <= 0:
            return False
        try:
            return rng.random() < math.exp(gain / t)
        except OverflowError:
            return False


# --------------------------------------------------------------------------
# warm start
# --------------------------------------------------------------------------


def warm_start(level_id: str) -> Plan | None:
    """The plan currently in `best/`, if any.

    Searching from your current best is almost always better than
    searching from scratch -- it is the whole reason run.py keeps `best/`.
    Returns None when there is nothing yet, so a planner can fall back to
    constructing one.
    """
    path = workspace.best_plan_path(level_id)
    if not path.is_file():
        return None
    try:
        saved = json.loads(path.read_text())
        plan = Plan.from_dict(saved["plan"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if str(plan.level) != str(level_id):
        return None
    return plan


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------


@dataclass
class SearchStats:
    iterations: int = 0
    accepted: int = 0
    improved: int = 0
    restarts: int = 0
    elapsed_s: float = 0.0
    best_score: float = NEG_INF
    start_score: float = NEG_INF
    # "budget" (ran out of time), "max_iters" (hit the iteration cap) or
    # "no_moves" (propose kept returning None). Only "max_iters" means the
    # run was reproducible -- see local_search's note on determinism.
    stopped_by: str = "budget"

    @property
    def per_second(self) -> float:
        return self.iterations / self.elapsed_s if self.elapsed_s > 0 else 0.0

    def as_meta(self) -> dict[str, Any]:
        return {
            "iterations": self.iterations,
            "accepted": self.accepted,
            "improved": self.improved,
            "restarts": self.restarts,
            "evals_per_s": round(self.per_second, 1),
            "start_score": self.start_score,
            "best_score": self.best_score,
            "stopped_by": self.stopped_by,
        }


def local_search(
    ctx: Any,
    problem: SearchProblem,
    *,
    objective: str = "max",
    accept: Acceptance | None = None,
    stall_limit: int | None = None,
    max_iters: int | None = None,
    clock_every: int = 64,
) -> Plan:
    """Search until the budget runs out. Returns the best plan seen.

    `stall_limit` non-improving iterations trigger a jump back to the best
    known state; without that, annealing drifts away late in the budget
    and finishes worse than it started. None picks a size from the plan.

    On determinism: the loop stops at whichever of the budget and
    `max_iters` comes first, so `max_iters` alone does NOT make a run
    reproducible -- a slow machine hits the clock first and silently does
    fewer iterations. The run was reproducible only if it stopped at the
    iteration cap, so the reason is recorded in
    `plan.meta["search"]["stopped_by"]`. For a comparison you can trust,
    set `max_iters` *and* a budget generous enough that `stopped_by ==
    "max_iters"`; run.py flags the comparison if it was not.
    """
    rng = ctx.rng
    better = (lambda a, b: a > b) if objective == "max" else (lambda a, b: a < b)

    start_score = problem.score()
    best_plan = problem.plan()
    best_score = start_score
    current = start_score

    stats = SearchStats(start_score=start_score, best_score=best_score)
    if accept is None:
        accept = hill_climb
    if stall_limit is None:
        stall_limit = max(200, 20 * max(1, len(best_plan)))

    started = time.monotonic()
    budget = max(getattr(ctx, "budget_s", 0.0), 1e-9)
    progress = 0.0
    stall = 0
    dead_proposals = 0

    while True:
        if stats.iterations % clock_every == 0:
            elapsed = time.monotonic() - started
            progress = min(elapsed / budget, 1.0)
            if elapsed >= budget:
                stats.stopped_by = "budget"
                break
        if max_iters is not None and stats.iterations >= max_iters:
            stats.stopped_by = "max_iters"
            break
        stats.iterations += 1

        move = problem.propose(rng)
        if move is None:
            # Nothing legal to do. Give it a few tries, then stop rather
            # than spin for the rest of the budget.
            dead_proposals += 1
            if dead_proposals > 1000:
                stats.stopped_by = "no_moves"
                break
            continue
        dead_proposals = 0

        problem.apply(move)
        candidate = problem.score()
        gain = (candidate - current) if objective == "max" else (current - candidate)

        if accept(gain, progress, rng):
            current = candidate
            stats.accepted += 1
            if better(candidate, best_score):
                best_score = candidate
                best_plan = problem.plan()
                stats.improved += 1
                stall = 0
            else:
                stall += 1
        else:
            problem.undo(move)
            stall += 1

        if stall >= stall_limit:
            problem.restore(best_plan)
            current = best_score
            stats.restarts += 1
            stall = 0

    stats.elapsed_s = time.monotonic() - started
    stats.best_score = best_score

    out = Plan(level=best_plan.level, actions=list(best_plan.actions),
               meta=dict(best_plan.meta))
    out.meta["search"] = stats.as_meta()
    return out


# --------------------------------------------------------------------------
# auditing the fast path
# --------------------------------------------------------------------------


def audit_incremental(
    problem: SearchProblem,
    rng: random.Random,
    full_eval: Callable[[Plan], float],
    *,
    moves: int = 200,
    tol: float = 0.0,
) -> dict[str, Any] | None:
    """Check an incremental problem against full re-evaluation.

    Applies random moves; after each, compares `problem.score()` with a
    full replay of `problem.plan()`, and on half of them undoes the move
    and checks the state came back exactly.

    Returns None if everything agreed, else a dict describing the first
    divergence. This is `diff_trace.py` for the search fast path: an
    incremental score that drifts sends the search up a hill that does not
    exist, and nothing else will tell you.
    """
    inc = problem.score()
    ref = full_eval(problem.plan())
    if abs(inc - ref) > tol:
        return {"kind": "initial", "move_index": -1, "incremental": inc, "full": ref,
                "delta": inc - ref}

    for i in range(moves):
        before_score = problem.score()
        before_plan = problem.plan().sha256()

        move = problem.propose(rng)
        if move is None:
            return None

        problem.apply(move)
        inc = problem.score()
        ref = full_eval(problem.plan())
        if abs(inc - ref) > tol:
            return {"kind": "apply", "move_index": i, "move": repr(move)[:200],
                    "incremental": inc, "full": ref, "delta": inc - ref}

        if rng.random() < 0.5:
            problem.undo(move)
            if abs(problem.score() - before_score) > tol:
                return {"kind": "undo-score", "move_index": i, "move": repr(move)[:200],
                        "incremental": problem.score(), "full": before_score,
                        "delta": problem.score() - before_score}
            if problem.plan().sha256() != before_plan:
                return {"kind": "undo-state", "move_index": i, "move": repr(move)[:200],
                        "incremental": None, "full": None, "delta": None}
    return None
