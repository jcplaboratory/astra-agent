import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from astra_domain import (
    ActorType,
    Approval,
    ApprovalState,
    AuditEvent,
    BackgroundJob,
    BackgroundJobKind,
    Capability,
    CapabilityKind,
    ConversationMessage,
    ConversationTurn,
    ConversationTurnState,
    EventType,
    MessageRole,
    Task,
    TaskState,
    ToolInvocation,
    ToolInvocationState,
    ToolInvocationTarget,
)
from astra_memory import ContextCompiler
from astra_model_providers import (
    MainModelProvider,
    ModelCompletion,
    ModelMessage,
    ModelProviderError,
    ModelStreamEvent,
    PlannerDecision,
    ToolCall,
    ToolDefinition,
)
from astra_policy import validate_planner_decision
from astra_runtime import LifecycleConflictError, RuntimeStore

from astra_agent.settings import Settings
from astra_agent.tools import LocalToolRegistry, ToolResult

_RUN_LEASE_DURATION = timedelta(minutes=5)
_MAX_TOOL_ITERATIONS = 8
_MAX_TOOL_RESULT_CHARS = 12_000
_FILE_READ_CAPABILITY = Capability(kind=CapabilityKind.FILE_READ, scope="workspace")
_ARA_FILE_READ_CAPABILITY = Capability(kind=CapabilityKind.FILE_READ, scope="repository")
_COMMAND_CAPABILITY = Capability(kind=CapabilityKind.COMMAND_EXECUTE, scope="workspace")
_ARA_DELEGATE_TOOL = ToolDefinition(
    name="delegate_ara",
    description="Delegate bounded read-only repository inspection to an Astra Remote Agent.",
    parameters={
        "type": "object",
        "properties": {
            "objective": {"type": "string", "minLength": 1},
            "context": {"type": "string"},
        },
        "required": ["objective"],
        "additionalProperties": False,
    },
)


