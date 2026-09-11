#!/usr/bin/env python3
"""Run every planner on every level, score it, compare, keep what won.

    python tools/run.py                 # all planners, all levels, 30s each
    python tools/run.py --time 5        # quick iteration pass
    python tools/run.py --level 2       # one level, leaves the other records alone
    python tools/run.py --dry-run       # score without writing anything

Planners run in parallel worker processes. Workers are pure: they return
a plan and its components and touch no files. All writing happens here in
the parent, after every result is in, under a lock -- so a crashed worker
cannot leave a half-updated record behind.
"""

# isort: skip_file -- `import _paths` puts src/ on sys.path and MUST stay above
# the engine/scoring/planners imports. Sorting this block breaks the tool.

from __future__ import annotations

import argparse
import concurrent.futures as cf
import contextlib
import os
import sys
import time
import zlib
from typing import Any

import _paths
import record

from engine import load_level, simulate
from engine.level import level_ids
from engine.model import sha256_of
import planners
import scoring

GRACE_S = 10.0  # hard wall on top of the planner's own budget


# --------------------------------------------------------------------------
# worker
# --------------------------------------------------------------------------


def derive_seed(base: int, level: str, planner: str) -> int:
    """Stable across runs, processes and platforms.

    Not hash() -- that is salted per process, which would make the same
    command produce different plans on different days.
    """
    return (base * 1000003 + zlib.crc32(f"{level}:{planner}".encode())) % (2**31)


def solve_one(task: dict[str, Any]) -> dict[str, Any]:
    """Runs in a worker process. Returns plain data, writes nothing."""
    level_id, planner_name = task["level"], task["planner"]
    out: dict[str, Any] = {
        "level": level_id,
        "planner": planner_name,
        "seed": task["seed"],
        "budget_s": task["budget_s"],
        "ok": False,
        "error": None,
        "score": None,
        "components": {},
        "valid": True,
        "violations": [],
        "plan": None,
        "n_actions": 0,
        "elapsed_s": 0.0,
    }
    started = time.monotonic()
    try:
        planners.load_all()
        level = load_level(level_id)
        ctx = planners.Context(level=level, seed=task["seed"], budget_s=task["budget_s"])
        plan = planners.REGISTRY[planner_name].fn(ctx)

        trace = simulate(level, plan)
        out["components"] = dict(sorted(trace.components.items()))
        out["valid"] = trace.valid
        out["violations"] = list(trace.violations)
        out["score"] = scoring.score(trace.components)
        out["plan"] = plan.to_dict()
        out["n_actions"] = len(plan.actions)
        out["trace"] = trace.to_dict() if task["keep_trace"] else None
        out["ok"] = True
    except Exception as e:  # a broken planner must not take the run down
        out["error"] = f"{type(e).__name__}: {e}"
    out["elapsed_s"] = round(time.monotonic() - started, 3)
    return out


# --------------------------------------------------------------------------
# presentation
# --------------------------------------------------------------------------

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _unicode_ok() -> bool:
    """Windows consoles still default to cp1252, which cannot encode the
    box-drawing gutter or a delta sign. Try to switch to UTF-8; fall back
    to ASCII rather than dying halfway through printing the table."""
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        "│Δ·★".encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


_UNI = _unicode_ok()
GUTTER = " │" if _UNI else " |"
DELTA_HEAD = "Δ BEST" if _UNI else "D BEST"
BULLET = "·" if _UNI else "-"


def c(text: str, *codes: str) -> str:
    if not _COLOR or not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


DIM, BOLD, RED, GREEN, YELLOW, CYAN = "2", "1", "31", "32", "33", "36"


def fmt_score(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:,.0f}" if abs(v - round(v)) < 1e-9 else f"{v:,.2f}"


def fmt_delta(delta: float | None, improved: bool) -> tuple[str, str]:
    """Returns (text, ansi_codes). This is the column the eye should land
    on, so it gets the sign, the separators and the only strong colour in
    the table."""
    if delta is None:
        return "NEW", GREEN + ";" + BOLD if improved else GREEN
    if delta > 0:
        return f"+{delta:,.0f}", f"{BOLD};{GREEN}"
    if delta < 0:
        return f"{delta:,.0f}", RED
    return "0", DIM


