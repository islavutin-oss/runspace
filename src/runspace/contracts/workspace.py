"""Workspace.yml schema — runtime-agnostic validation + access."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    """One agent in the `apps:` block of workspace.yml."""

    name: str = ""
    role: str = ""
    avatar: str = ""
    color: str = ""
    group: Literal["backoffice", "customer", "default"] = "default"
    type: Literal["agentino", "openclaw", "codex", "claude_code", "pi", "http", "webhook"] = (
        "agentino"
    )
    soul: str | None = None
    tools: str | None = None
    shared_tools: str | list[str] | None = None
    model: str | None = None
    endpoint: str | None = None
    enabled: bool = True
    gates: dict[str, Any] | None = None
    # New runtime-side fields used by openclaw
    openclaw_plugin: str | None = None
    openclaw_skills: list[str] = Field(default_factory=list)


class UserConfig(BaseModel):
    name: str = ""
    role: str = ""
    avatar: str = ""
    default: bool = False


class ChannelConfig(BaseModel):
    id: str
    label: str = ""
    icon: str = ""
    type: str = "chat"


class ProviderConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    provider: str = ""


class WorkspaceConfig(BaseModel):
    """Top-level workspace.yml shape. `extra='allow'` because tenants
    legitimately add custom blocks (settings, automation, persona…)
    that are read by tenant-specific code paths."""

    name: str = ""
    icon: str = ""
    brand_color: str = ""
    sidebar_color: str = ""
    tenant_id: str | None = None
    apps: dict[str, AppConfig] = Field(default_factory=dict)
    users: dict[str, UserConfig] = Field(default_factory=dict)
    channels: list[ChannelConfig] = Field(default_factory=list)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


def load_workspace(path: str | Path) -> WorkspaceConfig:
    """Read + validate a workspace.yml. Raises pydantic ValidationError
    on shape errors. Pure I/O + parse, no runtime side effects."""
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return WorkspaceConfig.model_validate(raw)
