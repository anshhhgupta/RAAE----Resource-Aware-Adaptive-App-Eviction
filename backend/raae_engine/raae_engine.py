"""RAAE Engine orchestration between Memory Manager and Conflict Manager."""

from dataclasses import replace
from typing import Any, Optional, Sequence

from backend.models.app import App
from backend.models.resource import Resource
from .conflict_manager import ConflictManager
from .models import (
    ConflictDecision,
    ConflictStatus,
    EvictionDecision,
    EvictionDecisionType,
    EvictionEvent,
    RAAEEngineResult,
)
from .persistence import EvictionPersistence


class RAAEEngine:
    """Coordinates safe eviction decisions for clock-selected candidates."""

    def __init__(
        self,
        conflict_manager: ConflictManager,
        memory_manager: Optional[Any] = None,
        resource_manager: Optional[Any] = None,
        persistence: Optional[EvictionPersistence] = None
    ) -> None:
        self.conflict_manager = conflict_manager
        self.memory_manager = memory_manager
        self.resource_manager = resource_manager
        self.persistence = persistence

    def orchestrate_safe_eviction(self, candidate: App) -> RAAEEngineResult:
        """Entry point used by Memory Manager after Clock selects a candidate."""
        return self.evaluate_eviction_candidate(candidate)

    def evaluate_eviction_candidate(self, candidate: App) -> RAAEEngineResult:
        """Intercepts and evaluates a clock-selected eviction candidate."""
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

    def execute_safe_eviction(
        self,
        candidate: App,
        memory_manager: Optional[Any] = None,
        resource_manager: Optional[Any] = None,
        persistence: Optional[EvictionPersistence] = None
    ) -> RAAEEngineResult:
        """Runs the full safe eviction workflow for a selected candidate."""
        result = self.evaluate_eviction_candidate(candidate)
        memory_before = candidate.memory_footprint
        active_memory_manager = memory_manager or self.memory_manager
        active_resource_manager = resource_manager or self.resource_manager
        active_persistence = persistence or self.persistence

        if result.decision_type == EvictionDecisionType.WAIT:
            return self._complete_without_eviction(
                result=result,
                memory_before=memory_before,
                event_result="WAIT",
                persistence=active_persistence,
                memory_manager=active_memory_manager
            )

        if result.decision_type == EvictionDecisionType.CONFLICT:
            return self._complete_without_eviction(
                result=result,
                memory_before=memory_before,
                event_result="CONFLICT",
                persistence=active_persistence,
                memory_manager=active_memory_manager
            )

        released_resource_ids = ()
        if result.decision_type == EvictionDecisionType.RELEASE_THEN_EVICT:
            if active_resource_manager is None:
                blocked_decision = ConflictDecision.blocked(
                    reason="Resource Manager is required before evicting a resource-holding candidate.",
                    resource_ids=[resource.resource_id for resource in result.held_resources]
                )
                blocked_result = self.build_eviction_decision(
                    candidate,
                    result.held_resources,
                    blocked_decision
                )
                return self._complete_without_eviction(
                    result=blocked_result,
                    memory_before=memory_before,
                    event_result="CONFLICT",
                    persistence=active_persistence,
                    memory_manager=active_memory_manager
                )

            released_resource_ids = tuple(sorted(
                active_resource_manager.release_all_resources_for_app(
                    candidate,
                    registered_apps=self._registered_apps(active_memory_manager)
                )
            ))

            if candidate.held_resources:
                blocked_decision = ConflictDecision.blocked(
                    reason="Candidate still holds resources after release attempt.",
                    resource_ids=sorted(candidate.held_resources)
                )
                blocked_result = self.build_eviction_decision(
                    candidate,
                    result.held_resources,
                    blocked_decision
                )
                return self._complete_without_eviction(
                    result=blocked_result,
                    memory_before=memory_before,
                    event_result="CONFLICT",
                    released_resource_ids=released_resource_ids,
                    persistence=active_persistence,
                    memory_manager=active_memory_manager
                )

        memory_after = self._evict_candidate(candidate, active_memory_manager)
        event = self._build_eviction_event(
            result=result,
            memory_before=memory_before,
            memory_after=memory_after,
            released_resource_ids=released_resource_ids,
            event_result="EVICTED"
        )
        persisted_event_id = self._persist_event(
            event=event,
            result=result,
            persistence=active_persistence,
            memory_manager=active_memory_manager
        )

        return replace(
            result,
            released_resource_ids=released_resource_ids,
            memory_released=max(0, memory_before - memory_after),
            eviction_event=event,
            persisted_event_id=persisted_event_id
        )

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
        if not held_resources and conflict_decision.status == ConflictStatus.NO_CONFLICT:
            decision_type = EvictionDecisionType.ALLOW_EVICTION
        elif held_resources and conflict_decision.status == ConflictStatus.NO_CONFLICT:
            decision_type = EvictionDecisionType.RELEASE_THEN_EVICT
        elif conflict_decision.status == ConflictStatus.WAIT:
            decision_type = EvictionDecisionType.WAIT
        else:
            decision_type = EvictionDecisionType.CONFLICT

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

    def _complete_without_eviction(
        self,
        result: RAAEEngineResult,
        memory_before: int,
        event_result: str,
        persistence: Optional[EvictionPersistence],
        memory_manager: Optional[Any],
        released_resource_ids: Sequence[str] = ()
    ) -> RAAEEngineResult:
        event = self._build_eviction_event(
            result=result,
            memory_before=memory_before,
            memory_after=memory_before,
            released_resource_ids=released_resource_ids,
            event_result=event_result
        )
        persisted_event_id = self._persist_event(
            event=event,
            result=result,
            persistence=persistence,
            memory_manager=memory_manager
        )

        return replace(
            result,
            released_resource_ids=tuple(released_resource_ids),
            memory_released=0,
            eviction_event=event,
            persisted_event_id=persisted_event_id
        )

    def _evict_candidate(self, candidate: App, memory_manager: Optional[Any]) -> int:
        if memory_manager is not None and candidate.app_id in memory_manager.apps:
            memory_manager.evict_app(candidate.app_id)
        else:
            candidate.memory_footprint = 0
            candidate.evict()

        return candidate.memory_footprint

    def _build_eviction_event(
        self,
        result: RAAEEngineResult,
        memory_before: int,
        memory_after: int,
        released_resource_ids: Sequence[str],
        event_result: str
    ) -> EvictionEvent:
        return EvictionEvent(
            app_id=result.candidate.app_id,
            algorithm="RAAE_CLOCK",
            reason=result.eviction_decision.reason,
            decision_type=result.decision_type,
            memory_before=memory_before,
            memory_after=memory_after,
            released_resource_ids=tuple(released_resource_ids),
            lock_checked=True,
            safe_release=event_result == "EVICTED",
            result=event_result
        )

    def _persist_event(
        self,
        event: EvictionEvent,
        result: RAAEEngineResult,
        persistence: Optional[EvictionPersistence],
        memory_manager: Optional[Any]
    ) -> Optional[int]:
        if persistence is None:
            return None

        return persistence.persist_eviction_event(
            event=event,
            candidate=result.candidate,
            resources=result.held_resources,
            registered_apps=self._registered_apps(memory_manager)
        )

    def _registered_apps(self, memory_manager: Optional[Any]) -> Optional[dict]:
        if memory_manager is None:
            return None

        return memory_manager.apps
