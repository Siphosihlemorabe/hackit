"""best-known.json: read-modify-write, atomically, one level at a time.

Two failure modes this guards against, both of which lose you a scored
plan you cannot cheaply recompute:

  1. A single-level run clobbering the other three records. Every write
     starts by re-reading the file on disk; only the touched level is
     replaced.
  2. A crash mid-write truncating the file. We write a temp file in the
     same directory, fsync it, then os.replace() -- atomic on Windows and
     POSIX. The real file is never open for writing.

History is a separate append-only .jsonl. Appends cannot truncate, and it
records every run, not just improvements, so a flat tail tells you a
level has plateaued.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import _paths

VERSION = 1


def utcstamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def empty_record(objective: str = "max") -> dict[str, Any]:
    return {"version": VERSION, "objective": objective, "levels": {}}


def load(path: Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else _paths.BEST_KNOWN
    if not p.is_file():
        return empty_record()
    try:
        rec = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"{p} is not valid JSON ({e}). It was not written by this tool -- "
            f"check for a stray edit before overwriting it."
        ) from None
    rec.setdefault("version", VERSION)
    rec.setdefault("objective", "max")
    rec.setdefault("levels", {})
    return rec


def write_json_atomic(path: Path, obj: Any) -> None:
    """Serialise fully, fsync, then rename over the target.

    Serialising to a string before opening the temp file means an
    unserialisable object raises before any file exists.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, sort_keys=True, indent=2) + "\n"
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            if tmp.exists():
                tmp.unlink()


@contextlib.contextmanager
def lock(path: Path | None = None, timeout: float = 30.0) -> Iterator[None]:
    """Cross-process lock, so two terminals running run.py cannot
    interleave a read-modify-write. Stale locks older than `timeout`
    are broken -- a killed run should not block the next one."""
    p = Path(path) if path else _paths.BEST_KNOWN
    lockfile = p.with_suffix(p.suffix + ".lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lockfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            age = time.monotonic() - deadline + timeout
            try:
                stale = (time.time() - lockfile.stat().st_mtime) > timeout
            except OSError:
                stale = False
            if stale:
                with contextlib.suppress(OSError):
                    lockfile.unlink()
                continue
            if time.monotonic() > deadline:
                raise SystemExit(
                    f"could not acquire {lockfile} after {age:.0f}s -- "
                    f"another run.py is writing, or delete the lock file"
                ) from None
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lockfile.unlink()


def is_improvement(new: float, old: float | None, objective: str) -> bool:
    if old is None:
        return True
    return new > old if objective == "max" else new < old


def update_level(
    level: str,
    entry: dict[str, Any],
    *,
    path: Path | None = None,
    objective: str = "max",
) -> dict[str, Any]:
    """Replace exactly one level's record. Every other level is carried
    through from whatever is on disk right now."""
    p = Path(path) if path else _paths.BEST_KNOWN
    rec = load(p)
    rec["objective"] = objective
    rec["levels"][str(level)] = entry
    write_json_atomic(p, rec)
    return rec


def append_history(row: dict[str, Any], path: Path | None = None) -> None:
    p = Path(path) if path else _paths.HISTORY
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
