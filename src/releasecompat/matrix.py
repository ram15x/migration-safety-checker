"""Builds the compatibility matrix from a configuration.

A rolling deploy moves the database through a series of states while old and
new application instances overlap. For each state we decide which application
versions can legitimately be running, and those are the checks that must pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config, Phase

OLD = "old"
NEW = "new"
ROLLBACK = "rollback"
FORWARD = "forward"


@dataclass
class State:
    name: str
    applied: list[Phase] = field(default_factory=list)
    partial: Phase | None = None
    old_required: bool = True
    new_required: bool = True


@dataclass
class Check:
    id: str
    state: State
    kind: str
    required: bool
    note: str = ""


def build_states(cfg: Config) -> list[State]:
    states = [
        State(
            name="baseline",
            old_required=True,
            new_required=cfg.new_on_baseline,
        )
    ]
    for i, phase in enumerate(cfg.phases):
        if phase.partial_sql or phase.partial_run:
            states.append(
                State(
                    name=f"partial:{phase.name}",
                    applied=cfg.phases[:i],
                    partial=phase,
                    old_required=phase.old_may_run,
                    new_required=phase.new_may_run,
                )
            )
        states.append(
            State(
                name=f"after:{phase.name}",
                applied=cfg.phases[: i + 1],
                old_required=phase.old_may_run,
                new_required=phase.new_may_run,
            )
        )
    return states


def build_checks(cfg: Config) -> list[Check]:
    checks: list[Check] = []
    for state in build_states(cfg):
        checks.append(
            Check(
                id=f"{OLD}@{state.name}",
                state=state,
                kind=OLD,
                required=state.old_required,
                note="" if state.old_required else "old version is retired in this state",
            )
        )
        checks.append(
            Check(
                id=f"{NEW}@{state.name}",
                state=state,
                kind=NEW,
                required=state.new_required,
                note="" if state.new_required else "new version is not deployed in this state",
            )
        )
        both = state.old_required and state.new_required
        if not cfg.forward:
            fnote = "forward checks are disabled"
        elif not both:
            fnote = "old and new versions do not overlap in this state"
        elif cfg.old.write is None:
            fnote = "apps.old.write is not configured"
        else:
            fnote = ""
        checks.append(
            Check(
                id=f"{FORWARD}@{state.name}",
                state=state,
                kind=FORWARD,
                required=cfg.forward and both and cfg.old.write is not None,
                note=fnote,
            )
        )
        can_roll_back = cfg.rollback and both
        if not cfg.rollback:
            note = "rollback checks are disabled"
        elif not (state.old_required and state.new_required):
            note = "rollback is not possible in this state"
        elif cfg.new.write is None:
            note = "apps.new.write is not configured"
        else:
            note = ""
        checks.append(
            Check(
                id=f"{ROLLBACK}@{state.name}",
                state=state,
                kind=ROLLBACK,
                required=can_roll_back and cfg.new.write is not None,
                note=note,
            )
        )
    return checks
