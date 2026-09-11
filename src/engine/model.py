"""Data types shared by engine, scoring and planners.

Problem-agnostic on purpose. Nothing in here knows what an action *is* --
that gets filled in once the briefing lands. The only rule this module
enforces is determinism: every serialisation sorts its keys, so two runs
that computed the same thing produce byte-identical files.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# An action is opaque until the briefing. Keep it picklable (ints, strings,
# tuples, dataclasses) -- planners run in worker processes.
Action = Any

# Facts the engine measured. Never weighted, never summed. See scoring.py.
Components = dict[str, float]


def canon(obj: Any) -> str:
    """Canonical JSON: sorted keys, no incidental whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canon(obj).encode()).hexdigest()


@dataclass
class Level:
    """One problem instance, loaded from levels/."""

    id: str
    raw: str = ""
    data: Any = None
    path: Path | None = None

    @property
    def loaded(self) -> bool:
        return self.path is not None


@dataclass
class Plan:
    """An ordered list of actions. The thing a planner produces and the
    thing we upload. Actions stay opaque here."""

    level: str
    actions: list[Action] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.actions)

    def sha256(self) -> str:
        return sha256_of({"level": self.level, "actions": self.actions})

    def to_submission_text(self) -> str:
        """Render for upload.

        TODO(briefing): replace with the portal's exact submission format.
        This is the ONLY place that knows it -- run.py, gen_probes.py and
        submit.py all come through here, so a format fix at 12:00 costs
        one edit and a re-emit, not a re-solve.

        Check for: a required header, an action count on line 1, and
        whether the empty plan should be an empty file or a bare newline.
        We send an empty file; if the portal rejects it, that is the first
        thing to flip.
        """
        if not self.actions:
            return ""
        return "\n".join(str(a) for a in self.actions) + "\n"

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "actions": self.actions, "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Plan:
        return cls(level=d["level"], actions=list(d["actions"]), meta=dict(d.get("meta", {})))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> Plan:
        return cls.from_dict(json.loads(Path(path).read_text()))


@dataclass
class StepTrace:
    """State after applying action `i`. Components are cumulative, so a
    step-by-step diff against a portal log localises a divergence to one
    action rather than to the whole plan."""

    i: int
    action: Action
    components: Components


@dataclass
class Trace:
    """What the engine produced from replaying a plan. Facts only."""

    level: str
    plan_sha256: str
    components: Components = field(default_factory=dict)
    steps: list[StepTrace] = field(default_factory=list)
    valid: bool = True
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "plan_sha256": self.plan_sha256,
            "components": dict(sorted(self.components.items())),
            "valid": self.valid,
            "violations": list(self.violations),
            "steps": [
                {"i": s.i, "action": s.action, "components": dict(sorted(s.components.items()))}
                for s in self.steps
            ],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Trace:
        return cls(
            level=d["level"],
            plan_sha256=d.get("plan_sha256", ""),
            components=dict(d.get("components", {})),
            valid=bool(d.get("valid", True)),
            violations=list(d.get("violations", [])),
            steps=[
                StepTrace(i=s["i"], action=s.get("action"), components=dict(s["components"]))
                for s in d.get("steps", [])
            ],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> Trace:
        return cls.from_dict(json.loads(Path(path).read_text()))
