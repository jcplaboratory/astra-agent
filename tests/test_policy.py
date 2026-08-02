from astra_domain import ApprovalState, Capability, CapabilityKind
from astra_model_providers import PlannerDecision, PlannerTask
from astra_policy import evaluate_capability, validate_planner_decision


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


def test_invalid_planner_capability_is_denied() -> None:
    decision = PlannerDecision(
        tasks=(
            PlannerTask(
                objective="Inspect the repository",
                required_capabilities=(
                    {"android_file_access": "read_directory"},
                ),
                deliverable_contract=(
                    "Return bounded repository findings with file and line evidence."
                ),
            ),
        )
    )

    policy = validate_planner_decision(decision, max_siblings=1)

    assert not policy.allowed
    assert policy.reason == "planner requested an invalid capability"
