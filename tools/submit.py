#!/usr/bin/env python3
"""Emit every submission from best/, package the source, say what to upload.

    python tools/submit.py                  # emit + verify + package
    python tools/submit.py --emit-only      # just rewrite the .txt files
    python tools/submit.py --package-only   # just rebuild the zip
    python tools/submit.py --level 2        # one level

No solving. This reads the plans run.py already recorded, so it is
instant -- which matters twice:

  - When you discover the submission format is wrong at 12:00, fix
    `Plan.to_submission_text()` and re-emit. One edit, no re-solve.
  - When you want to be sure the .txt on disk is the plan you think it
    is, rather than something a half-finished run left behind.

It verifies three things that go stale silently, each with its own fix:

  plan hash   best/ disagrees with best-known.json -> files out of sync
  components  re-simulating the plan gives different facts -> the engine
              changed since this was recorded
  score       re-scoring the recorded components gives a different total
              -> the formula changed since this was recorded

That last one bites hardest: the moment `fit.py --write` lands a real
formula, every score recorded under the placeholder is fiction. This is
what tells you.

The zip is deterministic -- fixed timestamps, sorted entries -- so the
same code always produces byte-identical output and you can tell whether
anything actually changed.
"""

# isort: skip_file -- `import _paths` puts src/ on sys.path and MUST stay above
# the engine/scoring/planners imports. Sorting this block breaks the tool.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import _paths
import record

from engine import load_level, simulate
from engine.level import level_ids
from engine.model import Plan, sha256_of
import scoring

DIST = _paths.ROOT / "dist"
DEFAULT_ZIP = DIST / "hackit.zip"

# Zip's epoch. A fixed timestamp is what makes the archive reproducible;
# real mtimes would change the bytes on every rebuild.
FIXED_TIME = (1980, 1, 1, 0, 0, 0)

SOURCE_DIRS = ("src", "tools")
SOURCE_FILES = ("CLAUDE.md", "README.md", "pyproject.toml", ".gitignore")
EXTRA_FILES = ("formula.json", "best-known.json")

