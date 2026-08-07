import json
from uuid import uuid4

from astra_agent.memory_tools import ControllerMemoryTools
from astra_domain import (
    ActorType,
    AuditEvent,
    Conversation,
    ConversationMessage,
    EventType,
    MessageRole,
)
from astra_memory import (
    DeterministicMemoryExtractor,
    MemoryContextCompiler,
    MemoryPipeline,
    NullVectorIndex,
)
from astra_runtime import InMemoryRuntimeStore


async def test_controller_memory_tools_recall_promoted_facts_and_scope_sessions_to_user() -> None:
    tenant_id, user_id, other_user_id = uuid4(), uuid4(), uuid4()
    store = InMemoryRuntimeStore()
    pipeline = MemoryPipeline(store, DeterministicMemoryExtractor(), NullVectorIndex())
    tools = ControllerMemoryTools(
        store, pipeline, MemoryContextCompiler(store, NullVectorIndex(), "persona")
    )
    conversation = Conversation(tenant_id=tenant_id, user_id=user_id, title="Project notes")
    other_conversation = Conversation(tenant_id=tenant_id, user_id=other_user_id, title="Other")
    for item, owner in ((conversation, user_id), (other_conversation, other_user_id)):
        await store.create_conversation(
            item,
            AuditEvent(
                tenant_id=tenant_id,
                event_type=EventType.CONVERSATION_CREATED,
                actor_type=ActorType.USER,
                actor_id=owner,
            ),
        )
    await store.append_message(
        ConversationMessage(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="The aurora project uses Python.",
        ),
        (),
    )
    await store.append_message(
        ConversationMessage(
            tenant_id=tenant_id,
            conversation_id=other_conversation.id,
            role=MessageRole.USER,
            content="The aurora password is secret.",
        ),
        (),
    )

    stored = json.loads(
        await tools.set_fact(tenant_id, uuid4(), "remember that the project is aurora")
    )
    recalled = json.loads(await tools.get_fact(tenant_id, "aurora project"))
    sessions = json.loads(await tools.search_session(tenant_id, user_id, "aurora", 8))

    assert stored["stored"][0]["state"] == "promoted"
    assert recalled["facts"][0]["content"] == "the project is aurora"
    assert [item["conversation_id"] for item in sessions["matches"]] == [str(conversation.id)]
