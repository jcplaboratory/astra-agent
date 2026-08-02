from uuid import uuid4

from astra_agent import create_app
from astra_domain import EventType, LearnedAdaptationState, LearnedPersonaAdaptation
from astra_memory import MemoryContextCompiler, NullVectorIndex
from astra_runtime import InMemoryRuntimeStore
from fastapi.testclient import TestClient


def _headers(tenant_id: object) -> dict[str, str]:
    return {"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-User-ID": str(uuid4())}


def _core(values: str = "Help users") -> dict[str, str]:
    return {
        "identity": "You are Astra, a trusted personal agent.",
        "values": values,
        "boundaries": "Do not claim certainty without evidence",
        "tone": "Direct and kind",
        "initiative": "Offer useful next steps",
        "emotional_range": "Warm but measured",
        "disagreement": "Explain disagreements respectfully",
    }


def test_persona_is_tenant_scoped_versioned_revertible_and_audited() -> None:
    tenant_id, other_tenant = uuid4(), uuid4()
    headers = _headers(tenant_id)
    with TestClient(create_app()) as client:
        first = client.put(
            f"/api/v1/tenants/{tenant_id}/persona",
            headers=headers,
            json={"tenant_id": str(tenant_id), "authored_core": _core("First value")},
        )
        assert first.status_code == 200
        assert first.json()["persona"]["version"] == 1
        second = client.put(
            f"/api/v1/tenants/{tenant_id}/persona",
            headers=headers,
            json={"tenant_id": str(tenant_id), "authored_core": _core("Second value")},
        )
        assert second.json()["persona"]["version"] == 2
        reverted = client.post(
            f"/api/v1/tenants/{tenant_id}/persona/revert",
            headers=headers,
            json={"tenant_id": str(tenant_id), "version": 1},
        )
        assert reverted.json()["persona"]["authored_core"]["values"] == "First value"
        assert (
            client.get(f"/api/v1/tenants/{other_tenant}/persona", headers=headers).status_code
            == 403
        )
        event_types = {
            item["event_type"]
            for item in client.get(f"/api/v1/tenants/{tenant_id}/events", headers=headers).json()
        }
        assert EventType.PERSONA_CREATED.value in event_types
        assert EventType.PERSONA_REVERTED.value in event_types


async def test_active_persona_is_compiled_within_bound_and_falls_back_to_settings_kernel() -> None:
    store = InMemoryRuntimeStore()
    tenant_id = uuid4()
    compiler = MemoryContextCompiler(store, NullVectorIndex(), "fallback persona", max_tokens=100)
    fallback = (await compiler.compile(tenant_id, "objective")).content
    assert fallback.startswith("Identity: You are Astra.")
    assert "fallback persona" in fallback
    client = TestClient(create_app(store=store))
    with client:
        response = client.put(
            f"/api/v1/tenants/{tenant_id}/persona",
            headers=_headers(tenant_id),
            json={"tenant_id": str(tenant_id), "authored_core": _core("x" * 100)},
        )
        assert response.status_code == 200
    briefing = await compiler.compile(tenant_id, "objective")
    assert briefing.content.startswith("Identity: You are Astra, a trusted personal agent.")
    assert briefing.content.index("Identity:") < briefing.content.index("Values:")
    assert "Values: " + "x" * 10 in briefing.content
    assert briefing.estimated_tokens <= 100


async def test_learned_adaptation_is_separate_and_reversible() -> None:
    store = InMemoryRuntimeStore()
    tenant_id = uuid4()
    profile_response = None
    with TestClient(create_app(store=store)) as client:
        profile_response = client.put(
            f"/api/v1/tenants/{tenant_id}/persona",
            headers=_headers(tenant_id),
            json={"tenant_id": str(tenant_id), "authored_core": _core()},
        )
    profile_id = profile_response.json()["persona"]["id"]
    adaptation = await store.create_learned_persona_adaptation(
        LearnedPersonaAdaptation(
            tenant_id=tenant_id,
            profile_id=profile_id,
            content="Prefer concise summaries when the user asks.",
            source="reviewed feedback",
        )
    )
    reversed_adaptation = await store.reverse_learned_persona_adaptation(tenant_id, adaptation.id)
    assert reversed_adaptation.state is LearnedAdaptationState.REVERSED
    assert (await store.get_active_persona(tenant_id)).authored_core.values == "Help users"  # type: ignore[union-attr]
