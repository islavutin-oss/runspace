"""Agentino Workspace — Slack-like back-office with AI agent gateway."""

from .activity_log import ActivityLog
from .app_registry import AgentApp, AppRegistry, ChatHistoryStore, request_user_name
from .bootstrap import create_app
from .gateway import WorkspaceGateway
from .registry import WorkspaceRegistry

__all__ = [
    "WorkspaceGateway",
    "WorkspaceRegistry",
    "ActivityLog",
    "AppRegistry",
    "AgentApp",
    "ChatHistoryStore",
    "request_user_name",
    "create_app",
]
