"""Tests for `runspace/scripts/agent_smoke.py`.

Two layers:
  1. Unit tests on the pure-Python helpers (env-var resolution, synth-yaml
     building, envelope parsing). No subprocess, no LLM.
  2. Mocked-subprocess tests that monkey-patch `subprocess.run` and assert
     the script's PASS/FAIL logic + reason aggregation, without paying
     real LLM calls.

Live verification of the full path lives outside pytest — it requires real
provider credentials and Router access. See
`docs/AGENT_RUNTIME.md#per-agent-liveness-smoke` for the manual reproduction
steps.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Add scripts/ to path so the agent_smoke module is importable.
_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import agent_smoke as smoke  # noqa: E402  (after sys.path setup)

# ---------------------------------------------------------------------------
# Unit: provider env-var resolution
# ---------------------------------------------------------------------------


def test_resolve_providers_substitutes_env_vars():
    raw = {
        "router": {
            "base_url": "https://r.example.com/v1",
            "api_key": "${TEST_ROUTER_KEY}",
        }
    }
    with patch.dict(os.environ, {"TEST_ROUTER_KEY": "pk_xyz"}):
        resolved, missing = smoke._resolve_providers(raw)
    assert resolved["router"]["api_key"] == "pk_xyz"
    assert resolved["router"]["base_url"] == "https://r.example.com/v1"
    assert missing == []


def test_resolve_providers_reports_missing_env_vars():
    raw = {"foo": {"api_key": "${MISSING_KEY_FOR_TEST}"}}
    # Make sure the env var really isn't set
    if "MISSING_KEY_FOR_TEST" in os.environ:
        del os.environ["MISSING_KEY_FOR_TEST"]
    resolved, missing = smoke._resolve_providers(raw)
    assert resolved["foo"]["api_key"] == ""
    assert "MISSING_KEY_FOR_TEST" in missing


def test_resolve_providers_passthrough_non_template_values():
    raw = {"x": {"api_key": "literal-key", "base_url": "https://x.com"}}
    resolved, missing = smoke._resolve_providers(raw)
    assert resolved["x"]["api_key"] == "literal-key"
    assert missing == []


def test_resolve_providers_handles_multiple_unresolved():
    raw = {
        "a": {"api_key": "${A_KEY}"},
        "b": {"api_key": "${B_KEY}", "extra": "${C_KEY}"},
    }
    for k in ("A_KEY", "B_KEY", "C_KEY"):
        os.environ.pop(k, None)
    resolved, missing = smoke._resolve_providers(raw)
    assert sorted(set(missing)) == ["A_KEY", "B_KEY", "C_KEY"]


# ---------------------------------------------------------------------------
# Unit: synthesised agents.yml shape
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_workspace(tmp_path: Path) -> Path:
    """Build a minimal tenant tree: workspace.yml + a SOUL.md."""
    soul = tmp_path / "agents" / "bot" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    soul.write_text("# Bot persona\n\nYou are {{persona_name}} for {{tenant_name}}.\n")

    ws_yml = tmp_path / "workspace.yml"
    ws_yml.write_text(
        yaml.safe_dump(
            {
                "name": "Test Tenant",
                "tenant_id": "test",
                "providers": {
                    "router": {
                        "base_url": "https://r.example.com/v1",
                        "api_key": "${TEST_KEY}",
                    }
                },
                "apps": {
                    "bot": {
                        "type": "agentino",
                        "name": "TestBot",
                        "soul": "agents/bot/SOUL.md",
                    }
                },
            }
        )
    )
    return ws_yml


def test_build_temp_agents_yaml_writes_valid_yaml(fake_workspace: Path):
    providers, _ = smoke._resolve_providers(
        {
            "router": {
                "base_url": "https://r.example.com/v1",
                "api_key": "pk_test",
            }
        }
    )
    cfg = yaml.safe_load(fake_workspace.read_text())
    name, tmp = smoke._build_temp_agents_yaml(
        fake_workspace,
        "bot",
        cfg["apps"]["bot"],
        providers,
        "Test Tenant",
    )
    try:
        assert name == "bot"
        assert tmp.exists()
        synth = yaml.safe_load(tmp.read_text())
        # Synthesised yaml has the right shape for agentino's CLI
        assert "providers" in synth
        assert "agents" in synth
        assert "bot" in synth["agents"]
        bot = synth["agents"]["bot"]
        # Model must be qualified with provider id
        assert bot["model"].startswith("router/")
        # Provider field present (defaulted to openai-codex by the synth)
        assert bot["provider"] == "openai-codex"
        # SOUL was flattened — persona/tenant tokens resolved
        assert "TestBot" in bot["instructions"]
        assert "Test Tenant" in bot["instructions"] or "TestBot" in bot["instructions"]
        # Provider config carried through
        assert synth["providers"]["router"]["api_key"] == "pk_test"
    finally:
        tmp.unlink()


def test_build_temp_agents_yaml_qualifies_bare_model(fake_workspace: Path):
    cfg = yaml.safe_load(fake_workspace.read_text())
    cfg["apps"]["bot"]["model"] = "gpt-5.3-codex"
    providers = {"router": {"base_url": "https://x", "api_key": "k", "provider": "openai-codex"}}
    _, tmp = smoke._build_temp_agents_yaml(
        fake_workspace,
        "bot",
        cfg["apps"]["bot"],
        providers,
        "T",
    )
    try:
        synth = yaml.safe_load(tmp.read_text())
        assert synth["agents"]["bot"]["model"] == "router/gpt-5.3-codex"
    finally:
        tmp.unlink()


def test_build_temp_agents_yaml_preserves_already_qualified_model(fake_workspace: Path):
    cfg = yaml.safe_load(fake_workspace.read_text())
    cfg["apps"]["bot"]["model"] = "router/gpt-5.4"
    providers = {"router": {"base_url": "https://x", "api_key": "k", "provider": "openai-codex"}}
    _, tmp = smoke._build_temp_agents_yaml(
        fake_workspace,
        "bot",
        cfg["apps"]["bot"],
        providers,
        "T",
    )
    try:
        synth = yaml.safe_load(tmp.read_text())
        assert synth["agents"]["bot"]["model"] == "router/gpt-5.4"
    finally:
        tmp.unlink()


def test_build_temp_agents_yaml_raises_when_soul_missing(fake_workspace: Path):
    cfg = yaml.safe_load(fake_workspace.read_text())
    cfg["apps"]["bot"]["soul"] = "agents/nonexistent/SOUL.md"
    providers = {"router": {"base_url": "https://x", "api_key": "k"}}
    with pytest.raises(FileNotFoundError, match="SOUL.md missing"):
        smoke._build_temp_agents_yaml(
            fake_workspace,
            "bot",
            cfg["apps"]["bot"],
            providers,
            "T",
        )


def test_build_temp_agents_yaml_raises_when_soul_field_absent(fake_workspace: Path):
    cfg = yaml.safe_load(fake_workspace.read_text())
    del cfg["apps"]["bot"]["soul"]
    providers = {"router": {"base_url": "https://x", "api_key": "k"}}
    with pytest.raises(ValueError, match="no soul"):
        smoke._build_temp_agents_yaml(
            fake_workspace,
            "bot",
            cfg["apps"]["bot"],
            providers,
            "T",
        )


def test_build_temp_agents_yaml_raises_when_no_providers(fake_workspace: Path):
    cfg = yaml.safe_load(fake_workspace.read_text())
    with pytest.raises(ValueError, match="no providers"):
        smoke._build_temp_agents_yaml(
            fake_workspace,
            "bot",
            cfg["apps"]["bot"],
            {},
            "T",
        )


def test_build_temp_agents_yaml_includes_tools_dir_when_set(fake_workspace: Path):
    cfg = yaml.safe_load(fake_workspace.read_text())
    cfg["apps"]["bot"]["tools"] = "agents/bot/tools"
    (fake_workspace.parent / "agents" / "bot" / "tools").mkdir(exist_ok=True)
    providers = {"router": {"base_url": "https://x", "api_key": "k"}}
    _, tmp = smoke._build_temp_agents_yaml(
        fake_workspace,
        "bot",
        cfg["apps"]["bot"],
        providers,
        "T",
    )
    try:
        synth = yaml.safe_load(tmp.read_text())
        assert "tools_dir" in synth["agents"]["bot"]
        assert synth["agents"]["bot"]["tools_dir"].endswith("agents/bot/tools")
    finally:
        tmp.unlink()


# ---------------------------------------------------------------------------
# Unit: envelope-parse + PASS/FAIL classification (via _probe_one with mocked
# subprocess)
# ---------------------------------------------------------------------------


def _mock_completed_proc(*, stdout: str = "", stderr: str = "", returncode: int = 0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


def test_probe_one_passes_when_envelope_has_text(fake_workspace: Path):
    cfg = yaml.safe_load(fake_workspace.read_text())
    providers = {"router": {"base_url": "https://x", "api_key": "k"}}
    envelope = {"type": "final", "text": "OK", "tools_used": [], "model": "x"}
    fake_proc = _mock_completed_proc(stdout=json.dumps(envelope), returncode=0)
    with patch("agent_smoke.subprocess.run", return_value=fake_proc):
        r = smoke._probe_one(
            fake_workspace, "bot", cfg["apps"]["bot"], providers, "T", "ping", timeout_s=5.0
        )
    assert r["ok"] is True
    assert r["agent"] == "bot"
    assert r["envelope"]["text"] == "OK"
    assert r["reason"] == ""


def test_probe_one_fails_when_envelope_text_starts_with_bracket(fake_workspace: Path):
    """Adapter-error markers like `[codex] runtime returned no reply` must FAIL."""
    cfg = yaml.safe_load(fake_workspace.read_text())
    providers = {"router": {"base_url": "https://x", "api_key": "k"}}
    envelope = {
        "type": "final",
        "text": "[codex] timed out after 30s",
        "tools_used": [],
        "model": "x",
    }
    fake_proc = _mock_completed_proc(stdout=json.dumps(envelope), returncode=0)
    with patch("agent_smoke.subprocess.run", return_value=fake_proc):
        r = smoke._probe_one(
            fake_workspace, "bot", cfg["apps"]["bot"], providers, "T", "ping", timeout_s=5.0
        )
    assert r["ok"] is False
    assert "[codex]" in r["reason"]


def test_probe_one_fails_when_text_contains_error(fake_workspace: Path):
    cfg = yaml.safe_load(fake_workspace.read_text())
    providers = {"router": {"base_url": "https://x", "api_key": "k"}}
    envelope = {
        "type": "final",
        "text": "Error: tool dispatch failed",
        "tools_used": [],
        "model": "x",
    }
    fake_proc = _mock_completed_proc(stdout=json.dumps(envelope), returncode=0)
    with patch("agent_smoke.subprocess.run", return_value=fake_proc):
        r = smoke._probe_one(
            fake_workspace, "bot", cfg["apps"]["bot"], providers, "T", "ping", timeout_s=5.0
        )
    assert r["ok"] is False
    assert "Error" in r["reason"]


def test_probe_one_fails_when_no_envelope_in_stdout(fake_workspace: Path):
    cfg = yaml.safe_load(fake_workspace.read_text())
    providers = {"router": {"base_url": "https://x", "api_key": "k"}}
    fake_proc = _mock_completed_proc(
        stdout="",
        stderr="auth failed: invalid credentials",
        returncode=1,
    )
    with patch("agent_smoke.subprocess.run", return_value=fake_proc):
        r = smoke._probe_one(
            fake_workspace, "bot", cfg["apps"]["bot"], providers, "T", "ping", timeout_s=5.0
        )
    assert r["ok"] is False
    assert "auth failed" in r["reason"]
    assert r["envelope"] is None


def test_probe_one_handles_subprocess_timeout(fake_workspace: Path):
    cfg = yaml.safe_load(fake_workspace.read_text())
    providers = {"router": {"base_url": "https://x", "api_key": "k"}}
    import subprocess as _sp

    with patch("agent_smoke.subprocess.run", side_effect=_sp.TimeoutExpired(cmd="x", timeout=5.0)):
        r = smoke._probe_one(
            fake_workspace, "bot", cfg["apps"]["bot"], providers, "T", "ping", timeout_s=5.0
        )
    assert r["ok"] is False
    assert "timeout" in r["reason"]
    assert r["elapsed_s"] == 5.0


def test_probe_one_recovers_from_pre_envelope_noise(fake_workspace: Path):
    """stdout may have warnings before the envelope; parse the LAST JSON line."""
    cfg = yaml.safe_load(fake_workspace.read_text())
    providers = {"router": {"base_url": "https://x", "api_key": "k"}}
    noisy_stdout = (
        "Warning: failed to load tools/foo.py: relative import\n"
        '{"type": "final", "text": "OK", "tools_used": [], "model": "x"}\n'
    )
    fake_proc = _mock_completed_proc(stdout=noisy_stdout, returncode=0)
    with patch("agent_smoke.subprocess.run", return_value=fake_proc):
        r = smoke._probe_one(
            fake_workspace, "bot", cfg["apps"]["bot"], providers, "T", "ping", timeout_s=5.0
        )
    assert r["ok"] is True


def test_probe_one_setup_error_propagates_as_fail(fake_workspace: Path):
    """If synth-yaml building fails, the agent reports a setup-error reason."""
    cfg = yaml.safe_load(fake_workspace.read_text())
    cfg["apps"]["bot"]["soul"] = "agents/nonexistent/SOUL.md"
    providers = {"router": {"base_url": "https://x", "api_key": "k"}}
    r = smoke._probe_one(
        fake_workspace, "bot", cfg["apps"]["bot"], providers, "T", "ping", timeout_s=5.0
    )
    assert r["ok"] is False
    assert "setup error" in r["reason"]
    assert "FileNotFoundError" in r["reason"]
