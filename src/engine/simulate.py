"""The simulator: replay an action list, produce components.

THIS FILE COMPUTES FACTS. It must not know coefficients, weights or what a
good score looks like. If you find yourself multiplying a count by a magic
number here, it belongs in scoring.py.

Swap ACTIVE to "real" once _real is written; everything downstream picks
that up without changes.
"""

from __future__ import annotations

from collections.abc import Callable

from .model import Components, Level, Plan, StepTrace, Trace

Simulator = Callable[[Level, Plan], Trace]

SIMULATORS: dict[str, Simulator] = {}

# Which simulator run.py and the tools use. Flip to "real" on the day.
ACTIVE = "placeholder"


def register_simulator(name: str) -> Callable[[Simulator], Simulator]:
    def deco(fn: Simulator) -> Simulator:
        SIMULATORS[name] = fn
        return fn

    return deco


def simulate(level: Level, plan: Plan, simulator: str | None = None) -> Trace:
    name = simulator or ACTIVE
    try:
        fn = SIMULATORS[name]
    except KeyError:
        known = ", ".join(sorted(SIMULATORS))
        raise KeyError(f"no simulator {name!r}; registered: {known}") from None
    return fn(level, plan)


@register_simulator("real")
def _real(level: Level, plan: Plan) -> Trace:
    """TODO(briefing): the actual simulator.

    Contract:
      - deterministic: same (level, plan) -> same Trace, always
      - records a cumulative component snapshot per step, so diff_trace.py
        can localise a divergence to a single action
      - sets valid=False + violations[] on a rule breach rather than
        raising, so an illegal plan scores instead of killing the run
    """
    raise NotImplementedError("engine/simulate.py:_real -- write after the briefing")


@register_simulator("placeholder")
def _placeholder(level: Level, plan: Plan) -> Trace:
    """Stand-in so the pipeline is exercisable before the briefing.

    Produces three arbitrary but deterministic components. Delete once the
    real simulator exists.
    """
    steps: list[StepTrace] = []
    running: Components = {"actions": 0.0, "distinct": 0.0, "weight": 0.0}
    seen: set[str] = set()

    for i, action in enumerate(plan.actions):
        key = str(action)
        running["actions"] += 1.0
        if key not in seen:
            seen.add(key)
            running["distinct"] += 1.0
        running["weight"] += float(action) if isinstance(action, (int, float)) else float(len(key))
        steps.append(StepTrace(i=i, action=action, components=dict(running)))

    return Trace(
        level=level.id,
        plan_sha256=plan.sha256(),
        components=dict(running),
        steps=steps,
        valid=True,
        violations=[],
    )
