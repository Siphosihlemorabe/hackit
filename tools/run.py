#!/usr/bin/env python3
"""Run every planner on every level, score it, compare, keep what won.

    python tools/run.py                 # all planners, all levels, 30s each
    python tools/run.py --time 5        # quick iteration pass
    python tools/run.py --level 2       # one level, leaves the other records alone
    python tools/run.py --seeds 4       # 4 seeds per planner instead of one per core
    python tools/run.py --dry-run       # score without writing anything

Every (level, planner) is solved --seeds times from independent seeds and
only the best replicate survives; the seed that won is recorded so the
plan can be regenerated. Defaults to one seed per core.

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
import math
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


def sign_test_p(wins: int, losses: int) -> float:
    """Exact two-sided sign test. Integer binomials, one division at the
    end -- no normal approximation, which is meaningless at four seeds
    anyway."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = max(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k, n + 1))
    return min(1.0, 2 * tail / (2**n))


def seeds_for_verdict(alpha: float = 0.05) -> int:
    """Smallest number of one-sided paired seeds that could reach `alpha`.
    Worth printing: at 4 seeds the best possible p is 0.125, so a run that
    cannot produce a verdict should say so before it starts."""
    n = 1
    while 2 / (2**n) > alpha and n < 64:
        n += 1
    return n


def paired_compare(
    results: list[dict[str, Any]], baseline: str, candidates: list[str], objective: str
) -> list[dict[str, Any]]:
    """Per-seed differences between planners that ran on the same seeds.

    Pairing is the point. Comparing two planners' *means* across
    independent seeds buries a real effect under seed variance; comparing
    them seed by seed cancels it, because both saw the same starting luck.
    """
    scores: dict[tuple[str, str, int], float] = {
        (r["level"], r["planner"], r["rep"]): r["score"]
        for r in results
        if r["ok"] and r["valid"] and r["score"] is not None
    }
    levels = sorted({k[0] for k in scores}, key=lambda s: (len(s), s))

    out = []
    for cand in candidates:
        for lv in levels:
            reps = sorted({k[2] for k in scores if k[0] == lv})
            deltas = []
            for rep in reps:
                a = scores.get((lv, baseline, rep))
                b = scores.get((lv, cand, rep))
                if a is None or b is None:
                    continue
                deltas.append((b - a) if objective == "max" else (a - b))
            if not deltas:
                continue
            wins = sum(1 for d in deltas if d > 0)
            losses = sum(1 for d in deltas if d < 0)
            ties = len(deltas) - wins - losses
            ordered = sorted(deltas)
            mid = len(ordered) // 2
            median = (ordered[mid] if len(ordered) % 2
                      else (ordered[mid - 1] + ordered[mid]) / 2)
            p = sign_test_p(wins, losses)
            if wins == losses == 0:
                verdict = "identical"
            elif p <= 0.05:
                verdict = "better" if wins > losses else "worse"
            else:
                verdict = "inconclusive"
            out.append({
                "level": lv, "candidate": cand, "baseline": baseline,
                "n": len(deltas), "mean": sum(deltas) / len(deltas), "median": median,
                "wins": wins, "losses": losses, "ties": ties, "p": p,
                "verdict": verdict,
            })
    return out