def print_table(rows: list[dict[str, Any]], objective: str) -> None:
    headers = ["LEVEL", "PLANNER", "ACTS", "SCORE", DELTA_HEAD, "STATUS"]
    body = []
    for r in rows:
        if not r["ok"]:
            body.append([r["level"], r["planner"], "-", "-", "-", "error"])
            continue
        dtext, _ = fmt_delta(r["delta"], r["improved"])
        status = "improved" if r["improved"] else ("invalid" if not r["valid"] else "")
        body.append(
            [
                r["level"],
                r["planner"],
                str(r["n_actions"]),
                fmt_score(r["score"]),
                dtext,
                status,
            ]
        )

    widths = [max(len(h), *(len(row[i]) for row in body)) if body else len(h)
              for i, h in enumerate(headers)]
    right = {2, 3, 4}

    def line(cells: list[str], deco: list[str] | None = None) -> str:
        parts = []
        for i, cell in enumerate(cells):
            txt = cell.rjust(widths[i]) if i in right else cell.ljust(widths[i])
            if deco and deco[i]:
                txt = c(txt, *deco[i].split(";"))
            parts.append(txt)
            if i == 3:  # gutter before the delta column
                parts.append(c(GUTTER, DIM))
        return "  ".join(parts[:4]) + parts[4] + "  " + "  ".join(parts[5:])

    print(line(headers, [DIM, DIM, DIM, DIM, f"{BOLD};{CYAN}", DIM]))
    for r, cells in zip(rows, body):
        deco = [""] * 6
        if not r["ok"]:
            deco = [DIM, DIM, DIM, DIM, RED, RED]
        else:
            deco[3] = DIM  # the totals all look alike; mute them
            _, dcodes = fmt_delta(r["delta"], r["improved"])
            deco[4] = dcodes
            if r["improved"]:
                deco[5] = GREEN
            elif not r["valid"]:
                deco[5] = f"{BOLD};{RED}"
        print(line(cells, deco))

    for r in rows:
        if not r["ok"]:
            print(c(f"  ! {r['level']}/{r['planner']}: {r['error']}", RED))
        elif r["violations"]:
            print(c(f"  ! {r['level']}/{r['planner']}: {'; '.join(r['violations'][:3])}", YELLOW))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--time", type=float, default=30.0, metavar="S",
                   help="solve budget per planner per level (default: 30)")
    p.add_argument("--level", action="append", metavar="ID",
                   help="restrict to a level; repeatable or comma-separated")
    p.add_argument("--planner", action="append", metavar="NAME",
                   help="restrict to a planner; repeatable or comma-separated")
    p.add_argument("--seed", type=int, default=0, help="base seed (default: 0)")
    p.add_argument("--jobs", type=int, default=0, help="worker processes (default: cpu count)")
    p.add_argument("--minimise", action="store_true", help="lower score is better")
    p.add_argument("--dry-run", action="store_true", help="score and print, write nothing")
    p.add_argument("--no-trace", action="store_true", help="skip writing traces/")
    p.add_argument("--serial", action="store_true", help="one process (for debugging planners)")
    return p.parse_args(argv)


