from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from astra_domain import (
    ActorType,
    Approval,
    ApprovalState,
    Artifact,
    AuditEvent,
    Capability,
    Conversation,
    ConversationMessage,
    ConversationTurn,
    ConversationTurnState,
    EventType,
    Lease,
    MemoryKind,
    MemoryRecord,
    MemoryState,
    MessageRole,
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
        registered_at=_naive_utc(remote_agent.registered_at),
        last_seen_at=_naive_utc(remote_agent.last_seen_at),
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


class MariaDBRuntimeStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

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
                    session.add_all([_conversation_message_row(user_message), candidate])
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

    async def lease_task(
        self, tenant_id: UUID, ara_id: UUID, expires_at: datetime
    ) -> tuple[Task, Lease] | None:
        async with self._sessions.begin() as session:
            remote_agent = await session.get(RemoteAgentRow, str(ara_id))
            if remote_agent is None or remote_agent.tenant_id != str(tenant_id):
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
            )
            .with_for_update()
        )
        rows = (await session.execute(statement)).one_or_none()
        if rows is None:
            raise LifecycleNotFoundError("task or lease not found")
        task, lease = rows
        if task.state is not TaskState.LEASED:
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
