"""Persistence adapters for RAAE eviction workflow results."""

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence

from backend.models.app import App
from backend.models.resource import Resource
from .models import EvictionEvent


@dataclass(frozen=True)
class ConflictRecord:
    """A conflict incident to record alongside an eviction.

    This is persistence input only: the decision about whether a conflict exists
    is made by the RAAE engine and the OS policy modules, never here.

    Attributes:
        waiting_app_id (str): Application blocked waiting for the resource.
        blocking_app_id (str): Application whose eviction caused the block.
        resource_id (str): Resource under contention.
        resolution_strategy (str): Strategy applied to the conflict.
        resolved_by (Optional[str]): Resolving party, if any.
        details (Optional[str]): Free-form detail text.
    """

    waiting_app_id: str
    blocking_app_id: str
    resource_id: str
    resolution_strategy: str = "WOUND_WAIT"
    resolved_by: Optional[str] = None
    details: Optional[str] = None


class EvictionPersistence(Protocol):
    """Persistence interface used by RAAEEngine without depending on SQLite."""

    def persist_eviction_event(
        self,
        event: EvictionEvent,
        candidate: App,
        resources: Sequence[Resource],
        registered_apps: Optional[Dict[str, App]] = None
    ) -> int:
        """Persists the completed eviction workflow and returns the event id."""


class DatabaseEvictionPersistence:
    """Repository-backed persistence adapter using DatabaseManager abstractions.

    All writes for one eviction - releasing the candidate's locks, saving app and
    resource state, and recording the eviction, memory and conflict logs - are
    issued inside a single database transaction. If any step raises, the whole
    group is rolled back, so the database never records a partially applied
    eviction.
    """

    def __init__(
        self,
        database_manager,
        conflict_records: Sequence[ConflictRecord] = ()
    ) -> None:
        self.database_manager = database_manager
        self.conflict_records: List[ConflictRecord] = list(conflict_records)

    def persist_eviction_event(
        self,
        event: EvictionEvent,
        candidate: App,
        resources: Sequence[Resource],
        registered_apps: Optional[Dict[str, App]] = None
    ) -> int:
        """Persists the eviction and its related logs atomically.

        Args:
            event (EvictionEvent): Eviction event produced by the engine.
            candidate (App): The evicted application.
            resources (Sequence[Resource]): Resources involved in the eviction.
            registered_apps (Optional[Dict[str, App]]): Apps whose state must be saved.

        Returns:
            int: The persisted EvictionLog event id.

        Raises:
            Exception: Any failure raised by a repository write propagates after
                the transaction is rolled back.
        """
        with self.database_manager.transaction():
            apps_to_save = registered_apps.values() if registered_apps else [candidate]
            for app in apps_to_save:
                self.database_manager.save_app(app)

            for resource in resources:
                self.database_manager.save_resource(resource)

            if event.result == "EVICTED":
                self.database_manager.resource_locks.release_all_for_app(candidate.app_id)

            self.database_manager.memory_events.log_event(
                app_id=event.app_id,
                action="EVICT" if event.result == "EVICTED" else "EVICT_ATTEMPT",
                memory_before=event.memory_before,
                memory_after=event.memory_after,
                pressure_level="RAAE"
            )

            event_id = self.database_manager.eviction_log.log_eviction(
                app_id=event.app_id,
                algorithm=event.algorithm,
                reason=event.reason,
                lock_checked=event.lock_checked,
                safe_release=event.safe_release,
                result=event.result
            )

            for record in self.conflict_records:
                self.database_manager.conflict_log.log_conflict(
                    waiting_app_id=record.waiting_app_id,
                    blocking_app_id=record.blocking_app_id,
                    resource_id=record.resource_id,
                    resolution_strategy=record.resolution_strategy,
                    resolved_by=record.resolved_by,
                    details=record.details
                )

            return event_id
