"""Sandbox-mode merge-gate lint — see ADR-0001."""

from __future__ import annotations

import re
import sys
from pathlib import Path

BANNED_IMPORTS = {
    "supabase": "use `from protocols import get_store` (Store protocol)",
    "postgrest": "use `from protocols import get_store` (Store protocol)",
    "telegram": "use `from protocols import get_transport` (Transport protocol)",
    "telebot": "use `from protocols import get_transport`",
    "python_telegram_bot": "use `from protocols import get_transport`",
}

# Match raw HTTP calls to provider URLs that bypass the Vision adapter.
BANNED_URL_PATTERNS = [
    (
        re.compile(r"/v1/codex/responses"),
        "use `from protocols import get_vision` (CodexVision is the only place that should hit this URL)",
    ),
    (
        re.compile(r"api\.openai\.com/v1/(chat|completions|responses)"),
        "use the agentino LLM client or services.get_vision()",
    ),
    (re.compile(r"chatgpt\.com/backend-api"), "go through agentino's Codex provider, not raw HTTP"),
]


def find_tool_files(root: Path) -> list[Path]:
    """Find every Python file under any `agents/*/tools/` subtree."""
    out = []
    for agents_dir in root.rglob("agents"):
        if not agents_dir.is_dir():
            continue
        for tools_dir in agents_dir.glob("*/tools"):
            if not tools_dir.is_dir():
                continue
            for py in tools_dir.rglob("*.py"):
                if py.name.startswith("_"):
                    continue
                out.append(py)
    return out


def check_file(path: Path) -> list[str]:
    """Return list of human-readable violations for one file."""
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    for line_num, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        # Skip comments / docstrings (cheap heuristic)
        if stripped.startswith("#") or stripped.startswith('"""'):
            continue
        # import X / from X import ...
        m = re.match(r"^\s*(?:from|import)\s+([\w\.]+)", stripped)
        if m:
            mod = m.group(1).split(".")[0]
            if mod in BANNED_IMPORTS:
                violations.append(
                    f"{path}:{line_num}: import `{mod}` is banned — "
                    f"{BANNED_IMPORTS[mod]}\n    {stripped}"
                )
        # raw URL strings
        for pat, suggestion in BANNED_URL_PATTERNS:
            if pat.search(line):
                violations.append(
                    f"{path}:{line_num}: raw URL to a backend — {suggestion}\n    {stripped}"
                )
                break
    return violations


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".")
    if not root.exists():
        print(f"sandbox_lint: path not found: {root}", file=sys.stderr)
        return 2

    files = find_tool_files(root)
    if not files:
        print(f"sandbox_lint: no agents/*/tools/*.py found under {root}")
        return 0

    all_violations: list[str] = []
    for f in files:
        v = check_file(f)
        all_violations.extend(v)
        print(f"  {'✗' if v else 'OK'}  {f}{'  (' + str(len(v)) + ' issues)' if v else ''}")

    if all_violations:
        print()
        print(
            f"sandbox_lint: {len(all_violations)} violation(s) — see ADR-0001 §«sandbox-mode contract»"
        )
        for v in all_violations:
            print()
            print(v)
        return 1

    print()
    print(f"sandbox_lint: all {len(files)} tool file(s) clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
