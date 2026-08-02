from dataclasses import dataclass

from astra_domain import ApprovalState, Capability, CapabilityKind
from astra_model_providers import PlannerDecision


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    reason: str


def evaluate_capability(
    requested: Capability,
    granted: tuple[Capability, ...],
    approval_state: ApprovalState | None = None,
) -> PolicyDecision:
    if requested not in granted:
        return PolicyDecision(False, False, "capability is not explicitly granted")

    if requested.kind is CapabilityKind.FILE_READ:
        return PolicyDecision(True, False, "explicitly scoped read access")

    if approval_state is ApprovalState.GRANTED:
        return PolicyDecision(True, False, "impactful capability approved")

    return PolicyDecision(False, True, "impactful capability requires approval")


def validate_planner_decision(decision: PlannerDecision, max_siblings: int) -> PolicyDecision:
    if not decision.tasks:
        return PolicyDecision(True, False, "no delegation requested")
    if len(decision.tasks) > max_siblings:
        return PolicyDecision(False, False, "planner exceeded sibling limit")
    objectives: set[str] = set()
    expected = (Capability(kind=CapabilityKind.FILE_READ, scope="repository"),)
    for task in decision.tasks:
        if len(task.objective) > 10_000 or len(task.context) > 12_000:
            return PolicyDecision(False, False, "planner task exceeds bounded input")
        if task.objective.strip() in objectives:
            return PolicyDecision(False, False, "planner contains duplicate sibling objectives")
        objectives.add(task.objective.strip())
        capabilities = tuple(Capability.model_validate(item) for item in task.required_capabilities)
        if capabilities != expected:
            return PolicyDecision(False, False, "planner requested a non-read-only capability")
        if (
            task.deliverable_contract
            != "Return bounded repository findings with file and line evidence."
        ):
            return PolicyDecision(False, False, "planner deliverable contract is not permitted")
    return PolicyDecision(True, False, "bounded read-only repository inspection")
