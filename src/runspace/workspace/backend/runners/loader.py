"""Load `<project>/runners.yml` and instantiate Runner objects."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

import yaml

from .ab import ABConfig, ABRunner, Variant
from .base import Runner
from .workload import WorkloadConfig, WorkloadRunner

log = logging.getLogger(__name__)


_BUILTIN_SCORERS: dict[str, str] = {
    "token_match": "runspace.workspace.backend.scoring.match.TokenMatchScorer",
    "regex_match": "runspace.workspace.backend.scoring.match.RegexMatchScorer",
    # "llm_judge"  — added when the LLM-judge scorer lands
}


def _load_scorer(name_or_path: str | None, project_root: Path) -> Any | None:
    """Resolve a scorer string to an instance. Returns None if blank."""
    if not name_or_path:
        return None
    # 1. Built-in?
    if name_or_path in _BUILTIN_SCORERS:
        modpath, _, cls = _BUILTIN_SCORERS[name_or_path].rpartition(".")
        mod = __import__(modpath, fromlist=[cls])
        return getattr(mod, cls)()
    # 2. Project-local file?
    p = (project_root / name_or_path).resolve()
    try:
        p.relative_to(project_root.resolve())
    except ValueError:
        raise ValueError(f"scorer path escapes project root: {name_or_path}")
    if p.is_file() and p.suffix == ".py":
        spec = importlib.util.spec_from_file_location(f"_scorer_{p.stem}", p)
        if not spec or not spec.loader:
            raise ImportError(f"cannot load scorer module: {p}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Convention: file exports a Scorer subclass named `Scorer` or
        # the user names it whatever; fall back to the first Scorer
        # subclass we find.
        from ..scoring.base import Scorer as _ScorerBase

        candidates = [
            v
            for v in vars(mod).values()
            if isinstance(v, type) and issubclass(v, _ScorerBase) and v is not _ScorerBase
        ]
        if not candidates:
            raise ImportError(f"no Scorer subclass found in {p}")
        return candidates[0]()
    raise ValueError(f"unknown scorer: {name_or_path!r}")


def load_runners(runners_yml_path: Path, registry: Any) -> dict[str, Runner]:
    """Parse runners.yml, instantiate every runner, return name→runner."""
    if not runners_yml_path.exists():
        return {}
    with open(runners_yml_path) as f:
        cfg = yaml.safe_load(f) or {}
    project_root = runners_yml_path.parent.resolve()
    out: dict[str, Runner] = {}
    for name, spec in (cfg.get("runners") or {}).items():
        if not isinstance(spec, dict):
            log.warning("runners.yml: %s is not a dict, skipping", name)
            continue
        rtype = spec.get("type")
        scorer = _load_scorer(spec.get("scorer"), project_root)
        if rtype == "workload":
            out[name] = WorkloadRunner(
                WorkloadConfig(
                    name=name,
                    file=spec["file"],
                    app_id=spec["app_id"],
                    scorer=spec.get("scorer"),
                    description=spec.get("description", ""),
                ),
                registry,
                scorer=scorer,
            )
        elif rtype == "ab":
            variants = [
                Variant(name=v["name"], overrides=v.get("overrides") or {})
                for v in (spec.get("variants") or [])
            ]
            if not variants:
                log.warning("runners.yml: %s (ab) has no variants", name)
                continue
            out[name] = ABRunner(
                ABConfig(
                    name=name,
                    file=spec["file"],
                    app_id=spec["app_id"],
                    variants=variants,
                    scorer=spec.get("scorer"),
                    description=spec.get("description", ""),
                ),
                registry,
                scorer=scorer,
            )
        else:
            log.warning("runners.yml: unknown runner type %r for %s", rtype, name)
    return out
