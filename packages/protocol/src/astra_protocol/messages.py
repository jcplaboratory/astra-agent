from datetime import datetime
from uuid import UUID

from astra_domain import (
    Approval,
    Artifact,
    Capability,
    Conversation,
    ConversationMessage,
    ConversationTurn,
    Lease,
    MemoryRecord,
    MigrationBatch,
    PersonaCore,
    PersonaProfile,
    RemoteAgent,
    Task,
    TaskState,
)
from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RegisterARARequest(Message):
    tenant_id: UUID
    name: str = Field(min_length=1, max_length=200)
    capabilities: tuple[Capability, ...] = ()
    runtime_version: str = Field(min_length=1, max_length=100)


class LeaseRequest(Message):
    tenant_id: UUID
    ara_id: UUID
    duration_seconds: int = Field(default=60, ge=10, le=900)


class LeaseResponse(Message):
    task: Task
    lease: Lease


class LeaseBoundRequest(Message):
    tenant_id: UUID
    ara_id: UUID
    task_id: UUID
    lease_id: UUID


class ARAEventRequest(LeaseBoundRequest):
    message: str = Field(min_length=1, max_length=5_000)
    progress_percent: int | None = Field(default=None, ge=0, le=100)


class RenewLeaseRequest(LeaseBoundRequest):
    duration_seconds: int = Field(default=60, ge=10, le=900)


class CompleteTaskRequest(LeaseBoundRequest):
    result: str = Field(min_length=1, max_length=50_000)
    artifacts: tuple[Artifact, ...] = ()


class CancelTaskRequest(LeaseBoundRequest):
    reason: str = Field(min_length=1, max_length=2_000)


class FailTaskRequest(LeaseBoundRequest):
    error: str = Field(min_length=1, max_length=4_000)


class ApprovalRequest(LeaseBoundRequest):
    capability: Capability
    reason: str = Field(min_length=1, max_length=2_000)


class ApprovalDecisionRequest(Message):
    tenant_id: UUID
    granted: bool


class ArtifactUploadRequest(LeaseBoundRequest):
    name: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ArtifactUploadResponse(Message):
    object_key: str
    upload_url: str
    expires_in_seconds: int


class ArtifactDownloadResponse(Message):
    id: UUID
    task_id: UUID
    name: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    download_url: str
    expires_in_seconds: int


class ArtifactMetadataResponse(Message):
    id: UUID
    task_id: UUID
    name: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    retention_until: datetime | None


class HeartbeatRequest(Message):
    tenant_id: UUID
    ara_id: UUID
    task_id: UUID | None = None
    lease_id: UUID | None = None


class HeartbeatResponse(Message):
    task_state: TaskState | None = None
    cancellation_requested: bool = False


class TaskLifecycleResponse(Message):
    task_id: UUID
    state: TaskState


class ApprovalResponse(Message):
    approval: Approval


class CreateConversationRequest(Message):
    tenant_id: UUID
    title: str = Field(default="New conversation", min_length=1, max_length=200)


class SendMessageRequest(Message):
    tenant_id: UUID
    client_request_id: UUID
    content: str = Field(min_length=1, max_length=50_000)


class ConversationResponse(Message):
    conversation: Conversation
    messages: tuple[ConversationMessage, ...] = ()


class ConversationTurnResponse(Message):
    turn: ConversationTurn | None = None
    user_message: ConversationMessage
    assistant_message: ConversationMessage | None = None


class ConversationTurnQueryResponse(Message):
    turn: ConversationTurn
    user_message: ConversationMessage
    assistant_message: ConversationMessage | None = None


class ConversationTurnsResponse(Message):
    turns: tuple[ConversationTurn, ...]


class MemoryListResponse(Message):
    memories: tuple[MemoryRecord, ...]


class MemoryExplanationResponse(Message):
    memory: MemoryRecord
    explanation: str


class MemoryReviewRequest(Message):
    tenant_id: UUID
    promote: bool
    replaces_memory_id: UUID | None = None


class MemoryPinRequest(Message):
    tenant_id: UUID
    pinned: bool


class PersonaUpdateRequest(Message):
    tenant_id: UUID
    authored_core: PersonaCore


class PersonaRevertRequest(Message):
    tenant_id: UUID
    version: int = Field(ge=1)


class PersonaResponse(Message):
    persona: PersonaProfile


class MigrationPersonaRequest(Message):
    tenant_id: UUID
    authored_core: PersonaCore


class MigrationBatchResponse(Message):
    batch: MigrationBatch


def remote_agent_from_registration(request: RegisterARARequest) -> RemoteAgent:
    return RemoteAgent(**request.model_dump())
