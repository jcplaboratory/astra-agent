from astra_domain import ApprovalState, Capability, CapabilityKind
from astra_policy import evaluate_capability


def test_read_must_be_explicitly_scoped() -> None:
    requested = Capability(kind=CapabilityKind.FILE_READ, scope="/repo")
    assert not evaluate_capability(requested, ()).allowed
    assert evaluate_capability(requested, (requested,)).allowed


def test_impactful_capabilities_require_approval() -> None:
    requested = Capability(kind=CapabilityKind.COMMAND_EXECUTE, scope="pytest")
    decision = evaluate_capability(requested, (requested,))
    assert not decision.allowed
    assert decision.requires_approval
    assert evaluate_capability(requested, (requested,), ApprovalState.GRANTED).allowed
