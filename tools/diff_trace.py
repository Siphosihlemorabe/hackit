#!/usr/bin/env python3
"""Find the first point where my engine and the portal disagree.

    python tools/diff_trace.py traces/level1.greedy.json log/level1-run7.txt

Compares component by component, step by step, and stops at the first
divergence. That step is the action your simulator models wrongly --
which is usually a one-line fix once you know which action it is.

If the portal only reports final figures, it falls back to comparing
totals and tells you so. Exit code 1 means they diverge.
"""

# isort: skip_file -- `import _paths` puts src/ on sys.path and MUST stay above
# the engine/scoring/planners imports. Sorting this block breaks the tool.

from __future__ import annotations

import argparse
from pathlib import Path

import _paths
import portal_log

from engine.model import Trace


def close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trace", type=Path, help="one of my traces from traces/")
    ap.add_argument("log", type=Path, help="the portal log for the same plan")
    ap.add_argument("--tol", type=float, default=0.0,
                    help="absolute tolerance (default 0 -- exact)")
    ap.add_argument("--all", action="store_true", help="report every divergence, not just the first")
    args = ap.parse_args(argv)

    mine = Trace.load(args.trace)
    theirs = portal_log.parse_file(args.log)

    print(f"mine   {args.trace}  level={mine.level}  steps={len(mine.steps)}")
    print(f"portal {args.log}  level={theirs.level}  steps={len(theirs.steps)}")
    if theirs.level and mine.level and theirs.level != str(mine.level):
        print(f"! level mismatch ({mine.level} vs {theirs.level}) -- comparing anyway")
    if not theirs.components:
        print("\n! parsed no components from the log. Fix the patterns in "
              "tools/portal_log.py before trusting anything below.")
        return 2
    print()

    divergences = 0

    # Per-step comparison, if the portal gave us steps.
    if theirs.steps and mine.steps:
        n = min(len(mine.steps), len(theirs.steps))
        shared = sorted(set(mine.steps[0].components) & set(theirs.steps[0]))
        if not shared:
            print("! no component names in common between trace and log -- "
                  "rename in tools/portal_log.py so they match the engine's names")
            return 2
        for i in range(n):
            ms, ts = mine.steps[i].components, theirs.steps[i]
            bad = [k for k in shared
                   if k in ts and not close(float(ms.get(k, 0.0)), float(ts[k]), args.tol)]
            if bad:
                divergences += 1
                print(f"FIRST DIVERGENCE at step {i}")
                print(f"  action: {mine.steps[i].action!r}")
                for k in bad:
                    m, t = float(ms.get(k, 0.0)), float(ts[k])
                    print(f"  {k:<20} mine={m:>18,.4f}  portal={t:>18,.4f}  "
                          f"delta={m - t:>+18,.4f}")
                if not args.all:
                    print("\nThis is the action your simulator gets wrong. Everything after "
                          "it is downstream noise -- fix this step first.")
                    return 1
        if len(mine.steps) != len(theirs.steps):
            print(f"! step count differs: mine {len(mine.steps)}, portal {len(theirs.steps)}")
            divergences += 1
    else:
        print("(no per-step figures in the log -- comparing final components only.")
        print(" If the portal can be made to print per-step output, it is worth the")
        print(" minute: step-level diffs localise the bug, totals do not.)\n")

    # Final components.
    keys = sorted(set(mine.components) | set(theirs.components))
    header = f"  {'component':<20} {'mine':>18} {'portal':>18} {'delta':>18}"
    printed = False
    for k in keys:
        m = mine.components.get(k)
        t = theirs.components.get(k)
        if m is None or t is None:
            if not printed:
                print(header)
                printed = True
            side = "portal only" if m is None else "mine only"
            print(f"  {k:<20} {'-' if m is None else f'{m:,.4f}':>18} "
                  f"{'-' if t is None else f'{t:,.4f}':>18} {side:>18}")
            divergences += 1
            continue
        if not close(float(m), float(t), args.tol):
            if not printed:
                print(header)
                printed = True
            print(f"  {k:<20} {float(m):>18,.4f} {float(t):>18,.4f} "
                  f"{float(m) - float(t):>+18,.4f}")
            divergences += 1

    if divergences == 0:
        print("no divergence -- engine agrees with the portal on every shared component")
        return 0
    print(f"\n{divergences} divergence(s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
