import json
import re
from uuid import UUID, uuid4

from astra_memory import MemoryContextCompiler, MemoryPipeline
from astra_runtime import RuntimeStore


class ControllerMemoryTools:
    """Tenant- and user-scoped durable memory operations for the controller."""

    def __init__(
        self, store: RuntimeStore, pipeline: MemoryPipeline, recall: MemoryContextCompiler
    ) -> None:
        self._store = store
        self._pipeline = pipeline
        self._recall = recall

    async def get_fact(self, tenant_id: UUID, topic: str) -> str:
        records = await self._recall.recall(tenant_id, topic)
        return json.dumps(
            {"topic": topic, "facts": [record.model_dump(mode="json") for record in records]},
            ensure_ascii=True,
        )

    async def set_fact(self, tenant_id: UUID, source_message_id: UUID, content: str) -> str:
        records = await self._pipeline.extract_content(
            tenant_id, uuid4(), source_message_id, content, promote_all=True
        )
        for record in records:
            await self._pipeline.sync_memory(record)
        return json.dumps(
            {"stored": [record.model_dump(mode="json") for record in records]}, ensure_ascii=True
        )

    async def search_session(
        self, tenant_id: UUID, user_id: UUID, query: str, limit: int
    ) -> str:
        terms = set(re.findall(r"[a-z0-9_]+", query.casefold()))
        matches: list[dict[str, object]] = []
        for conversation in await self._store.list_conversations(tenant_id):
            if conversation.user_id != user_id:
                continue
            for message in await self._store.list_messages(tenant_id, conversation.id, limit=200):
                message_terms = set(re.findall(r"[a-z0-9_]+", message.content.casefold()))
                score = len(terms.intersection(message_terms))
                if score:
                    matches.append(
                        {
                            "conversation_id": str(conversation.id),
                            "title": conversation.title,
                            "role": message.role.value,
                            "content": message.content,
                            "created_at": message.created_at.isoformat(),
                            "score": score,
                        }
                    )
        matches.sort(
            key=lambda item: (-item["score"], item["created_at"]),  # type: ignore[operator]
        )
        return json.dumps({"query": query, "matches": matches[:limit]}, ensure_ascii=True)
