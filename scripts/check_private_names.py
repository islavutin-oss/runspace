#!/usr/bin/env python3
"""Fail on anything that names private work.

This package is published to PyPI and written on machines that also hold
private client projects. Twice a comment carried something across that line —
a client's tool name used as an example, and a group chat's real id used to
illustrate what a title replaces. Neither was a credential, which is why
review missed both: a scan for `ghp_` or `sk-` finds nothing here. The leak is
an identity, not a secret.

**The list of names lives outside this repository.** Writing them into a
tracked file would publish exactly what the check exists to keep out — the
first version of this file did, which is the same mistake one level up. The
patterns are read from, in order:

    $RUNSPACE_PRIVATE_NAMES    a path
    .private-names             at the repository root, git-ignored

One extended-regex pattern per line, `#` for comments; see
`.private-names.example`. With no such file the generic checks still run and
the name check says it is not configured rather than passing silently.

    python3 scripts/check_private_names.py            # every tracked file
    python3 scripts/check_private_names.py --staged   # what is about to commit
    python3 scripts/check_private_names.py a.py b.md  # named files
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A real Telegram group id, as opposed to the placeholder the docs use. This
# one is a shape rather than a name, so it is safe to keep in the open.
CHAT_ID_RE = re.compile(r"-100(?!1234567890\b)\d{10,}")

SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__", ".ruff_cache"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".pdf"}


def names_file() -> Path | None:
    explicit = (os.environ.get("RUNSPACE_PRIVATE_NAMES") or "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    local = ROOT / ".private-names"
    return local if local.is_file() else None


def name_pattern() -> re.Pattern[str] | None:
    path = names_file()
    if path is None:
        return None
    patterns = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return re.compile("|".join(patterns), re.I) if patterns else None


def _tracked() -> list[str]:
    return subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()


def _staged() -> list[str]:
    return subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()


def _read(rel: str, staged: bool) -> str | None:
    """The content to judge: the staged blob when committing, else the file."""
    if staged:
        r = subprocess.run(["git", "show", f":{rel}"], cwd=ROOT, capture_output=True)
        if r.returncode != 0:
            return None
        try:
            return r.stdout.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        return (ROOT / rel).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return None


def scan(files: list[str], staged: bool = False, names: re.Pattern[str] | None = None) -> list[str]:
    names = names if names is not None else name_pattern()
    findings: list[str] = []
    for rel in files:
        if set(Path(rel).parts) & SKIP_DIRS or Path(rel).suffix.lower() in SKIP_SUFFIXES:
            continue
        text = _read(rel, staged)
        if text is None:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            hits = [("private name", names.search(line))] if names else []
            hits.append(("real chat id", CHAT_ID_RE.search(line)))
            for label, m in hits:
                if m:
                    findings.append(f"{rel}:{n}: {label} {m.group(0)!r} — {line.strip()[:70]}")
    return findings


def main(argv: list[str]) -> int:
    if "--staged" in argv:
        files, staged = _staged(), True
    elif argv:
        files, staged = argv, False
    else:
        files, staged = _tracked(), False

    if name_pattern() is None:
        print(
            "check_private_names: no name list configured — copy "
            ".private-names.example to .private-names (it is git-ignored) or set "
            "$RUNSPACE_PRIVATE_NAMES. Checking the generic rules only.",
            file=sys.stderr,
        )

    findings = scan(files, staged)
    if not findings:
        return 0
    print("This repository is public. These name private work:\n", file=sys.stderr)
    for f in findings:
        print(f"  {f}", file=sys.stderr)
    print(
        "\nUse a generic stand-in — `run_sql`, `top_sellers`, `-100...` — or, if "
        "\nthe name genuinely belongs here, drop it from your .private-names.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
