#!/usr/bin/env python3
"""Emit calibration submissions that isolate one scoring component each.

    python tools/gen_probes.py --list
    python tools/gen_probes.py --level 1

STUB. The structure is here; the probes are not, because they depend on
tomorrow's action types. Fill in the TODOs once you know what an action
is -- each probe should differ from the empty plan in exactly one
measurable way, so its log pins down exactly one coefficient.

Workflow:
  1. gen_probes.py writes submissions/probes/*.txt
  2. upload each, download the log into log/
  3. tools/fit.py solves for the coefficients
  4. if it says UNDERDETERMINED, it names the probe to write next

A probe that moves two components at once tells you their sum and
nothing else. One knob at a time.
"""

# isort: skip_file -- `import _paths` puts src/ on sys.path and MUST stay above
# the engine/scoring/planners imports. Sorting this block breaks the tool.

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass

import _paths

from engine import load_level
from engine.level import level_ids
from engine.model import Plan

PROBE_DIR = _paths.SUBMISSIONS / "probes"


@dataclass(frozen=True)
class Probe:
    name: str
    isolates: str  # the coefficient this is meant to pin down
    build: Callable[[str], Plan]
    note: str = ""


PROBES: list[Probe] = []


def probe(name: str, isolates: str, note: str = "") -> Callable[..., Callable[[str], Plan]]:
    def deco(fn: Callable[[str], Plan]) -> Callable[[str], Plan]:
        PROBES.append(Probe(name=name, isolates=isolates, build=fn, note=note))
        return fn

    return deco


# --- problem-agnostic: keep this one ------------------------------------


@probe("empty", isolates="const",
       note="Zero actions. Whatever it scores IS the additive constant. "
            "Submit it first on every level -- it is also your guaranteed "
            "valid submission while the real planners are still broken.")
def empty_plan(level_id: str) -> Plan:
    return Plan(level=level_id, actions=[], meta={"probe": "empty"})


# --- fill in after the briefing -----------------------------------------
#
# @probe("single-<action>", isolates="<component>",
#        note="one action of type X, nothing else")
# def single_x(level_id: str) -> Plan:
#     return Plan(level=level_id, actions=[...], meta={"probe": "single-x"})
#
# @probe("double-<action>", isolates="<component> linearity",
#        note="two identical actions: confirms the component is linear, "
#             "not capped or superlinear, before you trust the solve")
# def double_x(level_id: str) -> Plan:
#     return Plan(level=level_id, actions=[..., ...], meta={"probe": "double-x"})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--level", action="append", metavar="ID")
    ap.add_argument("--list", action="store_true", help="show probes without writing")
    args = ap.parse_args(argv)

    if args.list or not PROBES:
        print(f"{len(PROBES)} probe(s) defined:")
        for p in PROBES:
            print(f"  {p.name:<16} isolates {p.isolates:<14} {p.note}")
        if len(PROBES) <= 1:
            print("\nOnly the problem-agnostic probe exists. Add the rest to "
                  "tools/gen_probes.py once you know the action types.")
        if args.list:
            return 0

    levels = args.level or level_ids()
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for level_id in levels:
        load_level(level_id)  # fails loudly now rather than after upload
        for p in PROBES:
            plan = p.build(level_id)
            path = PROBE_DIR / f"level{level_id}.{p.name}.txt"
            path.write_text(plan.to_submission_text() if plan.actions else "", newline="\n")
            written.append(path)

    print(f"\nwrote {len(written)} probe submission(s) to {PROBE_DIR}")
    for path in written:
        print(f"  {path.relative_to(_paths.ROOT)}")
    print("\nUpload these, then save each log into log/ and run tools/fit.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
