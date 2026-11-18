"""Configuration loading and validation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Raised when a configuration file is missing, malformed or inconsistent."""


@dataclass
class AppCommands:
    """Shell commands that exercise one application version.

    ``test`` must exit 0 when the application works against the current database.
    ``write`` makes the application write data through its own code path. It is
    used by the rollback check: the new version writes, then the old version reads.
    ``read`` verifies the application can read the data already in the database.
    The forward and rollback checks run it after the other version's write; when it
    is absent they fall back to ``test``, which suits suites that tolerate existing rows.
    """

    test: str | None = None
    write: str | None = None
    read: str | None = None


@dataclass
class Phase:
    """One step of the deployment: a set of schema or data changes."""

    name: str
    sql: list[str]
    partial_sql: list[str] = field(default_factory=list)
    run: list[str] = field(default_factory=list)
    partial_run: list[str] = field(default_factory=list)
    old_may_run: bool = True
    new_may_run: bool = True


@dataclass
class Invariant:
    """A query that returns violating rows. Any returned row fails the check."""

    name: str
    sql: str


@dataclass
class Config:
    name: str
    root: Path
    admin_url: str
    baseline_sql: list[str]
    baseline_run: list[str]
    phases: list[Phase]
    old: AppCommands
    new: AppCommands
    invariants: list[Invariant] = field(default_factory=list)
    rollback: bool = True
    forward: bool = True
    live: bool = True
    live_rows: int = 2
    new_on_baseline: bool = False
    timeout: int = 120


_ENV = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _expand(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        var, default = match.group(1), match.group(2)
        found = os.environ.get(var)
        if found is not None:
            return found
        if default is not None:
            return default
        raise ConfigError(f"environment variable {var} is not set and has no default")

    return _ENV.sub(repl, value)


def _as_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ConfigError(f"{where} must be a path or a list of paths")


def _commands(raw: Any, where: str) -> AppCommands:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a mapping with 'test' and optionally 'write'")
    if not raw.get("test"):
        raise ConfigError(f"{where}.test is required")
    return AppCommands(test=str(raw["test"]), write=raw.get("write"), read=raw.get("read"))


def load(path: str | os.PathLike[str]) -> Config:
    file = Path(path)
    if not file.is_file():
        raise ConfigError(f"config file not found: {file}")
    try:
        raw = yaml.safe_load(file.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {file}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    root = file.resolve().parent

    database = raw.get("database") or {}
    admin_url = _expand(str(database.get("admin_url", "${MIGRATION_SAFETY_CHECKER_ADMIN_URL}")))

    baseline = _as_list((raw.get("baseline") or {}).get("sql"), "baseline.sql")
    baseline_run = _as_list((raw.get("baseline") or {}).get("run"), "baseline.run")
    if not baseline and not baseline_run:
        raise ConfigError(
            "baseline.sql or baseline.run is required: the schema and data of the old release"
        )

    phases_raw = raw.get("phases")
    if not isinstance(phases_raw, list) or not phases_raw:
        raise ConfigError("phases must be a non-empty list")
    phases: list[Phase] = []
    seen: set[str] = set()
    for i, item in enumerate(phases_raw):
        if not isinstance(item, dict) or "name" not in item:
            raise ConfigError(f"phases[{i}] needs a name")
        name = str(item["name"])
        if name in seen:
            raise ConfigError(f"duplicate phase name: {name}")
        seen.add(name)
        sql = _as_list(item.get("sql"), f"phases[{i}].sql")
        run = _as_list(item.get("run"), f"phases[{i}].run")
        if not sql and not run:
            raise ConfigError(f"phases[{i}] needs sql or run")
        phases.append(
            Phase(
                name=name,
                sql=sql,
                partial_sql=_as_list(item.get("partial_sql"), f"phases[{i}].partial_sql"),
                run=run,
                partial_run=_as_list(item.get("partial_run"), f"phases[{i}].partial_run"),
                old_may_run=bool(item.get("old_may_run", True)),
                new_may_run=bool(item.get("new_may_run", True)),
            )
        )

    apps = raw.get("apps") or {}
    old = _commands(apps.get("old"), "apps.old")
    new = _commands(apps.get("new"), "apps.new")

    invariants: list[Invariant] = []
    for i, item in enumerate(raw.get("invariants") or []):
        if not isinstance(item, dict) or "name" not in item or "sql" not in item:
            raise ConfigError(f"invariants[{i}] needs name and sql")
        invariants.append(Invariant(name=str(item["name"]), sql=str(item["sql"])))

    cfg = Config(
        name=str(raw.get("name", file.stem)),
        root=root,
        admin_url=admin_url,
        baseline_sql=baseline,
        baseline_run=baseline_run,
        phases=phases,
        old=old,
        new=new,
        invariants=invariants,
        rollback=bool(raw.get("rollback", True)),
        forward=bool(raw.get("forward", True)),
        live=bool(raw.get("live", True)),
        live_rows=int(raw.get("live_rows", 2)),
        new_on_baseline=bool(raw.get("new_on_baseline", False)),
        timeout=int(raw.get("timeout", 120)),
    )

    if cfg.live_rows < 1:
        raise ConfigError("live_rows must be at least 1")

    for rel in cfg.baseline_sql + [p for ph in phases for p in ph.sql + ph.partial_sql]:
        if not (root / rel).is_file():
            raise ConfigError(f"SQL file not found: {rel}")
    return cfg
