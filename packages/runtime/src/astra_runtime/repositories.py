from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from astra_domain import (
    Approval,
    ApprovalState,
    Artifact,
    AuditEvent,
    Conversation,
    ConversationMessage,
    ConversationTurn,
    Lease,
    MemoryRecord,
    RemoteAgent,
    Task,
    TaskState,
    ToolInvocation,
)


class LifecycleError(Exception):
    pass


class LifecycleNotFoundError(LifecycleError):
    pass


class LifecycleConflictError(LifecycleError):
    pass


class RuntimeStore(Protocol):
    async def upsert_memory(
        self, memory: MemoryRecord, events: tuple[AuditEvent, ...]
    ) -> MemoryRecord: ...

    async def get_memory(self, tenant_id: UUID, memory_id: UUID) -> MemoryRecord | None: ...

    async def list_memories(
        self, tenant_id: UUID, *, include_candidates: bool = True
    ) -> tuple[MemoryRecord, ...]: ...

    async def delete_memory(
        self, tenant_id: UUID, memory_id: UUID, actor_id: UUID
    ) -> MemoryRecord: ...

    async def review_memory(
        self,
        tenant_id: UUID,
        memory_id: UUID,
        promote: bool,
        actor_id: UUID,
        replaces_memory_id: UUID | None = None,
    ) -> MemoryRecord: ...

    async def create_conversation(
        self, conversation: Conversation, event: AuditEvent
    ) -> Conversation: ...

    async def get_conversation(
        self, tenant_id: UUID, conversation_id: UUID
    ) -> Conversation | None: ...

    async def list_conversations(self, tenant_id: UUID) -> tuple[Conversation, ...]: ...

    async def append_message(
        self, message: ConversationMessage, events: tuple[AuditEvent, ...]
    ) -> ConversationMessage: ...

    async def list_messages(
        self, tenant_id: UUID, conversation_id: UUID, limit: int = 50
    ) -> tuple[ConversationMessage, ...]: ...

    async def create_turn(
        self,
        user_message: ConversationMessage,
        turn: ConversationTurn,
        events: tuple[AuditEvent, ...],
    ) -> ConversationTurn: ...

    async def get_turn(self, tenant_id: UUID, turn_id: UUID) -> ConversationTurn | None: ...

    async def list_conversation_turns(
        self, tenant_id: UUID, conversation_id: UUID
    ) -> tuple[ConversationTurn, ...]: ...

    async def claim_pending_turn(
        self, tenant_id: UUID, run_lease_id: UUID, run_lease_expires_at: datetime
    ) -> ConversationTurn | None: ...

    async def checkpoint_turn(
        self, tenant_id: UUID, turn_id: UUID, run_lease_id: UUID, checkpoint: dict[str, Any]
    ) -> ConversationTurn: ...

    async def pause_turn(
        self, tenant_id: UUID, turn_id: UUID, run_lease_id: UUID, checkpoint: dict[str, Any]
    ) -> ConversationTurn: ...

    async def create_tool_invocation(self, invocation: ToolInvocation) -> ToolInvocation: ...

    async def get_tool_invocation(
        self, tenant_id: UUID, turn_id: UUID, tool_call_id: str
    ) -> ToolInvocation | None: ...

    async def complete_tool_invocation(
        self, tenant_id: UUID, invocation_id: UUID
    ) -> ToolInvocation: ...

    async def fail_tool_invocation(
        self, tenant_id: UUID, invocation_id: UUID
    ) -> ToolInvocation: ...

    async def create_coordinator_approval(self, approval: Approval) -> Approval: ...

    async def complete_turn(
        self,
        tenant_id: UUID,
        turn_id: UUID,
        run_lease_id: UUID,
        assistant_message: ConversationMessage,
    ) -> ConversationTurn: ...

    async def register_ara(self, remote_agent: RemoteAgent, event: AuditEvent) -> RemoteAgent: ...

    async def add_task(self, task: Task, event: AuditEvent) -> Task: ...

    async def get_task(self, tenant_id: UUID, task_id: UUID) -> Task | None: ...

    async def lease_task(
        self, tenant_id: UUID, ara_id: UUID, expires_at: datetime
    ) -> tuple[Task, Lease] | None: ...

    async def record_progress(
        self,
        tenant_id: UUID,
        ara_id: UUID,
        task_id: UUID,
        lease_id: UUID,
        message: str,
        progress_percent: int | None,
    ) -> None: ...

    async def renew_lease(
        self,
        tenant_id: UUID,
        ara_id: UUID,
        task_id: UUID,
        lease_id: UUID,
        expires_at: datetime,
    ) -> Lease: ...

    async def finish_task(
        self,
        tenant_id: UUID,
        ara_id: UUID,
        task_id: UUID,
        lease_id: UUID,
        state: TaskState,
        detail: str,
        artifacts: tuple[Artifact, ...] = (),
    ) -> Task: ...

    async def request_approval(
        self,
        tenant_id: UUID,
        ara_id: UUID,
        task_id: UUID,
        lease_id: UUID,
        approval: Approval,
    ) -> Approval: ...

    async def decide_approval(
        self,
        tenant_id: UUID,
        approval_id: UUID,
        state: ApprovalState,
        actor_id: UUID,
    ) -> Approval: ...

    async def list_approvals(self, tenant_id: UUID) -> tuple[Approval, ...]: ...

    async def list_tasks(self, tenant_id: UUID) -> tuple[Task, ...]: ...

    async def append_event(self, event: AuditEvent) -> None: ...

    async def list_events(self, tenant_id: UUID) -> tuple[AuditEvent, ...]: ...

    async def close(self) -> None: ...
