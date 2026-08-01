import asyncio
from datetime import UTC, datetime
from uuid import UUID

from astra_domain import (
    ActorType,
    Approval,
    ApprovalState,
    Artifact,
    AuditEvent,
    Conversation,
    ConversationMessage,
    EventType,
    Lease,
    MemoryRecord,
    MemoryState,
    RemoteAgent,
    Task,
    TaskState,
)
from astra_policy import evaluate_capability

from astra_runtime.repositories import LifecycleConflictError, LifecycleNotFoundError


class InMemoryRuntimeStore:
    def __init__(self) -> None:
        self._remote_agents: dict[UUID, RemoteAgent] = {}
        self._conversations: dict[UUID, Conversation] = {}
        self._messages: dict[UUID, ConversationMessage] = {}
        self._memories: dict[UUID, MemoryRecord] = {}
        self._tasks: dict[UUID, Task] = {}
        self._leases: dict[UUID, Lease] = {}
        self._events: list[AuditEvent] = []
        self._approvals: dict[UUID, Approval] = {}
        self._artifacts: dict[UUID, Artifact] = {}
        self._lock = asyncio.Lock()

    async def upsert_memory(
        self, memory: MemoryRecord, events: tuple[AuditEvent, ...]
    ) -> MemoryRecord:
        async with self._lock:
            duplicate = next(
                (
                    item
                    for item in self._memories.values()
                    if item.tenant_id == memory.tenant_id
                    and item.normalized_content == memory.normalized_content
                    and item.state is not MemoryState.DELETED
                ),
                None,
            )
            if duplicate is not None:
                return duplicate
            self._memories[memory.id] = memory
            self._events.extend(events)
            return memory

    async def get_memory(self, tenant_id: UUID, memory_id: UUID) -> MemoryRecord | None:
        async with self._lock:
            memory = self._memories.get(memory_id)
            if memory is None or memory.tenant_id != tenant_id:
                return None
            return memory

    async def list_memories(
        self, tenant_id: UUID, *, include_candidates: bool = True
    ) -> tuple[MemoryRecord, ...]:
        async with self._lock:
            return tuple(
                item
                for item in sorted(self._memories.values(), key=lambda value: value.updated_at)
                if item.tenant_id == tenant_id
                and item.state is not MemoryState.DELETED
                and (include_candidates or item.state is MemoryState.PROMOTED)
            )

    async def delete_memory(self, tenant_id: UUID, memory_id: UUID, actor_id: UUID) -> MemoryRecord:
        async with self._lock:
            memory = self._memories.get(memory_id)
            if memory is None or memory.tenant_id != tenant_id:
                raise LifecycleNotFoundError("memory not found")
            if memory.state is MemoryState.DELETED:
                raise LifecycleConflictError("memory is already deleted")
            now = datetime.now(UTC)
            deleted = memory.model_copy(
                update={"state": MemoryState.DELETED, "deleted_at": now, "updated_at": now}
            )
            self._memories[memory_id] = deleted
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.MEMORY_DELETED,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    payload={"memory_id": str(memory_id)},
                )
            )
            return deleted

    async def review_memory(
        self,
        tenant_id: UUID,
        memory_id: UUID,
        promote: bool,
        actor_id: UUID,
        replaces_memory_id: UUID | None = None,
    ) -> MemoryRecord:
        async with self._lock:
            memory = self._memories.get(memory_id)
            if memory is None or memory.tenant_id != tenant_id:
                raise LifecycleNotFoundError("memory not found")
            if memory.state is not MemoryState.CANDIDATE:
                raise LifecycleConflictError("only candidate memories can be reviewed")
            replacement = None
            if replaces_memory_id is not None:
                replacement = self._memories.get(replaces_memory_id)
                if (
                    replacement is None
                    or replacement.tenant_id != tenant_id
                    or replacement.state is not MemoryState.PROMOTED
                ):
                    raise LifecycleNotFoundError("replacement memory not found")
                if not promote:
                    raise LifecycleConflictError("rejection cannot replace a memory")
            now = datetime.now(UTC)
            state = MemoryState.PROMOTED if promote else MemoryState.REJECTED
            reviewed = memory.model_copy(
                update={
                    "state": state,
                    "confirmed": promote,
                    "contradiction_of": replaces_memory_id,
                    "reviewed_at": now,
                    "reviewed_by": actor_id,
                    "updated_at": now,
                }
            )
            self._memories[memory_id] = reviewed
            event_type = EventType.MEMORY_PROMOTED if promote else EventType.MEMORY_REJECTED
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=event_type,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    payload={"memory_id": str(memory_id)},
                )
            )
            if replacement is not None:
                self._memories[replacement.id] = replacement.model_copy(
                    update={
                        "state": MemoryState.DELETED,
                        "deleted_at": now,
                        "updated_at": now,
                    }
                )
                self._events.append(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.MEMORY_CONTRADICTION_RESOLVED,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload={
                            "memory_id": str(memory_id),
                            "replaced_memory_id": str(replacement.id),
                        },
                    )
                )
            return reviewed

    async def create_conversation(
        self, conversation: Conversation, event: AuditEvent
    ) -> Conversation:
        async with self._lock:
            self._conversations[conversation.id] = conversation
            self._events.append(event)
        return conversation

    async def get_conversation(self, tenant_id: UUID, conversation_id: UUID) -> Conversation | None:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.tenant_id != tenant_id:
                return None
            return conversation

    async def list_conversations(self, tenant_id: UUID) -> tuple[Conversation, ...]:
        async with self._lock:
            return tuple(
                item
                for item in sorted(
                    self._conversations.values(), key=lambda value: value.updated_at, reverse=True
                )
                if item.tenant_id == tenant_id
            )

    async def append_message(
        self, message: ConversationMessage, events: tuple[AuditEvent, ...]
    ) -> ConversationMessage:
        async with self._lock:
            conversation = self._conversations.get(message.conversation_id)
            if conversation is None or conversation.tenant_id != message.tenant_id:
                raise LifecycleNotFoundError("conversation not found")
            self._messages[message.id] = message
            self._conversations[conversation.id] = conversation.model_copy(
                update={"updated_at": message.created_at}
            )
            self._events.extend(events)
        return message

    async def list_messages(
        self, tenant_id: UUID, conversation_id: UUID, limit: int = 50
    ) -> tuple[ConversationMessage, ...]:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.tenant_id != tenant_id:
                raise LifecycleNotFoundError("conversation not found")
            messages = sorted(
                (
                    item
                    for item in self._messages.values()
                    if item.tenant_id == tenant_id and item.conversation_id == conversation_id
                ),
                key=lambda item: item.created_at,
            )
            return tuple(messages[-limit:])

    async def register_ara(self, remote_agent: RemoteAgent, event: AuditEvent) -> RemoteAgent:
        async with self._lock:
            self._remote_agents[remote_agent.id] = remote_agent
            self._events.append(event)
        return remote_agent

    async def add_task(self, task: Task, event: AuditEvent) -> Task:
        async with self._lock:
            self._tasks[task.id] = task
            self._events.append(event)
        return task

    async def get_task(self, tenant_id: UUID, task_id: UUID) -> Task | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.tenant_id != tenant_id:
                return None
            return task

    async def lease_task(
        self, tenant_id: UUID, ara_id: UUID, expires_at: datetime
    ) -> tuple[Task, Lease] | None:
        async with self._lock:
            remote_agent = self._remote_agents.get(ara_id)
            if remote_agent is None or remote_agent.tenant_id != tenant_id:
                return None
            now = datetime.now(expires_at.tzinfo)
            expired_task_ids = {
                lease.task_id for lease in self._leases.values() if lease.expires_at <= now
            }
            for task in sorted(self._tasks.values(), key=lambda item: item.created_at):
                available = task.state is TaskState.PENDING or task.id in expired_task_ids
                if task.tenant_id != tenant_id or not available:
                    continue
                if not all(
                    capability in remote_agent.capabilities
                    for capability in task.required_capabilities
                ):
                    continue
                lease = Lease(
                    tenant_id=tenant_id,
                    task_id=task.id,
                    ara_id=ara_id,
                    expires_at=expires_at,
                )
                leased_task = task.model_copy(update={"state": TaskState.LEASED})
                self._tasks[task.id] = leased_task
                for lease_id, old_lease in tuple(self._leases.items()):
                    if old_lease.task_id == task.id:
                        del self._leases[lease_id]
                self._leases[lease.id] = lease
                self._events.append(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.TASK_LEASED,
                        actor_type=ActorType.ARA,
                        actor_id=ara_id,
                        task_id=task.id,
                        payload={"lease_id": str(lease.id), "expires_at": expires_at.isoformat()},
                    )
                )
                return leased_task, lease
        return None

    def _active_lease(
        self, tenant_id: UUID, ara_id: UUID, task_id: UUID, lease_id: UUID
    ) -> tuple[Task, Lease]:
        task = self._tasks.get(task_id)
        lease = self._leases.get(lease_id)
        if task is None or lease is None or task.tenant_id != tenant_id:
            raise LifecycleNotFoundError("task or lease not found")
        if lease.tenant_id != tenant_id or lease.ara_id != ara_id or lease.task_id != task_id:
            raise LifecycleNotFoundError("task or lease not found")
        if task.state is not TaskState.LEASED:
            raise LifecycleConflictError("task is not leased")
        if lease.expires_at <= datetime.now(UTC):
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
        async with self._lock:
            self._active_lease(tenant_id, ara_id, task_id, lease_id)
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.ARA_PROGRESS,
                    actor_type=ActorType.ARA,
                    actor_id=ara_id,
                    task_id=task_id,
                    payload={"message": message, "progress_percent": progress_percent},
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
        async with self._lock:
            _, lease = self._active_lease(tenant_id, ara_id, task_id, lease_id)
            renewed = lease.model_copy(update={"expires_at": expires_at})
            self._leases[lease_id] = renewed
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.LEASE_RENEWED,
                    actor_type=ActorType.ARA,
                    actor_id=ara_id,
                    task_id=task_id,
                    payload={"lease_id": str(lease_id), "expires_at": expires_at.isoformat()},
                )
            )
            return renewed

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
        async with self._lock:
            task, _ = self._active_lease(tenant_id, ara_id, task_id, lease_id)
            if state is TaskState.COMPLETED:
                approvals = tuple(
                    approval
                    for approval in self._approvals.values()
                    if approval.task_id == task_id and approval.state is ApprovalState.GRANTED
                )
                for capability in task.required_capabilities:
                    approval_state = next(
                        (item.state for item in approvals if item.capability == capability), None
                    )
                    decision = evaluate_capability(
                        capability, task.required_capabilities, approval_state
                    )
                    if not decision.allowed:
                        raise LifecycleConflictError(decision.reason)
            for artifact in artifacts:
                if artifact.tenant_id != tenant_id or artifact.task_id != task_id:
                    raise LifecycleConflictError("artifact ownership mismatch")
            finished = task.model_copy(
                update={"state": state, "result": detail, "completed_at": datetime.now(UTC)}
            )
            self._tasks[task_id] = finished
            event_type = {
                TaskState.COMPLETED: EventType.TASK_COMPLETED,
                TaskState.FAILED: EventType.TASK_FAILED,
                TaskState.CANCELLED: EventType.TASK_CANCELLED,
            }[state]
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=event_type,
                    actor_type=ActorType.ARA,
                    actor_id=ara_id,
                    task_id=task_id,
                    payload={"detail": detail},
                )
            )
            for artifact in artifacts:
                self._artifacts[artifact.id] = artifact
                self._events.append(
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
                )
            return finished

    async def request_approval(
        self,
        tenant_id: UUID,
        ara_id: UUID,
        task_id: UUID,
        lease_id: UUID,
        approval: Approval,
    ) -> Approval:
        async with self._lock:
            task, _ = self._active_lease(tenant_id, ara_id, task_id, lease_id)
            if approval.tenant_id != tenant_id or approval.task_id != task.id:
                raise LifecycleConflictError("approval ownership mismatch")
            if approval.capability not in task.required_capabilities:
                raise LifecycleConflictError("capability is not part of the task")
            self._approvals[approval.id] = approval
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.APPROVAL_REQUESTED,
                    actor_type=ActorType.ARA,
                    actor_id=ara_id,
                    task_id=task_id,
                    payload={"approval_id": str(approval.id), "reason": approval.reason},
                )
            )
            return approval

    async def decide_approval(
        self,
        tenant_id: UUID,
        approval_id: UUID,
        state: ApprovalState,
        actor_id: UUID,
    ) -> Approval:
        async with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None or approval.tenant_id != tenant_id:
                raise LifecycleNotFoundError("approval not found")
            if approval.state is not ApprovalState.PENDING:
                raise LifecycleConflictError("approval is already decided")
            decided = approval.model_copy(update={"state": state, "decided_at": datetime.now(UTC)})
            self._approvals[approval_id] = decided
            event_type = (
                EventType.APPROVAL_GRANTED
                if state is ApprovalState.GRANTED
                else EventType.APPROVAL_DENIED
            )
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=event_type,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    task_id=approval.task_id,
                    payload={"approval_id": str(approval_id)},
                )
            )
            return decided

    async def list_approvals(self, tenant_id: UUID) -> tuple[Approval, ...]:
        async with self._lock:
            return tuple(item for item in self._approvals.values() if item.tenant_id == tenant_id)

    async def list_tasks(self, tenant_id: UUID) -> tuple[Task, ...]:
        async with self._lock:
            return tuple(item for item in self._tasks.values() if item.tenant_id == tenant_id)

    async def append_event(self, event: AuditEvent) -> None:
        async with self._lock:
            self._events.append(event)

    async def list_events(self, tenant_id: UUID) -> tuple[AuditEvent, ...]:
        async with self._lock:
            return tuple(event for event in self._events if event.tenant_id == tenant_id)

    async def close(self) -> None:
        return None
