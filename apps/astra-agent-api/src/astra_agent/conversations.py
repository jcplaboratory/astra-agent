import asyncio
from uuid import UUID

from astra_domain import (
    ActorType,
    AuditEvent,
    Capability,
    CapabilityKind,
    ConversationMessage,
    EventType,
    MessageRole,
    Task,
    TaskState,
)
from astra_memory import ContextCompiler, MemoryPipeline
from astra_model_providers import MainModelProvider, ModelMessage, ModelProviderError
from astra_runtime import RuntimeStore


class ConversationOrchestrator:
    def __init__(
        self,
        store: RuntimeStore,
        compiler: ContextCompiler,
        model_provider: MainModelProvider,
        history_limit: int,
        memory_pipeline: MemoryPipeline | None = None,
        delegation_wait_seconds: float = 30,
        delegation_poll_seconds: float = 0.25,
        delegation_enabled: bool = True,
    ) -> None:
        self._store = store
        self._compiler = compiler
        self._model_provider = model_provider
        self._history_limit = history_limit
        self._memory_pipeline = memory_pipeline
        self._delegation_wait_seconds = delegation_wait_seconds
        self._delegation_poll_seconds = delegation_poll_seconds
        self._delegation_enabled = delegation_enabled

    @staticmethod
    def should_delegate(content: str) -> bool:
        lowered = content.casefold()
        action = any(term in lowered for term in ("inspect", "research", "analyze", "review"))
        target = any(term in lowered for term in ("repository", "repo", "codebase"))
        return action and target

    async def _delegate(self, tenant_id: UUID, content: str, context: str) -> Task:
        capability = Capability(kind=CapabilityKind.FILE_READ, scope="repository")
        task = Task(
            tenant_id=tenant_id,
            objective=content,
            context=context,
            required_capabilities=(capability,),
            deliverable_contract="Repository inventory and evidence-backed findings",
        )
        await self._store.add_task(
            task,
            AuditEvent(
                tenant_id=tenant_id,
                event_type=EventType.TASK_CREATED,
                actor_type=ActorType.COORDINATOR,
                task_id=task.id,
                payload={"delegation_reason": "explicit repository inspection request"},
            ),
        )
        deadline = asyncio.get_running_loop().time() + self._delegation_wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            current = await self._store.get_task(tenant_id, task.id)
            if current is not None and current.state in {
                TaskState.COMPLETED,
                TaskState.FAILED,
                TaskState.CANCELLED,
            }:
                return current
            await asyncio.sleep(self._delegation_poll_seconds)
        current = await self._store.get_task(tenant_id, task.id)
        return current or task

    async def respond(
        self,
        tenant_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
        content: str,
    ) -> tuple[ConversationMessage, ConversationMessage]:
        user_message = ConversationMessage(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=content,
        )
        received_event = AuditEvent(
            tenant_id=tenant_id,
            event_type=EventType.CONVERSATION_RECEIVED,
            actor_type=ActorType.USER,
            actor_id=user_id,
            payload={
                "conversation_id": str(conversation_id),
                "message_id": str(user_message.id),
            },
        )
        await self._store.append_message(
            user_message,
            (received_event,),
        )
        if self._memory_pipeline is not None:
            try:
                await self._memory_pipeline.process_message(
                    tenant_id, received_event.id, user_message.id, content
                )
            except Exception:
                # Raw conversation persistence must not depend on optional memory processing.
                pass
        briefing = await self._compiler.compile(tenant_id, content)
        await self._store.append_event(
            AuditEvent(
                tenant_id=tenant_id,
                event_type=EventType.CONTEXT_COMPILED,
                actor_type=ActorType.COORDINATOR,
                payload={
                    "conversation_id": str(conversation_id),
                    "estimated_tokens": briefing.estimated_tokens,
                    "source_memory_ids": [str(item) for item in briefing.source_memory_ids],
                },
            )
        )
        history = await self._store.list_messages(tenant_id, conversation_id, self._history_limit)
        model_messages = (
            ModelMessage(role="system", content=briefing.content),
            *(ModelMessage(role=item.role.value, content=item.content) for item in history),
        )
        if self._delegation_enabled and self.should_delegate(content):
            delegated = await self._delegate(tenant_id, content, briefing.content)
            if delegated.state is TaskState.COMPLETED and delegated.result:
                model_messages = (
                    *model_messages,
                    ModelMessage(
                        role="system",
                        content=(
                            "ARA findings follow. Synthesize them into a direct answer, "
                            "cite file paths and line evidence, and do not invent details.\n\n"
                            f"{delegated.result}"
                        ),
                    ),
                )
            else:
                model_messages = (
                    *model_messages,
                    ModelMessage(
                        role="system",
                        content=(
                            f"Repository task {delegated.id} is {delegated.state.value}. "
                            "Tell the user it is delegated and visible in task progress."
                        ),
                    ),
                )
        await self._store.append_event(
            AuditEvent(
                tenant_id=tenant_id,
                event_type=EventType.MODEL_REQUEST,
                actor_type=ActorType.COORDINATOR,
                payload={
                    "conversation_id": str(conversation_id),
                    "message_count": len(model_messages),
                },
            )
        )
        try:
            response = await self._model_provider.complete(model_messages)
        except ModelProviderError:
            await self._store.append_event(
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.MODEL_FAILED,
                    actor_type=ActorType.COORDINATOR,
                    payload={"conversation_id": str(conversation_id)},
                )
            )
            raise
        assistant_message = ConversationMessage(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=response,
        )
        await self._store.append_message(
            assistant_message,
            (
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.MODEL_RESPONSE,
                    actor_type=ActorType.COORDINATOR,
                    payload={
                        "conversation_id": str(conversation_id),
                        "message_id": str(assistant_message.id),
                    },
                ),
            ),
        )
        return user_message, assistant_message
