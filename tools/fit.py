#!/usr/bin/env python3
"""Recover the scoring formula from portal logs. Solve it, do not fit it.

    python tools/fit.py                 # report only
    python tools/fit.py --write         # write formula.json (only if UNIQUE)
    python tools/fit.py --log-dir DIR   # read logs from elsewhere

Reads every log in log/, treats each as one linear equation

    sum_i  coeff_i * component_i  (+ constant)  =  reported total

and solves the system exactly over the rationals. Three outcomes, and
only one of them produces a formula:

  UNIQUE          -- solved. Coefficients are exact.
  UNDERDETERMINED -- not enough independent probes. Says which probe
                     would pin down the most remaining coefficients.
  INCONSISTENT    -- the observations contradict each other, so the score
                     is not linear in these components (or a log is
                     mis-parsed). Names the conflicting logs.

There is deliberately no least-squares path. A best-fit formula looks
like success and ranks plans wrongly.
"""

from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path

import _paths
import exact_linalg as la
import portal_log
import record


def to_fraction(x: float) -> Fraction:
    """Exact, via the decimal string -- Fraction(0.1) is not 1/10."""
    return Fraction(str(x))


def build_system(observations, *, constant: bool):
    names = sorted({k for o in observations for k in o.components})
    if constant:
        names.append("const")
    rows = []
    labels = []
    for o in observations:
        row = [to_fraction(o.components.get(n, 0.0)) for n in names]
        if constant:
            row[-1] = Fraction(1)
        rows.append((row, to_fraction(o.total)))
        labels.append(o.label)
    return names, rows, labels


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir", type=Path, default=None)
    ap.add_argument("--write", action="store_true", help="write formula.json if UNIQUE")
    ap.add_argument("--no-constant", action="store_true",
                    help="assume the formula has no additive constant")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    log_dir = args.log_dir or _paths.LOG
    parsed = portal_log.load_dir(log_dir)
    usable = [o for o in parsed if o.usable]

    print(f"logs in {log_dir}: {len(parsed)} file(s), {len(usable)} usable")
    for o in parsed:
        if not o.usable:
            why = "no total" if o.total is None else "no components"
            print(f"  skip {o.label}: {why} -- check the patterns in tools/portal_log.py")
    if not usable:
        print("\nnothing to solve. Submit some probes (tools/gen_probes.py) and "
              "download their logs into log/.")
        return 1

    names, rows, labels = build_system(usable, constant=not args.no_constant)
    print(f"unknowns: {len(names)} ({', '.join(names)})")
    print(f"equations: {len(rows)}\n")

    sol = la.solve(names, rows, labels)

    if sol.status == la.INCONSISTENT:
        print("INCONSISTENT -- no linear formula over these components fits these logs.")
        print("Not returning an approximation.\n")
        for cflt in sol.conflicts[:5]:
            combo = cflt["combination"]
            terms = "  ".join(f"{la.show(v)}*{k}" for k, v in sorted(combo.items()))
            print(f"  contradiction: {terms}  =>  0 = {la.show(cflt['residual'])}")
        print("\nLikely causes, in the order worth checking:")
        print("  1. tools/portal_log.py is mis-parsing a figure (check units and commas)")
        print("  2. a component is missing -- the score depends on something not logged")
        print("  3. the score is not linear (a cap, a floor, a ratio, or a bonus threshold)")
        return 2

    if sol.status == la.UNDERDETERMINED:
        print(f"UNDERDETERMINED -- rank {sol.rank} of {len(names)} unknowns.")
        if sol.determined:
            print("\n  pinned down already:")
            for k, v in sorted(sol.determined.items()):
                print(f"    {k:<24} = {la.show(v)}")
        print(f"\n  still free: {', '.join(sol.free)}")
        ranked = la.most_informative_probe(names, rows)
        if ranked:
            print("\n  most informative next probe -- isolate one of these,")
            print("  i.e. submit two plans differing only in that component:")
            for name, gain in ranked[:5]:
                if name == "const":
                    print(f"    {name:<24} +{gain} coefficient(s)  "
                          f"(an empty action list gives you this one free)")
                else:
                    print(f"    {name:<24} +{gain} coefficient(s)")
        return 3

    print("UNIQUE -- solved exactly.\n")
    const = sol.solution.get("const", Fraction(0))
    coeffs = {k: v for k, v in sol.solution.items() if k != "const"}
    suspicious = []
    for k, v in sorted(coeffs.items()):
        flag = "" if la.is_round(v) else "   <-- not round"
        if flag:
            suspicious.append(k)
        print(f"  {k:<24} = {la.show(v):>24}{flag}")
    cflag = "" if la.is_round(const) else "   <-- not round"
    if cflag:
        suspicious.append("const")
    print(f"  {'const':<24} = {la.show(const):>24}{cflag}")

    if suspicious:
        print(f"\n! {len(suspicious)} coefficient(s) are not round: {', '.join(suspicious)}.")
        print("  Real scoring formulas use round numbers. Treat this as an unfinished")
        print("  derivation -- a missing component or a unit error -- not as a result.")

    if args.write:
        if suspicious:
            print("\nrefusing --write while coefficients look unfinished; "
                  "pass --write again after you have explained them "
                  "(or edit formula.json by hand and set verified accordingly).")
            return 4
        out = args.out or _paths.FORMULA
        payload = {
            "coefficients": {k: str(v) for k, v in sorted(coeffs.items())},
            "constant": str(const),
            "verified": True,
            "source": f"fit.py from {len(usable)} log(s) in {log_dir.name}/",
            "notes": f"exact solve, rank {sol.rank}/{len(names)}, {record.utcstamp()}",
        }
        record.write_json_atomic(out, payload)
        print(f"\nwrote {out}")
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("\n(report only -- pass --write to save formula.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
