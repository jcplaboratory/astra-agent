from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from astra_domain import Lease, PersonaCore, Task
from pydantic import ValidationError


def test_tenant_is_required_for_task() -> None:
    with pytest.raises(ValidationError):
        Task(objective="inspect", deliverable_contract="report")


def test_lease_requires_positive_aware_window() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        Lease(
            tenant_id=uuid4(),
            task_id=uuid4(),
            ara_id=uuid4(),
            acquired_at=now,
            expires_at=now - timedelta(seconds=1),
        )


def test_legacy_persona_core_gets_safe_astra_identity() -> None:
    core = PersonaCore.model_validate(
        {
            "values": "v",
            "boundaries": "b",
            "tone": "t",
            "initiative": "i",
            "emotional_range": "e",
            "disagreement": "d",
        }
    )

    assert core.identity == "You are Astra."
