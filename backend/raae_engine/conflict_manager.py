"""Conflict Manager boundary for RAAE eviction decisions."""

from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

from backend.conflict_manager.conflict_manager import (
    ConflictDecision as WoundWaitDecision,
    ConflictManager as WoundWaitConflictManager,
    ConflictResult,
)
from backend.models.app import App
from backend.models.resource import Resource
from backend.resource_manager.resource_manager import ResourceManager
from .models import ConflictDecision, ConflictStatus


class ConflictManager(Protocol):
    """Minimal interface the RAAE Engine needs from conflict management."""

    def get_held_resources(self, candidate: App) -> List[Resource]:
        """Returns resources currently held by the eviction candidate."""

    def get_waiting_resources(self, candidate: App) -> List[Resource]:
        """Returns resources for which the candidate is queued as a waiter."""

    def evaluate_eviction_conflict(
        self,
        candidate: App,
        conflict_resources: Sequence[Resource]
    ) -> ConflictDecision:
        """Returns conflict status for a candidate's held or requested resources."""


class ResourceConflictManager:
    """Connects ResourceManager state and Wound-Wait to the RAAE interface."""

    def __init__(
        self,
        resource_manager: ResourceManager,
        registered_apps: Optional[Dict[str, App]] = None,
        db: Optional[Any] = None,
    ) -> None:
        self.resource_manager = resource_manager
        self.conflict_manager = WoundWaitConflictManager(
            resource_manager=resource_manager,
        )
        self.registered_apps = registered_apps if registered_apps is not None else {}
        self.db = db

    def get_held_resources(self, candidate: App) -> List[Resource]:
        held_resources = []

        for resource_id in sorted(candidate.held_resources):
            resource = self.resource_manager.get_resource(resource_id)
            if resource is not None:
                held_resources.append(resource)

        return held_resources

    def get_waiting_resources(self, candidate: App) -> List[Resource]:
        """Finds resources on which a Clock-selected candidate is blocked."""
        return [
            resource
            for resource in self.resource_manager.get_all_resources()
            if candidate.app_id in resource.waiting_queue
        ]

    def evaluate_eviction_conflict(
        self,
        candidate: App,
        conflict_resources: Sequence[Resource]
    ) -> ConflictDecision:
        resources = self._get_conflict_resources(candidate, conflict_resources)

        if candidate.is_evicted:
            return ConflictDecision.not_safe(
                reason="Candidate is already evicted.",
                resource_ids=[resource.resource_id for resource in resources]
            )

        if not resources:
            return ConflictDecision.no_conflict(
                reason="Candidate has no held or requested resources."
            )

        release_plan: List[Tuple[App, Resource]] = []
        planned_release_keys = set()
        conflict_events: List[Tuple[ConflictResult, str]] = []
        waiting_events: List[Tuple[ConflictResult, str]] = []
        waiting_resource_ids = []

        for resource in resources:
            is_waiting = candidate.app_id in resource.waiting_queue
            is_holder = candidate.app_id in resource.holders
            if not is_waiting and not is_holder:
                return ConflictDecision.not_safe(
                    reason=f"Candidate/resource ownership is inconsistent for '{resource.resource_id}'.",
                    resource_ids=[resource.resource_id],
                )

            other_holders = [
                holder_id for holder_id in resource.holders if holder_id != candidate.app_id
            ]
            if is_waiting and other_holders:
                waiting_resource_ids.append(resource.resource_id)
                for holder_id in other_holders:
                    holder = self.registered_apps.get(holder_id)
                    if holder is None:
                        self._persist_conflict_events(waiting_events)
                        return ConflictDecision.resolve_required(
                            reason=f"Register holder '{holder_id}' to apply Wound-Wait.",
                            resource_ids=[resource.resource_id],
                            waiting_app_ids=[candidate.app_id],
                        )

                    result = self.conflict_manager.resolve_conflict(
                        requester=candidate,
                        holder=holder,
                        resource=resource,
                    )
                    if result.decision == WoundWaitDecision.WAIT:
                        waiting_events.append((result, "WAIT"))
                        self._persist_conflict_events(conflict_events + waiting_events)
                        return ConflictDecision.wait(
                            reason=result.reason,
                            resource_ids=[resource.resource_id],
                            waiting_app_ids=[candidate.app_id],
                        )

                    conflict_events.append((result, "WOUND"))
                    self._add_release(release_plan, planned_release_keys, holder, resource)

                if not is_holder:
                    if not self._will_candidate_be_awakened(
                        candidate,
                        resource,
                        release_plan,
                    ):
                        self._persist_conflict_events(conflict_events + waiting_events)
                        return ConflictDecision.wait(
                            reason=f"Candidate remains behind earlier waiters for '{resource.resource_id}'.",
                            resource_ids=[resource.resource_id],
                            waiting_app_ids=[candidate.app_id],
                        )
                    self._add_release(release_plan, planned_release_keys, candidate, resource)

            if is_holder:
                for waiter_id in resource.waiting_queue:
                    if waiter_id == candidate.app_id:
                        continue
                    waiter = self.registered_apps.get(waiter_id)
                    if waiter is None or waiter.is_evicted:
                        self._persist_conflict_events(conflict_events + waiting_events)
                        return ConflictDecision.resolve_required(
                            reason=f"Register active waiter '{waiter_id}' to apply Wound-Wait.",
                            resource_ids=[resource.resource_id],
                            waiting_app_ids=[waiter_id],
                        )

                    result = self.conflict_manager.resolve_conflict(
                        requester=waiter,
                        holder=candidate,
                        resource=resource,
                    )
                    if result.decision == WoundWaitDecision.WOUND_HOLDER:
                        conflict_events.append((result, "WOUND"))
                    else:
                        waiting_events.append((result, "WAIT"))
                        self._persist_conflict_events(conflict_events + waiting_events)
                        return ConflictDecision.wait(
                            reason=result.reason,
                            resource_ids=[resource.resource_id],
                            waiting_app_ids=[waiter_id],
                        )

                self._add_release(release_plan, planned_release_keys, candidate, resource)

        if not release_plan:
            self._persist_conflict_events(conflict_events + waiting_events)
            return ConflictDecision.wait(
                reason="Candidate remains blocked by a resource conflict.",
                resource_ids=waiting_resource_ids,
                waiting_app_ids=[candidate.app_id],
            )

        for app, resource in release_plan:
            if app.app_id == candidate.app_id:
                continue
            units = resource.holders.get(app.app_id, 0)
            if units <= 0 or resource.resource_id not in self.resource_manager.locks:
                self._persist_conflict_events(waiting_events)
                return ConflictDecision.not_safe(
                    reason=f"Cannot safely release '{resource.resource_id}' from '{app.app_id}'.",
                    resource_ids=[resource.resource_id],
                )

        for app, resource in release_plan:
            if app.app_id == candidate.app_id:
                continue
            units = resource.holders.get(app.app_id, 0)
            if units and not self.resource_manager.release_resource(
                app,
                resource.resource_id,
                units=units,
                registered_apps=self.registered_apps,
            ):
                self._persist_conflict_events(waiting_events)
                return ConflictDecision.not_safe(
                    reason=f"Resource Manager could not release '{resource.resource_id}' from '{app.app_id}'.",
                    resource_ids=[resource.resource_id],
                )

        for app, resource in release_plan:
            if app.app_id != candidate.app_id:
                continue
            units = resource.holders.get(candidate.app_id, 0)
            if units and not self.resource_manager.release_resource(
                candidate,
                resource.resource_id,
                units=units,
                registered_apps={
                    app_id: app
                    for app_id, app in self.registered_apps.items()
                    if app_id != candidate.app_id
                },
            ):
                self._persist_conflict_events(waiting_events)
                return ConflictDecision.not_safe(
                    reason=f"Resource Manager could not release candidate's '{resource.resource_id}' units.",
                    resource_ids=[resource.resource_id],
                )

        remaining_resources = [
            resource.resource_id
            for resource in resources
            if candidate.app_id in resource.holders or candidate.app_id in resource.waiting_queue
        ]
        if remaining_resources:
            self._persist_conflict_events(waiting_events)
            return ConflictDecision.not_safe(
                reason="Candidate still holds or waits for resources after release.",
                resource_ids=remaining_resources,
                waiting_app_ids=[candidate.app_id],
            )

        self._persist_conflict_events(conflict_events + waiting_events)

        return ConflictDecision.no_conflict(
            reason="Wound-Wait conflicts were resolved and candidate resources are releasable.",
            resource_ids=[resource.resource_id for resource in resources],
        )

    def _get_conflict_resources(
        self,
        candidate: App,
        conflict_resources: Sequence[Resource],
    ) -> List[Resource]:
        resources_by_id = {}
        for resource in (
            *conflict_resources,
            *self.get_held_resources(candidate),
            *self.get_waiting_resources(candidate),
        ):
            current_resource = self.resource_manager.get_resource(resource.resource_id)
            resources_by_id[resource.resource_id] = current_resource or resource
        return [resources_by_id[key] for key in sorted(resources_by_id)]

    @staticmethod
    def _add_release(
        release_plan: List[Tuple[App, Resource]],
        planned_release_keys: set,
        app: App,
        resource: Resource,
    ) -> None:
        key = (app.app_id, resource.resource_id)
        if key not in planned_release_keys:
            planned_release_keys.add(key)
            release_plan.append((app, resource))

    def _will_candidate_be_awakened(
        self,
        candidate: App,
        resource: Resource,
        release_plan: Sequence[Tuple[App, Resource]],
    ) -> bool:
        units_available = resource.available_units + sum(
            resource.holders.get(app.app_id, 0)
            for app, planned_resource in release_plan
            if planned_resource.resource_id == resource.resource_id
            and app.app_id != candidate.app_id
        )
        for app_id in resource.waiting_queue:
            app = self.registered_apps.get(app_id)
            if app_id == candidate.app_id:
                return units_available > 0
            if app is not None and not app.is_evicted and units_available > 0:
                units_available -= 1
        return False

    def _persist_conflict_events(self, events: Sequence[Tuple[ConflictResult, str]]) -> None:
        if self.db is None:
            return
        for result, resolved_by in events:
            self.db.conflict_log.log_conflict(
                waiting_app_id=result.requester or result.candidate_id or "UNKNOWN",
                blocking_app_id=result.holder or result.holder_id or "UNKNOWN",
                resource_id=result.resource or result.resource_id or "UNKNOWN",
                resolution_strategy=result.strategy,
                resolved_by=resolved_by,
                details=result.reason,
                timestamp=result.resolution_timestamp or result.timestamp,
            )