# Never package: caches, VCS, and the organisers' own material -- their
# spec and level files are not yours to redistribute, and they are the
# bulk of the bytes.
EXCLUDE_DIRS = {"__pycache__", ".git", ".venv", "venv", ".ruff_cache",
                ".pytest_cache", ".mypy_cache", "dist", "traces", "log", "docs"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd", ".tmp", ".lock", ".zip"}

SIZE_WARN_MB = 25.0

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
DIM, BOLD, RED, GREEN, YELLOW = "2", "1", "31", "32", "33"


def c(text: str, *codes: str) -> str:
    if not _COLOR or not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


# --------------------------------------------------------------------------
# emit
# --------------------------------------------------------------------------


def emit_level(level_id: str, rec: dict[str, Any], verify: bool) -> dict[str, Any]:
    """Rewrite one submission from best/ and check it is still true."""
    out: dict[str, Any] = {
        "level": level_id, "status": "ok", "notes": [],
        "score": None, "n_actions": 0, "bytes": 0, "path": None, "sha": None,
    }
    path = _paths.best_plan_path(level_id)
    if not path.is_file():
        out["status"] = "missing"
        out["notes"].append("no best/ plan -- run tools/run.py first")
        return out

    try:
        saved = json.loads(path.read_text())
        plan = Plan.from_dict(saved["plan"])
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        out["status"] = "broken"
        out["notes"].append(f"{path.name} unreadable: {type(e).__name__}: {e}")
        return out

    out["n_actions"] = len(plan.actions)
    out["score"] = saved.get("score")
    out["sha"] = plan.sha256()

    text = plan.to_submission_text()
    dest = _paths.submission_path(level_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    previous = dest.read_text() if dest.is_file() else None
    dest.write_text(text, newline="\n")
    out["path"] = dest
    out["bytes"] = len(text.encode())
    if previous is not None and previous != text:
        out["notes"].append("submission text changed since last emit")
    if not text:
        out["notes"].append("empty submission -- valid, but check the portal accepts it")

    if not verify:
        return out

    entry = rec["levels"].get(level_id, {})

    # 1. Are best/ and best-known.json talking about the same plan?
    recorded_sha = entry.get("plan_sha256")
    if recorded_sha and recorded_sha != out["sha"]:
        out["status"] = "desync"
        out["notes"].append(
            "best/ plan does not match best-known.json's plan_sha256 -- one of them "
            "was written by a run that did not finish, or edited by hand")
        return out

    # 2. Does the engine still produce the same facts for this plan?
    try:
        trace = simulate(load_level(level_id), plan)
    except Exception as e:
        out["status"] = "stale"
        out["notes"].append(f"re-simulation failed: {type(e).__name__}: {e}")
        return out
    if not trace.valid:
        out["status"] = "invalid"
        out["notes"].append(
            f"the engine now calls this plan invalid: {'; '.join(trace.violations[:3])}")
        return out

    fresh = dict(sorted(trace.components.items()))
    old_components = entry.get("components") or saved.get("components")
    if old_components and {k: float(v) for k, v in fresh.items()} != {
            k: float(v) for k, v in old_components.items()}:
        out["status"] = "stale"
        out["notes"].append(
            "the engine produces different components for this plan than when it was "
            "recorded -- the recorded score is no longer meaningful; re-run run.py")

    # 3. Does the current formula still give the recorded score?
    try:
        fresh_score = scoring.score(trace.components)
    except scoring.UnknownComponent as e:
        out["status"] = "stale"
        out["notes"].append(f"formula cannot score this plan: {e}")
        return out
    out["rescored"] = fresh_score
    if out["score"] is not None and abs(fresh_score - float(out["score"])) > 1e-6:
        if out["status"] == "ok":
            out["status"] = "stale"
        out["notes"].append(
            f"recorded {float(out['score']):,.0f} but the current formula gives "
            f"{fresh_score:,.0f} -- scored under a different formula; re-run run.py "
            f"so the records and the leaderboard agree")
    return out


# --------------------------------------------------------------------------
# package
# --------------------------------------------------------------------------


def collect_files(include_best: bool, include_inputs: bool,
                  include_submissions: bool) -> list[tuple[str, Path]]:
    """(archive name, source path) pairs, sorted. Sorted order is part of
    what makes the zip reproducible."""
    excluded = set(EXCLUDE_DIRS)
    if include_inputs:
        excluded -= {"docs"}
    found: list[tuple[str, Path]] = []

    def walk(base: Path) -> None:
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            parts = set(path.relative_to(_paths.ROOT).parts)
            if parts & excluded or path.suffix.lower() in EXCLUDE_SUFFIXES:
                continue
            found.append((path.relative_to(_paths.ROOT).as_posix(), path))

    for d in SOURCE_DIRS:
        if (_paths.ROOT / d).is_dir():
            walk(_paths.ROOT / d)
    for name in SOURCE_FILES + EXTRA_FILES:
        p = _paths.ROOT / name
        if p.is_file():
            found.append((name, p))
    if include_submissions and _paths.SUBMISSIONS.is_dir():
        walk(_paths.SUBMISSIONS)
    if include_best and _paths.BEST.is_dir():
        walk(_paths.BEST)
    if include_inputs:
        for d in (_paths.LEVELS, _paths.DOCS):
            if d.is_dir():
                walk(d)

    return sorted(set(found))


def manifest_hash(files: list[tuple[str, Path]]) -> str:
    """Content hash of the whole payload. Lets a rebuild that would
    produce identical bytes skip the work and say so."""
    h = hashlib.sha256()
    for name, path in files:
        h.update(name.encode())
        h.update(b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def build_zip(out_path: Path, files: list[tuple[str, Path]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(f"{out_path.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for name, path in files:
                info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, path.read_bytes())
        os.replace(tmp, out_path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--level", action="append", metavar="ID",
                    help="restrict emit to a level; repeatable or comma-separated")
    ap.add_argument("--emit-only", action="store_true")
    ap.add_argument("--package-only", action="store_true")
    ap.add_argument("--out", type=Path, default=DEFAULT_ZIP)
    ap.add_argument("--no-verify", action="store_true",
                    help="skip re-simulation and re-scoring (emit is already fast)")
    ap.add_argument("--include-best", action="store_true",
                    help="put best/*.json in the zip (full plans, can be large)")
    ap.add_argument("--include-inputs", action="store_true",
                    help="put levels/ and docs/ in the zip -- the organisers' own "
                         "material; off by default")
    ap.add_argument("--no-submissions", action="store_true",
                    help="leave submissions/ out of the zip")
    ap.add_argument("--force", action="store_true",
                    help="rebuild the zip even if the contents are unchanged")
    args = ap.parse_args(argv)

    started = time.monotonic()
    rc = 0

    if not args.package_only:
        levels = []
        for v in args.level or []:
            levels.extend(part.strip() for part in v.split(",") if part.strip())
        levels = levels or level_ids()

        rec = record.load()
        formula = scoring.load_formula()
        if not formula.verified:
            print(c(f"! formula is UNVERIFIED ({formula.source}) -- these scores are "
                    f"not comparable to the portal's", BOLD, YELLOW))

        results = [emit_level(lv, rec, verify=not args.no_verify) for lv in levels]

        print(c(f"{'LEVEL':<6} {'ACTS':>7} {'SCORE':>18} {'BYTES':>9}  STATUS", DIM))
        for r in results:
            codes = {"ok": GREEN, "missing": YELLOW, "stale": f"{BOLD};{YELLOW}",
                     "desync": f"{BOLD};{RED}", "broken": f"{BOLD};{RED}",
                     "invalid": f"{BOLD};{RED}"}[r["status"]]
            score = f"{float(r['score']):,.0f}" if r["score"] is not None else "-"
            print(f"{r['level']:<6} {r['n_actions']:>7,} {score:>18} "
                  f"{r['bytes']:>9,}  {c(r['status'], *codes.split(';'))}")
        for r in results:
            for note in r["notes"]:
                print(c(f"  ! level {r['level']}: {note}", YELLOW))

        ok = [r for r in results if r["status"] == "ok"]
        bad = [r for r in results if r["status"] not in ("ok",)]
        print()
        print(f"{len(ok)}/{len(results)} submission(s) ready in "
              f"{_paths.SUBMISSIONS.relative_to(_paths.ROOT)}/")
        if bad:
            rc = 1

    if not args.emit_only:
        files = collect_files(
            include_best=args.include_best,
            include_inputs=args.include_inputs,
            include_submissions=not args.no_submissions,
        )
        if not files:
            print(c("nothing to package", RED))
            return 1

        digest = manifest_hash(files)
        stamp_path = args.out.with_suffix(args.out.suffix + ".sha256")
        previous = stamp_path.read_text().strip() if stamp_path.is_file() else None

        if previous == digest and args.out.is_file() and not args.force:
            size = args.out.stat().st_size
            print(f"\n{args.out.relative_to(_paths.ROOT)} unchanged "
                  f"({len(files)} files, {size / 1024:.0f} KB) -- nothing to rebuild")
        else:
            build_zip(args.out, files)
            stamp_path.write_text(digest + "\n")
            size = args.out.stat().st_size
            print(f"\nwrote {args.out.relative_to(_paths.ROOT)} "
                  f"({len(files)} files, {size / 1024:.0f} KB)")
            if size / 1e6 > SIZE_WARN_MB:
                print(c(f"! {size / 1e6:.1f} MB -- portals often cap upload size; "
                        f"consider dropping --include-best/--include-inputs", YELLOW))

    print(c(f"\n{time.monotonic() - started:.2f}s", DIM))
    if rc:
        print(c("some submissions are not ready -- see the notes above", BOLD, YELLOW))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
