"""Local search over a plan. TEMPLATE -- adapt the marked parts tomorrow.

Two things here are yours to replace, and nothing else:

  `_initial`  how to build a starting plan from nothing
  `_mutate`   how to change a plan into a neighbouring plan

The rest -- acceptance, cooling, restarts, warm starting from best/,
budget handling, best-tracking -- is generic and already works.

Move quality is where the score is. A mutation that respects the
problem's structure (swap two stops on a route, move one item between
bins) beats a random perturbation by more than any amount of tuning the
temperature schedule. Spend your time on `_mutate`.
"""

from __future__ import annotations

import random

import scoring
from engine import simulate
from engine.model import Plan

from . import Context, register, register_problem
from .search import (
    NEG_INF,
    Anneal,
    PlanMutationProblem,
    hill_climb,
    local_search,
    warm_start,
)

# ==========================================================================
# problem-specific: replace both of these after the briefing
# ==========================================================================

_PLACEHOLDER_ACTION_RANGE = 1000
_PLACEHOLDER_START_LEN = 100


def _initial(ctx: Context) -> Plan:
    """A starting plan, built from nothing.

    TODO(briefing): a greedy constructive heuristic goes here. A decent
    start is worth more than a long search from a bad one -- the search
    improves what you give it, it does not rescue it.
    """
    actions = [ctx.rng.randrange(_PLACEHOLDER_ACTION_RANGE)
               for _ in range(_PLACEHOLDER_START_LEN)]
    return Plan(level=ctx.level.id, actions=actions, meta={"planner": "anneal"})


def _mutate(plan: Plan, rng: random.Random) -> Plan | None:
    """One neighbouring plan, or None if no move is available.

    TODO(briefing): replace with moves that mean something in the problem.
    The placeholder does replace / swap / insert / delete on an opaque
    action list, which is the generic fallback and rarely the best one.

    Returns a new Plan -- see PlanMutationProblem in search.py for why
    that is the slow path, and tools/check_incremental.py for how to
    check the fast one once you write it.
    """
    actions = list(plan.actions)
    kind = rng.random()

    if not actions:
        actions.append(rng.randrange(_PLACEHOLDER_ACTION_RANGE))
    elif kind < 0.45:  # replace
        actions[rng.randrange(len(actions))] = rng.randrange(_PLACEHOLDER_ACTION_RANGE)
    elif kind < 0.70 and len(actions) > 1:  # swap
        i, j = rng.randrange(len(actions)), rng.randrange(len(actions))
        actions[i], actions[j] = actions[j], actions[i]
    elif kind < 0.90:  # insert
        actions.insert(rng.randrange(len(actions) + 1),
                       rng.randrange(_PLACEHOLDER_ACTION_RANGE))
    else:  # delete
        actions.pop(rng.randrange(len(actions)))

    return Plan(level=plan.level, actions=actions, meta=dict(plan.meta))


# ==========================================================================
# generic from here down
# ==========================================================================


def _evaluate_with(level):  # noqa: ANN001
    """Full-replay evaluator: simulate, then score. -inf for an illegal
    plan, so the search treats invalidity as a wall rather than a cost."""

    def evaluate(plan: Plan) -> float:
        trace = simulate(level, plan)
        if not trace.valid:
            return NEG_INF
        return scoring.score(trace.components)

    return evaluate


def _build_problem(ctx: Context) -> PlanMutationProblem:
    start = warm_start(ctx.level.id) or _initial(ctx)
    return PlanMutationProblem(
        plan_=start,
        mutate=_mutate,
        evaluate=_evaluate_with(ctx.level),
    )


register_problem("anneal")(_build_problem)
register_problem("climb")(_build_problem)


@register("anneal")
def anneal(ctx: Context) -> Plan:
    """Warm-start from best/, then anneal for the remaining budget."""
    problem = _build_problem(ctx)
    return local_search(
        ctx,
        problem,
        accept=Anneal.relative(problem.score()),
    )


@register("climb")
def climb(ctx: Context) -> Plan:
    """Same moves, strictly uphill. The honest baseline for whether
    annealing is earning its keep on this landscape."""
    return local_search(ctx, _build_problem(ctx), accept=hill_climb)
