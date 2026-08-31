"""Session folder writer — one directory per experiment run, fully traceable."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SESSIONS_INDEX = Path.home() / ".runspace" / "sessions_index.jsonl"


def _git_state(repo: Path) -> dict[str, Any]:
    """Capture git revision + dirty flag + branch + diff. Best-effort — if the
    repo isn't a git checkout the fields are populated with placeholders so
    downstream tooling doesn't have to special-case missing values."""
    out: dict[str, Any] = {
        "rev": None,
        "branch": None,
        "dirty": False,
        "diff": "",
        "git_available": False,
    }
    try:
        rev = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=str(repo),
                stderr=subprocess.DEVNULL,
            )
            .decode("utf-8")
            .strip()
        )
        branch = (
            subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(repo),
                stderr=subprocess.DEVNULL,
            )
            .decode("utf-8")
            .strip()
        )
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(repo),
            stderr=subprocess.DEVNULL,
        ).decode("utf-8")
        diff = subprocess.check_output(
            ["git", "diff", "HEAD"],
            cwd=str(repo),
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="replace")
        out.update(
            {
                "rev": rev,
                "branch": branch,
                "dirty": bool(status.strip()),
                "diff": diff,
                "git_available": True,
            }
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    return out


@dataclass
class SessionWriter:
    project: str
    run_id: str
    name: str | None
    runtime: str | None
    model: str | None
    benchmark_id: str | None
    project_root: Path
    session_dir: Path
    started_at: int
    config: dict[str, Any] = field(default_factory=dict)
    _trial_count: int = 0
    _pass_count: int = 0
    _closed: bool = False

    def write_trace(
        self,
        task_id: str,
        trial_id: str,
        stdout: str,
        stderr: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        traces_dir = self.session_dir / "traces"
        traces_dir.mkdir(exist_ok=True)
        # Use task_id in filename for human readability; trial_id is stored
        # inside the file's first record so dedup-by-trial_id still works.
        safe_task = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)
        out_path = traces_dir / f"{safe_task}.jsonl"
        # Prepend a header line that names the trial_id so downstream
        # readers don't have to thread it.
        header = json.dumps(
            {
                "type": "_trial_header",
                "trial_id": trial_id,
                "task_id": task_id,
                "ingested_at": int(time.time()),
                "extra": extra or {},
            },
            ensure_ascii=False,
        )
        with open(out_path, "w") as f:
            f.write(header + "\n")
            f.write(stdout)
            if not stdout.endswith("\n"):
                f.write("\n")
        if stderr:
            (traces_dir / f"{safe_task}.stderr").write_text(stderr[:50000])

    def write_trial_result(
        self,
        task_id: str,
        trial_id: str,
        score: float,
        score_detail: list[str],
        elapsed_s: float,
        passed: bool,
        extra: dict[str, Any] | None = None,
    ) -> None:
        results_dir = self.session_dir / "results"
        results_dir.mkdir(exist_ok=True)
        safe_task = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)
        record = {
            "task_id": task_id,
            "trial_id": trial_id,
            "score": score,
            "score_detail": score_detail,
            "elapsed_s": elapsed_s,
            "passed": passed,
            **(extra or {}),
        }
        (results_dir / f"{safe_task}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2)
        )
        self._trial_count += 1
        if passed:
            self._pass_count += 1

    def close(self, *, status: str = "ok", extra: dict[str, Any] | None = None) -> None:
        if self._closed:
            return
        finished_at = int(time.time())
        summary = {
            "run_id": self.run_id,
            "project": self.project,
            "name": self.name,
            "runtime": self.runtime,
            "model": self.model,
            "benchmark_id": self.benchmark_id,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "elapsed_s": finished_at - self.started_at,
            "n_trials": self._trial_count,
            "n_passed": self._pass_count,
            "score_pct": (
                round(100.0 * self._pass_count / self._trial_count, 1)
                if self._trial_count
                else None
            ),
            "status": status,
            **(extra or {}),
        }
        (self.session_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2)
        )
        self._closed = True


def _slugify(s: str | None) -> str:
    if not s:
        return "run"
    out = "".join(c if c.isalnum() or c in "-_" else "-" for c in s).strip("-")
    return out[:60] or "run"


def start_session(
    *,
    project: str,
    project_root: str | Path,
    name: str | None = None,
    runtime: str | None = None,
    model: str | None = None,
    benchmark_id: str | None = None,
    config_files: list[str | Path] | None = None,
    env_capture_keys: list[str] | None = None,
) -> SessionWriter:
    """Initialize a fresh session folder under <project_root>/.sessions/.

    Captures: git rev + dirty + diff, snapshots of named config files,
    a config.json with run metadata + a non-secret env subset.
    """
    project_root = Path(project_root).resolve()
    started_at = int(time.time())
    run_id = f"{started_at}-{_slugify(name)}"
    session_dir = project_root / ".sessions" / run_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # Git state
    git = _git_state(project_root)
    rev_lines = [
        f"rev:    {git['rev'] or '(no git)'}",
        f"branch: {git['branch'] or '(no git)'}",
        f"dirty:  {git['dirty']}",
    ]
    (session_dir / "revision.txt").write_text("\n".join(rev_lines) + "\n")
    if git["diff"]:
        (session_dir / "diff.patch").write_text(git["diff"])

    # Config snapshots
    if config_files:
        snap_dir = session_dir / "snapshots"
        snap_dir.mkdir(exist_ok=True)
        for src in config_files:
            src = Path(src)
            if not src.exists():
                continue
            try:
                rel = src.resolve().relative_to(project_root)
                target = snap_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
            except ValueError:
                target = snap_dir / src.name
            try:
                shutil.copy2(src, target)
            except OSError:
                pass

    # Sanitized env capture (no api keys, just shape)
    env_subset: dict[str, str] = {}
    keys = env_capture_keys or []
    redact_substrings = ("KEY", "SECRET", "TOKEN", "PASSWORD")
    for k in keys:
        if k in os.environ:
            v = os.environ[k]
            if any(r in k.upper() for r in redact_substrings):
                env_subset[k] = f"<redacted len={len(v)}>"
            else:
                env_subset[k] = v

    config = {
        "run_id": run_id,
        "project": project,
        "name": name,
        "runtime": runtime,
        "model": model,
        "benchmark_id": benchmark_id,
        "started_at": started_at,
        "git": {k: v for k, v in git.items() if k != "diff"},
        "env": env_subset,
    }
    (session_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2))

    # Cross-project session index
    _SESSIONS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    with open(_SESSIONS_INDEX, "a") as f:
        f.write(
            json.dumps(
                {
                    "run_id": run_id,
                    "project": project,
                    "name": name,
                    "path": str(session_dir),
                    "started_at": started_at,
                    "runtime": runtime,
                    "model": model,
                    "benchmark_id": benchmark_id,
                }
            )
            + "\n"
        )

    return SessionWriter(
        project=project,
        run_id=run_id,
        name=name,
        runtime=runtime,
        model=model,
        benchmark_id=benchmark_id,
        project_root=project_root,
        session_dir=session_dir,
        started_at=started_at,
        config=config,
    )
