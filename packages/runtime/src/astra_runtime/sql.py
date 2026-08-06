from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from astra_domain import (
    ActorType,
    Approval,
    ApprovalState,
    Artifact,
    AuditEvent,
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobState,
    Capability,
    Conversation,
    ConversationMessage,
    ConversationTurn,
    ConversationTurnState,
    EventType,
    JobAttempt,
    JobError,
    LearnedAdaptationState,
    LearnedPersonaAdaptation,
    Lease,
    MemoryKind,
    MemoryRecord,
    MemoryState,
    MessageRole,
    MigrationBatch,
    MigrationBatchState,
    PersonaCore,
    PersonaProfile,
    RemoteAgent,
    RemoteAgentStatus,
    Task,
    TaskState,
    ToolInvocation,
    ToolInvocationState,
    ToolInvocationTarget,
)
from astra_policy import evaluate_capability
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    or_,
    select,
)
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from astra_runtime.repositories import LifecycleConflictError, LifecycleNotFoundError


class Base(DeclarativeBase):
    pass


class RemoteAgentRow(Base):
    __tablename__ = "remote_agents"
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    name: Mapped[str] = mapped_column(String(200))
    capabilities: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    runtime_version: Mapped[str] = mapped_column(String(100))
    status: Mapped[RemoteAgentStatus] = mapped_column(Enum(RemoteAgentStatus))
    trust_level: Mapped[int] = mapped_column(default=1)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConversationRow(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    user_id: Mapped[str] = mapped_column(CHAR(36))
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ConversationMessageRow(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, autoincrement=True, unique=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    conversation_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("conversations.id"), index=True
    )
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ConversationTurnRow(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "client_request_id", name="uq_turns_tenant_request"),
    )
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    conversation_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("conversations.id"), index=True
    )
    user_message_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("conversation_messages.id"))
    client_request_id: Mapped[str] = mapped_column(CHAR(36))
    state: Mapped[ConversationTurnState] = mapped_column(Enum(ConversationTurnState), index=True)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON)
    run_lease_id: Mapped[str | None] = mapped_column(CHAR(36), index=True)
    run_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ToolInvocationRow(Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (UniqueConstraint("turn_id", "tool_call_id", name="uq_invocations_turn_call"),)
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    turn_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("conversation_turns.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(CHAR(36), ForeignKey("tasks.id"), index=True)
    tool_call_id: Mapped[str] = mapped_column(String(200))
    tool_name: Mapped[str] = mapped_column(String(200))
    target: Mapped[ToolInvocationTarget] = mapped_column(Enum(ToolInvocationTarget), index=True)
    state: Mapped[ToolInvocationState] = mapped_column(Enum(ToolInvocationState), index=True)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON)
    arguments_sha256: Mapped[str] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class MemoryRecordRow(Base):
    __tablename__ = "memory_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "normalized_content", name="uq_memory_records_tenant_normalized"
        ),
    )
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    kind: Mapped[MemoryKind] = mapped_column(Enum(MemoryKind))
    content: Mapped[str] = mapped_column(Text)
    normalized_content: Mapped[str] = mapped_column(Text)
    source_event_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    source_message_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("conversation_messages.id"), index=True
    )
    confidence: Mapped[float] = mapped_column(Float)
    confirmed: Mapped[bool] = mapped_column(Boolean)
    state: Mapped[MemoryState] = mapped_column(Enum(MemoryState), index=True)
    sensitivity: Mapped[str] = mapped_column(String(100))
    visibility: Mapped[str] = mapped_column(String(100))
    retention: Mapped[str] = mapped_column(String(100))
    contradiction_of: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("memory_records.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(CHAR(36))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    import_batch_id: Mapped[str | None] = mapped_column(CHAR(36), index=True)
    source_system: Mapped[str | None] = mapped_column(String(100))
    source_database_fingerprint: Mapped[str | None] = mapped_column(CHAR(64))
    source_external_id: Mapped[str | None] = mapped_column(String(500))
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MigrationBatchRow(Base):
    __tablename__ = "migration_batches"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_database_fingerprint",
            name="uq_migration_batch_source",
        ),
    )
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    source_system: Mapped[str] = mapped_column(String(100))
    source_database_fingerprint: Mapped[str] = mapped_column(CHAR(64))
    state: Mapped[MigrationBatchState] = mapped_column(
        Enum(MigrationBatchState, values_callable=lambda values: [item.value for item in values])
    )
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON)
    persona_draft: Mapped[dict[str, str] | None] = mapped_column(JSON)
    persona_profile_id: Mapped[str | None] = mapped_column(CHAR(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonaProfileRow(Base):
    __tablename__ = "persona_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_persona_profiles_tenant_version"),
    )
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    version: Mapped[int]
    authored_core: Mapped[dict[str, str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ActivePersonaRow(Base):
    __tablename__ = "active_personas"
    tenant_id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("persona_profiles.id"), unique=True
    )


class LearnedPersonaAdaptationRow(Base):
    __tablename__ = "learned_persona_adaptations"
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    profile_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("persona_profiles.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    state: Mapped[LearnedAdaptationState] = mapped_column(Enum(LearnedAdaptationState), index=True)
    source: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskRow(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    objective: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text)
    required_capabilities: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    deliverable_contract: Mapped[str] = mapped_column(Text)
    state: Mapped[TaskState] = mapped_column(Enum(TaskState), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    target_ara_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("remote_agents.id"), index=True
    )
    completed_by_ara_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("remote_agents.id")
    )


class LeaseRow(Base):
    __tablename__ = "leases"
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    task_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("tasks.id"), unique=True)
    ara_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("remote_agents.id"))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ApprovalRow(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    task_id: Mapped[str | None] = mapped_column(CHAR(36), ForeignKey("tasks.id"), index=True)
    tool_invocation_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("tool_invocations.id"), index=True
    )
    capability: Mapped[dict[str, Any]] = mapped_column(JSON)
    requested_by: Mapped[str | None] = mapped_column(CHAR(36), ForeignKey("remote_agents.id"))
    requestor_type: Mapped[ActorType] = mapped_column(Enum(ActorType))
    state: Mapped[ApprovalState] = mapped_column(Enum(ApprovalState), index=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(CHAR(36))


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    task_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("tasks.id"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(200))
    object_key: Mapped[str] = mapped_column(String(1000))
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    event_type: Mapped[EventType] = mapped_column(Enum(EventType))
    actor_type: Mapped[ActorType] = mapped_column(Enum(ActorType))
    actor_id: Mapped[str | None] = mapped_column(CHAR(36))
    task_id: Mapped[str | None] = mapped_column(CHAR(36), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BackgroundJobRow(Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "source_id", name="uq_background_jobs_source"),
    )
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    kind: Mapped[BackgroundJobKind] = mapped_column(Enum(BackgroundJobKind))
    source_id: Mapped[str] = mapped_column(CHAR(36))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[BackgroundJobState] = mapped_column(Enum(BackgroundJobState), index=True)
    attempt_count: Mapped[int]
    max_attempts: Mapped[int]
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobAttemptRow(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt", name="uq_job_attempts_job_attempt"),)
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    job_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("background_jobs.id"), index=True)
    attempt: Mapped[int]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _remote_agent_row(remote_agent: RemoteAgent) -> RemoteAgentRow:
    return RemoteAgentRow(
        id=str(remote_agent.id),
        tenant_id=str(remote_agent.tenant_id),
        name=remote_agent.name,
        capabilities=[item.model_dump(mode="json") for item in remote_agent.capabilities],
        runtime_version=remote_agent.runtime_version,
        status=remote_agent.status,
        trust_level=remote_agent.trust_level,
        registered_at=_naive_utc(remote_agent.registered_at),
        last_seen_at=_naive_utc(remote_agent.last_seen_at),
    )


def _remote_agent(row: RemoteAgentRow) -> RemoteAgent:
    return RemoteAgent(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        name=row.name,
        capabilities=tuple(Capability.model_validate(item) for item in row.capabilities),
        runtime_version=row.runtime_version,
        status=row.status,
        trust_level=row.trust_level,
        registered_at=_aware_utc(row.registered_at),
        last_seen_at=_aware_utc(row.last_seen_at),
    )


def _conversation_row(conversation: Conversation) -> ConversationRow:
    return ConversationRow(
        id=str(conversation.id),
        tenant_id=str(conversation.tenant_id),
        user_id=str(conversation.user_id),
        title=conversation.title,
        created_at=_naive_utc(conversation.created_at),
        updated_at=_naive_utc(conversation.updated_at),
    )


def _conversation_message_row(message: ConversationMessage) -> ConversationMessageRow:
    return ConversationMessageRow(
        id=str(message.id),
        tenant_id=str(message.tenant_id),
        conversation_id=str(message.conversation_id),
        role=message.role,
        content=message.content,
        created_at=_naive_utc(message.created_at),
    )


def _conversation_turn_row(turn: ConversationTurn) -> ConversationTurnRow:
    return ConversationTurnRow(
        id=str(turn.id),
        tenant_id=str(turn.tenant_id),
        conversation_id=str(turn.conversation_id),
        user_message_id=str(turn.user_message_id),
        client_request_id=str(turn.client_request_id),
        state=turn.state,
        checkpoint=turn.checkpoint,
        run_lease_id=str(turn.run_lease_id) if turn.run_lease_id else None,
        run_lease_expires_at=_naive_utc(turn.run_lease_expires_at)
        if turn.run_lease_expires_at
        else None,
        created_at=_naive_utc(turn.created_at),
        updated_at=_naive_utc(turn.updated_at),
        completed_at=_naive_utc(turn.completed_at) if turn.completed_at else None,
    )


def _tool_invocation_row(invocation: ToolInvocation) -> ToolInvocationRow:
    return ToolInvocationRow(
        id=str(invocation.id),
        tenant_id=str(invocation.tenant_id),
        turn_id=str(invocation.turn_id),
        task_id=str(invocation.task_id) if invocation.task_id else None,
        tool_call_id=invocation.tool_call_id,
        tool_name=invocation.tool_name,
        target=invocation.target,
        state=invocation.state,
        arguments=invocation.arguments,
        arguments_sha256=invocation.arguments_sha256,
        created_at=_naive_utc(invocation.created_at),
        updated_at=_naive_utc(invocation.updated_at),
        completed_at=_naive_utc(invocation.completed_at) if invocation.completed_at else None,
    )


def _memory_row(memory: MemoryRecord) -> MemoryRecordRow:
    return MemoryRecordRow(
        id=str(memory.id),
        tenant_id=str(memory.tenant_id),
        kind=memory.kind,
        content=memory.content,
        normalized_content=memory.normalized_content,
        source_event_id=str(memory.source_event_id),
        source_message_id=str(memory.source_message_id),
        confidence=memory.confidence,
        confirmed=memory.confirmed,
        state=memory.state,
        sensitivity=memory.sensitivity,
        visibility=memory.visibility,
        retention=memory.retention,
        contradiction_of=str(memory.contradiction_of) if memory.contradiction_of else None,
        created_at=_naive_utc(memory.created_at),
        updated_at=_naive_utc(memory.updated_at),
        reviewed_at=_naive_utc(memory.reviewed_at) if memory.reviewed_at else None,
        reviewed_by=str(memory.reviewed_by) if memory.reviewed_by else None,
        deleted_at=_naive_utc(memory.deleted_at) if memory.deleted_at else None,
        import_batch_id=str(memory.import_batch_id) if memory.import_batch_id else None,
        source_system=memory.source_system,
        source_database_fingerprint=memory.source_database_fingerprint,
        source_external_id=memory.source_external_id,
        source_metadata=memory.source_metadata,
    )


def _persona_profile_row(profile: PersonaProfile) -> PersonaProfileRow:
    return PersonaProfileRow(
        id=str(profile.id),
        tenant_id=str(profile.tenant_id),
        version=profile.version,
        authored_core=profile.authored_core.model_dump(mode="json"),
        created_at=_naive_utc(profile.created_at),
    )


def _task_row(task: Task) -> TaskRow:
    return TaskRow(
        id=str(task.id),
        tenant_id=str(task.tenant_id),
        objective=task.objective,
        context=task.context,
        required_capabilities=[item.model_dump(mode="json") for item in task.required_capabilities],
        deliverable_contract=task.deliverable_contract,
        state=task.state,
        created_at=_naive_utc(task.created_at),
        deadline=_naive_utc(task.deadline) if task.deadline else None,
        result=task.result,
        completed_at=_naive_utc(task.completed_at) if task.completed_at else None,
        target_ara_id=str(task.target_ara_id) if task.target_ara_id else None,
        completed_by_ara_id=str(task.completed_by_ara_id) if task.completed_by_ara_id else None,
    )


def _event_row(event: AuditEvent) -> AuditEventRow:
    return AuditEventRow(
        id=str(event.id),
        tenant_id=str(event.tenant_id),
        event_type=event.event_type,
        actor_type=event.actor_type,
        actor_id=str(event.actor_id) if event.actor_id else None,
        task_id=str(event.task_id) if event.task_id else None,
        payload=event.payload,
        occurred_at=_naive_utc(event.occurred_at),
    )


def _job_row(job: BackgroundJob) -> BackgroundJobRow:
    return BackgroundJobRow(
        id=str(job.id),
        tenant_id=str(job.tenant_id),
        kind=job.kind,
        source_id=str(job.source_id),
        payload=job.payload,
        state=job.state,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        available_at=_naive_utc(job.available_at),
        lease_id=str(job.lease_id) if job.lease_id else None,
        lease_expires_at=_naive_utc(job.lease_expires_at) if job.lease_expires_at else None,
        last_error=job.last_error.model_dump() if job.last_error else None,
        created_at=_naive_utc(job.created_at),
        updated_at=_naive_utc(job.updated_at),
        completed_at=_naive_utc(job.completed_at) if job.completed_at else None,
    )


def _job(row: BackgroundJobRow) -> BackgroundJob:
    return BackgroundJob(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        kind=row.kind,
        source_id=UUID(row.source_id),
        payload=row.payload,
        state=row.state,
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        available_at=_aware_utc(row.available_at),
        lease_id=UUID(row.lease_id) if row.lease_id else None,
        lease_expires_at=_aware_utc(row.lease_expires_at) if row.lease_expires_at else None,
        last_error=JobError.model_validate(row.last_error) if row.last_error else None,
        created_at=_aware_utc(row.created_at),
        updated_at=_aware_utc(row.updated_at),
        completed_at=_aware_utc(row.completed_at) if row.completed_at else None,
    )


def _attempt(row: JobAttemptRow) -> JobAttempt:
    return JobAttempt(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        job_id=UUID(row.job_id),
        attempt=row.attempt,
        started_at=_aware_utc(row.started_at),
        finished_at=_aware_utc(row.finished_at) if row.finished_at else None,
        error=JobError.model_validate(row.error) if row.error else None,
    )


def _approval_row(approval: Approval) -> ApprovalRow:
    return ApprovalRow(
        id=str(approval.id),
        tenant_id=str(approval.tenant_id),
        task_id=str(approval.task_id) if approval.task_id else None,
        tool_invocation_id=str(approval.tool_invocation_id)
        if approval.tool_invocation_id
        else None,
        capability=approval.capability.model_dump(mode="json"),
        requested_by=str(approval.requested_by) if approval.requested_by else None,
        requestor_type=approval.requestor_type,
        state=approval.state,
        reason=approval.reason,
        created_at=_naive_utc(approval.created_at),
        decided_at=_naive_utc(approval.decided_at) if approval.decided_at else None,
        decided_by=str(approval.decided_by) if approval.decided_by else None,
    )


def _artifact_row(artifact: Artifact) -> ArtifactRow:
    return ArtifactRow(
        id=str(artifact.id),
        tenant_id=str(artifact.tenant_id),
        task_id=str(artifact.task_id),
        name=artifact.name,
        media_type=artifact.media_type,
        object_key=artifact.object_key,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        created_at=_naive_utc(artifact.created_at),
        deleted_at=_naive_utc(artifact.deleted_at) if artifact.deleted_at else None,
        retention_until=_naive_utc(artifact.retention_until) if artifact.retention_until else None,
    )


def _task(row: TaskRow) -> Task:
    return Task(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        objective=row.objective,
        context=row.context,
        required_capabilities=tuple(
            Capability.model_validate(item) for item in row.required_capabilities
        ),
        deliverable_contract=row.deliverable_contract,
        state=row.state,
        created_at=_aware_utc(row.created_at),
        deadline=_aware_utc(row.deadline) if row.deadline else None,
        result=row.result,
        completed_at=_aware_utc(row.completed_at) if row.completed_at else None,
        target_ara_id=UUID(row.target_ara_id) if row.target_ara_id else None,
        completed_by_ara_id=UUID(row.completed_by_ara_id) if row.completed_by_ara_id else None,
    )


def _conversation(row: ConversationRow) -> Conversation:
    return Conversation(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        user_id=UUID(row.user_id),
        title=row.title,
        created_at=_aware_utc(row.created_at),
        updated_at=_aware_utc(row.updated_at),
    )


def _conversation_message(row: ConversationMessageRow) -> ConversationMessage:
    return ConversationMessage(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        conversation_id=UUID(row.conversation_id),
        role=row.role,
        content=row.content,
        created_at=_aware_utc(row.created_at),
    )


def _conversation_turn(row: ConversationTurnRow) -> ConversationTurn:
    return ConversationTurn(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        conversation_id=UUID(row.conversation_id),
        user_message_id=UUID(row.user_message_id),
        client_request_id=UUID(row.client_request_id),
        state=row.state,
        checkpoint=row.checkpoint,
        run_lease_id=UUID(row.run_lease_id) if row.run_lease_id else None,
        run_lease_expires_at=_aware_utc(row.run_lease_expires_at)
        if row.run_lease_expires_at
        else None,
        created_at=_aware_utc(row.created_at),
        updated_at=_aware_utc(row.updated_at),
        completed_at=_aware_utc(row.completed_at) if row.completed_at else None,
    )


def _tool_invocation(row: ToolInvocationRow) -> ToolInvocation:
    return ToolInvocation(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        turn_id=UUID(row.turn_id),
        task_id=UUID(row.task_id) if row.task_id else None,
        tool_call_id=row.tool_call_id,
        tool_name=row.tool_name,
        target=row.target,
        state=row.state,
        arguments=row.arguments,
        arguments_sha256=row.arguments_sha256,
        created_at=_aware_utc(row.created_at),
        updated_at=_aware_utc(row.updated_at),
        completed_at=_aware_utc(row.completed_at) if row.completed_at else None,
    )


def _memory(row: MemoryRecordRow) -> MemoryRecord:
    return MemoryRecord(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        kind=row.kind,
        content=row.content,
        normalized_content=row.normalized_content,
        source_event_id=UUID(row.source_event_id),
        source_message_id=UUID(row.source_message_id),
        confidence=row.confidence,
        confirmed=row.confirmed,
        state=row.state,
        sensitivity=row.sensitivity,
        visibility=row.visibility,
        retention=row.retention,
        contradiction_of=UUID(row.contradiction_of) if row.contradiction_of else None,
        created_at=_aware_utc(row.created_at),
        updated_at=_aware_utc(row.updated_at),
        reviewed_at=_aware_utc(row.reviewed_at) if row.reviewed_at else None,
        reviewed_by=UUID(row.reviewed_by) if row.reviewed_by else None,
        deleted_at=_aware_utc(row.deleted_at) if row.deleted_at else None,
        import_batch_id=UUID(row.import_batch_id) if row.import_batch_id else None,
        source_system=row.source_system,
        source_database_fingerprint=row.source_database_fingerprint,
        source_external_id=row.source_external_id,
        source_metadata=row.source_metadata,
    )


def _migration_batch(row: MigrationBatchRow) -> MigrationBatch:
    return MigrationBatch(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        source_system=row.source_system,
        source_database_fingerprint=row.source_database_fingerprint,
        state=row.state,
        source_metadata=row.source_metadata,
        persona_draft=PersonaCore.model_validate(row.persona_draft) if row.persona_draft else None,
        persona_profile_id=UUID(row.persona_profile_id) if row.persona_profile_id else None,
        created_at=_aware_utc(row.created_at),
        activated_at=_aware_utc(row.activated_at) if row.activated_at else None,
        rolled_back_at=_aware_utc(row.rolled_back_at) if row.rolled_back_at else None,
    )


def _persona_profile(row: PersonaProfileRow) -> PersonaProfile:
    return PersonaProfile(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        version=row.version,
        authored_core=PersonaCore.model_validate(row.authored_core),
        created_at=_aware_utc(row.created_at),
    )


def _learned_persona_adaptation_row(
    adaptation: LearnedPersonaAdaptation,
) -> LearnedPersonaAdaptationRow:
    return LearnedPersonaAdaptationRow(
        id=str(adaptation.id),
        tenant_id=str(adaptation.tenant_id),
        profile_id=str(adaptation.profile_id),
        content=adaptation.content,
        state=adaptation.state,
        source=adaptation.source,
        created_at=_naive_utc(adaptation.created_at),
        reversed_at=_naive_utc(adaptation.reversed_at) if adaptation.reversed_at else None,
    )


def _learned_persona_adaptation(
    row: LearnedPersonaAdaptationRow,
) -> LearnedPersonaAdaptation:
    return LearnedPersonaAdaptation(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        profile_id=UUID(row.profile_id),
        content=row.content,
        state=row.state,
        source=row.source,
        created_at=_aware_utc(row.created_at),
        reversed_at=_aware_utc(row.reversed_at) if row.reversed_at else None,
    )


def _lease(row: LeaseRow) -> Lease:
    return Lease(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        task_id=UUID(row.task_id),
        ara_id=UUID(row.ara_id),
        acquired_at=_aware_utc(row.acquired_at),
        expires_at=_aware_utc(row.expires_at),
    )


def _approval(row: ApprovalRow) -> Approval:
    return Approval(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        task_id=UUID(row.task_id) if row.task_id else None,
        tool_invocation_id=UUID(row.tool_invocation_id) if row.tool_invocation_id else None,
        capability=Capability.model_validate(row.capability),
        requested_by=UUID(row.requested_by) if row.requested_by else None,
        requestor_type=row.requestor_type,
        state=row.state,
        reason=row.reason,
        created_at=_aware_utc(row.created_at),
        decided_at=_aware_utc(row.decided_at) if row.decided_at else None,
        decided_by=UUID(row.decided_by) if row.decided_by else None,
    )


def _artifact(row: ArtifactRow) -> Artifact:
    return Artifact(
        id=UUID(row.id),
        tenant_id=UUID(row.tenant_id),
        task_id=UUID(row.task_id),
        name=row.name,
        media_type=row.media_type,
        object_key=row.object_key,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        created_at=_aware_utc(row.created_at),
        deleted_at=_aware_utc(row.deleted_at) if row.deleted_at else None,
        retention_until=_aware_utc(row.retention_until) if row.retention_until else None,
    )


class MariaDBRuntimeStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def stage_migration_batch(
        self, batch: MigrationBatch, memories: tuple[MemoryRecord, ...], actor_id: UUID
    ) -> MigrationBatch:
        async with self._sessions.begin() as session:
            existing = await session.scalar(
                select(MigrationBatchRow).where(
                    MigrationBatchRow.tenant_id == str(batch.tenant_id),
                    MigrationBatchRow.source_system == batch.source_system,
                    MigrationBatchRow.source_database_fingerprint
                    == batch.source_database_fingerprint,
                )
            )
            if existing is not None:
                return _migration_batch(existing)
            conversation = Conversation(
                tenant_id=batch.tenant_id,
                user_id=actor_id,
                title=f"Imported {batch.source_system} migration",
            )
            message = ConversationMessage(
                tenant_id=batch.tenant_id,
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content="Imported source provenance",
            )
            # The synthetic source message retains import provenance and must reference a
            # persisted conversation before any autoflush caused by duplicate checks.
            session.add(_conversation_row(conversation))
            await session.flush()
            session.add(_conversation_message_row(message))
            session.add(
                MigrationBatchRow(
                    id=str(batch.id),
                    tenant_id=str(batch.tenant_id),
                    source_system=batch.source_system,
                    source_database_fingerprint=batch.source_database_fingerprint,
                    state=batch.state,
                    source_metadata=batch.source_metadata,
                    persona_draft=batch.persona_draft.model_dump() if batch.persona_draft else None,
                    persona_profile_id=None,
                    created_at=_naive_utc(batch.created_at),
                    activated_at=None,
                    rolled_back_at=None,
                )
            )
            for memory in memories:
                duplicate = await session.scalar(
                    select(MemoryRecordRow).where(
                        MemoryRecordRow.tenant_id == str(batch.tenant_id),
                        MemoryRecordRow.source_system == memory.source_system,
                        MemoryRecordRow.source_database_fingerprint
                        == memory.source_database_fingerprint,
                        MemoryRecordRow.source_external_id == memory.source_external_id,
                    )
                )
                if duplicate is None:
                    session.add(
                        _memory_row(memory.model_copy(update={"source_message_id": message.id}))
                    )
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=batch.tenant_id,
                        event_type=EventType.MIGRATION_STAGED,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload={"batch_id": str(batch.id)},
                    )
                )
            )
        return batch

    async def get_migration_batch(self, tenant_id: UUID, batch_id: UUID) -> MigrationBatch | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(MigrationBatchRow).where(
                    MigrationBatchRow.id == str(batch_id),
                    MigrationBatchRow.tenant_id == str(tenant_id),
                )
            )
        return _migration_batch(row) if row else None

    async def list_migration_batches(self, tenant_id: UUID) -> tuple[MigrationBatch, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(MigrationBatchRow)
                    .where(MigrationBatchRow.tenant_id == str(tenant_id))
                    .order_by(MigrationBatchRow.created_at)
                )
            ).all()
        return tuple(_migration_batch(row) for row in rows)

    async def activate_migration_batch(
        self, tenant_id: UUID, batch_id: UUID, actor_id: UUID, authored_core: object
    ) -> MigrationBatch:
        if not isinstance(authored_core, PersonaCore):
            raise LifecycleConflictError("authored persona core is required")
        async with self._sessions.begin() as session:
            batch = await session.scalar(
                select(MigrationBatchRow)
                .where(
                    MigrationBatchRow.id == str(batch_id),
                    MigrationBatchRow.tenant_id == str(tenant_id),
                )
                .with_for_update()
            )
            if batch is None:
                raise LifecycleNotFoundError("migration batch not found")
            if batch.state is not MigrationBatchState.STAGED:
                raise LifecycleConflictError("migration batch is not staged")
            memories = (
                await session.scalars(
                    select(MemoryRecordRow)
                    .where(
                        MemoryRecordRow.tenant_id == str(tenant_id),
                        MemoryRecordRow.import_batch_id == str(batch_id),
                        MemoryRecordRow.state.in_((MemoryState.CANDIDATE, MemoryState.PROMOTED)),
                    )
                    .with_for_update()
                )
            ).all()
            if not memories:
                raise LifecycleConflictError("an approved imported fact is required")
            now = _naive_utc(datetime.now(UTC))
            promoted = [row for row in memories if row.state is MemoryState.CANDIDATE]
            for row in promoted:
                row.state = MemoryState.PROMOTED
                row.confirmed = True
                row.reviewed_at = now
                row.reviewed_by = str(actor_id)
                row.updated_at = now
            latest = await session.scalar(
                select(PersonaProfileRow.version)
                .where(PersonaProfileRow.tenant_id == str(tenant_id))
                .order_by(PersonaProfileRow.version.desc())
                .limit(1)
            )
            profile = PersonaProfile(
                tenant_id=tenant_id, version=(latest or 0) + 1, authored_core=authored_core
            )
            session.add(_persona_profile_row(profile))
            current = await session.scalar(
                select(ActivePersonaRow)
                .where(ActivePersonaRow.tenant_id == str(tenant_id))
                .with_for_update()
            )
            if current is None:
                session.add(ActivePersonaRow(tenant_id=str(tenant_id), profile_id=str(profile.id)))
            else:
                current.profile_id = str(profile.id)
            batch.state, batch.persona_profile_id, batch.activated_at = (
                MigrationBatchState.ACTIVE,
                str(profile.id),
                now,
            )
            session.add_all(
                [
                    *(
                        _event_row(
                            AuditEvent(
                                tenant_id=tenant_id,
                                event_type=EventType.MEMORY_PROMOTED,
                                actor_type=ActorType.USER,
                                actor_id=actor_id,
                                payload={
                                    "memory_id": str(row.id),
                                    "import_batch_id": str(batch_id),
                                },
                            )
                        )
                        for row in promoted
                    ),
                    _event_row(
                        AuditEvent(
                            tenant_id=tenant_id,
                            event_type=EventType.MIGRATION_ACTIVATED,
                            actor_type=ActorType.USER,
                            actor_id=actor_id,
                            payload={"batch_id": str(batch_id)},
                        )
                    ),
                ]
            )
            return _migration_batch(batch)

    async def rollback_migration_batch(
        self, tenant_id: UUID, batch_id: UUID, actor_id: UUID
    ) -> tuple[MigrationBatch, tuple[MemoryRecord, ...]]:
        async with self._sessions.begin() as session:
            batch = await session.scalar(
                select(MigrationBatchRow)
                .where(
                    MigrationBatchRow.id == str(batch_id),
                    MigrationBatchRow.tenant_id == str(tenant_id),
                )
                .with_for_update()
            )
            if batch is None:
                raise LifecycleNotFoundError("migration batch not found")
            if batch.state is MigrationBatchState.ROLLED_BACK:
                raise LifecycleConflictError("migration batch is already rolled back")
            rows = (
                await session.scalars(
                    select(MemoryRecordRow)
                    .where(
                        MemoryRecordRow.import_batch_id == str(batch_id),
                        MemoryRecordRow.state != MemoryState.DELETED,
                    )
                    .with_for_update()
                )
            ).all()
            now = _naive_utc(datetime.now(UTC))
            for row in rows:
                row.state, row.deleted_at, row.updated_at = MemoryState.DELETED, now, now
            batch.state, batch.rolled_back_at = MigrationBatchState.ROLLED_BACK, now
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.MIGRATION_ROLLED_BACK,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload={"batch_id": str(batch_id)},
                    )
                )
            )
            return _migration_batch(batch), tuple(_memory(row) for row in rows)

    async def get_active_persona(self, tenant_id: UUID) -> PersonaProfile | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(PersonaProfileRow)
                .join(ActivePersonaRow, ActivePersonaRow.profile_id == PersonaProfileRow.id)
                .where(ActivePersonaRow.tenant_id == str(tenant_id))
            )
        return _persona_profile(row) if row is not None else None

    async def create_persona_profile(
        self, profile: PersonaProfile, actor_id: UUID
    ) -> PersonaProfile:
        async with self._sessions.begin() as session:
            current = await session.scalar(
                select(ActivePersonaRow)
                .where(ActivePersonaRow.tenant_id == str(profile.tenant_id))
                .with_for_update()
            )
            latest = await session.scalar(
                select(PersonaProfileRow.version)
                .where(PersonaProfileRow.tenant_id == str(profile.tenant_id))
                .order_by(PersonaProfileRow.version.desc())
                .limit(1)
            )
            if profile.version != (latest or 0) + 1:
                raise LifecycleConflictError("persona version is not next for tenant")
            session.add(_persona_profile_row(profile))
            await session.flush()
            if current is None:
                session.add(
                    ActivePersonaRow(tenant_id=str(profile.tenant_id), profile_id=str(profile.id))
                )
            else:
                current.profile_id = str(profile.id)
            session.add_all(
                (
                    _event_row(
                        AuditEvent(
                            tenant_id=profile.tenant_id,
                            event_type=EventType.PERSONA_CREATED,
                            actor_type=ActorType.USER,
                            actor_id=actor_id,
                            payload={"persona_id": str(profile.id), "version": profile.version},
                        )
                    ),
                    _event_row(
                        AuditEvent(
                            tenant_id=profile.tenant_id,
                            event_type=EventType.PERSONA_ACTIVATED,
                            actor_type=ActorType.USER,
                            actor_id=actor_id,
                            payload={"persona_id": str(profile.id), "version": profile.version},
                        )
                    ),
                )
            )
        return profile

    async def revert_persona_profile(
        self, tenant_id: UUID, version: int, actor_id: UUID
    ) -> PersonaProfile:
        async with self._sessions.begin() as session:
            profile = await session.scalar(
                select(PersonaProfileRow).where(
                    PersonaProfileRow.tenant_id == str(tenant_id),
                    PersonaProfileRow.version == version,
                )
            )
            if profile is None:
                raise LifecycleNotFoundError("persona version not found")
            current = await session.scalar(
                select(ActivePersonaRow)
                .where(ActivePersonaRow.tenant_id == str(tenant_id))
                .with_for_update()
            )
            if current is None:
                raise LifecycleNotFoundError("active persona not found")
            current.profile_id = profile.id
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.PERSONA_REVERTED,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload={"persona_id": profile.id, "version": version},
                    )
                )
            )
            result = _persona_profile(profile)
        return result

    async def create_learned_persona_adaptation(
        self, adaptation: LearnedPersonaAdaptation
    ) -> LearnedPersonaAdaptation:
        async with self._sessions.begin() as session:
            profile = await session.scalar(
                select(PersonaProfileRow).where(
                    PersonaProfileRow.id == str(adaptation.profile_id),
                    PersonaProfileRow.tenant_id == str(adaptation.tenant_id),
                )
            )
            if profile is None:
                raise LifecycleNotFoundError("persona profile not found")
            session.add(_learned_persona_adaptation_row(adaptation))
        return adaptation

    async def reverse_learned_persona_adaptation(
        self, tenant_id: UUID, adaptation_id: UUID
    ) -> LearnedPersonaAdaptation:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(LearnedPersonaAdaptationRow)
                .where(
                    LearnedPersonaAdaptationRow.id == str(adaptation_id),
                    LearnedPersonaAdaptationRow.tenant_id == str(tenant_id),
                )
                .with_for_update()
            )
            if row is None:
                raise LifecycleNotFoundError("learned persona adaptation not found")
            if row.state is LearnedAdaptationState.REVERSED:
                raise LifecycleConflictError("learned persona adaptation is already reversed")
            row.state = LearnedAdaptationState.REVERSED
            row.reversed_at = _naive_utc(datetime.now(UTC))
            return _learned_persona_adaptation(row)

    async def upsert_memory(
        self, memory: MemoryRecord, events: tuple[AuditEvent, ...]
    ) -> MemoryRecord:
        async with self._sessions.begin() as session:
            inserted = False
            row = await session.scalar(
                select(MemoryRecordRow)
                .where(
                    MemoryRecordRow.tenant_id == str(memory.tenant_id),
                    MemoryRecordRow.normalized_content == memory.normalized_content,
                )
                .with_for_update()
            )
            if row is None:
                candidate = _memory_row(memory)
                try:
                    async with session.begin_nested():
                        session.add(candidate)
                        await session.flush()
                    row = candidate
                    inserted = True
                except IntegrityError:
                    row = await session.scalar(
                        select(MemoryRecordRow)
                        .where(
                            MemoryRecordRow.tenant_id == str(memory.tenant_id),
                            MemoryRecordRow.normalized_content == memory.normalized_content,
                        )
                        .with_for_update()
                    )
                    if row is None:
                        raise LifecycleConflictError("memory deduplication conflict") from None
            if row.state is not MemoryState.DELETED:
                if not inserted:
                    return _memory(row)
            else:
                revived = _memory_row(memory)
                for column in (
                    "kind",
                    "content",
                    "source_event_id",
                    "source_message_id",
                    "confidence",
                    "confirmed",
                    "state",
                    "sensitivity",
                    "visibility",
                    "retention",
                    "contradiction_of",
                    "created_at",
                    "updated_at",
                    "reviewed_at",
                    "reviewed_by",
                    "deleted_at",
                ):
                    setattr(row, column, getattr(revived, column))
            session.add_all(_event_row(event) for event in events)
            return _memory(row)

    async def enqueue_job(self, job: BackgroundJob) -> BackgroundJob:
        async with self._sessions.begin() as session:
            existing = await session.scalar(
                select(BackgroundJobRow).where(
                    BackgroundJobRow.tenant_id == str(job.tenant_id),
                    BackgroundJobRow.kind == job.kind,
                    BackgroundJobRow.source_id == str(job.source_id),
                )
            )
            if existing is not None:
                return _job(existing)
            row = _job_row(job)
            try:
                async with session.begin_nested():
                    session.add(row)
                    await session.flush()
            except IntegrityError:
                existing = await session.scalar(
                    select(BackgroundJobRow).where(
                        BackgroundJobRow.tenant_id == str(job.tenant_id),
                        BackgroundJobRow.kind == job.kind,
                        BackgroundJobRow.source_id == str(job.source_id),
                    )
                )
                if existing is None:
                    raise LifecycleConflictError("job enqueue conflict") from None
                return _job(existing)
            return _job(row)

    async def claim_job(self, lease_id: UUID, lease_expires_at: datetime) -> BackgroundJob | None:
        async with self._sessions.begin() as session:
            now = _naive_utc(datetime.now(UTC))
            row = await session.scalar(
                select(BackgroundJobRow)
                .where(
                    or_(
                        (
                            BackgroundJobRow.state.in_(
                                (BackgroundJobState.PENDING, BackgroundJobState.RETRY)
                            )
                        )
                        & (BackgroundJobRow.available_at <= now),
                        (BackgroundJobRow.state == BackgroundJobState.RUNNING)
                        & (BackgroundJobRow.lease_expires_at <= now),
                    )
                )
                .order_by(BackgroundJobRow.available_at, BackgroundJobRow.created_at)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.state = BackgroundJobState.RUNNING
            row.attempt_count += 1
            row.lease_id = str(lease_id)
            row.lease_expires_at = _naive_utc(lease_expires_at)
            row.updated_at = now
            session.add(
                JobAttemptRow(
                    id=str(uuid4()),
                    tenant_id=row.tenant_id,
                    job_id=row.id,
                    attempt=row.attempt_count,
                    started_at=now,
                    finished_at=None,
                    error=None,
                )
            )
            return _job(row)

    async def _active_job(
        self, session: AsyncSession, tenant_id: UUID, job_id: UUID, lease_id: UUID
    ) -> BackgroundJobRow:
        row = await session.scalar(
            select(BackgroundJobRow)
            .where(BackgroundJobRow.id == str(job_id), BackgroundJobRow.tenant_id == str(tenant_id))
            .with_for_update()
        )
        if row is None:
            raise LifecycleNotFoundError("job not found")
        if row.state is not BackgroundJobState.RUNNING or row.lease_id != str(lease_id):
            raise LifecycleConflictError("job is not actively claimed")
        return row

    async def complete_job(self, tenant_id: UUID, job_id: UUID, lease_id: UUID) -> BackgroundJob:
        async with self._sessions.begin() as session:
            row = await self._active_job(session, tenant_id, job_id, lease_id)
            now = _naive_utc(datetime.now(UTC))
            row.state, row.lease_id, row.lease_expires_at, row.completed_at, row.updated_at = (
                BackgroundJobState.COMPLETED,
                None,
                None,
                now,
                now,
            )
            attempt = await session.scalar(
                select(JobAttemptRow)
                .where(JobAttemptRow.job_id == row.id, JobAttemptRow.attempt == row.attempt_count)
                .with_for_update()
            )
            if attempt is not None:
                attempt.finished_at = now
            return _job(row)

    async def retry_job(
        self, tenant_id: UUID, job_id: UUID, lease_id: UUID, error: JobError, available_at: datetime
    ) -> BackgroundJob:
        async with self._sessions.begin() as session:
            row = await self._active_job(session, tenant_id, job_id, lease_id)
            now = _naive_utc(datetime.now(UTC))
            row.state = (
                BackgroundJobState.FAILED
                if row.attempt_count >= row.max_attempts
                else BackgroundJobState.RETRY
            )
            row.lease_id, row.lease_expires_at, row.last_error, row.available_at, row.updated_at = (
                None,
                None,
                error.model_dump(),
                _naive_utc(available_at),
                now,
            )
            if row.state is BackgroundJobState.FAILED:
                row.completed_at = now
            attempt = await session.scalar(
                select(JobAttemptRow)
                .where(JobAttemptRow.job_id == row.id, JobAttemptRow.attempt == row.attempt_count)
                .with_for_update()
            )
            if attempt is not None:
                attempt.finished_at, attempt.error = now, error.model_dump()
            return _job(row)

    async def list_jobs(self, tenant_id: UUID) -> tuple[BackgroundJob, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(BackgroundJobRow)
                    .where(BackgroundJobRow.tenant_id == str(tenant_id))
                    .order_by(BackgroundJobRow.created_at.desc())
                )
            ).all()
        return tuple(_job(row) for row in rows)

    async def list_job_attempts(self, tenant_id: UUID, job_id: UUID) -> tuple[JobAttempt, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(JobAttemptRow)
                    .where(
                        JobAttemptRow.tenant_id == str(tenant_id),
                        JobAttemptRow.job_id == str(job_id),
                    )
                    .order_by(JobAttemptRow.attempt)
                )
            ).all()
        return tuple(_attempt(row) for row in rows)

    async def requeue_job(self, tenant_id: UUID, job_id: UUID, actor_id: UUID) -> BackgroundJob:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(BackgroundJobRow)
                .where(
                    BackgroundJobRow.id == str(job_id), BackgroundJobRow.tenant_id == str(tenant_id)
                )
                .with_for_update()
            )
            if row is None:
                raise LifecycleNotFoundError("job not found")
            if row.state is not BackgroundJobState.FAILED:
                raise LifecycleConflictError("only failed jobs can be requeued")
            now = _naive_utc(datetime.now(UTC))
            row.state = BackgroundJobState.PENDING
            row.attempt_count = 0
            row.available_at = now
            row.lease_id = None
            row.lease_expires_at = None
            row.last_error = None
            row.completed_at = None
            row.updated_at = now
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.JOB_REQUEUED,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload={"job_id": str(job_id)},
                    )
                )
            )
            return _job(row)

    async def get_memory(self, tenant_id: UUID, memory_id: UUID) -> MemoryRecord | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(MemoryRecordRow).where(
                    MemoryRecordRow.id == str(memory_id),
                    MemoryRecordRow.tenant_id == str(tenant_id),
                )
            )
        return _memory(row) if row is not None else None

    async def list_memories(
        self, tenant_id: UUID, *, include_candidates: bool = True
    ) -> tuple[MemoryRecord, ...]:
        statement = select(MemoryRecordRow).where(
            MemoryRecordRow.tenant_id == str(tenant_id),
            MemoryRecordRow.state != MemoryState.DELETED,
        )
        if not include_candidates:
            statement = statement.where(MemoryRecordRow.state == MemoryState.PROMOTED)
        async with self._sessions() as session:
            rows = (await session.scalars(statement.order_by(MemoryRecordRow.updated_at))).all()
        return tuple(_memory(row) for row in rows)

    async def delete_memory(self, tenant_id: UUID, memory_id: UUID, actor_id: UUID) -> MemoryRecord:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(MemoryRecordRow)
                .where(
                    MemoryRecordRow.id == str(memory_id),
                    MemoryRecordRow.tenant_id == str(tenant_id),
                )
                .with_for_update()
            )
            if row is None:
                raise LifecycleNotFoundError("memory not found")
            if row.state is MemoryState.DELETED:
                raise LifecycleConflictError("memory is already deleted")
            now = datetime.now(UTC)
            row.state = MemoryState.DELETED
            row.deleted_at = _naive_utc(now)
            row.updated_at = _naive_utc(now)
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.MEMORY_DELETED,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload={"memory_id": str(memory_id)},
                    )
                )
            )
            return _memory(row)

    async def review_memory(
        self,
        tenant_id: UUID,
        memory_id: UUID,
        promote: bool,
        actor_id: UUID,
        replaces_memory_id: UUID | None = None,
    ) -> MemoryRecord:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(MemoryRecordRow)
                .where(
                    MemoryRecordRow.id == str(memory_id),
                    MemoryRecordRow.tenant_id == str(tenant_id),
                )
                .with_for_update()
            )
            if row is None:
                raise LifecycleNotFoundError("memory not found")
            if row.state is not MemoryState.CANDIDATE:
                raise LifecycleConflictError("only candidate memories can be reviewed")

            replacement = None
            if replaces_memory_id is not None:
                replacement = await session.scalar(
                    select(MemoryRecordRow)
                    .where(
                        MemoryRecordRow.id == str(replaces_memory_id),
                        MemoryRecordRow.id != str(memory_id),
                        MemoryRecordRow.tenant_id == str(tenant_id),
                        MemoryRecordRow.state == MemoryState.PROMOTED,
                    )
                    .with_for_update()
                )
                if replacement is None:
                    raise LifecycleNotFoundError("replacement memory not found")
                if not promote:
                    raise LifecycleConflictError("rejection cannot replace a memory")

            now = datetime.now(UTC)
            row.state = MemoryState.PROMOTED if promote else MemoryState.REJECTED
            row.confirmed = promote
            row.contradiction_of = str(replaces_memory_id) if replaces_memory_id else None
            row.reviewed_at = _naive_utc(now)
            row.reviewed_by = str(actor_id)
            row.updated_at = _naive_utc(now)
            event_type = EventType.MEMORY_PROMOTED if promote else EventType.MEMORY_REJECTED
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=event_type,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload={"memory_id": str(memory_id)},
                    )
                )
            )
            if replacement is not None:
                replacement.state = MemoryState.DELETED
                replacement.deleted_at = _naive_utc(now)
                replacement.updated_at = _naive_utc(now)
                session.add(
                    _event_row(
                        AuditEvent(
                            tenant_id=tenant_id,
                            event_type=EventType.MEMORY_CONTRADICTION_RESOLVED,
                            actor_type=ActorType.USER,
                            actor_id=actor_id,
                            payload={
                                "memory_id": str(memory_id),
                                "replaced_memory_id": replacement.id,
                            },
                        )
                    )
                )
            return _memory(row)

    async def create_conversation(
        self, conversation: Conversation, event: AuditEvent
    ) -> Conversation:
        async with self._sessions.begin() as session:
            session.add_all([_conversation_row(conversation), _event_row(event)])
        return conversation

    async def get_conversation(self, tenant_id: UUID, conversation_id: UUID) -> Conversation | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ConversationRow).where(
                    ConversationRow.id == str(conversation_id),
                    ConversationRow.tenant_id == str(tenant_id),
                )
            )
        return _conversation(row) if row is not None else None

    async def list_conversations(self, tenant_id: UUID) -> tuple[Conversation, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(ConversationRow)
                    .where(ConversationRow.tenant_id == str(tenant_id))
                    .order_by(ConversationRow.updated_at.desc())
                )
            ).all()
        return tuple(_conversation(row) for row in rows)

    async def append_message(
        self, message: ConversationMessage, events: tuple[AuditEvent, ...]
    ) -> ConversationMessage:
        async with self._sessions.begin() as session:
            conversation = await session.scalar(
                select(ConversationRow)
                .where(
                    ConversationRow.id == str(message.conversation_id),
                    ConversationRow.tenant_id == str(message.tenant_id),
                )
                .with_for_update()
            )
            if conversation is None:
                raise LifecycleNotFoundError("conversation not found")
            conversation.updated_at = _naive_utc(message.created_at)
            session.add(_conversation_message_row(message))
            session.add_all(_event_row(event) for event in events)
        return message

    async def list_messages(
        self, tenant_id: UUID, conversation_id: UUID, limit: int = 50
    ) -> tuple[ConversationMessage, ...]:
        async with self._sessions() as session:
            conversation = await session.scalar(
                select(ConversationRow.id).where(
                    ConversationRow.id == str(conversation_id),
                    ConversationRow.tenant_id == str(tenant_id),
                )
            )
            if conversation is None:
                raise LifecycleNotFoundError("conversation not found")
            if limit <= 0:
                return ()
            rows = (
                await session.scalars(
                    select(ConversationMessageRow)
                    .where(
                        ConversationMessageRow.tenant_id == str(tenant_id),
                        ConversationMessageRow.conversation_id == str(conversation_id),
                    )
                    .order_by(ConversationMessageRow.sequence.desc())
                    .limit(limit)
                )
            ).all()
        return tuple(_conversation_message(row) for row in reversed(rows))

    async def create_turn(
        self,
        user_message: ConversationMessage,
        turn: ConversationTurn,
        events: tuple[AuditEvent, ...],
        extraction_job: BackgroundJob,
    ) -> ConversationTurn:
        async with self._sessions.begin() as session:
            existing = await session.scalar(
                select(ConversationTurnRow).where(
                    ConversationTurnRow.tenant_id == str(turn.tenant_id),
                    ConversationTurnRow.client_request_id == str(turn.client_request_id),
                )
            )
            if existing is not None:
                return _conversation_turn(existing)
            conversation = await session.scalar(
                select(ConversationRow)
                .where(
                    ConversationRow.id == str(turn.conversation_id),
                    ConversationRow.tenant_id == str(turn.tenant_id),
                )
                .with_for_update()
            )
            if (
                conversation is None
                or user_message.tenant_id != turn.tenant_id
                or user_message.conversation_id != turn.conversation_id
                or user_message.id != turn.user_message_id
                or user_message.role is not MessageRole.USER
            ):
                raise LifecycleNotFoundError("conversation not found")
            candidate = _conversation_turn_row(turn)
            try:
                async with session.begin_nested():
                    session.add_all(
                        [
                            _conversation_message_row(user_message),
                            candidate,
                            _job_row(extraction_job),
                        ]
                    )
                    await session.flush()
            except IntegrityError:
                existing = await session.scalar(
                    select(ConversationTurnRow).where(
                        ConversationTurnRow.tenant_id == str(turn.tenant_id),
                        ConversationTurnRow.client_request_id == str(turn.client_request_id),
                    )
                )
                if existing is None:
                    raise LifecycleConflictError("turn creation conflict") from None
                return _conversation_turn(existing)
            conversation.updated_at = _naive_utc(user_message.created_at)
            session.add_all(_event_row(event) for event in events)
            return _conversation_turn(candidate)

    async def get_turn(self, tenant_id: UUID, turn_id: UUID) -> ConversationTurn | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ConversationTurnRow).where(
                    ConversationTurnRow.id == str(turn_id),
                    ConversationTurnRow.tenant_id == str(tenant_id),
                )
            )
        return _conversation_turn(row) if row is not None else None

    async def list_conversation_turns(
        self, tenant_id: UUID, conversation_id: UUID
    ) -> tuple[ConversationTurn, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(ConversationTurnRow)
                    .where(
                        ConversationTurnRow.tenant_id == str(tenant_id),
                        ConversationTurnRow.conversation_id == str(conversation_id),
                    )
                    .order_by(ConversationTurnRow.created_at)
                )
            ).all()
        return tuple(_conversation_turn(row) for row in rows)

    @staticmethod
    def _validate_run_lease_expiry(expires_at: datetime) -> None:
        now = datetime.now(UTC)
        if expires_at <= now or expires_at > now + timedelta(minutes=15):
            raise LifecycleConflictError("run lease must expire within 15 minutes")

    async def _active_turn(
        self, session: AsyncSession, tenant_id: UUID, turn_id: UUID, run_lease_id: UUID
    ) -> ConversationTurnRow:
        row = await session.scalar(
            select(ConversationTurnRow)
            .where(
                ConversationTurnRow.id == str(turn_id),
                ConversationTurnRow.tenant_id == str(tenant_id),
            )
            .with_for_update()
        )
        if row is None:
            raise LifecycleNotFoundError("turn not found")
        if (
            row.state is not ConversationTurnState.RUNNING
            or row.run_lease_id != str(run_lease_id)
            or row.run_lease_expires_at is None
            or row.run_lease_expires_at <= _naive_utc(datetime.now(UTC))
        ):
            raise LifecycleConflictError("turn is not actively claimed")
        return row

    async def claim_pending_turn(
        self, tenant_id: UUID, run_lease_id: UUID, run_lease_expires_at: datetime
    ) -> ConversationTurn | None:
        self._validate_run_lease_expiry(run_lease_expires_at)
        async with self._sessions.begin() as session:
            now = _naive_utc(datetime.now(UTC))
            row = await session.scalar(
                select(ConversationTurnRow)
                .where(
                    ConversationTurnRow.tenant_id == str(tenant_id),
                    or_(
                        ConversationTurnRow.state == ConversationTurnState.PENDING,
                        (ConversationTurnRow.state == ConversationTurnState.RUNNING)
                        & (ConversationTurnRow.run_lease_expires_at <= now),
                    ),
                )
                .order_by(ConversationTurnRow.created_at)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.state = ConversationTurnState.RUNNING
            row.run_lease_id = str(run_lease_id)
            row.run_lease_expires_at = _naive_utc(run_lease_expires_at)
            row.updated_at = now
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.CONVERSATION_TURN_RUNNING,
                        actor_type=ActorType.COORDINATOR,
                        payload={"turn_id": row.id},
                    )
                )
            )
            return _conversation_turn(row)

    async def claim_turn(
        self, tenant_id: UUID, turn_id: UUID, run_lease_id: UUID, run_lease_expires_at: datetime
    ) -> ConversationTurn | None:
        self._validate_run_lease_expiry(run_lease_expires_at)
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(ConversationTurnRow)
                .where(
                    ConversationTurnRow.id == str(turn_id),
                    ConversationTurnRow.tenant_id == str(tenant_id),
                    ConversationTurnRow.state == ConversationTurnState.PENDING,
                )
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            now = _naive_utc(datetime.now(UTC))
            row.state = ConversationTurnState.RUNNING
            row.run_lease_id = str(run_lease_id)
            row.run_lease_expires_at = _naive_utc(run_lease_expires_at)
            row.updated_at = now
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.CONVERSATION_TURN_RUNNING,
                        actor_type=ActorType.COORDINATOR,
                        payload={"turn_id": row.id},
                    )
                )
            )
            return _conversation_turn(row)

    async def checkpoint_turn(
        self,
        tenant_id: UUID,
        turn_id: UUID,
        run_lease_id: UUID,
        checkpoint: dict[str, Any],
    ) -> ConversationTurn:
        async with self._sessions.begin() as session:
            row = await self._active_turn(session, tenant_id, turn_id, run_lease_id)
            row.checkpoint = checkpoint
            row.updated_at = _naive_utc(datetime.now(UTC))
            return _conversation_turn(row)

    async def pause_turn(
        self,
        tenant_id: UUID,
        turn_id: UUID,
        run_lease_id: UUID,
        checkpoint: dict[str, Any],
    ) -> ConversationTurn:
        async with self._sessions.begin() as session:
            row = await self._active_turn(session, tenant_id, turn_id, run_lease_id)
            row.state = ConversationTurnState.PAUSED
            row.checkpoint = checkpoint
            row.run_lease_id = None
            row.run_lease_expires_at = None
            row.updated_at = _naive_utc(datetime.now(UTC))
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.CONVERSATION_TURN_PAUSED,
                        actor_type=ActorType.COORDINATOR,
                        payload={"turn_id": row.id},
                    )
                )
            )
            return _conversation_turn(row)

    async def create_tool_invocation(self, invocation: ToolInvocation) -> ToolInvocation:
        async with self._sessions.begin() as session:
            existing = await session.scalar(
                select(ToolInvocationRow).where(
                    ToolInvocationRow.turn_id == str(invocation.turn_id),
                    ToolInvocationRow.tool_call_id == invocation.tool_call_id,
                )
            )
            if existing is not None:
                return _tool_invocation(existing)
            turn = await session.scalar(
                select(ConversationTurnRow)
                .where(
                    ConversationTurnRow.id == str(invocation.turn_id),
                    ConversationTurnRow.tenant_id == str(invocation.tenant_id),
                )
                .with_for_update()
            )
            if turn is None:
                raise LifecycleNotFoundError("turn not found")
            row = _tool_invocation_row(invocation)
            session.add_all(
                [
                    row,
                    _event_row(
                        AuditEvent(
                            tenant_id=invocation.tenant_id,
                            event_type=EventType.TOOL_INVOCATION_REQUESTED,
                            actor_type=ActorType.COORDINATOR,
                            payload={"invocation_id": str(invocation.id)},
                        )
                    ),
                ]
            )
            return _tool_invocation(row)

    async def create_delegated_tasks(
        self,
        tenant_id: UUID,
        turn_id: UUID,
        run_lease_id: UUID,
        tasks: tuple[Task, ...],
        invocations: tuple[ToolInvocation, ...],
        checkpoint: dict[str, Any],
    ) -> tuple[Task, ...]:
        if len(tasks) != len(invocations) or not tasks:
            raise LifecycleConflictError("delegation tasks and invocations must match")
        async with self._sessions.begin() as session:
            turn = await self._active_turn(session, tenant_id, turn_id, run_lease_id)
            now = _naive_utc(datetime.now(UTC))
            agents = (
                await session.scalars(
                    select(RemoteAgentRow)
                    .where(
                        RemoteAgentRow.tenant_id == str(tenant_id),
                        RemoteAgentRow.status == RemoteAgentStatus.ACTIVE,
                        RemoteAgentRow.trust_level > 0,
                        RemoteAgentRow.last_seen_at >= now - timedelta(minutes=2),
                    )
                    .order_by(RemoteAgentRow.trust_level.desc(), RemoteAgentRow.id)
                )
            ).all()
            busy = set(
                (
                    await session.scalars(select(LeaseRow.ara_id).where(LeaseRow.expires_at > now))
                ).all()
            )
            selected: set[str] = set()
            assigned: list[Task] = []
            for task in tasks:
                capabilities = tuple(
                    item.model_dump(mode="json") for item in task.required_capabilities
                )
                agent = next(
                    (
                        candidate
                        for candidate in agents
                        if candidate.id not in busy | selected
                        and all(item in candidate.capabilities for item in capabilities)
                    ),
                    None,
                )
                if agent is None:
                    raise LifecycleConflictError("no eligible trusted ARA for planned task")
                selected.add(agent.id)
                assigned.append(task.model_copy(update={"target_ara_id": UUID(agent.id)}))
            for task, invocation in zip(assigned, invocations, strict=True):
                if invocation.task_id != task.id or invocation.turn_id != turn_id:
                    raise LifecycleConflictError("delegation invocation ownership mismatch")
                session.add_all((_task_row(task), _tool_invocation_row(invocation)))
                session.add_all(
                    (
                        _event_row(
                            AuditEvent(
                                tenant_id=tenant_id,
                                event_type=EventType.TASK_CREATED,
                                actor_type=ActorType.COORDINATOR,
                                task_id=task.id,
                                payload={
                                    "turn_id": str(turn_id),
                                    "target_ara_id": str(task.target_ara_id),
                                },
                            )
                        ),
                        _event_row(
                            AuditEvent(
                                tenant_id=tenant_id,
                                event_type=EventType.TOOL_INVOCATION_REQUESTED,
                                actor_type=ActorType.COORDINATOR,
                                payload={"invocation_id": str(invocation.id)},
                            )
                        ),
                    )
                )
            (
                turn.state,
                turn.checkpoint,
                turn.run_lease_id,
                turn.run_lease_expires_at,
                turn.updated_at,
            ) = (ConversationTurnState.PAUSED, checkpoint, None, None, now)
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.CONVERSATION_TURN_PAUSED,
                        actor_type=ActorType.COORDINATOR,
                        payload={"turn_id": str(turn_id)},
                    )
                )
            )
            return tuple(assigned)

    async def get_tool_invocation(
        self, tenant_id: UUID, turn_id: UUID, tool_call_id: str
    ) -> ToolInvocation | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ToolInvocationRow).where(
                    ToolInvocationRow.tenant_id == str(tenant_id),
                    ToolInvocationRow.turn_id == str(turn_id),
                    ToolInvocationRow.tool_call_id == tool_call_id,
                )
            )
        return _tool_invocation(row) if row is not None else None

    async def _finish_tool_invocation(
        self, tenant_id: UUID, invocation_id: UUID, state: ToolInvocationState
    ) -> ToolInvocation:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(ToolInvocationRow)
                .where(
                    ToolInvocationRow.id == str(invocation_id),
                    ToolInvocationRow.tenant_id == str(tenant_id),
                )
                .with_for_update()
            )
            if row is None:
                raise LifecycleNotFoundError("tool invocation not found")
            if row.state in {
                ToolInvocationState.COMPLETED,
                ToolInvocationState.FAILED,
                ToolInvocationState.DENIED,
            }:
                if row.state is state:
                    return _tool_invocation(row)
                raise LifecycleConflictError("tool invocation is already finished")
            now = _naive_utc(datetime.now(UTC))
            row.state = state
            row.updated_at = now
            row.completed_at = now
            event_type = (
                EventType.TOOL_INVOCATION_COMPLETED
                if state is ToolInvocationState.COMPLETED
                else EventType.TOOL_INVOCATION_FAILED
            )
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=event_type,
                        actor_type=ActorType.COORDINATOR,
                        payload={"invocation_id": str(invocation_id)},
                    )
                )
            )
            return _tool_invocation(row)

    async def complete_tool_invocation(
        self, tenant_id: UUID, invocation_id: UUID
    ) -> ToolInvocation:
        return await self._finish_tool_invocation(
            tenant_id, invocation_id, ToolInvocationState.COMPLETED
        )

    async def fail_tool_invocation(self, tenant_id: UUID, invocation_id: UUID) -> ToolInvocation:
        return await self._finish_tool_invocation(
            tenant_id, invocation_id, ToolInvocationState.FAILED
        )

    async def create_coordinator_approval(self, approval: Approval) -> Approval:
        if (
            approval.requestor_type is not ActorType.COORDINATOR
            or approval.tool_invocation_id is None
        ):
            raise LifecycleConflictError("coordinator approval must reference a tool invocation")
        async with self._sessions.begin() as session:
            invocation = await session.scalar(
                select(ToolInvocationRow)
                .where(
                    ToolInvocationRow.id == str(approval.tool_invocation_id),
                    ToolInvocationRow.tenant_id == str(approval.tenant_id),
                )
                .with_for_update()
            )
            if invocation is None:
                raise LifecycleNotFoundError("tool invocation not found")
            if invocation.state is not ToolInvocationState.PENDING:
                raise LifecycleConflictError("tool invocation cannot await approval")
            turn = await session.scalar(
                select(ConversationTurnRow)
                .where(ConversationTurnRow.id == invocation.turn_id)
                .with_for_update()
            )
            if turn is None:
                raise LifecycleNotFoundError("turn not found")
            now = _naive_utc(datetime.now(UTC))
            invocation.state = ToolInvocationState.AWAITING_APPROVAL
            invocation.updated_at = now
            turn.state = ConversationTurnState.PAUSED
            turn.run_lease_id = None
            turn.run_lease_expires_at = None
            turn.updated_at = now
            session.add_all(
                [
                    _approval_row(approval),
                    _event_row(
                        AuditEvent(
                            tenant_id=approval.tenant_id,
                            event_type=EventType.APPROVAL_REQUESTED,
                            actor_type=ActorType.COORDINATOR,
                            payload={"approval_id": str(approval.id)},
                        )
                    ),
                ]
            )
            return approval

    async def complete_turn(
        self,
        tenant_id: UUID,
        turn_id: UUID,
        run_lease_id: UUID,
        assistant_message: ConversationMessage,
    ) -> ConversationTurn:
        async with self._sessions.begin() as session:
            row = await self._active_turn(session, tenant_id, turn_id, run_lease_id)
            if (
                assistant_message.tenant_id != tenant_id
                or assistant_message.conversation_id != UUID(row.conversation_id)
                or assistant_message.role is not MessageRole.ASSISTANT
            ):
                raise LifecycleConflictError("assistant message ownership mismatch")
            conversation = await session.scalar(
                select(ConversationRow)
                .where(ConversationRow.id == row.conversation_id)
                .with_for_update()
            )
            if conversation is None:
                raise LifecycleNotFoundError("conversation not found")
            now = _naive_utc(datetime.now(UTC))
            row.state = ConversationTurnState.COMPLETED
            row.run_lease_id = None
            row.run_lease_expires_at = None
            row.updated_at = now
            row.completed_at = now
            conversation.updated_at = _naive_utc(assistant_message.created_at)
            session.add_all(
                [
                    _conversation_message_row(assistant_message),
                    _event_row(
                        AuditEvent(
                            tenant_id=tenant_id,
                            event_type=EventType.CONVERSATION_TURN_COMPLETED,
                            actor_type=ActorType.COORDINATOR,
                            payload={"turn_id": str(turn_id)},
                        )
                    ),
                ]
            )
            return _conversation_turn(row)

    async def register_ara(self, remote_agent: RemoteAgent, event: AuditEvent) -> RemoteAgent:
        async with self._sessions.begin() as session:
            existing = await session.get(RemoteAgentRow, str(remote_agent.id))
            if existing is not None and existing.tenant_id == str(remote_agent.tenant_id):
                remote_agent = remote_agent.model_copy(
                    update={"status": existing.status, "trust_level": existing.trust_level}
                )
            await session.merge(_remote_agent_row(remote_agent))
            session.add(_event_row(event))
        return remote_agent

    async def add_task(self, task: Task, event: AuditEvent) -> Task:
        async with self._sessions.begin() as session:
            session.add_all([_task_row(task), _event_row(event)])
        return task

    async def get_task(self, tenant_id: UUID, task_id: UUID) -> Task | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(TaskRow).where(
                    TaskRow.id == str(task_id),
                    TaskRow.tenant_id == str(tenant_id),
                )
            )
        return _task(row) if row is not None else None

    async def request_task_cancellation(
        self, tenant_id: UUID, task_id: UUID, actor_id: UUID
    ) -> Task:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(TaskRow)
                .where(TaskRow.id == str(task_id), TaskRow.tenant_id == str(tenant_id))
                .with_for_update()
            )
            if row is None:
                raise LifecycleNotFoundError("task not found")
            if row.state is not TaskState.LEASED:
                raise LifecycleConflictError("task is not leased")
            row.state = TaskState.CANCELLING
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.TASK_CANCELLED,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        task_id=task_id,
                        payload={"requested": True},
                    )
                )
            )
            return _task(row)

    async def heartbeat(
        self,
        tenant_id: UUID,
        ara_id: UUID,
        task_id: UUID | None = None,
        lease_id: UUID | None = None,
    ) -> Task | None:
        async with self._sessions.begin() as session:
            ara = await session.scalar(
                select(RemoteAgentRow)
                .where(RemoteAgentRow.id == str(ara_id), RemoteAgentRow.tenant_id == str(tenant_id))
                .with_for_update()
            )
            if ara is None:
                raise LifecycleNotFoundError("ARA not found")
            ara.last_seen_at = _naive_utc(datetime.now(UTC))
            task: Task | None = None
            if task_id is not None or lease_id is not None:
                if task_id is None or lease_id is None:
                    raise LifecycleConflictError("task and lease are required together")
                row, _ = await self._active_lease(session, tenant_id, ara_id, task_id, lease_id)
                if row.state not in {TaskState.LEASED, TaskState.CANCELLING}:
                    raise LifecycleConflictError("task is not active")
                task = _task(row)
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.ARA_HEARTBEAT,
                        actor_type=ActorType.ARA,
                        actor_id=ara_id,
                        task_id=task_id,
                        payload={"lease_id": str(lease_id) if lease_id else None},
                    )
                )
            )
            return task

    async def list_remote_agents(self, tenant_id: UUID) -> tuple[RemoteAgent, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(RemoteAgentRow).where(RemoteAgentRow.tenant_id == str(tenant_id))
                )
            ).all()
        now = datetime.now(UTC)
        return tuple(
            _remote_agent(row).model_copy(update={"status": RemoteAgentStatus.OFFLINE})
            if row.status is RemoteAgentStatus.ACTIVE
            and now - _aware_utc(row.last_seen_at) > timedelta(minutes=2)
            else _remote_agent(row)
            for row in rows
        )

    async def set_remote_agent_trust(
        self, tenant_id: UUID, ara_id: UUID, trust_level: int, actor_id: UUID
    ) -> RemoteAgent:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(RemoteAgentRow)
                .where(RemoteAgentRow.id == str(ara_id), RemoteAgentRow.tenant_id == str(tenant_id))
                .with_for_update()
            )
            if row is None:
                raise LifecycleNotFoundError("ARA not found")
            if row.status is RemoteAgentStatus.REVOKED:
                raise LifecycleConflictError("revoked ARA cannot regain trust")
            row.trust_level = trust_level
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.ARA_TRUST_UPDATED,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload={"ara_id": str(ara_id), "trust_level": trust_level},
                    )
                )
            )
            return _remote_agent(row)

    async def revoke_remote_agent(
        self, tenant_id: UUID, ara_id: UUID, actor_id: UUID
    ) -> RemoteAgent:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(RemoteAgentRow)
                .where(RemoteAgentRow.id == str(ara_id), RemoteAgentRow.tenant_id == str(tenant_id))
                .with_for_update()
            )
            if row is None:
                raise LifecycleNotFoundError("ARA not found")
            row.status = RemoteAgentStatus.REVOKED
            row.trust_level = 0
            await session.execute(
                select(LeaseRow)
                .where(LeaseRow.tenant_id == str(tenant_id), LeaseRow.ara_id == str(ara_id))
                .with_for_update()
            )
            now = _naive_utc(datetime.now(UTC))
            for lease in (
                await session.scalars(
                    select(LeaseRow).where(
                        LeaseRow.tenant_id == str(tenant_id), LeaseRow.ara_id == str(ara_id)
                    )
                )
            ).all():
                lease.expires_at = now
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.ARA_REVOKED,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload={"ara_id": str(ara_id)},
                    )
                )
            )
            return _remote_agent(row)

    async def get_artifact(self, tenant_id: UUID, artifact_id: UUID) -> Artifact | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ArtifactRow).where(
                    ArtifactRow.id == str(artifact_id), ArtifactRow.tenant_id == str(tenant_id)
                )
            )
        return _artifact(row) if row is not None else None

    async def list_artifacts(self, tenant_id: UUID) -> tuple[Artifact, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(ArtifactRow).where(
                        ArtifactRow.tenant_id == str(tenant_id), ArtifactRow.deleted_at.is_(None)
                    )
                )
            ).all()
        return tuple(_artifact(row) for row in rows)

    async def delete_artifact(
        self, tenant_id: UUID, artifact_id: UUID, actor_id: UUID, retention_until: datetime
    ) -> Artifact:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(ArtifactRow)
                .where(ArtifactRow.id == str(artifact_id), ArtifactRow.tenant_id == str(tenant_id))
                .with_for_update()
            )
            if row is None or row.deleted_at is not None:
                raise LifecycleNotFoundError("artifact not found")
            row.deleted_at = _naive_utc(datetime.now(UTC))
            row.retention_until = _naive_utc(retention_until)
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.ARTIFACT_DELETED,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        task_id=UUID(row.task_id),
                        payload={
                            "artifact_id": str(artifact_id),
                            "retention_until": retention_until.isoformat(),
                        },
                    )
                )
            )
            return _artifact(row)

    async def lease_task(
        self, tenant_id: UUID, ara_id: UUID, expires_at: datetime
    ) -> tuple[Task, Lease] | None:
        async with self._sessions.begin() as session:
            remote_agent = await session.get(RemoteAgentRow, str(ara_id))
            if remote_agent is None or remote_agent.tenant_id != str(tenant_id):
                return None
            if remote_agent.status is not RemoteAgentStatus.ACTIVE or remote_agent.trust_level <= 0:
                return None
            if _naive_utc(datetime.now(UTC)) - remote_agent.last_seen_at > timedelta(minutes=2):
                return None
            statement = (
                select(TaskRow)
                .outerjoin(LeaseRow, LeaseRow.task_id == TaskRow.id)
                .where(
                    TaskRow.tenant_id == str(tenant_id),
                    or_(
                        TaskRow.state == TaskState.PENDING,
                        LeaseRow.expires_at <= _naive_utc(datetime.now(UTC)),
                    ),
                )
                .order_by(TaskRow.created_at)
                .with_for_update(skip_locked=True)
            )
            rows = (await session.scalars(statement)).all()
            ara_capabilities = tuple(
                Capability.model_validate(item) for item in remote_agent.capabilities
            )
            row = next(
                (
                    candidate
                    for candidate in rows
                    if all(
                        Capability.model_validate(item) in ara_capabilities
                        for item in candidate.required_capabilities
                    )
                    and (candidate.target_ara_id is None or candidate.target_ara_id == str(ara_id))
                ),
                None,
            )
            if row is None:
                return None
            row.state = TaskState.LEASED
            lease = Lease(
                tenant_id=tenant_id,
                task_id=UUID(row.id),
                ara_id=ara_id,
                expires_at=expires_at,
            )
            existing_lease = await session.scalar(
                select(LeaseRow).where(LeaseRow.task_id == row.id).with_for_update()
            )
            if existing_lease is None:
                session.add(
                    LeaseRow(
                        id=str(lease.id),
                        tenant_id=str(tenant_id),
                        task_id=row.id,
                        ara_id=str(ara_id),
                        acquired_at=_naive_utc(lease.acquired_at),
                        expires_at=_naive_utc(lease.expires_at),
                    )
                )
            else:
                existing_lease.id = str(lease.id)
                existing_lease.ara_id = str(ara_id)
                existing_lease.acquired_at = _naive_utc(lease.acquired_at)
                existing_lease.expires_at = _naive_utc(lease.expires_at)
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.TASK_LEASED,
                        actor_type=ActorType.ARA,
                        actor_id=ara_id,
                        task_id=UUID(row.id),
                        payload={"lease_id": str(lease.id), "expires_at": expires_at.isoformat()},
                    )
                )
            )
            task = _task(row).model_copy(update={"state": TaskState.LEASED})
            return task, lease

    async def _active_lease(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        ara_id: UUID,
        task_id: UUID,
        lease_id: UUID,
    ) -> tuple[TaskRow, LeaseRow]:
        statement = (
            select(TaskRow, LeaseRow)
            .join(LeaseRow, LeaseRow.task_id == TaskRow.id)
            .join(RemoteAgentRow, RemoteAgentRow.id == LeaseRow.ara_id)
            .where(
                TaskRow.id == str(task_id),
                TaskRow.tenant_id == str(tenant_id),
                LeaseRow.id == str(lease_id),
                LeaseRow.tenant_id == str(tenant_id),
                LeaseRow.task_id == str(task_id),
                LeaseRow.ara_id == str(ara_id),
                RemoteAgentRow.id == str(ara_id),
                RemoteAgentRow.tenant_id == str(tenant_id),
                RemoteAgentRow.status == RemoteAgentStatus.ACTIVE,
                RemoteAgentRow.trust_level > 0,
            )
            .with_for_update()
        )
        rows = (await session.execute(statement)).one_or_none()
        if rows is None:
            raise LifecycleNotFoundError("task or lease not found")
        task, lease = rows
        if task.state not in {TaskState.LEASED, TaskState.CANCELLING}:
            raise LifecycleConflictError("task is not leased")
        if lease.expires_at <= _naive_utc(datetime.now(UTC)):
            raise LifecycleConflictError("lease has expired")
        return task, lease

    async def record_progress(
        self,
        tenant_id: UUID,
        ara_id: UUID,
        task_id: UUID,
        lease_id: UUID,
        message: str,
        progress_percent: int | None,
    ) -> None:
        async with self._sessions.begin() as session:
            await self._active_lease(session, tenant_id, ara_id, task_id, lease_id)
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.ARA_PROGRESS,
                        actor_type=ActorType.ARA,
                        actor_id=ara_id,
                        task_id=task_id,
                        payload={"message": message, "progress_percent": progress_percent},
                    )
                )
            )

    async def renew_lease(
        self,
        tenant_id: UUID,
        ara_id: UUID,
        task_id: UUID,
        lease_id: UUID,
        expires_at: datetime,
    ) -> Lease:
        async with self._sessions.begin() as session:
            _, row = await self._active_lease(session, tenant_id, ara_id, task_id, lease_id)
            row.expires_at = _naive_utc(expires_at)
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.LEASE_RENEWED,
                        actor_type=ActorType.ARA,
                        actor_id=ara_id,
                        task_id=task_id,
                        payload={"lease_id": str(lease_id), "expires_at": expires_at.isoformat()},
                    )
                )
            )
            return _lease(row)

    async def finish_task(
        self,
        tenant_id: UUID,
        ara_id: UUID,
        task_id: UUID,
        lease_id: UUID,
        state: TaskState,
        detail: str,
        artifacts: tuple[Artifact, ...] = (),
    ) -> Task:
        if state not in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}:
            raise LifecycleConflictError("invalid terminal task state")
        async with self._sessions.begin() as session:
            row, _ = await self._active_lease(session, tenant_id, ara_id, task_id, lease_id)
            if state is TaskState.COMPLETED:
                approvals = (
                    await session.scalars(
                        select(ApprovalRow).where(
                            ApprovalRow.tenant_id == str(tenant_id),
                            ApprovalRow.task_id == str(task_id),
                            ApprovalRow.state == ApprovalState.GRANTED,
                        )
                    )
                ).all()
                granted = tuple(_approval(item) for item in approvals)
                required = tuple(
                    Capability.model_validate(item) for item in row.required_capabilities
                )
                for capability in required:
                    approval_state = next(
                        (item.state for item in granted if item.capability == capability), None
                    )
                    decision = evaluate_capability(capability, required, approval_state)
                    if not decision.allowed:
                        raise LifecycleConflictError(decision.reason)
            for artifact in artifacts:
                if artifact.tenant_id != tenant_id or artifact.task_id != task_id:
                    raise LifecycleConflictError("artifact ownership mismatch")
            row.state = state
            row.result = detail
            now = _naive_utc(datetime.now(UTC))
            row.completed_at = now
            row.completed_by_ara_id = str(ara_id)
            invocation = await session.scalar(
                select(ToolInvocationRow)
                .where(
                    ToolInvocationRow.task_id == row.id,
                    ToolInvocationRow.tenant_id == str(tenant_id),
                )
                .with_for_update()
            )
            if invocation is not None:
                turn = await session.scalar(
                    select(ConversationTurnRow)
                    .where(ConversationTurnRow.id == invocation.turn_id)
                    .with_for_update()
                )
                if turn is None:
                    raise LifecycleNotFoundError("turn not found")
                invocation.state = (
                    ToolInvocationState.COMPLETED
                    if state is TaskState.COMPLETED
                    else ToolInvocationState.FAILED
                )
                invocation.updated_at = now
                invocation.completed_at = now
                siblings = (
                    await session.scalars(
                        select(ToolInvocationRow).where(
                            ToolInvocationRow.turn_id == invocation.turn_id,
                            ToolInvocationRow.target == ToolInvocationTarget.ARA,
                        )
                    )
                ).all()
                if siblings and all(
                    item.state
                    in {
                        ToolInvocationState.COMPLETED,
                        ToolInvocationState.FAILED,
                        ToolInvocationState.DENIED,
                    }
                    for item in siblings
                ):
                    turn.state = ConversationTurnState.PENDING
                    turn.updated_at = now
            event_type = {
                TaskState.COMPLETED: EventType.TASK_COMPLETED,
                TaskState.FAILED: EventType.TASK_FAILED,
                TaskState.CANCELLED: EventType.TASK_CANCELLED,
            }[state]
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=event_type,
                        actor_type=ActorType.ARA,
                        actor_id=ara_id,
                        task_id=task_id,
                        payload={"detail": detail},
                    )
                )
            )
            for artifact in artifacts:
                session.add_all(
                    [
                        _artifact_row(artifact),
                        _event_row(
                            AuditEvent(
                                tenant_id=tenant_id,
                                event_type=EventType.ARTIFACT_PRODUCED,
                                actor_type=ActorType.ARA,
                                actor_id=ara_id,
                                task_id=task_id,
                                payload={
                                    "artifact_id": str(artifact.id),
                                    "object_key": artifact.object_key,
                                },
                            )
                        ),
                    ]
                )
            return _task(row)

    async def request_approval(
        self,
        tenant_id: UUID,
        ara_id: UUID,
        task_id: UUID,
        lease_id: UUID,
        approval: Approval,
    ) -> Approval:
        async with self._sessions.begin() as session:
            task_row, _ = await self._active_lease(session, tenant_id, ara_id, task_id, lease_id)
            if (
                approval.tenant_id != tenant_id
                or approval.task_id != task_id
                or approval.requested_by != ara_id
                or approval.requestor_type is not ActorType.ARA
            ):
                raise LifecycleConflictError("approval ownership mismatch")
            required_capabilities = tuple(
                Capability.model_validate(item) for item in task_row.required_capabilities
            )
            if approval.capability not in required_capabilities:
                raise LifecycleConflictError("capability is not part of the task")
            session.add_all(
                [
                    _approval_row(approval),
                    _event_row(
                        AuditEvent(
                            tenant_id=tenant_id,
                            event_type=EventType.APPROVAL_REQUESTED,
                            actor_type=ActorType.ARA,
                            actor_id=ara_id,
                            task_id=task_id,
                            payload={
                                "approval_id": str(approval.id),
                                "reason": approval.reason,
                            },
                        )
                    ),
                ]
            )
            return approval

    async def decide_approval(
        self, tenant_id: UUID, approval_id: UUID, state: ApprovalState, actor_id: UUID
    ) -> Approval:
        if state not in {ApprovalState.GRANTED, ApprovalState.DENIED}:
            raise LifecycleConflictError("invalid approval decision")
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(ApprovalRow)
                .where(
                    ApprovalRow.id == str(approval_id),
                    ApprovalRow.tenant_id == str(tenant_id),
                )
                .with_for_update()
            )
            if row is None:
                raise LifecycleNotFoundError("approval not found")
            if row.state is state:
                return _approval(row)
            if row.state is not ApprovalState.PENDING:
                raise LifecycleConflictError("approval is already decided")
            row.state = state
            row.decided_at = _naive_utc(datetime.now(UTC))
            row.decided_by = str(actor_id)
            if row.tool_invocation_id is not None:
                invocation = await session.scalar(
                    select(ToolInvocationRow)
                    .where(ToolInvocationRow.id == row.tool_invocation_id)
                    .with_for_update()
                )
                if invocation is None:
                    raise LifecycleNotFoundError("tool invocation not found")
                turn = await session.scalar(
                    select(ConversationTurnRow)
                    .where(ConversationTurnRow.id == invocation.turn_id)
                    .with_for_update()
                )
                if turn is None:
                    raise LifecycleNotFoundError("turn not found")
                now = _naive_utc(datetime.now(UTC))
                invocation.state = (
                    ToolInvocationState.PENDING
                    if state is ApprovalState.GRANTED
                    else ToolInvocationState.DENIED
                )
                invocation.updated_at = now
                invocation.completed_at = now if state is ApprovalState.DENIED else None
                turn.state = ConversationTurnState.PENDING
                turn.updated_at = now
                if state is ApprovalState.DENIED:
                    session.add(
                        _event_row(
                            AuditEvent(
                                tenant_id=tenant_id,
                                event_type=EventType.TOOL_INVOCATION_DENIED,
                                actor_type=ActorType.USER,
                                actor_id=actor_id,
                                payload={"invocation_id": invocation.id},
                            )
                        )
                    )
            session.add(
                _event_row(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=(
                            EventType.APPROVAL_GRANTED
                            if state is ApprovalState.GRANTED
                            else EventType.APPROVAL_DENIED
                        ),
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        task_id=UUID(row.task_id) if row.task_id else None,
                        payload={"approval_id": str(approval_id)},
                    )
                )
            )
            return _approval(row)

    async def list_approvals(self, tenant_id: UUID) -> tuple[Approval, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(ApprovalRow)
                    .where(ApprovalRow.tenant_id == str(tenant_id))
                    .order_by(ApprovalRow.created_at)
                )
            ).all()
        return tuple(_approval(row) for row in rows)

    async def list_tasks(self, tenant_id: UUID) -> tuple[Task, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(TaskRow)
                    .where(TaskRow.tenant_id == str(tenant_id))
                    .order_by(TaskRow.created_at)
                )
            ).all()
        return tuple(_task(row) for row in rows)

    async def append_event(self, event: AuditEvent) -> None:
        async with self._sessions.begin() as session:
            session.add(_event_row(event))

    async def list_events(self, tenant_id: UUID) -> tuple[AuditEvent, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(AuditEventRow)
                    .where(AuditEventRow.tenant_id == str(tenant_id))
                    .order_by(AuditEventRow.occurred_at)
                )
            ).all()
        return tuple(
            AuditEvent(
                id=UUID(row.id),
                tenant_id=UUID(row.tenant_id),
                event_type=row.event_type,
                actor_type=row.actor_type,
                actor_id=UUID(row.actor_id) if row.actor_id else None,
                task_id=UUID(row.task_id) if row.task_id else None,
                payload=row.payload,
                occurred_at=_aware_utc(row.occurred_at),
            )
            for row in rows
        )

    async def close(self) -> None:
        await self._engine.dispose()


async def create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
