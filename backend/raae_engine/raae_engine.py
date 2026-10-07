"""RAAE Engine orchestration between Memory Manager and Conflict Manager."""

from typing import Sequence

from backend.models.app import App
from backend.models.resource import Resource
from .conflict_manager import ConflictManager
from .models import (
    ConflictDecision,
    ConflictStatus,
    EvictionDecision,
    EvictionDecisionType,
    RAAEEngineResult,
)


class RAAEEngine:
    """Coordinates safe eviction decisions for clock-selected candidates."""

    def __init__(self, conflict_manager: ConflictManager) -> None:
        self.conflict_manager = conflict_manager

    def orchestrate_safe_eviction(self, candidate: App) -> RAAEEngineResult:
        """Entry point used by Memory Manager after Clock selects a candidate."""
        return self.evaluate_eviction_candidate(candidate)

    def evaluate_eviction_candidate(self, candidate: App) -> RAAEEngineResult:
        """Evaluates whether a clock-selected candidate is safe to evict."""
        held_resources = self.check_held_resources(candidate)

        if not held_resources:
            return self.build_eviction_decision(
                candidate=candidate,
                held_resources=held_resources,
                conflict_decision=ConflictDecision.no_conflict(
                    reason="Candidate holds no resources."
                )
            )

        conflict_decision = self.request_conflict_decision(candidate, held_resources)
        return self.build_eviction_decision(candidate, held_resources, conflict_decision)

    def check_held_resources(self, candidate: App) -> Sequence[Resource]:
        """Asks the Conflict Manager boundary for resources held by candidate."""
        return tuple(self.conflict_manager.get_held_resources(candidate))

    def request_conflict_decision(
        self,
        candidate: App,
        held_resources: Sequence[Resource]
    ) -> ConflictDecision:
        """Requests conflict status for a resource-holding candidate."""
        if not held_resources:
            return ConflictDecision.no_conflict(
                reason="Candidate holds no resources."
            )

        decision = self.conflict_manager.evaluate_eviction_conflict(candidate, held_resources)
        if decision is None:
            return ConflictDecision.not_safe(
                reason="Conflict Manager did not return a decision.",
                resource_ids=[resource.resource_id for resource in held_resources]
            )

        return decision

    def build_eviction_decision(
        self,
        candidate: App,
        held_resources: Sequence[Resource],
        conflict_decision: ConflictDecision
    ) -> RAAEEngineResult:
        """Maps conflict status to the final eviction decision model."""
        decision_type_by_status = {
            ConflictStatus.NO_CONFLICT: EvictionDecisionType.SAFE_TO_EVICT,
            ConflictStatus.WAIT: EvictionDecisionType.WAIT,
            ConflictStatus.RESOLVE_REQUIRED: EvictionDecisionType.RESOLVE_REQUIRED,
            ConflictStatus.BLOCKED: EvictionDecisionType.BLOCKED,
            ConflictStatus.NOT_SAFE: EvictionDecisionType.NOT_SAFE,
        }

        decision_type = decision_type_by_status.get(
            conflict_decision.status,
            EvictionDecisionType.NOT_SAFE
        )

        eviction_decision = EvictionDecision(
            decision_type=decision_type,
            reason=conflict_decision.reason
        )

        return RAAEEngineResult(
            candidate=candidate,
            held_resources=tuple(held_resources),
            conflict_decision=conflict_decision,
            eviction_decision=eviction_decision
        )