def expand(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    out = []
    for v in values:
        out.extend(part.strip() for part in v.split(",") if part.strip())
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    objective = "min" if args.minimise else "max"

    existing = record.load()
    if existing["levels"] and existing["objective"] != objective:
        print(c(f"! best-known.json says objective={existing['objective']} but you passed "
                f"{objective}. Refusing to mix. Delete the file or drop --minimise.", BOLD, RED))
        return 2

    planners.load_all()
    if not planners.REGISTRY:
        print("no planners registered in src/planners/", file=sys.stderr)
        return 2

    levels = expand(args.level) or level_ids()
    want_planners = expand(args.planner)

    tasks = []
    for lv in levels:
        for name in planners.available(lv):
            if want_planners and name not in want_planners:
                continue
            tasks.append({
                "level": lv,
                "planner": name,
                "seed": derive_seed(args.seed, lv, name),
                "budget_s": args.time,
                "keep_trace": not args.no_trace,
            })
    if not tasks:
        print("nothing to run: no planner matched those levels", file=sys.stderr)
        return 2

    formula = scoring.load_formula()
    banner = (f"{len(tasks)} task(s)  levels={','.join(levels)}  budget={args.time:g}s  "
              f"seed={args.seed}  objective={objective}")
    print(c(banner, DIM))
    if not formula.verified:
        print(c(f"! formula is UNVERIFIED ({formula.source}) -- local scores are not "
                f"comparable to the portal. Run tools/fit.py.", BOLD, YELLOW))

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    if args.serial:
        results = [solve_one(t) for t in tasks]
    else:
        jobs = args.jobs or min(len(tasks), os.cpu_count() or 4)
        with cf.ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(solve_one, t): t for t in tasks}
            for fut in cf.as_completed(futures, timeout=args.time + GRACE_S + 30):
                t = futures[fut]
                try:
                    results.append(fut.result(timeout=GRACE_S))
                except Exception as e:
                    results.append({
                        **{k: t[k] for k in ("level", "planner", "seed", "budget_s")},
                        "ok": False, "error": f"worker: {type(e).__name__}: {e}",
                        "score": None, "components": {}, "valid": True, "violations": [],
                        "plan": None, "n_actions": 0, "elapsed_s": 0.0, "trace": None,
                    })

    results.sort(key=lambda r: (len(r["level"]), r["level"], r["planner"]))

    # Compare and persist. One lock, one read-modify-write per improvement.
    stamp = record.utcstamp()
    n_improved = 0
    with record.lock():
        current = record.load()
        for r in results:
            prev = current["levels"].get(r["level"], {}).get("score")
            r["best_before"] = prev
            r["improved"] = False
            r["delta"] = None
            if not r["ok"]:
                continue
            if prev is not None:
                r["delta"] = r["score"] - prev
            if r["valid"] and record.is_improvement(r["score"], prev, objective):
                r["improved"] = True

        # Where several planners beat the record on one level, only the best wins.
        by_level: dict[str, dict[str, Any]] = {}
        for r in results:
            if not r["improved"]:
                continue
            cur = by_level.get(r["level"])
            if cur is None or record.is_improvement(r["score"], cur["score"], objective):
                by_level[r["level"]] = r
        for r in results:
            if r["improved"] and by_level.get(r["level"]) is not r:
                r["improved"] = False

        # With no prior record, "NEW" only makes sense for the row that
        # became the record. Everything else on that level is measured
        # against the winner, so the column always answers the same
        # question: how far off the pace is this?
        for r in results:
            if r["ok"] and r["best_before"] is None and not r["improved"]:
                winner = by_level.get(r["level"])
                if winner is not None:
                    r["delta"] = r["score"] - winner["score"]

        for level_id in sorted(by_level):
            r = by_level[level_id]
            if args.dry_run:
                continue
            plan_text = "\n".join(str(a) for a in r["plan"]["actions"])
            plan_text = plan_text + "\n" if plan_text else ""
            _paths.SUBMISSIONS.mkdir(parents=True, exist_ok=True)
            _paths.BEST.mkdir(parents=True, exist_ok=True)
            (_paths.SUBMISSIONS / f"level{level_id}.txt").write_text(plan_text, newline="\n")
            record.write_json_atomic(_paths.BEST / f"level{level_id}.json", {
                "plan": r["plan"], "components": r["components"], "score": r["score"],
                "planner": r["planner"], "seed": r["seed"], "budget_s": r["budget_s"],
                "timestamp": stamp,
            })
            record.update_level(level_id, {
                "score": r["score"],
                "budget_s": r["budget_s"],
                "seed": r["seed"],
                "planner": r["planner"],
                "timestamp": stamp,
                "plan_sha256": sha256_of(
                    {"level": r["plan"]["level"], "actions": r["plan"]["actions"]}),
                "n_actions": r["n_actions"],
                "components": r["components"],
                "formula_verified": formula.verified,
                "formula_source": formula.source,
            }, objective=objective)
            n_improved += 1

        if not args.dry_run:
            for r in results:
                record.append_history({
                    "timestamp": stamp, "level": r["level"], "planner": r["planner"],
                    "score": r["score"], "delta": r["delta"], "improved": r["improved"],
                    "best_before": r["best_before"], "budget_s": r["budget_s"],
                    "seed": r["seed"], "valid": r.get("valid", True),
                    "elapsed_s": r["elapsed_s"], "error": r["error"],
                    "formula_verified": formula.verified,
                })

    if not args.no_trace and not args.dry_run:
        _paths.TRACES.mkdir(parents=True, exist_ok=True)
        for r in results:
            if r.get("trace"):
                record.write_json_atomic(
                    _paths.TRACES / f"level{r['level']}.{r['planner']}.json", r["trace"])

    print()
    print_table(results, objective)
    print()
    msg = f"{n_improved} improved  {BULLET}  {time.monotonic() - started:.1f}s wall"
    if args.dry_run:
        msg += f"  {BULLET}  DRY RUN, nothing written"
    print(c(msg, DIM))

    # Standing reminder: a level with no recorded score is worth more than
    # any optimisation of a level that already has one.
    final = record.load()
    missing = [lv for lv in level_ids() if lv not in final["levels"]]
    zeroed = [lv for lv, e in sorted(final["levels"].items()) if e.get("score") == 0]
    broken = sorted({r["level"] for r in results if not r["ok"]})
    if missing:
        print(c(f"! no recorded score yet: level {', '.join(missing)} -- get any valid "
                f"submission up there before optimising anything", BOLD, YELLOW))
    if zeroed:
        print(c(f"! best is still zero: level {', '.join(zeroed)}", BOLD, YELLOW))
    if broken:
        print(c(f"! planner errored: level {', '.join(broken)} (see above)", BOLD, RED))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
