from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from astra_agent.conversations import ConversationOrchestrator
from astra_agent.settings import Settings, TenantWorkspace
from astra_agent.tools import LocalToolRegistry, ToolResult
from astra_domain import (
    ActorType,
    ApprovalState,
    AuditEvent,
    Capability,
    CapabilityKind,
    Conversation,
    ConversationTurnState,
    EventType,
    RemoteAgent,
    TaskState,
    ToolInvocationTarget,
)
from astra_memory import BoundedContextCompiler
from astra_model_providers import (
    ModelCompletion,
    ModelMessage,
    PlannerDecision,
    PlannerTask,
    ToolCall,
    ToolDefinition,
)
from astra_runtime import InMemoryRuntimeStore


class ScriptedProvider:
    def __init__(self, calls: tuple[ModelCompletion, ...]) -> None:
        self._calls = iter(calls)
        self.requests: list[tuple[ModelMessage, ...]] = []

    async def complete(
        self, messages: tuple[ModelMessage, ...], tools: tuple[ToolDefinition, ...]
    ) -> ModelCompletion:
        self.requests.append(messages)
        return next(self._calls)

    async def close(self) -> None:
        return None


class PlannedProvider(ScriptedProvider):
    async def plan(self, messages: tuple[ModelMessage, ...], max_siblings: int) -> PlannerDecision:
        assert max_siblings == 2
        return PlannerDecision(
            tasks=(
                PlannerTask(
                    objective="Inspect authentication",
                    required_capabilities=({"kind": "file.read", "scope": "repository"},),
                    deliverable_contract=(
                        "Return bounded repository findings with file and line evidence."
                    ),
                ),
                PlannerTask(
                    objective="Inspect configuration",
                    required_capabilities=({"kind": "file.read", "scope": "repository"},),
                    deliverable_contract=(
                        "Return bounded repository findings with file and line evidence."
                    ),
                ),
            )
        )


class FakeRunner:
    async def run(self, workspace: Path, argv: tuple[str, ...]) -> ToolResult:
        return ToolResult(success=True, content=f"ran {' '.join(argv)}")


async def _conversation(
    store: InMemoryRuntimeStore, tenant_id: UUID, user_id: UUID
) -> Conversation:
    conversation = Conversation(tenant_id=tenant_id, user_id=user_id)
    await store.create_conversation(
        conversation,
        AuditEvent(
            tenant_id=tenant_id,
            event_type=EventType.CONVERSATION_CREATED,
            actor_type=ActorType.USER,
            actor_id=user_id,
        ),
    )
    return conversation