def print_compare(cmp_rows: list[dict[str, Any]], n_seeds: int) -> None:
    if not cmp_rows:
        print(c("no paired results to compare -- did both planners run on the same "
                "levels?", YELLOW))
        return
    by_cand: dict[str, list[dict[str, Any]]] = {}
    for row in cmp_rows:
        by_cand.setdefault(row["candidate"], []).append(row)

    need = seeds_for_verdict()
    for cand, rows_ in by_cand.items():
        base = rows_[0]["baseline"]
        print(c(f"paired: {cand} vs {base} (baseline), {n_seeds} seed(s) per level",
                BOLD))
        head = f"  {'LEVEL':<6} {'Δ MEAN':>16} {'Δ MEDIAN':>16} {'W-L-T':>9} {'p':>8}  VERDICT"
        print(c(head, DIM))
        for r in rows_:
            wlt = f"{r['wins']}-{r['losses']}-{r['ties']}"
            codes = {"better": f"{BOLD};{GREEN}", "worse": RED,
                     "identical": DIM, "inconclusive": YELLOW}[r["verdict"]]
            print(f"  {r['level']:<6} {r['mean']:>+16,.0f} {r['median']:>+16,.0f} "
                  f"{wlt:>9} {r['p']:>8.3f}  {c(r['verdict'], *codes.split(';'))}")
        if any(r["verdict"] == "inconclusive" for r in rows_):
            print(c(f"  inconclusive means the seeds disagree -- the difference is "
                    f"inside the noise, not proven absent.", DIM))
            print(c(f"  {need}+ seeds all pointing one way are needed for p<=0.05; "
                    f"re-run with --seeds {max(need, n_seeds + 2)}.", DIM))
        print()


def derive_seed(base: int, level: str, planner: str | None, rep: int = 0) -> int:
    """Stable across runs, processes and platforms.

    Not hash() -- that is salted per process, which would make the same
    command produce different plans on different days.

    `rep` is the replicate index for multi-seed search. It goes through
    crc32 with the rest rather than being added on, so replicate 1 is not
    a near neighbour of replicate 0 -- adjacent seeds can produce
    correlated search paths, which would waste half the fleet.

    `planner=None` drops the planner from the derivation, so every planner
    on a level gets the *same* seed per replicate. That is what makes
    --compare a paired comparison instead of two independent samples.
    """
    key = f"{level}:{rep}" if planner is None else f"{level}:{planner}:{rep}"
    return (base * 1000003 + zlib.crc32(key.encode())) % (2**31)


