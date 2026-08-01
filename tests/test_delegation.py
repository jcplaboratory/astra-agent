import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from astra_agent.conversations import ConversationOrchestrator
from astra_ara.executor import ARARepositoryExecutor
from astra_domain import (
    ActorType,
    AuditEvent,
    Capability,
    CapabilityKind,
    Conversation,
    EventType,
    RemoteAgent,
    TaskState,
)
from astra_memory import BoundedContextCompiler
from astra_model_providers import DevelopmentModelProvider
from astra_runtime import InMemoryRuntimeStore


async def test_repository_request_delegates_executes_and_synthesizes(tmp_path: Path) -> None:
    source = tmp_path / "auth.py"
    source.write_text(
        "def verify_token(token: str) -> bool:\n    return token.startswith('jwt-')\n",
        encoding="utf-8",
    )
    store = InMemoryRuntimeStore()
    tenant_id = uuid4()
    user_id = uuid4()
    ara = RemoteAgent(
        tenant_id=tenant_id,
        name="repo-ara",
        runtime_version="1",
        capabilities=(Capability(kind=CapabilityKind.FILE_READ, scope="repository"),),
    )
    await store.register_ara(
        ara,
        AuditEvent(
            tenant_id=tenant_id,
            event_type=EventType.ARA_REGISTERED,
            actor_type=ActorType.ARA,
            actor_id=ara.id,
        ),
    )
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
    executor = ARARepositoryExecutor(tmp_path)

    async def execute_one() -> None:
        while True:
            leased = await store.lease_task(
                tenant_id, ara.id, datetime.now(UTC) + timedelta(minutes=1)
            )
            if leased:
                task, lease = leased
                result = executor.execute(task)
                await store.finish_task(
                    tenant_id,
                    ara.id,
                    task.id,
                    lease.id,
                    TaskState.COMPLETED,
                    result,
                )
                return
            await asyncio.sleep(0.01)

    orchestrator = ConversationOrchestrator(
        store,
        BoundedContextCompiler("safe persona"),
        DevelopmentModelProvider(),
        12,
        delegation_wait_seconds=2,
        delegation_poll_seconds=0.01,
    )
    ara_task = asyncio.create_task(execute_one())
    _, response = await orchestrator.respond(
        tenant_id,
        user_id,
        conversation.id,
        "Inspect this repository authentication flow",
    )
    await ara_task
    assert "Repository inspection completed" in response.content
    assert "auth.py" in response.content
    assert "verify_token" in response.content
    tasks = await store.list_tasks(tenant_id)
    assert tasks[0].state is TaskState.COMPLETED
    assert tasks[0].result is not None


def test_repository_executor_never_leaves_configured_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("password secret", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)
    (root / "README.md").write_text("authentication overview", encoding="utf-8")
    from astra_domain import Task

    result = ARARepositoryExecutor(root).execute(
        Task(
            tenant_id=uuid4(),
            objective="Inspect repository authentication",
            deliverable_contract="report",
        )
    )
    assert "README.md" in result
    assert "link.txt" not in result
    assert "password secret" not in result