async def test_read_file_tool_result_reaches_final_assistant(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("the launch code is aurora", encoding="utf-8")
    tenant_id, user_id = uuid4(), uuid4()
    provider = ScriptedProvider(
        (
            ModelCompletion(
                tool_calls=(
                    ToolCall(id="read-1", name="read_file", arguments={"path": "notes.txt"}),
                )
            ),
            ModelCompletion(content="The launch code is aurora."),
        )
    )
    settings = Settings(
        tenant_workspaces={
            tenant_id: TenantWorkspace(
                root=tmp_path,
                grants=(Capability(kind=CapabilityKind.FILE_READ, scope="workspace"),),
            )
        }
    )
    store = InMemoryRuntimeStore()
    conversation = await _conversation(store, tenant_id, user_id)
    orchestrator = ConversationOrchestrator(
        store,
        BoundedContextCompiler("safe persona"),
        provider,
        12,
        tool_registry=LocalToolRegistry(settings),
    )

    _, response = await orchestrator.respond(tenant_id, user_id, conversation.id, "Read the notes")

    assert response.content == "The launch code is aurora."
    assert "aurora" in provider.requests[1][-1].content


async def test_run_command_pauses_for_approval_then_resumes(tmp_path: Path) -> None:
    tenant_id, user_id = uuid4(), uuid4()
    provider = ScriptedProvider(
        (
            ModelCompletion(
                tool_calls=(
                    ToolCall(id="command-1", name="run_command", arguments={"argv": ["true"]}),
                )
            ),
            ModelCompletion(content="Command completed."),
        )
    )
    settings = Settings(
        tenant_workspaces={
            tenant_id: TenantWorkspace(
                root=tmp_path,
                grants=(Capability(kind=CapabilityKind.COMMAND_EXECUTE, scope="workspace"),),
            )
        }
    )
    store = InMemoryRuntimeStore()
    conversation = await _conversation(store, tenant_id, user_id)
    orchestrator = ConversationOrchestrator(
        store,
        BoundedContextCompiler("safe persona"),
        provider,
        12,
        tool_registry=LocalToolRegistry(settings, runner=FakeRunner()),
    )
    _, turn = await orchestrator.start_turn(
        tenant_id, user_id, conversation.id, uuid4(), "Run true"
    )

    paused = await orchestrator.advance_one(tenant_id)
    assert paused is not None and paused.state is ConversationTurnState.PAUSED
    approval = (await store.list_approvals(tenant_id))[0]
    await store.decide_approval(tenant_id, approval.id, ApprovalState.GRANTED, user_id)
    completed = await orchestrator.advance_one(tenant_id)

    assert completed is not None and completed.state is ConversationTurnState.COMPLETED


async def test_delegate_ara_pauses_then_resumes_with_task_result(tmp_path: Path) -> None:
    tenant_id, user_id, ara_id = uuid4(), uuid4(), uuid4()
    provider = ScriptedProvider(
        (
            ModelCompletion(
                tool_calls=(
                    ToolCall(
                        id="ara-1",
                        name="delegate_ara",
                        arguments={"objective": "Inspect auth", "context": "only auth files"},
                    ),
                )
            ),
            ModelCompletion(content="ARA findings synthesized."),
        )
    )
    store = InMemoryRuntimeStore()
    conversation = await _conversation(store, tenant_id, user_id)
    orchestrator = ConversationOrchestrator(
        store,
        BoundedContextCompiler("safe persona"),
        provider,
        12,
        tool_registry=LocalToolRegistry(Settings(tenant_workspaces={})),
    )
    _, turn = await orchestrator.start_turn(tenant_id, user_id, conversation.id, uuid4(), "Inspect")

    paused = await orchestrator.advance_one(tenant_id)

    assert paused is not None and paused.state is ConversationTurnState.PAUSED
    assert provider.requests[0] and provider.requests[0][-1].role == "user"
    task = (await store.list_tasks(tenant_id))[0]
    assert task.context == "only auth files"
    assert task.required_capabilities == (
        Capability(kind=CapabilityKind.FILE_READ, scope="repository"),
    )
    invocation = await store.get_tool_invocation(tenant_id, turn.id, "ara-1")
    assert invocation is not None
    assert invocation.target is ToolInvocationTarget.ARA
    assert invocation.task_id == task.id
    await store.register_ara(
        RemoteAgent(
            id=ara_id,
            tenant_id=tenant_id,
            name="repository",
            capabilities=task.required_capabilities,
            runtime_version="test",
        ),
        AuditEvent(
            tenant_id=tenant_id,
            event_type=EventType.ARA_REGISTERED,
            actor_type=ActorType.ARA,
            actor_id=ara_id,
        ),
    )
    leased = await store.lease_task(tenant_id, ara_id, datetime.now(UTC) + timedelta(minutes=1))
    assert leased is not None
    _, lease = leased
    await store.finish_task(
        tenant_id, ara_id, task.id, lease.id, TaskState.COMPLETED, "auth is sound"
    )

    completed = await orchestrator.advance_one(tenant_id)

    assert completed is not None and completed.state is ConversationTurnState.COMPLETED
    assert "auth is sound" in provider.requests[1][-1].content


async def test_planned_siblings_are_distinct_targeted_and_report_partial_failure() -> None:
    tenant_id, user_id = uuid4(), uuid4()
    capability = Capability(kind=CapabilityKind.FILE_READ, scope="repository")
    provider = PlannedProvider((ModelCompletion(content="Partial repository findings."),))
    store = InMemoryRuntimeStore()
    conversation = await _conversation(store, tenant_id, user_id)
    aras = tuple(uuid4() for _ in range(2))
    for ara_id in aras:
        await store.register_ara(
            RemoteAgent(
                id=ara_id,
                tenant_id=tenant_id,
                name=str(ara_id),
                capabilities=(capability,),
                runtime_version="test",
            ),
            AuditEvent(
                tenant_id=tenant_id,
                event_type=EventType.ARA_REGISTERED,
                actor_type=ActorType.ARA,
                actor_id=ara_id,
            ),
        )
    orchestrator = ConversationOrchestrator(
        store,
        BoundedContextCompiler("safe persona"),
        provider,
        12,
        tool_registry=LocalToolRegistry(Settings(tenant_workspaces={})),
    )
    _, turn = await orchestrator.start_turn(tenant_id, user_id, conversation.id, uuid4(), "Inspect")

    paused = await orchestrator.advance_one(tenant_id)
    tasks = await store.list_tasks(tenant_id)
    assert paused is not None and paused.state is ConversationTurnState.PAUSED
    assert {task.target_ara_id for task in tasks} == set(aras)
    assert (
        await store.lease_task(tenant_id, uuid4(), datetime.now(UTC) + timedelta(minutes=1)) is None
    )
    for index, task in enumerate(tasks):
        ara_id = task.target_ara_id
        assert ara_id is not None
        leased = await store.lease_task(tenant_id, ara_id, datetime.now(UTC) + timedelta(minutes=1))
        assert leased is not None
        await store.finish_task(
            tenant_id,
            ara_id,
            task.id,
            leased[1].id,
            TaskState.COMPLETED if index == 0 else TaskState.FAILED,
            "finding" if index == 0 else "inspection failed",
        )

    completed = await orchestrator.advance_one(tenant_id)
    assert completed is not None and completed.state is ConversationTurnState.COMPLETED
    assert '"partial_failure": true' in provider.requests[-1][-1].content
