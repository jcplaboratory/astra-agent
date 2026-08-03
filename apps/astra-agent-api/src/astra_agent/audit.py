from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID


class SessionAudit:
    """Ephemeral, full-fidelity traces requested by an interactive client."""

    def __init__(self) -> None:
        self._traces: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
        self._message_traces: dict[UUID, UUID] = {}
        self._tenants: dict[UUID, UUID] = {}

    def begin(self, trace_id: UUID, tenant_id: UUID, content: str) -> None:
        self._tenants[trace_id] = tenant_id
        self.record(trace_id, "user.input", {"content": content})

    def attach_message(self, trace_id: UUID, message_id: UUID) -> None:
        self._message_traces[message_id] = trace_id

    def for_message(self, message_id: UUID) -> UUID | None:
        return self._message_traces.get(message_id)

    def for_content(self, content: str) -> UUID | None:
        for _message_id, trace_id in reversed(tuple(self._message_traces.items())):
            events = self._traces[trace_id]
            if events and events[0].get("content") == content:
                return trace_id
        return None

    def tenant_for_content(self, content: str) -> UUID | None:
        trace_id = self.for_content(content)
        return self._tenants.get(trace_id) if trace_id is not None else None

    def for_objective(self, objective: str) -> UUID | None:
        return self.for_content(objective)

    def record(self, trace_id: UUID, stage: str, payload: dict[str, Any]) -> None:
        self._traces[trace_id].append(
            {"at": datetime.now(UTC).isoformat(), "stage": stage, **payload}
        )

    def get(self, trace_id: UUID) -> list[dict[str, Any]]:
        return self._traces.get(trace_id, [])

    def belongs_to(self, trace_id: UUID, tenant_id: UUID) -> bool:
        return self._tenants.get(trace_id) == tenant_id