class ConversationOrchestrator:
    def __init__(
        self,
        store: RuntimeStore,
        compiler: ContextCompiler,
        model_provider: MainModelProvider,
        history_limit: int,
        delegation_wait_seconds: float = 30,
        delegation_poll_seconds: float = 0.25,
        delegation_enabled: bool = True,
        tool_registry: LocalToolRegistry | None = None,
        max_tool_iterations: int = _MAX_TOOL_ITERATIONS,
        max_delegation_siblings: int = 2,
    ) -> None:
        self._store = store
        self._compiler = compiler
        self._model_provider = model_provider
        self._history_limit = history_limit
        self._delegation_enabled = delegation_enabled
        # Kept for callers still constructing this class with the former signature.
        self._tool_registry = tool_registry or LocalToolRegistry(Settings())
        self._max_tool_iterations = max_tool_iterations
        self._max_delegation_siblings = max_delegation_siblings

    async def start_turn(
        self,
        tenant_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
        client_request_id: UUID,
        content: str,
    ) -> tuple[ConversationMessage, ConversationTurn]:
        user_message = ConversationMessage(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=content,
        )
        turn = ConversationTurn(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_message_id=user_message.id,
            client_request_id=client_request_id,
        )
        received_event = AuditEvent(
            tenant_id=tenant_id,
            event_type=EventType.CONVERSATION_RECEIVED,
            actor_type=ActorType.USER,
            actor_id=user_id,
            payload={"conversation_id": str(conversation_id), "message_id": str(user_message.id)},
        )
        created = await self._store.create_turn(
            user_message,
            turn,
            (
                received_event,
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.CONVERSATION_TURN_CREATED,
                    actor_type=ActorType.COORDINATOR,
                    payload={"turn_id": str(turn.id)},
                ),
            ),
            BackgroundJob(
                tenant_id=tenant_id,
                kind=BackgroundJobKind.MEMORY_EXTRACTION,
                source_id=user_message.id,
                payload={"source_event_id": str(received_event.id), "content": content},
            ),
        )
        if created.id != turn.id:
            messages = await self._store.list_messages(
                tenant_id, conversation_id, self._history_limit
            )
            existing = next((item for item in messages if item.id == created.user_message_id), None)
            if existing is None:
                raise LifecycleConflictError("turn user message is unavailable")
            return existing, created
        return user_message, created

    async def advance_one(self, tenant_id: UUID) -> ConversationTurn | None:
        run_lease_id = uuid4()
        turn = await self._store.claim_pending_turn(
            tenant_id, run_lease_id, datetime.now(UTC) + _RUN_LEASE_DURATION
        )
        if turn is None:
            return None
        return await self.run_turn(turn, run_lease_id)

    async def run_turn(self, turn: ConversationTurn, run_lease_id: UUID) -> ConversationTurn:
        messages, iterations, pending_calls = await self._messages_for_turn(turn)
        if "planned_task_ids" in turn.checkpoint:
            messages.append(await self._sibling_provenance(turn))
        elif self._delegation_enabled:
            planner = getattr(self._model_provider, "plan", None)
            try:
                decision = (
                    await planner(tuple(messages), self._max_delegation_siblings)
                    if callable(planner)
                    else PlannerDecision()
                )
            except ModelProviderError:
                await self._store.append_event(
                    AuditEvent(
                        tenant_id=turn.tenant_id,
                        event_type=EventType.PLANNER_DENIED,
                        actor_type=ActorType.COORDINATOR,
                        payload={"turn_id": str(turn.id), "reason": "planner provider failed"},
                    )
                )
                decision = PlannerDecision()
            policy = validate_planner_decision(decision, self._max_delegation_siblings)
            if not policy.allowed:
                await self._store.append_event(
                    AuditEvent(
                        tenant_id=turn.tenant_id,
                        event_type=EventType.PLANNER_DENIED,
                        actor_type=ActorType.COORDINATOR,
                        payload={"turn_id": str(turn.id), "reason": policy.reason},
                    )
                )
            elif decision.tasks:
                tasks = tuple(
                    Task(
                        tenant_id=turn.tenant_id,
                        objective=item.objective,
                        context=item.context,
                        required_capabilities=tuple(
                            Capability.model_validate(capability)
                            for capability in item.required_capabilities
                        ),
                        deliverable_contract=item.deliverable_contract,
                    )
                    for item in decision.tasks
                )
                invocations = tuple(
                    ToolInvocation(
                        tenant_id=turn.tenant_id,
                        turn_id=turn.id,
                        task_id=task.id,
                        tool_call_id=f"planner-{index}",
                        tool_name=_ARA_DELEGATE_TOOL.name,
                        target=ToolInvocationTarget.ARA,
                        arguments={"objective": task.objective, "context": task.context},
                        arguments_sha256=self._arguments_sha256(
                            {"objective": task.objective, "context": task.context}
                        ),
                    )
                    for index, task in enumerate(tasks)
                )
                checkpoint = {
                    "messages": [message.model_dump() for message in messages],
                    "iterations": iterations,
                    "pending_calls": [],
                    "planned_task_ids": [str(task.id) for task in tasks],
                }
                return (
                    await self._store.create_delegated_tasks(
                        turn.tenant_id, turn.id, run_lease_id, tasks, invocations, checkpoint
                    )
                    and (await self._store.get_turn(turn.tenant_id, turn.id))
                    or turn
                )
        definitions = self._tool_registry.definitions(turn.tenant_id)
        if self._delegation_enabled:
            definitions = (*definitions, _ARA_DELEGATE_TOOL)
        known_tools = {definition.name for definition in definitions}

        while pending_calls:
            call, *remaining_calls = pending_calls
            paused = await self._execute_call(turn, run_lease_id, call, known_tools, messages)
            if paused is not None:
                return paused
            pending_calls = tuple(remaining_calls)
            turn = await self._checkpoint(turn, run_lease_id, messages, iterations, pending_calls)

        while iterations < self._max_tool_iterations:
            await self._store.append_event(
                AuditEvent(
                    tenant_id=turn.tenant_id,
                    event_type=EventType.MODEL_REQUEST,
                    actor_type=ActorType.COORDINATOR,
                    payload={"turn_id": str(turn.id), "message_count": len(messages)},
                )
            )
            try:
                completion = await self._model_provider.complete(tuple(messages), definitions)
            except ModelProviderError:
                await self._store.append_event(
                    AuditEvent(
                        tenant_id=turn.tenant_id,
                        event_type=EventType.MODEL_FAILED,
                        actor_type=ActorType.COORDINATOR,
                        payload={"turn_id": str(turn.id)},
                    )
                )
                raise
            if not isinstance(completion, ModelCompletion):
                raise TypeError("tool-enabled model completion must return ModelCompletion")
            await self._store.append_event(
                AuditEvent(
                    tenant_id=turn.tenant_id,
                    event_type=EventType.MODEL_RESPONSE,
                    actor_type=ActorType.COORDINATOR,
                    payload={
                        "turn_id": str(turn.id),
                        "tool_call_count": len(completion.tool_calls),
                    },
                )
            )
            if not completion.tool_calls:
                content = completion.content or "I could not produce a response."
                return await self._store.complete_turn(
                    turn.tenant_id,
                    turn.id,
                    run_lease_id,
                    ConversationMessage(
                        tenant_id=turn.tenant_id,
                        conversation_id=turn.conversation_id,
                        role=MessageRole.ASSISTANT,
                        content=content,
                    ),
                )

            messages.append(
                ModelMessage(
                    role="system",
                    content=self._tool_call_summary(completion.tool_calls, completion.content),
                )
            )
            iterations += 1
            pending_calls = completion.tool_calls
            turn = await self._checkpoint(turn, run_lease_id, messages, iterations, pending_calls)
            while pending_calls:
                call, *remaining_calls = pending_calls
                paused = await self._execute_call(turn, run_lease_id, call, known_tools, messages)
                if paused is not None:
                    return paused
                pending_calls = tuple(remaining_calls)
                turn = await self._checkpoint(
                    turn, run_lease_id, messages, iterations, pending_calls
                )

        return await self._store.complete_turn(
            turn.tenant_id,
            turn.id,
            run_lease_id,
            ConversationMessage(
                tenant_id=turn.tenant_id,
                conversation_id=turn.conversation_id,
                role=MessageRole.ASSISTANT,
                content="I stopped after reaching the tool-call limit.",
            ),
        )

    async def stream_turn(
        self, turn: ConversationTurn, run_lease_id: UUID
    ) -> AsyncIterator[ModelStreamEvent]:
        messages, iterations, _ = await self._messages_for_turn(turn)
        definitions = self._tool_registry.definitions(turn.tenant_id)
        if self._delegation_enabled:
            definitions = (*definitions, _ARA_DELEGATE_TOOL)
        streamer = getattr(self._model_provider, "stream", None)
        if not callable(streamer):
            raise ModelProviderError("model provider does not support streaming")
        try:
            while iterations < self._max_tool_iterations:
                content: list[str] = []
                calls: list[ToolCall] = []
                async for event in streamer(tuple(messages), definitions):
                    if event.kind == "content":
                        content.append(event.content)
                    elif event.kind == "tool_call" and event.tool_call is not None:
                        calls.append(event.tool_call)
                    yield event
                if not calls:
                    await self._store.complete_turn(
                        turn.tenant_id,
                        turn.id,
                        run_lease_id,
                        ConversationMessage(
                            tenant_id=turn.tenant_id,
                            conversation_id=turn.conversation_id,
                            role=MessageRole.ASSISTANT,
                            content="".join(content) or "I could not produce a response.",
                        ),
                    )
                    return
                messages.append(
                    ModelMessage(
                        role="system",
                        content=self._tool_call_summary(tuple(calls), "".join(content) or None),
                    )
                )
                known_tools = {definition.name for definition in definitions}
                for call in calls:
                    paused = await self._execute_call(
                        turn, run_lease_id, call, known_tools, messages
                    )
                    if paused is not None:
                        return
                iterations += 1
            await self._store.complete_turn(
                turn.tenant_id,
                turn.id,
                run_lease_id,
                ConversationMessage(
                    tenant_id=turn.tenant_id,
                    conversation_id=turn.conversation_id,
                    role=MessageRole.ASSISTANT,
                    content="I stopped after reaching the tool-call limit.",
                ),
            )
        except ModelProviderError:
            await self._store.append_event(
                AuditEvent(
                    tenant_id=turn.tenant_id,
                    event_type=EventType.MODEL_FAILED,
                    actor_type=ActorType.COORDINATOR,
                    payload={"turn_id": str(turn.id)},
                )
            )
            raise

    async def _messages_for_turn(
        self, turn: ConversationTurn
    ) -> tuple[list[ModelMessage], int, tuple[ToolCall, ...]]:
        checkpoint_messages = turn.checkpoint.get("messages")
        if isinstance(checkpoint_messages, list):
            messages = [ModelMessage.model_validate(item) for item in checkpoint_messages]
            iterations = turn.checkpoint.get("iterations", 0)
            if isinstance(iterations, int) and iterations >= 0:
                pending_calls = turn.checkpoint.get("pending_calls", [])
                if not isinstance(pending_calls, list):
                    raise LifecycleConflictError("turn checkpoint has invalid pending calls")
                return (
                    messages,
                    iterations,
                    tuple(ToolCall.model_validate(item) for item in pending_calls),
                )
            raise LifecycleConflictError("turn checkpoint has invalid iteration count")

        history = await self._store.list_messages(
            turn.tenant_id, turn.conversation_id, self._history_limit
        )
        user_message = next((item for item in history if item.id == turn.user_message_id), None)
        if user_message is None:
            raise LifecycleConflictError("turn user message is unavailable")
        briefing = await self._compiler.compile(turn.tenant_id, user_message.content)
        await self._store.append_event(
            AuditEvent(
                tenant_id=turn.tenant_id,
                event_type=EventType.CONTEXT_COMPILED,
                actor_type=ActorType.COORDINATOR,
                payload={
                    "turn_id": str(turn.id),
                    "estimated_tokens": briefing.estimated_tokens,
                    "source_memory_ids": [str(item) for item in briefing.source_memory_ids],
                },
            )
        )
        return (
            [
                ModelMessage(role="system", content=briefing.content),
                *(ModelMessage(role=item.role.value, content=item.content) for item in history),
            ],
            0,
            (),
        )

    async def _execute_call(
        self,
        turn: ConversationTurn,
        run_lease_id: UUID,
        call: ToolCall,
        known_tools: set[str],
        messages: list[ModelMessage],
    ) -> ConversationTurn | None:
        if call.name == _ARA_DELEGATE_TOOL.name:
            return await self._delegate_ara(turn, run_lease_id, call, messages)
        invocation_id = uuid4()
        invocation = await self._store.create_tool_invocation(
            ToolInvocation(
                id=invocation_id,
                tenant_id=turn.tenant_id,
                turn_id=turn.id,
                tool_call_id=call.id,
                tool_name=call.name,
                target=ToolInvocationTarget.LOCAL,
                arguments=call.arguments,
                arguments_sha256=self._arguments_sha256(call.arguments),
            )
        )
        if invocation.state is ToolInvocationState.DENIED:
            messages.append(self._tool_message(call.name, "Tool use was denied by policy."))
            return None
        if call.name not in known_tools:
            await self._store.fail_tool_invocation(turn.tenant_id, invocation.id)
            messages.append(self._tool_message(call.name, "Unknown or unavailable local tool."))
            return None

        approval_state = ApprovalState.GRANTED if invocation.id != invocation_id else None
        result = await self._tool_registry.dispatch(
            turn.tenant_id, call.name, call.arguments, approval_state
        )
        if result.data.get("requires_approval") is True:
            await self._store.create_coordinator_approval(
                Approval(
                    tenant_id=turn.tenant_id,
                    tool_invocation_id=invocation.id,
                    capability=self._capability_for(call.name),
                    requestor_type=ActorType.COORDINATOR,
                    reason=result.error or f"Approval required to use {call.name}",
                )
            )
            paused = await self._store.get_turn(turn.tenant_id, turn.id)
            if paused is None:
                raise LifecycleConflictError("paused turn is unavailable")
            return paused
        if result.success:
            await self._store.complete_tool_invocation(turn.tenant_id, invocation.id)
            tool_content = self._bounded_result(result)
        else:
            await self._store.fail_tool_invocation(turn.tenant_id, invocation.id)
            tool_content = "The tool request was denied or could not be completed."
        messages.append(self._tool_message(call.name, tool_content))
        return None

    async def _delegate_ara(
        self,
        turn: ConversationTurn,
        run_lease_id: UUID,
        call: ToolCall,
        messages: list[ModelMessage],
    ) -> ConversationTurn | None:
        objective = call.arguments.get("objective")
        context = call.arguments.get("context", "")
        if not isinstance(objective, str) or not objective or not isinstance(context, str):
            messages.append(self._tool_message(call.name, "Invalid delegation arguments."))
            return None
        existing_invocation = await self._store.get_tool_invocation(
            turn.tenant_id, turn.id, call.id
        )
        if existing_invocation is not None:
            if existing_invocation.task_id is None:
                raise LifecycleConflictError("delegation invocation has no task")
            existing = await self._store.get_task(turn.tenant_id, existing_invocation.task_id)
            if existing is None:
                raise LifecycleConflictError("delegated task is unavailable")
            if existing.state in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}:
                messages.append(self._tool_message(call.name, self._bounded_task_result(existing)))
                return None
            return await self._store.pause_turn(
                turn.tenant_id, turn.id, run_lease_id, turn.checkpoint
            )
        task = Task(
            tenant_id=turn.tenant_id,
            objective=objective[:10_000],
            context=context[:12_000],
            required_capabilities=(_ARA_FILE_READ_CAPABILITY,),
            deliverable_contract="Return bounded repository findings with file and line evidence.",
        )
        await self._store.add_task(
            task,
            AuditEvent(
                tenant_id=turn.tenant_id,
                event_type=EventType.TASK_CREATED,
                actor_type=ActorType.COORDINATOR,
                task_id=task.id,
                payload={"turn_id": str(turn.id)},
            ),
        )
        await self._store.create_tool_invocation(
            ToolInvocation(
                tenant_id=turn.tenant_id,
                turn_id=turn.id,
                task_id=task.id,
                tool_call_id=call.id,
                tool_name=call.name,
                target=ToolInvocationTarget.ARA,
                arguments=call.arguments,
                arguments_sha256=self._arguments_sha256(call.arguments),
            )
        )
        return await self._store.pause_turn(turn.tenant_id, turn.id, run_lease_id, turn.checkpoint)

    async def _checkpoint(
        self,
        turn: ConversationTurn,
        run_lease_id: UUID,
        messages: list[ModelMessage],
        iterations: int,
        pending_calls: tuple[ToolCall, ...],
    ) -> ConversationTurn:
        return await self._store.checkpoint_turn(
            turn.tenant_id,
            turn.id,
            run_lease_id,
            {
                "messages": [message.model_dump() for message in messages],
                "iterations": iterations,
                "pending_calls": [call.model_dump() for call in pending_calls],
            },
        )

    async def _sibling_provenance(self, turn: ConversationTurn) -> ModelMessage:
        raw_ids = turn.checkpoint.get("planned_task_ids", [])
        if not isinstance(raw_ids, list):
            raise LifecycleConflictError("turn checkpoint has invalid planned task ids")
        results: list[dict[str, str | None]] = []
        for raw_id in raw_ids:
            task = await self._store.get_task(turn.tenant_id, UUID(str(raw_id)))
            if task is None:
                raise LifecycleConflictError("planned task is unavailable")
            results.append(
                {
                    "task_id": str(task.id),
                    "objective": task.objective,
                    "state": task.state.value,
                    "result": task.result,
                    "target_ara_id": str(task.target_ara_id) if task.target_ara_id else None,
                    "completed_by_ara_id": str(task.completed_by_ara_id)
                    if task.completed_by_ara_id
                    else None,
                }
            )
        failures = sum(item["state"] != TaskState.COMPLETED.value for item in results)
        return ModelMessage(
            role="system",
            content="ARA sibling provenance (synthesize transparently; "
            "partial failures must be explicit):\n"
            + json.dumps({"partial_failure": failures > 0, "results": results}, sort_keys=True),
        )

    @staticmethod
    def _arguments_sha256(arguments: dict[str, Any]) -> str:
        encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode()).hexdigest()

    @staticmethod
    def _tool_call_summary(calls: tuple[ToolCall, ...], content: str | None) -> str:
        prefix = content or "The model requested local tools."
        return f"{prefix}\nTool calls: " + ", ".join(call.name for call in calls)

    @staticmethod
    def _tool_message(name: str, content: str) -> ModelMessage:
        return ModelMessage(role="system", content=f"Tool result for {name}:\n{content}")

    @staticmethod
    def _bounded_result(result: ToolResult) -> str:
        payload = json.dumps(result.model_dump(), sort_keys=True, ensure_ascii=True)
        return payload[:_MAX_TOOL_RESULT_CHARS]

    @staticmethod
    def _bounded_task_result(task: Task) -> str:
        return (task.result or "ARA completed without findings.")[:_MAX_TOOL_RESULT_CHARS]

    @staticmethod
    def _capability_for(name: str) -> Capability:
        if name == "run_command":
            return _COMMAND_CAPABILITY
        return _FILE_READ_CAPABILITY

    async def respond(
        self,
        tenant_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
        content: str,
    ) -> tuple[ConversationMessage, ConversationMessage]:
        user_message, turn = await self.start_turn(
            tenant_id, user_id, conversation_id, uuid4(), content
        )
        while turn.state not in {ConversationTurnState.COMPLETED, ConversationTurnState.PAUSED}:
            await self.advance_one(tenant_id)
            refreshed = await self._store.get_turn(tenant_id, turn.id)
            if refreshed is None:
                raise LifecycleConflictError("turn is unavailable")
            turn = refreshed
        if turn.state is ConversationTurnState.PAUSED:
            raise LifecycleConflictError("turn is awaiting approval")
        messages = await self._store.list_messages(tenant_id, conversation_id, self._history_limit)
        assistant_message = next(
            (item for item in reversed(messages) if item.role is MessageRole.ASSISTANT), None
        )
        if assistant_message is None:
            raise LifecycleConflictError("completed turn has no assistant message")
        return user_message, assistant_message
