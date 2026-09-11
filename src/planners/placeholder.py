"""A trivial planner that exists only to exercise the harness.

It does not solve anything. Delete it once a real planner exists -- or
keep it as a template: it shows the deadline check, the seeded rng, and
the Plan shape, which is all the boilerplate a new planner needs.
"""

from __future__ import annotations

from engine.model import Plan

from . import Context, register


@register("placeholder")
def placeholder(ctx: Context) -> Plan:
    n = 80 + (ctx.seed % 40)
    actions = []
    for _ in range(n):
        if ctx.expired():  # every planner must honour the budget
            break
        actions.append(ctx.rng.randrange(1000))
    return Plan(level=ctx.level.id, actions=actions, meta={"planner": "placeholder"})


@register("empty")
def empty(ctx: Context) -> Plan:
    """The do-nothing plan. Worth keeping: it is the cheapest valid
    submission and the baseline every other planner must beat."""
    return Plan(level=ctx.level.id, actions=[], meta={"planner": "empty"})
