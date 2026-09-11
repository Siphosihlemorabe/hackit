"""Planners: the only place that decides anything.

A planner takes a Context (level, seed, deadline) and returns a Plan. It
must not score -- run.py scores it. It must not simulate outside the
engine. Keep the decision logic here and the facts in engine/.

Register with @register("name"). run.py discovers every module in this
package automatically, so a new file with a decorated function is enough.
"""

from __future__ import annotations

import importlib
import pkgutil
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from engine.model import Level, Plan

__all__ = ["Context", "Planner", "available", "load_all", "register", "REGISTRY"]


@dataclass
class Context:
    """Everything a planner is allowed to depend on.

    Notably absent: the wall clock, os.environ, and anything global. Use
    ctx.rng for randomness and ctx.time_left() for pacing, never
    time.time() or the bare random module.
    """

    level: Level
    seed: int
    budget_s: float
    _started: float = field(default_factory=time.monotonic)

    def time_left(self) -> float:
        return self.budget_s - (time.monotonic() - self._started)

    def expired(self) -> bool:
        return self.time_left() <= 0.0

    @property
    def rng(self) -> random.Random:
        if not hasattr(self, "_rng"):
            self._rng = random.Random(self.seed)
        return self._rng


Planner = Callable[[Context], Plan]


@dataclass(frozen=True)
class Spec:
    name: str
    fn: Planner
    levels: tuple[str, ...] | None  # None = every level


REGISTRY: dict[str, Spec] = {}


def register(name: str, *, levels: tuple[str, ...] | None = None) -> Callable[[Planner], Planner]:
    def deco(fn: Planner) -> Planner:
        if name in REGISTRY:
            raise ValueError(f"planner {name!r} registered twice")
        REGISTRY[name] = Spec(name=name, fn=fn, levels=levels)
        return fn

    return deco


def load_all() -> None:
    """Import every module in this package so decorators fire."""
    for mod in pkgutil.iter_modules(__path__):
        if not mod.name.startswith("_"):
            importlib.import_module(f"{__name__}.{mod.name}")


def available(level_id: str) -> list[str]:
    """Planner names that apply to a level, in stable order."""
    load_all()
    return sorted(
        name for name, spec in REGISTRY.items() if spec.levels is None or level_id in spec.levels
    )
