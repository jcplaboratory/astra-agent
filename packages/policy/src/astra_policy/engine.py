from dataclasses import dataclass

from astra_domain import ApprovalState, Capability, CapabilityKind


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