def solve_one(task: dict[str, Any]) -> dict[str, Any]:
    """Runs in a worker process. Returns plain data, writes nothing."""
    level_id, planner_name = task["level"], task["planner"]
    out: dict[str, Any] = {
        "level": level_id,
        "planner": planner_name,
        "seed": task["seed"],
        "rep": task["rep"],
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


def print_table(rows: list[dict[str, Any]], objective: str, show_seeds: bool) -> None:
    # Columns after the delta are diagnostics; the delta keeps the gutter
    # and the only strong colour so the eye still lands there first.
    headers = ["LEVEL", "PLANNER", "ACTS", "SCORE", DELTA_HEAD]
    if show_seeds:
        headers += ["SEED", "SPREAD"]
    headers += ["STATUS"]

    body = []
    for r in rows:
        if not r["ok"]:
            cells = [r["level"], r["planner"], "-", "-", "-"]
            if show_seeds:
                cells += ["-", "-"]
            body.append(cells + ["error"])
            continue
        dtext, _ = fmt_delta(r["delta"], r["improved"])
        status = "improved" if r["improved"] else ("invalid" if not r["valid"] else "")
        if r.get("n_failed"):
            status = (status + " " if status else "") + f"({r['n_failed']} failed)"
        cells = [r["level"], r["planner"], str(r["n_actions"]),
                 fmt_score(r["score"]), dtext]
        if show_seeds:
            spread = r.get("spread") or 0.0
            cells += [str(r["seed"]), f"{spread:,.0f}" if spread else "0"]
        body.append(cells + [status])

    ncols = len(headers)
    widths = [max(len(h), *(len(row[i]) for row in body)) if body else len(h)
              for i, h in enumerate(headers)]
    right = {2, 3, 4} | ({5, 6} if show_seeds else set())

    def line(cells: list[str], deco: list[str]) -> str:
        rendered = []
        for i, cell in enumerate(cells):
            txt = cell.rjust(widths[i]) if i in right else cell.ljust(widths[i])
            if deco[i]:
                txt = c(txt, *deco[i].split(";"))
            rendered.append(txt)
        # gutter sits between SCORE and the delta
        return "  ".join(rendered[:4]) + c(GUTTER, DIM) + "  " + "  ".join(rendered[4:])

    head_deco = [DIM] * ncols
    head_deco[4] = f"{BOLD};{CYAN}"
    print(line(headers, head_deco))

    for r, cells in zip(rows, body):
        deco = [""] * ncols
        if not r["ok"]:
            deco = [DIM] * ncols
            deco[4] = deco[-1] = RED
        else:
            deco[3] = DIM  # the totals all look alike; mute them
            _, dcodes = fmt_delta(r["delta"], r["improved"])
            deco[4] = dcodes
            if show_seeds:
                deco[5] = DIM
                deco[6] = DIM if not r.get("spread") else ""
            if r["improved"]:
                deco[-1] = GREEN
            elif not r["valid"]:
                deco[-1] = f"{BOLD};{RED}"
        print(line(cells, deco))

    for r in rows:
        for err in (r.get("errors") or ([r["error"]] if r.get("error") else [])):
            print(c(f"  ! {r['level']}/{r['planner']}: {err}", RED))
        if r.get("violations"):
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
    p.add_argument("--seeds", type=int, default=0, metavar="N",
                   help=f"independent solves per planner per level, different seeds, "
                        f"best kept (default: core count = {os.cpu_count() or 4})")
    p.add_argument("--jobs", type=int, default=0, help="worker processes (default: cpu count)")
    p.add_argument("--compare", metavar="A,B",
                   help="paired A/B: run these planners on identical seeds and report "
                        "whether the difference beats the seed noise")
    p.add_argument("--minimise", action="store_true", help="lower score is better")
    p.add_argument("--dry-run", action="store_true", help="score and print, write nothing")
    p.add_argument("--no-trace", action="store_true", help="skip writing traces/")
    p.add_argument("--serial", action="store_true", help="one process (for debugging planners)")
    return p.parse_args(argv)


def collapse_replicates(
    results: list[dict[str, Any]], objective: str
) -> list[dict[str, Any]]:
    """Reduce N seeds per (level, planner) to the one that won.

    Carries the spread with it. Spread is the number that tells you
    whether more seeds would pay: a wide spread means the planner is
    luck-dependent and the fleet is earning its keep, a spread of zero
    means every seed found the same answer and the other N-1 cores were
    wasted.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in results:
        groups.setdefault((r["level"], r["planner"]), []).append(r)

    rows = []
    for key in sorted(groups, key=lambda k: (len(k[0]), k[0], k[1])):
        reps = sorted(groups[key], key=lambda r: r["rep"])
        pool = [r for r in reps if r["ok"] and r["valid"]] or [r for r in reps if r["ok"]]
        if pool:
            pick = max if objective == "max" else min
            # reps are sorted by index, so ties go to the lowest replicate
            # and the same command always reports the same winner.
            best = pick(pool, key=lambda r: r["score"])
        else:
            best = reps[0]

        scores = [r["score"] for r in pool]
        row = dict(best)
        row["seeds_tried"] = len(reps)
        row["seeds_ok"] = len(pool)
        row["spread"] = (max(scores) - min(scores)) if len(scores) > 1 else 0.0
        row["worst"] = min(scores) if scores else None
        row["errors"] = sorted({r["error"] for r in reps if r["error"]})
        row["n_failed"] = len(reps) - len(pool)
        rows.append(row)
    return rows


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
    n_seeds = max(1, args.seeds or (os.cpu_count() or 4))

    compare = expand([args.compare]) if args.compare else None
    if compare:
        if len(compare) < 2:
            print("--compare needs at least two planners, e.g. --compare climb,anneal",
                  file=sys.stderr)
            return 2
        unknown = [p for p in compare if p not in planners.REGISTRY]
        if unknown:
            print(f"unknown planner(s): {', '.join(unknown)}. "
                  f"registered: {', '.join(sorted(planners.REGISTRY))}", file=sys.stderr)
            return 2
        want_planners = compare

    tasks = []
    for lv in levels:
        for name in planners.available(lv):
            if want_planners and name not in want_planners:
                continue
            for rep in range(n_seeds):
                tasks.append({
                    "level": lv,
                    "planner": name,
                    "rep": rep,
                    # In compare mode the planner is left out of the seed
                    # derivation so both sides see identical seeds.
                    "seed": derive_seed(args.seed, lv, None if compare else name, rep),
                    "budget_s": args.time,
                    # Only the winning replicate's trace is kept, but we
                    # cannot know which that is until they have all run.
                    "keep_trace": not args.no_trace,
                })
    if not tasks:
        print("nothing to run: no planner matched those levels", file=sys.stderr)
        return 2

    jobs = args.jobs or min(len(tasks), os.cpu_count() or 4)
    waves = -(-len(tasks) // max(1, jobs))  # ceiling division

    formula = scoring.load_formula()
    banner = (f"{len(tasks)} task(s)  levels={','.join(levels)}  budget={args.time:g}s  "
              f"seeds={n_seeds}  seed={args.seed}  objective={objective}")
    print(c(banner, DIM))
    if compare:
        need = seeds_for_verdict()
        print(c(f"  paired compare: {' vs '.join(compare)} on identical seeds", DIM))
        if n_seeds < need:
            print(c(f"! {n_seeds} seeds cannot reach p<=0.05 even if every seed agrees "
                    f"(best possible p={2 / 2**n_seeds:.3f}). Use --seeds {need} or more "
                    f"for a verdict.", BOLD, YELLOW))
    if waves > 1 and not args.serial:
        # With N seeds the task count outruns the core count, so wall clock
        # is no longer roughly --time. Say so before they wander off.
        print(c(f"  {jobs} worker(s), {waves} wave(s) -- up to ~{waves * args.time:g}s wall",
                DIM))
    if not formula.verified:
        print(c(f"! formula is UNVERIFIED ({formula.source}) -- local scores are not "
                f"comparable to the portal. Run tools/fit.py.", BOLD, YELLOW))

    def failed(t: dict[str, Any], why: str) -> dict[str, Any]:
        return {
            **{k: t[k] for k in ("level", "planner", "seed", "rep", "budget_s")},
            "ok": False, "error": why, "score": None, "components": {},
            "valid": True, "violations": [], "plan": None, "n_actions": 0,
            "elapsed_s": 0.0, "trace": None,
        }

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    if args.serial:
        results = [solve_one(t) for t in tasks]
    else:
        overall = waves * (args.time + GRACE_S) + 60
        with cf.ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(solve_one, t): t for t in tasks}
            done = set()
            try:
                for fut in cf.as_completed(futures, timeout=overall):
                    t = futures[fut]
                    done.add(id(fut))
                    try:
                        results.append(fut.result(timeout=GRACE_S))
                    except Exception as e:
                        results.append(failed(t, f"worker: {type(e).__name__}: {e}"))
            except cf.TimeoutError:
                # Whatever finished still counts -- a stuck replicate must
                # not throw away the ones that succeeded.
                for fut, t in futures.items():
                    if id(fut) not in done:
                        fut.cancel()
                        results.append(failed(t, f"timed out after {overall:.0f}s"))

    results.sort(key=lambda r: (len(r["level"]), r["level"], r["planner"], r["rep"]))
    rows = collapse_replicates(results, objective)

    # Compare and persist. One lock, one read-modify-write per improvement.
    stamp = record.utcstamp()
    n_improved = 0
    with record.lock():
        current = record.load()
        for r in rows:
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
        for r in rows:
            if not r["improved"]:
                continue
            cur = by_level.get(r["level"])
            if cur is None or record.is_improvement(r["score"], cur["score"], objective):
                by_level[r["level"]] = r
        for r in rows:
            if r["improved"] and by_level.get(r["level"]) is not r:
                r["improved"] = False

        # With no prior record, "NEW" only makes sense for the row that
        # became the record. Everything else on that level is measured
        # against the winner, so the column always answers the same
        # question: how far off the pace is this?
        for r in rows:
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
                # The seed that actually produced this plan, plus enough to
                # regenerate it: replay with --seed <base_seed> --seeds
                # <seeds_tried>, or go straight at it with the derived seed.
                "seed": r["seed"],
                "base_seed": args.seed,
                "seed_index": r["rep"],
                "seeds_tried": r["seeds_tried"],
                "seed_spread": r["spread"],
                # Which derivation produced `seed` -- compare mode leaves
                # the planner out, so the two modes give different seeds
                # for the same (level, rep) and the record must say which.
                "seed_mode": "paired" if compare else "per-planner",
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
            # One line per (level, planner): the winning seed plus the
            # spread across the fleet, so the history shows both whether a
            # level is still climbing and whether seeds are still buying
            # anything.
            for r in rows:
                record.append_history({
                    "timestamp": stamp, "level": r["level"], "planner": r["planner"],
                    "score": r["score"], "delta": r["delta"], "improved": r["improved"],
                    "best_before": r["best_before"], "budget_s": r["budget_s"],
                    "seed": r["seed"], "seed_index": r["rep"],
                    "seeds_tried": r["seeds_tried"], "seeds_ok": r["seeds_ok"],
                    "spread": r["spread"], "worst": r["worst"],
                    "valid": r.get("valid", True),
                    "elapsed_s": r["elapsed_s"], "error": r["error"],
                    "formula_verified": formula.verified,
                })

    if not args.no_trace and not args.dry_run:
        # Only the winning replicate -- the losers share a filename and
        # would just overwrite each other.
        _paths.TRACES.mkdir(parents=True, exist_ok=True)
        for r in rows:
            if r.get("trace"):
                record.write_json_atomic(
                    _paths.TRACES / f"level{r['level']}.{r['planner']}.json", r["trace"])

    print()
    print_table(rows, objective, show_seeds=n_seeds > 1)
    print()
    if compare:
        print_compare(paired_compare(results, compare[0], compare[1:], objective), n_seeds)
        # A search cut off by the clock did a machine-dependent number of
        # iterations, so re-running the comparison can flip it. Say so --
        # otherwise a verdict looks firmer than it is.
        timed_out = sorted({
            r["planner"] for r in results
            if r["ok"] and (r.get("plan") or {}).get("meta", {})
            .get("search", {}).get("stopped_by") == "budget"
        })
        if timed_out:
            print(c(f"! {', '.join(timed_out)} hit the time budget, not an iteration "
                    f"cap -- iteration counts vary with machine load, so this verdict "
                    f"is not exactly reproducible. Pass max_iters in the planner for a "
                    f"comparison that is.", YELLOW))
            print()
    msg = (f"{n_improved} improved  {BULLET}  {len(tasks)} solve(s) across {n_seeds} seed(s)"
           f"  {BULLET}  {time.monotonic() - started:.1f}s wall")
    if args.dry_run:
        msg += f"  {BULLET}  DRY RUN, nothing written"
    print(c(msg, DIM))

    # A planner whose seeds all agree is deterministic: the other N-1
    # cores did nothing. Spend them on a level that is still moving.
    if n_seeds > 1:
        flat = [f"{r['level']}/{r['planner']}" for r in rows
                if r["ok"] and r["seeds_ok"] > 1 and not r["spread"]]
        if flat:
            print(c(f"! zero spread across {n_seeds} seeds: {', '.join(flat)} -- "
                    f"deterministic, so --seeds is buying nothing here", YELLOW))

    # Standing reminder: a level with no recorded score is worth more than
    # any optimisation of a level that already has one.
    final = record.load()
    missing = [lv for lv in level_ids() if lv not in final["levels"]]
    zeroed = [lv for lv, e in sorted(final["levels"].items()) if e.get("score") == 0]
    broken = sorted({r["level"] for r in rows if not r["ok"]})
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
