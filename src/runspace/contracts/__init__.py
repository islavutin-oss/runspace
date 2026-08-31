"""Runtime-agnostic contracts."""

from .chat import (
    AttachmentInput,
    ChatRequest,
    ChatResponse,
    FileAttachmentResponse,
    RoutineCreateRequest,
    RoutineDelivery,
)
from .runtime import AgentRuntime, AgentTurnDelta, AgentTurnResult, Attachment
from .scheduling import (
    CronJob,
    Delivery,
    JobStatus,
    Payload,
    Schedule,
    ScheduleKind,
)
from .tool import AgentTool
from .workspace import (
    AppConfig,
    ChannelConfig,
    ProviderConfig,
    UserConfig,
    WorkspaceConfig,
    load_workspace,
)

__all__ = [
    # chat protocol
    "AttachmentInput",
    "ChatRequest",
    "ChatResponse",
    "FileAttachmentResponse",
    "RoutineCreateRequest",
    "RoutineDelivery",
    # tool contract
    "AgentTool",
    # runtime contract (the dispatcher seam)
    "AgentRuntime",
    "AgentTurnResult",
    "AgentTurnDelta",
    "Attachment",
    # workspace.yml schema
    "AppConfig",
    "ChannelConfig",
    "ProviderConfig",
    "UserConfig",
    "WorkspaceConfig",
    "load_workspace",
    # scheduling primitives
    "CronJob",
    "Delivery",
    "JobStatus",
    "Payload",
    "Schedule",
    "ScheduleKind",
]
