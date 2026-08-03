import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx
from astra_domain import (
    ActorType,
    AuditEvent,
    EventType,
    MemoryKind,
    MemoryRecord,
    MemoryState,
    PersonaProfile,
)
from astra_model_providers import LocalModelProvider

from astra_memory.compiler import ContextBriefing, persona_identity_prefix


class MemoryRepository(Protocol):
    async def get_active_persona(self, tenant_id: UUID) -> PersonaProfile | None: ...
    async def upsert_memory(
        self, memory: MemoryRecord, events: tuple[AuditEvent, ...]
    ) -> MemoryRecord: ...

    async def list_memories(
        self, tenant_id: UUID, *, include_candidates: bool = True
    ) -> tuple[MemoryRecord, ...]: ...


class VectorIndex(Protocol):
    async def ensure_ready(self) -> None: ...

    async def upsert(self, memory: MemoryRecord, vector: tuple[float, ...]) -> None: ...

    async def rank(
        self,
        tenant_id: UUID,
        visibility: str,
        allowed_ids: tuple[UUID, ...],
        vector: tuple[float, ...],
        limit: int,
    ) -> tuple[UUID, ...]: ...

    async def delete(self, memory_id: UUID) -> None: ...

    async def close(self) -> None: ...


class NullVectorIndex:
    async def ensure_ready(self) -> None:
        return None

    async def upsert(self, memory: MemoryRecord, vector: tuple[float, ...]) -> None:
        return None

    async def rank(
        self,
        tenant_id: UUID,
        visibility: str,
        allowed_ids: tuple[UUID, ...],
        vector: tuple[float, ...],
        limit: int,
    ) -> tuple[UUID, ...]:
        return ()

    async def delete(self, memory_id: UUID) -> None:
        return None

    async def close(self) -> None:
        return None


