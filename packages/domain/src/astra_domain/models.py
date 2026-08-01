from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TenantOwnedModel(DomainModel):
    tenant_id: UUID


class CapabilityKind(StrEnum):
    FILE_READ = "file.read"
    FILE_WRITE = "file.write"
    COMMAND_EXECUTE = "command.execute"
    NETWORK_ACCESS = "network.access"
    CREDENTIAL_ACCESS = "credential.access"
    EXTERNAL_MESSAGE = "external.message"


class Capability(DomainModel):
    kind: CapabilityKind
    scope: str = Field(min_length=1, max_length=500)


class RemoteAgentStatus(StrEnum):
    ACTIVE = "active"
    OFFLINE = "offline"
    REVOKED = "revoked"


class TaskState(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalState(StrEnum):
    PENDING = "pending"
    GRANTED = "granted"
    DENIED = "denied"


class ActorType(StrEnum):
    USER = "user"
    COORDINATOR = "coordinator"
    ARA = "ara"


class EventType(StrEnum):
    CONVERSATION_CREATED = "conversation.created"
    CONVERSATION_RECEIVED = "conversation.received"
    CONTEXT_COMPILED = "context.compiled"
    MODEL_REQUEST = "model.request"
    MODEL_RESPONSE = "model.response"
    MODEL_FAILED = "model.failed"
    MEMORY_CANDIDATE_CREATED = "memory.candidate_created"
    MEMORY_PROMOTED = "memory.promoted"
    MEMORY_REJECTED = "memory.rejected"
    MEMORY_CONTRADICTION_RESOLVED = "memory.contradiction_resolved"
    MEMORY_DELETED = "memory.deleted"
    TASK_CREATED = "task.created"
    ARA_REGISTERED = "ara.registered"
    TASK_LEASED = "task.leased"
    LEASE_RENEWED = "lease.renewed"
    ARA_PROGRESS = "ara.progress"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_DENIED = "approval.denied"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    ARTIFACT_PRODUCED = "artifact.produced"


class MemoryKind(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    PROJECT = "project"
    DECISION = "decision"
    COMMITMENT = "commitment"


class MemoryState(StrEnum):
    CANDIDATE = "candidate"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    DELETED = "deleted"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class Tenant(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    created_at: datetime = Field(default_factory=utc_now)


class Conversation(TenantOwnedModel):
    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConversationMessage(TenantOwnedModel):
    id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    role: MessageRole
    content: str = Field(min_length=1, max_length=50_000)
    created_at: datetime = Field(default_factory=utc_now)


class RemoteAgent(TenantOwnedModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    capabilities: tuple[Capability, ...] = ()
    runtime_version: str = Field(min_length=1, max_length=100)
    status: RemoteAgentStatus = RemoteAgentStatus.ACTIVE
    registered_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)


class Task(TenantOwnedModel):
    id: UUID = Field(default_factory=uuid4)
    objective: str = Field(min_length=1, max_length=10_000)
    context: str = Field(default="", max_length=50_000)
    required_capabilities: tuple[Capability, ...] = ()
    deliverable_contract: str = Field(min_length=1, max_length=5_000)
    state: TaskState = TaskState.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    deadline: datetime | None = None
    result: str | None = Field(default=None, max_length=50_000)
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_deadline(self) -> Task:
        if self.deadline is not None and self.deadline.tzinfo is None:
            raise ValueError("deadline must be timezone-aware")
        return self


class Lease(TenantOwnedModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    ara_id: UUID
    acquired_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime

    @model_validator(mode="after")
    def validate_window(self) -> Lease:
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        if self.expires_at <= self.acquired_at:
            raise ValueError("expires_at must be after acquired_at")
        return self


class Approval(TenantOwnedModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    capability: Capability
    requested_by: UUID
    state: ApprovalState = ApprovalState.PENDING
    reason: str = Field(min_length=1, max_length=2_000)
    created_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None


class Artifact(TenantOwnedModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    name: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=200)
    object_key: str = Field(min_length=1, max_length=1_000)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=utc_now)


class AuditEvent(TenantOwnedModel):
    id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    actor_type: ActorType
    actor_id: UUID | None = None
    task_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)


class Persona(TenantOwnedModel):
    id: UUID = Field(default_factory=uuid4)
    version: int = Field(ge=1)
    authored_core: dict[str, Any]
    learned_adaptation: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class MemoryRecord(TenantOwnedModel):
    id: UUID = Field(default_factory=uuid4)
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=50_000)
    normalized_content: str = Field(min_length=1, max_length=50_000)
    source_event_id: UUID
    source_message_id: UUID
    confidence: float = Field(ge=0, le=1)
    confirmed: bool = False
    state: MemoryState = MemoryState.CANDIDATE
    sensitivity: str = Field(default="normal", min_length=1, max_length=100)
    visibility: str = Field(default="private", min_length=1, max_length=100)
    retention: str = Field(default="durable", min_length=1, max_length=100)
    contradiction_of: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    reviewed_at: datetime | None = None
    reviewed_by: UUID | None = None
    deleted_at: datetime | None = None
