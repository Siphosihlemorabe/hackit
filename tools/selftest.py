#!/usr/bin/env python3
"""Checks on the two things that fail silently: the exact solver and the
record store.

    python tools/selftest.py

Not a test suite, and not something to maintain during the event. These
two are here because their failure modes are invisible: a solver that
quietly returns a best fit, and a writer that quietly truncates
best-known.json. Everything else in this repo fails loudly and does not
need a test.
"""

from __future__ import annotations

import json
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

import _paths  # noqa: F401
import exact_linalg as la
import record

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def F(*xs):
    return [Fraction(x) for x in xs]


def test_solver() -> None:
    print("exact solver")

    # score = 1000*a + 5*b + 20
    names = ["a", "b", "const"]
    obs = [
        (F(0, 0, 1), Fraction(20)),
        (F(1, 0, 1), Fraction(1020)),
        (F(0, 1, 1), Fraction(25)),
    ]
    s = la.solve(names, obs)
    check("unique system solves", s.status == la.UNIQUE, s.status)
    check("coefficients exact",
          s.solution == {"a": Fraction(1000), "b": Fraction(5), "const": Fraction(20)},
          str(s.solution))

    # Only the sum a+b is observable.
    obs2 = [(F(1, 1, 1), Fraction(1025)), (F(2, 2, 1), Fraction(2030))]
    s2 = la.solve(names, obs2)
    check("underdetermined detected", s2.status == la.UNDERDETERMINED, s2.status)
    check("names the free coefficients", set(s2.free) >= {"a", "b"}, str(s2.free))
    ranked = la.most_informative_probe(names, obs2)
    check("suggests a probe", bool(ranked) and ranked[0][1] > 0, str(ranked))

    # No solution: same input, two different totals.
    obs3 = [(F(1, 0, 1), Fraction(1020)), (F(1, 0, 1), Fraction(1021))]
    s3 = la.solve(names, obs3, labels=["log-A", "log-B"])
    check("inconsistent detected", s3.status == la.INCONSISTENT, s3.status)
    check("no approximation returned", not s3.solution)
    check("names the conflicting logs",
          bool(s3.conflicts) and set(s3.conflicts[0]["combination"]) == {"log-A", "log-B"},
          str(s3.conflicts))

    # Floats that are not representable in binary must still solve exactly.
    s4 = la.solve(["x", "const"], [(F(0, 1), Fraction(0)),
                                   ([Fraction("0.1"), Fraction(1)], Fraction("0.3"))])
    check("no float drift", s4.status == la.UNIQUE and s4.solution["x"] == Fraction(3),
          str(s4.solution))

    check("roundness: 1_500_000 is round", la.is_round(Fraction(1_500_000)))
    check("roundness: 1/2 is round", la.is_round(Fraction(1, 2)))
    check("roundness: 104729 is not", not la.is_round(Fraction(104729)))
    check("roundness: 7/13 is not", not la.is_round(Fraction(7, 13)))


def test_record() -> None:
    print("record store")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "best-known.json"

        record.write_json_atomic(p, {
            "version": 1, "objective": "max",
            "levels": {str(i): {"score": i * 100.0, "seed": i} for i in (1, 2, 3, 4)},
        })
        before = json.loads(p.read_text())

        record.update_level("2", {"score": 999.0, "seed": 7}, path=p)
        after = json.loads(p.read_text())
        check("updated level changed", after["levels"]["2"]["score"] == 999.0)
        check("other levels untouched",
              all(after["levels"][k] == before["levels"][k] for k in ("1", "3", "4")))
        check("level count preserved", len(after["levels"]) == 4, str(sorted(after["levels"])))

        # Crash mid-write: an unserialisable value blows up during dumps.
        class Boom:
            pass

        intact = json.loads(p.read_text())
        try:
            record.write_json_atomic(p, {"levels": {"1": Boom()}})
        except TypeError:
            pass
        else:
            FAILURES.append("crash simulation did not raise")
        check("file survives a crashed write", json.loads(p.read_text()) == intact)
        check("no temp files left behind",
              not list(Path(td).glob("*.tmp")), str(list(Path(td).glob("*.tmp"))))

        # A truncating writer would leave invalid JSON; ours renames instead.
        big = {"version": 1, "objective": "max",
               "levels": {str(i): {"score": float(i), "pad": "x" * 5000} for i in range(1, 5)}}
        record.write_json_atomic(p, big)
        check("large write is complete JSON", json.loads(p.read_text()) == big)

        with record.lock(p):
            check("lock file created", p.with_suffix(".json.lock").exists())
        check("lock released", not p.with_suffix(".json.lock").exists())

        check("improvement, max", record.is_improvement(10, 5, "max"))
        check("no improvement, max", not record.is_improvement(5, 10, "max"))
        check("improvement, min", record.is_improvement(5, 10, "min"))
        check("first result always improves", record.is_improvement(0, None, "max"))


def main() -> int:
    test_solver()
    print()
    test_record()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
