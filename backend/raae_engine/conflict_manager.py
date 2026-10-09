"""Conflict Manager boundary for RAAE eviction decisions."""

from typing import Any, List, Optional, Protocol, Sequence

from backend.conflict_manager import ConflictDecision as WoundWaitDecision
from backend.models.app import App
from backend.models.resource import Resource
from backend.resource_manager.resource_manager import ResourceManager
from .models import ConflictDecision


class ConflictManager(Protocol):
    """Minimal interface the RAAE Engine needs from conflict management."""

    def get_held_resources(self, candidate: App) -> List[Resource]:
        """Returns resources currently held by the eviction candidate."""

    def evaluate_eviction_conflict(
        self,
        candidate: App,
        held_resources: Sequence[Resource]
    ) -> ConflictDecision:
        """Returns conflict status for a candidate and its held resources."""


class ResourceConflictManager:
    """Adapts the existing ResourceManager to the RAAE conflict interface."""

    def __init__(self, resource_manager: ResourceManager) -> None:
        self.resource_manager = resource_manager

    def get_held_resources(self, candidate: App) -> List[Resource]:
        held_resources = []

        for resource_id in sorted(candidate.held_resources):
            resource = self.resource_manager.get_resource(resource_id)
            if resource is None:
                resource = Resource(
                    resource_id=resource_id,
                    name=f"Unknown Resource {resource_id}",
                    capacity=1,
                    available_units=0,
                    holders={candidate.app_id: 1}
                )
            held_resources.append(resource)

        return held_resources

    def evaluate_eviction_conflict(
        self,
        candidate: App,
        held_resources: Sequence[Resource]
    ) -> ConflictDecision:
        if candidate.is_evicted:
            return ConflictDecision.not_safe(
                reason="Candidate is already evicted.",
                resource_ids=[resource.resource_id for resource in held_resources]
            )

        if not held_resources:
            return ConflictDecision.no_conflict(
                reason="Candidate holds no resources."
            )

        waiting_app_ids = []
        waiting_resource_ids = []

        for resource in held_resources:
            blocked_waiters = [
                app_id
                for app_id in resource.waiting_queue
                if app_id != candidate.app_id
            ]
            if blocked_waiters:
                waiting_resource_ids.append(resource.resource_id)
                waiting_app_ids.extend(blocked_waiters)

        if waiting_app_ids:
            return ConflictDecision.resolve_required(
                reason="Candidate holds resources with waiting applications.",
                resource_ids=waiting_resource_ids,
                waiting_app_ids=waiting_app_ids
            )

        return ConflictDecision.no_conflict(
            reason="Candidate-held resources have no waiting applications.",
            resource_ids=[resource.resource_id for resource in held_resources]
        )


class WoundWaitConflictAdapter:
    """Exposes the Wound-Wait ConflictManager through the RAAE conflict interface.

    ``backend.conflict_manager.ConflictManager`` already implements Wound-Wait
    and already logs its resolutions to ConflictLog, but it presents a different
    surface: ``resolve_conflict(requester, holder, resource)``. The RAAE Engine
    asks for ``get_held_resources`` and ``evaluate_eviction_conflict`` instead.
    Rather than reshape either module, this adapter supplies the RAAE interface
    and delegates every contested decision to Wound-Wait.

    Mapping of Wound-Wait outcomes onto RAAE decisions:

    * No blocked waiters -> ``NO_CONFLICT``, so the engine safely releases the
      resources and evicts.
    * Waiter outranks the candidate -> the candidate is wounded, reported as
      ``RESOLVE_REQUIRED`` and therefore blocked from eviction.
    * Candidate outranks the waiter -> Wound-Wait says the candidate keeps the
      resource, but evicting it would still strand the waiter, so it is reported
      as ``WAIT`` and the candidate is protected.

    In both contested cases the candidate survives eviction, which is the point
    of RAAE: a shared resource that somebody is waiting for is never dropped.
    """

    def __init__(
        self,
        conflict_manager: Any,
        resource_manager: Optional[ResourceManager] = None
    ) -> None:
        """Args:
            conflict_manager: The Wound-Wait ConflictManager to delegate to.
            resource_manager: Used to resolve held resources and waiting queues.
        """
        self.conflict_manager = conflict_manager
        self.resource_manager = resource_manager

    def get_held_resources(self, candidate: App) -> List[Resource]:
        """Returns resources currently held by the eviction candidate."""
        if self.resource_manager is None:
            return []

        held = []
        for resource_id in sorted(candidate.held_resources):
            resource = self.resource_manager.get_resource(resource_id)
            if resource is not None:
                held.append(resource)
        return held

    def _resolve_app(self, app_or_id: Any) -> Any:
        """Returns the registered App for an id, or the value unchanged.

        Wound-Wait needs each side's priority and access time to compare them.
        Handing it bare id strings would make every pair look equally
        precedence-matched, so the registered model is looked up when available.
        """
        if not isinstance(app_or_id, str) or self.resource_manager is None:
            return app_or_id
        return self.resource_manager.apps.get(app_or_id, app_or_id)

    def evaluate_eviction_conflict(
        self,
        candidate: App,
        held_resources: Sequence[Resource]
    ) -> ConflictDecision:
        """Runs Wound-Wait for each contended resource and reports the outcome."""
        if candidate.is_evicted:
            return ConflictDecision.not_safe(
                reason="Candidate is already evicted.",
                resource_ids=[resource.resource_id for resource in held_resources]
            )

        if not held_resources:
            return ConflictDecision.no_conflict(reason="Candidate holds no resources.")

        waiting_app_ids: List[str] = []
        waiting_resource_ids: List[str] = []
        reasons: List[str] = []
        outcomes: List[Any] = []

        for resource in held_resources:
            blocked_waiters = [
                app_id for app_id in resource.waiting_queue if app_id != candidate.app_id
            ]
            if not blocked_waiters:
                continue

            result = self.conflict_manager.resolve_conflict(
                requester=self._resolve_app(blocked_waiters[0]),
                holder=candidate,
                resource=resource
            )

            waiting_resource_ids.append(resource.resource_id)
            waiting_app_ids.extend(blocked_waiters)
            reasons.append(result.reason)
            outcomes.append(result.decision)

        if not waiting_app_ids:
            return ConflictDecision.no_conflict(
                reason="Candidate-held resources have no waiting applications.",
                resource_ids=[resource.resource_id for resource in held_resources]
            )

        detail = " | ".join(reasons)
        return self._blocked_decision(detail, waiting_resource_ids, waiting_app_ids, outcomes)

    def _blocked_decision(
        self,
        detail: str,
        resource_ids: Sequence[str],
        waiting_app_ids: Sequence[str],
        outcomes: Sequence[Any],
    ) -> ConflictDecision:
        """Chooses the RAAE status matching the recorded Wound-Wait outcomes.

        A wounded candidate reports ``RESOLVE_REQUIRED``; a candidate that
        outranked its waiters reports ``WAIT``. Either way the engine refuses
        the eviction, so the contended resource is never orphaned.
        """
        if any(outcome == WoundWaitDecision.WOUND_HOLDER for outcome in outcomes):
            return ConflictDecision.resolve_required(
                reason=f"Wound-Wait wounded the candidate. {detail}",
                resource_ids=resource_ids,
                waiting_app_ids=waiting_app_ids
            )

        return ConflictDecision.wait(
            reason=f"Wound-Wait kept the candidate ahead of the waiters. {detail}",
            resource_ids=resource_ids,
            waiting_app_ids=waiting_app_ids
        )