class QdrantVectorIndex:
    def __init__(
        self,
        base_url: str,
        collection: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        headers = {"api-key": api_key} if api_key else None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=10
        )
        self._owns_client = client is None
        self._collection = collection

    async def ensure_ready(self) -> None:
        response = await self._client.get(f"/collections/{self._collection}")
        if response.status_code == 404:
            created = await self._client.put(
                f"/collections/{self._collection}",
                json={"vectors": {"size": 32, "distance": "Cosine"}},
            )
            created.raise_for_status()
            return
        response.raise_for_status()
        vectors = (
            response.json().get("result", {}).get("config", {}).get("params", {}).get("vectors")
        )
        if not isinstance(vectors, dict) or vectors.get("size") != 32:
            raise RuntimeError("Qdrant collection has an incompatible vector schema")

    async def upsert(self, memory: MemoryRecord, vector: tuple[float, ...]) -> None:
        response = await self._client.put(
            f"/collections/{self._collection}/points",
            json={
                "points": [
                    {
                        "id": str(memory.id),
                        "vector": vector,
                        "payload": {
                            "tenant_id": str(memory.tenant_id),
                            "visibility": memory.visibility,
                        },
                    }
                ]
            },
        )
        response.raise_for_status()

    async def rank(
        self,
        tenant_id: UUID,
        visibility: str,
        allowed_ids: tuple[UUID, ...],
        vector: tuple[float, ...],
        limit: int,
    ) -> tuple[UUID, ...]:
        if not allowed_ids:
            return ()
        response = await self._client.post(
            f"/collections/{self._collection}/points/query",
            json={
                "query": vector,
                "filter": {
                    "must": [
                        {"key": "tenant_id", "match": {"value": str(tenant_id)}},
                        {"key": "visibility", "match": {"value": visibility}},
                        {"has_id": [str(item) for item in allowed_ids]},
                    ]
                },
                "limit": limit,
                "with_payload": False,
            },
        )
        response.raise_for_status()
        points = response.json().get("result", {}).get("points", [])
        allowed = set(allowed_ids)
        return tuple(
            point_id
            for item in points
            if isinstance(item, dict)
            and (point_id := _uuid_or_none(item.get("id"))) is not None
            and point_id in allowed
        )

    async def delete(self, memory_id: UUID) -> None:
        response = await self._client.post(
            f"/collections/{self._collection}/points/delete",
            json={"points": [str(memory_id)]},
        )
        response.raise_for_status()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _uuid_or_none(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


def deterministic_embedding(content: str, dimensions: int = 32) -> tuple[float, ...]:
    digest = hashlib.sha256(content.casefold().encode()).digest()
    return tuple((digest[index % len(digest)] / 127.5) - 1 for index in range(dimensions))


@dataclass(frozen=True)
class ExtractedMemory:
    kind: MemoryKind
    content: str
    confidence: float
    promoted: bool


class DeterministicMemoryExtractor:
    _patterns = (
        (re.compile(r"^remember that\s+(.+)$", re.IGNORECASE), MemoryKind.FACT, 0.99, True),
        (re.compile(r"^i prefer\s+(.+)$", re.IGNORECASE), MemoryKind.PREFERENCE, 0.95, True),
        (re.compile(r"^my project is\s+(.+)$", re.IGNORECASE), MemoryKind.PROJECT, 0.95, True),
        (re.compile(r"^i use\s+(.+)$", re.IGNORECASE), MemoryKind.FACT, 0.75, False),
    )

    async def extract(self, content: str) -> tuple[ExtractedMemory, ...]:
        candidates: list[ExtractedMemory] = []
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", content.strip()):
            cleaned = sentence.strip().rstrip(".!?").strip()
            for pattern, kind, confidence, promoted in self._patterns:
                match = pattern.match(cleaned)
                if match:
                    candidates.append(
                        ExtractedMemory(kind, match.group(1).strip(), confidence, promoted)
                    )
                    break
        return tuple(candidates)


class MemoryExtractor(Protocol):
    async def extract(self, content: str) -> tuple[ExtractedMemory, ...]: ...


class ModelBackedMemoryExtractor:
    _instruction = """Extract durable user memory as strict JSON only.
Return a JSON array of objects with keys: kind, content, confidence, promoted.
kind must be fact, preference, project, decision, or commitment.
Only promote explicit user requests or unambiguous preferences/project facts.
Do not infer sensitive facts. Return [] when nothing should be remembered."""

    def __init__(
        self,
        provider: LocalModelProvider,
        fallback: MemoryExtractor | None = None,
        audit: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._provider = provider
        self._fallback = fallback or DeterministicMemoryExtractor()
        self._audit = audit

    async def extract(self, content: str) -> tuple[ExtractedMemory, ...]:
        try:
            if self._audit is not None:
                self._audit(
                    "local_model.request", {"instruction": self._instruction, "content": content}
                )
            raw = await self._provider.process(self._instruction, content)
            if self._audit is not None:
                self._audit("local_model.response", {"input": content, "content": raw})
            # Instruction-tuned local models commonly wrap otherwise-valid JSON in prose or fences.
            start, end = raw.find("["), raw.rfind("]")
            if start < 0 or end < start:
                raise ValueError("memory extraction returned no JSON array")
            parsed = json.loads(raw[start : end + 1])
            if not isinstance(parsed, list):
                raise ValueError("memory extraction must return an array")
            results = []
            for item in parsed[:10]:
                if not isinstance(item, dict):
                    raise ValueError("memory candidate must be an object")
                results.append(
                    ExtractedMemory(
                        kind=MemoryKind(item["kind"]),
                        content=str(item["content"]).strip(),
                        confidence=float(item["confidence"]),
                        promoted=bool(item["promoted"]),
                    )
                )
            if any(not item.content or not 0 <= item.confidence <= 1 for item in results):
                raise ValueError("invalid memory candidate")
            return tuple(results)
        except Exception as error:
            if self._audit is not None:
                self._audit("local_model.failed", {"input": content, "error": str(error)})
            return await self._fallback.extract(content)


class MemoryPipeline:
    def __init__(
        self,
        repository: MemoryRepository,
        extractor: MemoryExtractor,
        vector_index: VectorIndex,
        audit: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._repository = repository
        self._extractor = extractor
        self._vector_index = vector_index
        self._audit = audit

    async def extract_message(
        self, tenant_id: UUID, source_event_id: UUID, source_message_id: UUID, content: str
    ) -> tuple[MemoryRecord, ...]:
        records: list[MemoryRecord] = []
        for candidate in await self._extractor.extract(content):
            normalized = " ".join(candidate.content.casefold().split())
            state = MemoryState.PROMOTED if candidate.promoted else MemoryState.CANDIDATE
            memory = MemoryRecord(
                tenant_id=tenant_id,
                kind=candidate.kind,
                content=candidate.content,
                normalized_content=normalized,
                source_event_id=source_event_id,
                source_message_id=source_message_id,
                confidence=candidate.confidence,
                confirmed=candidate.promoted,
                state=state,
            )
            events = [
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.MEMORY_CANDIDATE_CREATED,
                    actor_type=ActorType.COORDINATOR,
                    payload={
                        "memory_id": str(memory.id),
                        "source_message_id": str(source_message_id),
                    },
                )
            ]
            if candidate.promoted:
                events.append(
                    AuditEvent(
                        tenant_id=tenant_id,
                        event_type=EventType.MEMORY_PROMOTED,
                        actor_type=ActorType.COORDINATOR,
                        payload={"memory_id": str(memory.id)},
                    )
                )
            stored = await self._repository.upsert_memory(memory, tuple(events))
            records.append(stored)
        return tuple(records)

    async def sync_memory(self, memory: MemoryRecord) -> None:
        if memory.state is MemoryState.PROMOTED:
            await self._vector_index.upsert(memory, deterministic_embedding(memory.content))
            if self._audit is not None:
                self._audit("memory.vector_sync", {"memory": memory.model_dump(mode="json")})
        elif memory.state is MemoryState.DELETED:
            await self._vector_index.delete(memory.id)
            if self._audit is not None:
                self._audit("memory.vector_delete", {"memory": memory.model_dump(mode="json")})


class MemoryContextCompiler:
    def __init__(
        self,
        repository: MemoryRepository,
        vector_index: VectorIndex,
        persona_kernel: str,
        max_tokens: int = 800,
        memory_limit: int = 8,
        audit: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._repository = repository
        self._vector_index = vector_index
        self._persona_kernel = persona_kernel.strip()
        self._max_tokens = max_tokens
        self._memory_limit = memory_limit
        self._audit = audit

    async def compile(self, tenant_id: UUID, objective: str) -> ContextBriefing:
        authorized = await self._repository.list_memories(tenant_id, include_candidates=False)
        by_id = {item.id: item for item in authorized if item.visibility == "private"}
        qdrant_available = True
        try:
            ranked_ids = await self._vector_index.rank(
                tenant_id,
                "private",
                tuple(by_id),
                deterministic_embedding(objective),
                self._memory_limit,
            )
        except Exception:
            # Vector availability must not prevent a safe lexical recall.
            ranked_ids = ()
            qdrant_available = False
        # Current vectors are deterministic hashes, not semantic embeddings. Keep Qdrant observable
        # for migration diagnostics, but use lexical relevance for safe, predictable recall.
        terms = set(re.findall(r"[a-z0-9_]+", objective.casefold()))
        ranked = sorted(
            by_id.values(),
            key=lambda item: (
                -len(terms.intersection(re.findall(r"[a-z0-9_]+", item.content.casefold()))),
                str(item.id),
            ),
        )[: self._memory_limit]
        if self._audit is not None:
            self._audit(
                "memory.ranking",
                {
                    "objective": objective,
                    "strategy": "lexical",
                    "qdrant_available": qdrant_available,
                    "authorized_memory_ids": [str(item) for item in by_id],
                    "qdrant_ranked_memory_ids": [str(item) for item in ranked_ids],
                    "selected_memory_ids": [str(item.id) for item in ranked],
                },
            )
        profile = await self._repository.get_active_persona(tenant_id)
        persona = (
            f"{persona_identity_prefix()}\nPersona guidance: {self._persona_kernel}"
            if profile is None
            else "\n".join(
                (
                    persona_identity_prefix(profile.authored_core.identity),
                    f"Values: {profile.authored_core.values}",
                    f"Boundaries: {profile.authored_core.boundaries}",
                    f"Tone: {profile.authored_core.tone}",
                    f"Initiative: {profile.authored_core.initiative}",
                    f"Emotional range: {profile.authored_core.emotional_range}",
                    f"Disagreement: {profile.authored_core.disagreement}",
                )
            )
        )
        sections = [persona]
        if ranked:
            sections.append(
                "Relevant approved memory:\n"
                + "\n".join(f"- [{item.kind.value}] {item.content}" for item in ranked)
            )
        sections.append(f"Current objective:\n{objective.strip()}")
        content = "\n\n".join(sections)
        max_characters = self._max_tokens * 4
        if len(content) > max_characters:
            content = content[: max_characters - 3].rstrip() + "..."
        included_ids = tuple(item.id for item in ranked if item.content in content)
        return ContextBriefing(
            tenant_id=tenant_id,
            content=content,
            source_memory_ids=included_ids,
            estimated_tokens=(len(content) + 3) // 4,
        )
