import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from astra_domain import (
    ActorType,
    Approval,
    ApprovalState,
    Artifact,
    AuditEvent,
    BackgroundJob,
    BackgroundJobState,
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
)
from astra_policy import evaluate_capability

from astra_runtime.repositories import LifecycleConflictError, LifecycleNotFoundError


class InMemoryRuntimeStore:
    def __init__(self) -> None:
        self._remote_agents: dict[UUID, RemoteAgent] = {}
        self._conversations: dict[UUID, Conversation] = {}
        self._messages: dict[UUID, ConversationMessage] = {}
        self._turns: dict[UUID, ConversationTurn] = {}
        self._tool_invocations: dict[UUID, ToolInvocation] = {}
        self._memories: dict[UUID, MemoryRecord] = {}
        self._migration_batches: dict[UUID, MigrationBatch] = {}
        self._personas: dict[UUID, PersonaProfile] = {}
        self._active_persona_ids: dict[UUID, UUID] = {}
        self._learned_adaptations: dict[UUID, LearnedPersonaAdaptation] = {}
        self._tasks: dict[UUID, Task] = {}
        self._leases: dict[UUID, Lease] = {}
        self._events: list[AuditEvent] = []
        self._jobs: dict[UUID, BackgroundJob] = {}
        self._job_attempts: dict[UUID, list[JobAttempt]] = {}
        self._approvals: dict[UUID, Approval] = {}
        self._artifacts: dict[UUID, Artifact] = {}
        self._lock = asyncio.Lock()

    async def stage_migration_batch(
        self, batch: MigrationBatch, memories: tuple[MemoryRecord, ...], actor_id: UUID
    ) -> MigrationBatch:
        async with self._lock:
            existing = next(
                (
                    item
                    for item in self._migration_batches.values()
                    if item.tenant_id == batch.tenant_id
                    and item.source_system == batch.source_system
                    and item.source_database_fingerprint == batch.source_database_fingerprint
                ),
                None,
            )
            if existing is not None:
                return existing
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
            self._conversations[conversation.id] = conversation
            self._messages[message.id] = message
            self._migration_batches[batch.id] = batch
            for memory in memories:
                duplicate = next(
                    (
                        item
                        for item in self._memories.values()
                        if item.tenant_id == batch.tenant_id
                        and item.source_system == memory.source_system
                        and item.source_database_fingerprint == memory.source_database_fingerprint
                        and item.source_external_id == memory.source_external_id
                    ),
                    None,
                )
                if duplicate is None:
                    stored = memory.model_copy(update={"source_message_id": message.id})
                    self._memories[stored.id] = stored
                    self._events.append(
                        AuditEvent(
                            tenant_id=batch.tenant_id,
                            event_type=EventType.MEMORY_CANDIDATE_CREATED,
                            actor_type=ActorType.USER,
                            actor_id=actor_id,
                            payload={"memory_id": str(stored.id), "import_batch_id": str(batch.id)},
                        )
                    )
            self._events.append(
                AuditEvent(
                    tenant_id=batch.tenant_id,
                    event_type=EventType.MIGRATION_STAGED,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    payload={"batch_id": str(batch.id)},
                )
            )
            return batch

    async def get_migration_batch(self, tenant_id: UUID, batch_id: UUID) -> MigrationBatch | None:
        async with self._lock:
            batch = self._migration_batches.get(batch_id)
            return batch if batch is not None and batch.tenant_id == tenant_id else None

    async def list_migration_batches(self, tenant_id: UUID) -> tuple[MigrationBatch, ...]:
        async with self._lock:
            return tuple(
                item for item in self._migration_batches.values() if item.tenant_id == tenant_id
            )

    async def activate_migration_batch(
        self, tenant_id: UUID, batch_id: UUID, actor_id: UUID, authored_core: object
    ) -> MigrationBatch:
        if not isinstance(authored_core, PersonaCore):
            raise LifecycleConflictError("authored persona core is required")
        async with self._lock:
            batch = self._migration_batches.get(batch_id)
            if batch is None or batch.tenant_id != tenant_id:
                raise LifecycleNotFoundError("migration batch not found")
            if batch.state is not MigrationBatchState.STAGED:
                raise LifecycleConflictError("migration batch is not staged")
            memories = [
                item
                for item in self._memories.values()
                if item.tenant_id == tenant_id and item.import_batch_id == batch_id
            ]
            if not memories or not any(
                item.state in {MemoryState.CANDIDATE, MemoryState.PROMOTED} for item in memories
            ):
                raise LifecycleConflictError("an approved imported fact is required")
            now = datetime.now(UTC)
            promoted = [item for item in memories if item.state is MemoryState.CANDIDATE]
            for item in promoted:
                self._memories[item.id] = item.model_copy(
                    update={
                        "state": MemoryState.PROMOTED,
                        "confirmed": True,
                        "reviewed_at": now,
                        "reviewed_by": actor_id,
                        "updated_at": now,
                    }
                )
            version = (
                max(
                    (
                        item.version
                        for item in self._personas.values()
                        if item.tenant_id == tenant_id
                    ),
                    default=0,
                )
                + 1
            )
            profile = PersonaProfile(
                tenant_id=tenant_id, version=version, authored_core=authored_core
            )
            self._personas[profile.id] = profile
            self._active_persona_ids[tenant_id] = profile.id
            active = batch.model_copy(
                update={
                    "state": MigrationBatchState.ACTIVE,
                    "persona_profile_id": profile.id,
                    "activated_at": now,
                }
            )
            self._migration_batches[batch_id] = active
            self._events.extend(
                [
                    *(
                        AuditEvent(
                            tenant_id=tenant_id,
                            event_type=EventType.MEMORY_PROMOTED,
                            actor_type=ActorType.USER,
                            actor_id=actor_id,
                            payload={"memory_id": str(item.id), "import_batch_id": str(batch_id)},
                        )
                        for item in promoted
                    ),
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.MIGRATION_ACTIVATED,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload={"batch_id": str(batch_id)},
                    ),
                ]
            )
            return active

    async def rollback_migration_batch(
        self, tenant_id: UUID, batch_id: UUID, actor_id: UUID
    ) -> tuple[MigrationBatch, tuple[MemoryRecord, ...]]:
        async with self._lock:
            batch = self._migration_batches.get(batch_id)
            if batch is None or batch.tenant_id != tenant_id:
                raise LifecycleNotFoundError("migration batch not found")
            if batch.state is MigrationBatchState.ROLLED_BACK:
                raise LifecycleConflictError("migration batch is already rolled back")
            now = datetime.now(UTC)
            deleted = tuple(
                item.model_copy(
                    update={"state": MemoryState.DELETED, "deleted_at": now, "updated_at": now}
                )
                for item in self._memories.values()
                if item.import_batch_id == batch_id and item.state is not MemoryState.DELETED
            )
            for item in deleted:
                self._memories[item.id] = item
            rolled = batch.model_copy(
                update={"state": MigrationBatchState.ROLLED_BACK, "rolled_back_at": now}
            )
            self._migration_batches[batch_id] = rolled
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.MIGRATION_ROLLED_BACK,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    payload={"batch_id": str(batch_id)},
                )
            )
            return rolled, deleted

    async def get_active_persona(self, tenant_id: UUID) -> PersonaProfile | None:
        async with self._lock:
            profile_id = self._active_persona_ids.get(tenant_id)
            return self._personas.get(profile_id) if profile_id is not None else None

    async def create_persona_profile(
        self, profile: PersonaProfile, actor_id: UUID
    ) -> PersonaProfile:
        async with self._lock:
            versions = [
                item.version
                for item in self._personas.values()
                if item.tenant_id == profile.tenant_id
            ]
            if profile.version != (max(versions, default=0) + 1):
                raise LifecycleConflictError("persona version is not next for tenant")
            self._personas[profile.id] = profile
            self._active_persona_ids[profile.tenant_id] = profile.id
            self._events.extend(
                (
                    AuditEvent(
                        tenant_id=profile.tenant_id,
                        event_type=EventType.PERSONA_CREATED,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload={"persona_id": str(profile.id), "version": profile.version},
                    ),
                    AuditEvent(
                        tenant_id=profile.tenant_id,
                        event_type=EventType.PERSONA_ACTIVATED,
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload={"persona_id": str(profile.id), "version": profile.version},
                    ),
                )
            )
            return profile

    async def revert_persona_profile(
        self, tenant_id: UUID, version: int, actor_id: UUID
    ) -> PersonaProfile:
        async with self._lock:
            profile = next(
                (
                    item
                    for item in self._personas.values()
                    if item.tenant_id == tenant_id and item.version == version
                ),
                None,
            )
            if profile is None:
                raise LifecycleNotFoundError("persona version not found")
            self._active_persona_ids[tenant_id] = profile.id
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.PERSONA_REVERTED,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    payload={"persona_id": str(profile.id), "version": version},
                )
            )
            return profile

    async def create_learned_persona_adaptation(
        self, adaptation: LearnedPersonaAdaptation
    ) -> LearnedPersonaAdaptation:
        async with self._lock:
            profile = self._personas.get(adaptation.profile_id)
            if profile is None or profile.tenant_id != adaptation.tenant_id:
                raise LifecycleNotFoundError("persona profile not found")
            self._learned_adaptations[adaptation.id] = adaptation
            return adaptation

    async def reverse_learned_persona_adaptation(
        self, tenant_id: UUID, adaptation_id: UUID
    ) -> LearnedPersonaAdaptation:
        async with self._lock:
            adaptation = self._learned_adaptations.get(adaptation_id)
            if adaptation is None or adaptation.tenant_id != tenant_id:
                raise LifecycleNotFoundError("learned persona adaptation not found")
            if adaptation.state is LearnedAdaptationState.REVERSED:
                raise LifecycleConflictError("learned persona adaptation is already reversed")
            reversed_adaptation = adaptation.model_copy(
                update={"state": LearnedAdaptationState.REVERSED, "reversed_at": datetime.now(UTC)}
            )
            self._learned_adaptations[adaptation_id] = reversed_adaptation
            return reversed_adaptation

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

    async def enqueue_job(self, job: BackgroundJob) -> BackgroundJob:
        async with self._lock:
            existing = next(
                (
                    item
                    for item in self._jobs.values()
                    if item.tenant_id == job.tenant_id
                    and item.kind == job.kind
                    and item.source_id == job.source_id
                ),
                None,
            )
            if existing is not None:
                return existing
            self._jobs[job.id] = job
            return job

    async def claim_job(self, lease_id: UUID, lease_expires_at: datetime) -> BackgroundJob | None:
        async with self._lock:
            now = datetime.now(UTC)
            job = next(
                (
                    item
                    for item in sorted(self._jobs.values(), key=lambda value: value.created_at)
                    if (
                        item.state in {BackgroundJobState.PENDING, BackgroundJobState.RETRY}
                        and item.available_at <= now
                    )
                    or (
                        item.state is BackgroundJobState.RUNNING
                        and item.lease_expires_at is not None
                        and item.lease_expires_at <= now
                    )
                ),
                None,
            )
            if job is None:
                return None
            claimed = job.model_copy(
                update={
                    "state": BackgroundJobState.RUNNING,
                    "attempt_count": job.attempt_count + 1,
                    "lease_id": lease_id,
                    "lease_expires_at": lease_expires_at,
                    "updated_at": now,
                }
            )
            self._jobs[job.id] = claimed
            self._job_attempts.setdefault(job.id, []).append(
                JobAttempt(tenant_id=job.tenant_id, job_id=job.id, attempt=claimed.attempt_count)
            )
            return claimed

    def _active_job(self, tenant_id: UUID, job_id: UUID, lease_id: UUID) -> BackgroundJob:
        job = self._jobs.get(job_id)
        if job is None or job.tenant_id != tenant_id:
            raise LifecycleNotFoundError("job not found")
        if job.state is not BackgroundJobState.RUNNING or job.lease_id != lease_id:
            raise LifecycleConflictError("job is not actively claimed")
        return job

    async def complete_job(self, tenant_id: UUID, job_id: UUID, lease_id: UUID) -> BackgroundJob:
        async with self._lock:
            job = self._active_job(tenant_id, job_id, lease_id)
            now = datetime.now(UTC)
            completed = job.model_copy(
                update={
                    "state": BackgroundJobState.COMPLETED,
                    "lease_id": None,
                    "lease_expires_at": None,
                    "completed_at": now,
                    "updated_at": now,
                }
            )
            self._jobs[job_id] = completed
            self._job_attempts[job_id][-1] = self._job_attempts[job_id][-1].model_copy(
                update={"finished_at": now}
            )
            return completed

    async def retry_job(
        self, tenant_id: UUID, job_id: UUID, lease_id: UUID, error: JobError, available_at: datetime
    ) -> BackgroundJob:
        async with self._lock:
            job = self._active_job(tenant_id, job_id, lease_id)
            now = datetime.now(UTC)
            state = (
                BackgroundJobState.FAILED
                if job.attempt_count >= job.max_attempts
                else BackgroundJobState.RETRY
            )
            retried = job.model_copy(
                update={
                    "state": state,
                    "lease_id": None,
                    "lease_expires_at": None,
                    "last_error": error,
                    "available_at": available_at,
                    "completed_at": now if state is BackgroundJobState.FAILED else None,
                    "updated_at": now,
                }
            )
            self._jobs[job_id] = retried
            self._job_attempts[job_id][-1] = self._job_attempts[job_id][-1].model_copy(
                update={"finished_at": now, "error": error}
            )
            return retried

    async def list_jobs(self, tenant_id: UUID) -> tuple[BackgroundJob, ...]:
        async with self._lock:
            return tuple(
                item
                for item in sorted(
                    self._jobs.values(), key=lambda value: value.created_at, reverse=True
                )
                if item.tenant_id == tenant_id
            )

    async def list_job_attempts(self, tenant_id: UUID, job_id: UUID) -> tuple[JobAttempt, ...]:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.tenant_id != tenant_id:
                return ()
            return tuple(self._job_attempts.get(job_id, ()))

    async def requeue_job(self, tenant_id: UUID, job_id: UUID, actor_id: UUID) -> BackgroundJob:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.tenant_id != tenant_id:
                raise LifecycleNotFoundError("job not found")
            if job.state is not BackgroundJobState.FAILED:
                raise LifecycleConflictError("only failed jobs can be requeued")
            now = datetime.now(UTC)
            requeued = job.model_copy(
                update={
                    "state": BackgroundJobState.PENDING,
                    "attempt_count": 0,
                    "available_at": now,
                    "lease_id": None,
                    "lease_expires_at": None,
                    "last_error": None,
                    "completed_at": None,
                    "updated_at": now,
                }
            )
            self._jobs[job_id] = requeued
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.JOB_REQUEUED,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    payload={"job_id": str(job_id)},
                )
            )
            return requeued

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

    async def create_turn(
        self,
        user_message: ConversationMessage,
        turn: ConversationTurn,
        events: tuple[AuditEvent, ...],
        extraction_job: BackgroundJob,
    ) -> ConversationTurn:
        async with self._lock:
            existing = next(
                (
                    item
                    for item in self._turns.values()
                    if item.tenant_id == turn.tenant_id
                    and item.client_request_id == turn.client_request_id
                ),
                None,
            )
            if existing is not None:
                return existing
            conversation = self._conversations.get(turn.conversation_id)
            if (
                conversation is None
                or conversation.tenant_id != turn.tenant_id
                or user_message.tenant_id != turn.tenant_id
                or user_message.conversation_id != turn.conversation_id
                or user_message.id != turn.user_message_id
                or user_message.role is not MessageRole.USER
            ):
                raise LifecycleNotFoundError("conversation not found")
            self._messages[user_message.id] = user_message
            self._turns[turn.id] = turn
            self._conversations[conversation.id] = conversation.model_copy(
                update={"updated_at": user_message.created_at}
            )
            self._events.extend(events)
            self._jobs[extraction_job.id] = extraction_job
            return turn

    async def get_turn(self, tenant_id: UUID, turn_id: UUID) -> ConversationTurn | None:
        async with self._lock:
            turn = self._turns.get(turn_id)
            return turn if turn is not None and turn.tenant_id == tenant_id else None

    async def list_conversation_turns(
        self, tenant_id: UUID, conversation_id: UUID
    ) -> tuple[ConversationTurn, ...]:
        async with self._lock:
            return tuple(
                item
                for item in sorted(self._turns.values(), key=lambda item: item.created_at)
                if item.tenant_id == tenant_id and item.conversation_id == conversation_id
            )

    @staticmethod
    def _validate_run_lease_expiry(expires_at: datetime) -> None:
        now = datetime.now(UTC)
        if expires_at <= now or expires_at > now.replace(microsecond=0) + timedelta(minutes=15):
            raise LifecycleConflictError("run lease must expire within 15 minutes")

    def _active_turn(self, tenant_id: UUID, turn_id: UUID, run_lease_id: UUID) -> ConversationTurn:
        turn = self._turns.get(turn_id)
        if turn is None or turn.tenant_id != tenant_id:
            raise LifecycleNotFoundError("turn not found")
        if (
            turn.state is not ConversationTurnState.RUNNING
            or turn.run_lease_id != run_lease_id
            or turn.run_lease_expires_at is None
            or turn.run_lease_expires_at <= datetime.now(UTC)
        ):
            raise LifecycleConflictError("turn is not actively claimed")
        return turn

    async def claim_pending_turn(
        self, tenant_id: UUID, run_lease_id: UUID, run_lease_expires_at: datetime
    ) -> ConversationTurn | None:
        self._validate_run_lease_expiry(run_lease_expires_at)
        async with self._lock:
            now = datetime.now(UTC)
            turn = next(
                (
                    item
                    for item in sorted(self._turns.values(), key=lambda item: item.created_at)
                    if item.tenant_id == tenant_id
                    and (
                        item.state is ConversationTurnState.PENDING
                        or (
                            item.state is ConversationTurnState.RUNNING
                            and item.run_lease_expires_at is not None
                            and item.run_lease_expires_at <= now
                        )
                    )
                ),
                None,
            )
            if turn is None:
                return None
            claimed = turn.model_copy(
                update={
                    "state": ConversationTurnState.RUNNING,
                    "run_lease_id": run_lease_id,
                    "run_lease_expires_at": run_lease_expires_at,
                    "updated_at": now,
                }
            )
            self._turns[turn.id] = claimed
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.CONVERSATION_TURN_RUNNING,
                    actor_type=ActorType.COORDINATOR,
                    payload={"turn_id": str(turn.id)},
                )
            )
            return claimed

    async def claim_turn(
        self, tenant_id: UUID, turn_id: UUID, run_lease_id: UUID, run_lease_expires_at: datetime
    ) -> ConversationTurn | None:
        self._validate_run_lease_expiry(run_lease_expires_at)
        async with self._lock:
            turn = self._turns.get(turn_id)
            if (
                turn is None
                or turn.tenant_id != tenant_id
                or turn.state is not ConversationTurnState.PENDING
            ):
                return None
            claimed = turn.model_copy(
                update={
                    "state": ConversationTurnState.RUNNING,
                    "run_lease_id": run_lease_id,
                    "run_lease_expires_at": run_lease_expires_at,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._turns[turn.id] = claimed
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.CONVERSATION_TURN_RUNNING,
                    actor_type=ActorType.COORDINATOR,
                    payload={"turn_id": str(turn.id)},
                )
            )
            return claimed

    async def checkpoint_turn(
        self, tenant_id: UUID, turn_id: UUID, run_lease_id: UUID, checkpoint: dict[str, Any]
    ) -> ConversationTurn:
        async with self._lock:
            turn = self._active_turn(tenant_id, turn_id, run_lease_id)
            updated = turn.model_copy(
                update={"checkpoint": checkpoint, "updated_at": datetime.now(UTC)}
            )
            self._turns[turn_id] = updated
            return updated

    async def pause_turn(
        self, tenant_id: UUID, turn_id: UUID, run_lease_id: UUID, checkpoint: dict[str, Any]
    ) -> ConversationTurn:
        async with self._lock:
            turn = self._active_turn(tenant_id, turn_id, run_lease_id)
            paused = turn.model_copy(
                update={
                    "state": ConversationTurnState.PAUSED,
                    "checkpoint": checkpoint,
                    "run_lease_id": None,
                    "run_lease_expires_at": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._turns[turn_id] = paused
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.CONVERSATION_TURN_PAUSED,
                    actor_type=ActorType.COORDINATOR,
                    payload={"turn_id": str(turn_id)},
                )
            )
            return paused

    async def create_tool_invocation(self, invocation: ToolInvocation) -> ToolInvocation:
        async with self._lock:
            existing = next(
                (
                    item
                    for item in self._tool_invocations.values()
                    if item.turn_id == invocation.turn_id
                    and item.tool_call_id == invocation.tool_call_id
                ),
                None,
            )
            if existing is not None:
                return existing
            turn = self._turns.get(invocation.turn_id)
            if turn is None or turn.tenant_id != invocation.tenant_id:
                raise LifecycleNotFoundError("turn not found")
            self._tool_invocations[invocation.id] = invocation
            self._events.append(
                AuditEvent(
                    tenant_id=invocation.tenant_id,
                    event_type=EventType.TOOL_INVOCATION_REQUESTED,
                    actor_type=ActorType.COORDINATOR,
                    payload={"invocation_id": str(invocation.id)},
                )
            )
            return invocation

    def _eligible_aras(self, tenant_id: UUID, task: Task) -> list[RemoteAgent]:
        now = datetime.now(UTC)
        busy = {lease.ara_id for lease in self._leases.values() if lease.expires_at > now}
        return sorted(
            (
                ara
                for ara in self._remote_agents.values()
                if ara.tenant_id == tenant_id
                and ara.status is RemoteAgentStatus.ACTIVE
                and ara.trust_level > 0
                and now - ara.last_seen_at <= timedelta(minutes=2)
                and ara.id not in busy
                and all(capability in ara.capabilities for capability in task.required_capabilities)
            ),
            key=lambda ara: (-ara.trust_level, ara.id.hex),
        )

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
        async with self._lock:
            self._active_turn(tenant_id, turn_id, run_lease_id)
            selected: set[UUID] = set()
            assigned: list[Task] = []
            for task in tasks:
                candidates = [
                    ara for ara in self._eligible_aras(tenant_id, task) if ara.id not in selected
                ]
                if not candidates:
                    raise LifecycleConflictError("no eligible trusted ARA for planned task")
                selected.add(candidates[0].id)
                assigned.append(task.model_copy(update={"target_ara_id": candidates[0].id}))
            now = datetime.now(UTC)
            for task, invocation in zip(assigned, invocations, strict=True):
                if invocation.task_id != task.id or invocation.turn_id != turn_id:
                    raise LifecycleConflictError("delegation invocation ownership mismatch")
                self._tasks[task.id] = task
                self._tool_invocations[invocation.id] = invocation
                self._events.extend(
                    (
                        AuditEvent(
                            tenant_id=tenant_id,
                            event_type=EventType.TASK_CREATED,
                            actor_type=ActorType.COORDINATOR,
                            task_id=task.id,
                            payload={
                                "turn_id": str(turn_id),
                                "target_ara_id": str(task.target_ara_id),
                            },
                        ),
                        AuditEvent(
                            tenant_id=tenant_id,
                            event_type=EventType.TOOL_INVOCATION_REQUESTED,
                            actor_type=ActorType.COORDINATOR,
                            payload={"invocation_id": str(invocation.id)},
                        ),
                    )
                )
            turn = self._turns[turn_id]
            self._turns[turn_id] = turn.model_copy(
                update={
                    "state": ConversationTurnState.PAUSED,
                    "checkpoint": checkpoint,
                    "run_lease_id": None,
                    "run_lease_expires_at": None,
                    "updated_at": now,
                }
            )
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.CONVERSATION_TURN_PAUSED,
                    actor_type=ActorType.COORDINATOR,
                    payload={"turn_id": str(turn_id)},
                )
            )
            return tuple(assigned)

    async def get_tool_invocation(
        self, tenant_id: UUID, turn_id: UUID, tool_call_id: str
    ) -> ToolInvocation | None:
        async with self._lock:
            return next(
                (
                    item
                    for item in self._tool_invocations.values()
                    if item.tenant_id == tenant_id
                    and item.turn_id == turn_id
                    and item.tool_call_id == tool_call_id
                ),
                None,
            )

    async def _finish_tool_invocation(
        self, tenant_id: UUID, invocation_id: UUID, state: ToolInvocationState
    ) -> ToolInvocation:
        async with self._lock:
            invocation = self._tool_invocations.get(invocation_id)
            if invocation is None or invocation.tenant_id != tenant_id:
                raise LifecycleNotFoundError("tool invocation not found")
            if invocation.state in {
                ToolInvocationState.COMPLETED,
                ToolInvocationState.FAILED,
                ToolInvocationState.DENIED,
            }:
                if invocation.state is state:
                    return invocation
                raise LifecycleConflictError("tool invocation is already finished")
            completed = invocation.model_copy(
                update={
                    "state": state,
                    "updated_at": datetime.now(UTC),
                    "completed_at": datetime.now(UTC),
                }
            )
            self._tool_invocations[invocation_id] = completed
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.TOOL_INVOCATION_COMPLETED
                    if state is ToolInvocationState.COMPLETED
                    else EventType.TOOL_INVOCATION_FAILED,
                    actor_type=ActorType.COORDINATOR,
                    payload={"invocation_id": str(invocation_id)},
                )
            )
            return completed

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
        async with self._lock:
            if (
                approval.requestor_type is not ActorType.COORDINATOR
                or approval.tool_invocation_id is None
            ):
                raise LifecycleConflictError(
                    "coordinator approval must reference a tool invocation"
                )
            invocation = self._tool_invocations.get(approval.tool_invocation_id)
            if invocation is None or invocation.tenant_id != approval.tenant_id:
                raise LifecycleNotFoundError("tool invocation not found")
            if invocation.state is not ToolInvocationState.PENDING:
                raise LifecycleConflictError("tool invocation cannot await approval")
            turn = self._turns[invocation.turn_id]
            self._approvals[approval.id] = approval
            self._tool_invocations[invocation.id] = invocation.model_copy(
                update={
                    "state": ToolInvocationState.AWAITING_APPROVAL,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._turns[turn.id] = turn.model_copy(
                update={
                    "state": ConversationTurnState.PAUSED,
                    "run_lease_id": None,
                    "run_lease_expires_at": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._events.append(
                AuditEvent(
                    tenant_id=approval.tenant_id,
                    event_type=EventType.APPROVAL_REQUESTED,
                    actor_type=ActorType.COORDINATOR,
                    payload={"approval_id": str(approval.id)},
                )
            )
            return approval

    async def complete_turn(
        self,
        tenant_id: UUID,
        turn_id: UUID,
        run_lease_id: UUID,
        assistant_message: ConversationMessage,
    ) -> ConversationTurn:
        async with self._lock:
            turn = self._active_turn(tenant_id, turn_id, run_lease_id)
            if (
                assistant_message.tenant_id != tenant_id
                or assistant_message.conversation_id != turn.conversation_id
                or assistant_message.role is not MessageRole.ASSISTANT
            ):
                raise LifecycleConflictError("assistant message ownership mismatch")
            now = datetime.now(UTC)
            completed = turn.model_copy(
                update={
                    "state": ConversationTurnState.COMPLETED,
                    "run_lease_id": None,
                    "run_lease_expires_at": None,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
            self._turns[turn_id] = completed
            self._messages[assistant_message.id] = assistant_message
            conversation = self._conversations[turn.conversation_id]
            self._conversations[conversation.id] = conversation.model_copy(
                update={"updated_at": assistant_message.created_at}
            )
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.CONVERSATION_TURN_COMPLETED,
                    actor_type=ActorType.COORDINATOR,
                    payload={"turn_id": str(turn_id)},
                )
            )
            return completed

    async def register_ara(self, remote_agent: RemoteAgent, event: AuditEvent) -> RemoteAgent:
        async with self._lock:
            existing = self._remote_agents.get(remote_agent.id)
            if existing is not None and existing.tenant_id == remote_agent.tenant_id:
                remote_agent = remote_agent.model_copy(
                    update={"status": existing.status, "trust_level": existing.trust_level}
                )
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

    async def request_task_cancellation(
        self, tenant_id: UUID, task_id: UUID, actor_id: UUID
    ) -> Task:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.tenant_id != tenant_id:
                raise LifecycleNotFoundError("task not found")
            if task.state is not TaskState.LEASED:
                raise LifecycleConflictError("task is not leased")
            requested = task.model_copy(update={"state": TaskState.CANCELLING})
            self._tasks[task_id] = requested
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.TASK_CANCELLED,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    task_id=task_id,
                    payload={"requested": True},
                )
            )
            return requested

    async def heartbeat(
        self,
        tenant_id: UUID,
        ara_id: UUID,
        task_id: UUID | None = None,
        lease_id: UUID | None = None,
    ) -> Task | None:
        async with self._lock:
            ara = self._remote_agents.get(ara_id)
            if ara is None or ara.tenant_id != tenant_id:
                raise LifecycleNotFoundError("ARA not found")
            now = datetime.now(UTC)
            self._remote_agents[ara_id] = ara.model_copy(update={"last_seen_at": now})
            task = None
            if task_id is not None or lease_id is not None:
                if task_id is None or lease_id is None:
                    raise LifecycleConflictError("task and lease are required together")
                task, lease = self._active_lease(tenant_id, ara_id, task_id, lease_id)
                if task.state not in {TaskState.LEASED, TaskState.CANCELLING}:
                    raise LifecycleConflictError("task is not active")
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.ARA_HEARTBEAT,
                    actor_type=ActorType.ARA,
                    actor_id=ara_id,
                    task_id=task.id if task else None,
                    payload={"lease_id": str(lease_id) if lease_id else None},
                )
            )
            return task

    async def list_remote_agents(self, tenant_id: UUID) -> tuple[RemoteAgent, ...]:
        async with self._lock:
            now = datetime.now(UTC)
            return tuple(
                agent.model_copy(update={"status": RemoteAgentStatus.OFFLINE})
                if agent.status is RemoteAgentStatus.ACTIVE
                and now - agent.last_seen_at > timedelta(minutes=2)
                else agent
                for agent in self._remote_agents.values()
                if agent.tenant_id == tenant_id
            )

    async def set_remote_agent_trust(
        self, tenant_id: UUID, ara_id: UUID, trust_level: int, actor_id: UUID
    ) -> RemoteAgent:
        async with self._lock:
            ara = self._remote_agents.get(ara_id)
            if ara is None or ara.tenant_id != tenant_id:
                raise LifecycleNotFoundError("ARA not found")
            if ara.status is RemoteAgentStatus.REVOKED:
                raise LifecycleConflictError("revoked ARA cannot regain trust")
            updated = ara.model_copy(update={"trust_level": trust_level})
            self._remote_agents[ara_id] = updated
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.ARA_TRUST_UPDATED,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    payload={"ara_id": str(ara_id), "trust_level": trust_level},
                )
            )
            return updated

    async def revoke_remote_agent(
        self, tenant_id: UUID, ara_id: UUID, actor_id: UUID
    ) -> RemoteAgent:
        async with self._lock:
            ara = self._remote_agents.get(ara_id)
            if ara is None or ara.tenant_id != tenant_id:
                raise LifecycleNotFoundError("ARA not found")
            revoked = ara.model_copy(update={"status": RemoteAgentStatus.REVOKED, "trust_level": 0})
            self._remote_agents[ara_id] = revoked
            for lease in tuple(self._leases.values()):
                if lease.tenant_id == tenant_id and lease.ara_id == ara_id:
                    self._leases[lease.task_id] = lease.model_copy(
                        update={"expires_at": datetime.now(UTC)}
                    )
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.ARA_REVOKED,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    payload={"ara_id": str(ara_id)},
                )
            )
            return revoked

    async def get_artifact(self, tenant_id: UUID, artifact_id: UUID) -> Artifact | None:
        async with self._lock:
            artifact = self._artifacts.get(artifact_id)
            return artifact if artifact is not None and artifact.tenant_id == tenant_id else None

    async def list_artifacts(self, tenant_id: UUID) -> tuple[Artifact, ...]:
        async with self._lock:
            return tuple(
                item
                for item in self._artifacts.values()
                if item.tenant_id == tenant_id and item.deleted_at is None
            )

    async def delete_artifact(
        self, tenant_id: UUID, artifact_id: UUID, actor_id: UUID, retention_until: datetime
    ) -> Artifact:
        async with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is not None and artifact.tenant_id != tenant_id:
                artifact = None
            if artifact is None or artifact.deleted_at is not None:
                raise LifecycleNotFoundError("artifact not found")
            deleted = artifact.model_copy(
                update={"deleted_at": datetime.now(UTC), "retention_until": retention_until}
            )
            self._artifacts[artifact_id] = deleted
            self._events.append(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.ARTIFACT_DELETED,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    task_id=artifact.task_id,
                    payload={
                        "artifact_id": str(artifact_id),
                        "retention_until": retention_until.isoformat(),
                    },
                )
            )
            return deleted

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
                if task.target_ara_id is not None and task.target_ara_id != ara_id:
                    continue
                if task.target_ara_id is not None and (
                    remote_agent.status is not RemoteAgentStatus.ACTIVE
                    or remote_agent.trust_level <= 0
                    or now - remote_agent.last_seen_at > timedelta(minutes=2)
                ):
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
        if task.state not in {TaskState.LEASED, TaskState.CANCELLING}:
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
                update={
                    "state": state,
                    "result": detail,
                    "completed_at": datetime.now(UTC),
                    "completed_by_ara_id": ara_id,
                }
            )
            self._tasks[task_id] = finished
            invocation = next(
                (item for item in self._tool_invocations.values() if item.task_id == task_id), None
            )
            if invocation is not None:
                invocation_state = (
                    ToolInvocationState.COMPLETED
                    if state is TaskState.COMPLETED
                    else ToolInvocationState.FAILED
                )
                self._tool_invocations[invocation.id] = invocation.model_copy(
                    update={
                        "state": invocation_state,
                        "updated_at": datetime.now(UTC),
                        "completed_at": datetime.now(UTC),
                    }
                )
                turn = self._turns[invocation.turn_id]
                siblings = [
                    item
                    for item in self._tool_invocations.values()
                    if item.turn_id == turn.id and item.target.value == "ara"
                ]
                if siblings and all(
                    item.state
                    in {
                        ToolInvocationState.COMPLETED,
                        ToolInvocationState.FAILED,
                        ToolInvocationState.DENIED,
                    }
                    for item in siblings
                ):
                    self._turns[turn.id] = turn.model_copy(
                        update={
                            "state": ConversationTurnState.PENDING,
                            "updated_at": datetime.now(UTC),
                        }
                    )
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
            if (
                approval.tenant_id != tenant_id
                or approval.task_id != task.id
                or approval.requested_by != ara_id
                or approval.requestor_type is not ActorType.ARA
            ):
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
        if state not in {ApprovalState.GRANTED, ApprovalState.DENIED}:
            raise LifecycleConflictError("invalid approval decision")
        async with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None or approval.tenant_id != tenant_id:
                raise LifecycleNotFoundError("approval not found")
            if approval.state is state:
                return approval
            if approval.state is not ApprovalState.PENDING:
                raise LifecycleConflictError("approval is already decided")
            decided = approval.model_copy(
                update={"state": state, "decided_at": datetime.now(UTC), "decided_by": actor_id}
            )
            self._approvals[approval_id] = decided
            if approval.tool_invocation_id is not None:
                invocation = self._tool_invocations[approval.tool_invocation_id]
                invocation_state = (
                    ToolInvocationState.PENDING
                    if state is ApprovalState.GRANTED
                    else ToolInvocationState.DENIED
                )
                self._tool_invocations[invocation.id] = invocation.model_copy(
                    update={
                        "state": invocation_state,
                        "updated_at": datetime.now(UTC),
                        "completed_at": datetime.now(UTC)
                        if invocation_state is ToolInvocationState.DENIED
                        else None,
                    }
                )
                turn = self._turns[invocation.turn_id]
                self._turns[turn.id] = turn.model_copy(
                    update={"state": ConversationTurnState.PENDING, "updated_at": datetime.now(UTC)}
                )
                if state is ApprovalState.DENIED:
                    self._events.append(
                        AuditEvent(
                            tenant_id=tenant_id,
                            event_type=EventType.TOOL_INVOCATION_DENIED,
                            actor_type=ActorType.USER,
                            actor_id=actor_id,
                            payload={"invocation_id": str(invocation.id)},
                        )
                    )
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
