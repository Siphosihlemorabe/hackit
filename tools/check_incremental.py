#!/usr/bin/env python3
"""Check a planner's incremental scoring against full re-simulation.

    python tools/check_incremental.py --planner anneal --level 1
    python tools/check_incremental.py --planner anneal --moves 2000

Run this the moment you write an incremental score, and again every time
you add a move type.

An incremental score that drifts from the full replay does not crash and
does not look wrong: the search simply climbs a hill that is not there,
and you find out from the leaderboard. This applies random moves, checks
`problem.score()` against a full replay of `problem.plan()` after each
one, and checks that undo puts the state back exactly.

Exit code 1 means they diverge.
"""

# isort: skip_file -- `import _paths` puts src/ on sys.path and MUST stay above
# the engine/scoring/planners imports. Sorting this block breaks the tool.

from __future__ import annotations

import argparse
import random
import sys

import _paths  # noqa: F401

import planners
import scoring
from engine import load_level, simulate
from engine.level import level_ids
from planners.search import NEG_INF, audit_incremental


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--planner", required=True, metavar="NAME")
    ap.add_argument("--level", action="append", metavar="ID",
                    help="repeatable; defaults to every level")
    ap.add_argument("--moves", type=int, default=200)
    ap.add_argument("--tol", type=float, default=0.0,
                    help="absolute tolerance (default 0 -- exact)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    planners.load_all()
    factory = planners.PROBLEMS.get(args.planner)
    if factory is None:
        known = ", ".join(sorted(planners.PROBLEMS)) or "(none)"
        print(f"planner {args.planner!r} has no registered search problem.\n"
              f"registered: {known}\n\n"
              f"Add one with @register_problem({args.planner!r}) in its module -- "
              f"see src/planners/anneal.py.", file=sys.stderr)
        return 2

    levels = args.level or level_ids()
    formula = scoring.load_formula()
    if not formula.verified:
        print(f"note: formula is unverified ({formula.source}); this checks that the "
              f"incremental path agrees with the full replay, which is worth knowing "
              f"either way.\n")

    failures = 0
    for level_id in levels:
        level = load_level(level_id)

        def full_eval(plan, _level=level) -> float:
            trace = simulate(_level, plan)
            return NEG_INF if not trace.valid else scoring.score(trace.components)

        ctx = planners.Context(level=level, seed=args.seed, budget_s=1e9)
        problem = factory(ctx)
        rng = random.Random(args.seed)

        bad = audit_incremental(problem, rng, full_eval, moves=args.moves, tol=args.tol)
        if bad is None:
            print(f"level {level_id}: ok -- {args.moves} move(s) agreed with full replay")
            continue

        failures += 1
        print(f"level {level_id}: DIVERGED ({bad['kind']}) at move {bad['move_index']}")
        if bad.get("move"):
            print(f"  move:        {bad['move']}")
        if bad["kind"] == "undo-state":
            print("  undo(move) did not restore the plan -- the state is being "
                  "mutated somewhere apply()/undo() does not account for")
        else:
            print(f"  incremental: {bad['incremental']:,.6f}")
            print(f"  full replay: {bad['full']:,.6f}")
            print(f"  delta:       {bad['delta']:+,.6f}")
        if bad["kind"] == "initial":
            print("  ...and they disagreed before any move: the problem's initial "
                  "score is wrong, not its move handling")

    print()
    if failures:
        print(f"{failures} level(s) diverged -- fix before searching, the score you "
              f"are climbing is not the score you get")
        return 1
    print("incremental scoring agrees with full re-simulation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
