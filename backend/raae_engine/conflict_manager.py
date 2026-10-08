"""Conflict Manager boundary for RAAE eviction decisions."""

from typing import List, Protocol, Sequence

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
