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


class ConversationTurnState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class ToolInvocationTarget(StrEnum):
    LOCAL = "local"
    ARA = "ara"


class ToolInvocationState(StrEnum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"


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
    CONVERSATION_TURN_CREATED = "conversation_turn.created"
    CONVERSATION_TURN_RUNNING = "conversation_turn.running"
    CONVERSATION_TURN_PAUSED = "conversation_turn.paused"
    CONVERSATION_TURN_COMPLETED = "conversation_turn.completed"
    CONVERSATION_TURN_FAILED = "conversation_turn.failed"
    TOOL_INVOCATION_REQUESTED = "tool_invocation.requested"
    TOOL_INVOCATION_COMPLETED = "tool_invocation.completed"
    TOOL_INVOCATION_FAILED = "tool_invocation.failed"
    TOOL_INVOCATION_DENIED = "tool_invocation.denied"


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


class ConversationTurn(TenantOwnedModel):
    id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    user_message_id: UUID
    client_request_id: UUID
    state: ConversationTurnState = ConversationTurnState.PENDING
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    run_lease_id: UUID | None = None
    run_lease_expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_run_lease(self) -> ConversationTurn:
        if (self.run_lease_id is None) != (self.run_lease_expires_at is None):
            raise ValueError("run lease id and expiry must be provided together")
        if self.run_lease_expires_at is not None and self.run_lease_expires_at.tzinfo is None:
            raise ValueError("run_lease_expires_at must be timezone-aware")
        return self


class ToolInvocation(TenantOwnedModel):
    id: UUID = Field(default_factory=uuid4)
    turn_id: UUID
    task_id: UUID | None = None
    tool_call_id: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(min_length=1, max_length=200)
    target: ToolInvocationTarget
    state: ToolInvocationState = ToolInvocationState.PENDING
    arguments: dict[str, Any]
    arguments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


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
    task_id: UUID | None = None
    tool_invocation_id: UUID | None = None
    capability: Capability
    requested_by: UUID | None = None
    requestor_type: ActorType = ActorType.ARA
    state: ApprovalState = ApprovalState.PENDING
    reason: str = Field(min_length=1, max_length=2_000)
    created_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None
    decided_by: UUID | None = None

    @model_validator(mode="after")
    def validate_subject_and_requestor(self) -> Approval:
        if (self.task_id is None) == (self.tool_invocation_id is None):
            raise ValueError("exactly one of task_id or tool_invocation_id is required")
        if self.requestor_type not in {ActorType.COORDINATOR, ActorType.ARA}:
            raise ValueError("requestor_type must be coordinator or ara")
        if self.requestor_type is ActorType.ARA and self.requested_by is None:
            raise ValueError("ARA approvals require requested_by")
        return self


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
