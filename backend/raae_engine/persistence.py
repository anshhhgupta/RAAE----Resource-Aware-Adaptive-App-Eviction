"""Persistence adapters for RAAE eviction workflow results."""

from typing import Dict, Optional, Protocol, Sequence

from backend.models.app import App
from backend.models.resource import Resource
from .models import EvictionEvent


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
    """Repository-backed persistence adapter using DatabaseManager abstractions."""

    def __init__(self, database_manager) -> None:
        self.database_manager = database_manager

    def persist_eviction_event(
        self,
        event: EvictionEvent,
        candidate: App,
        resources: Sequence[Resource],
        registered_apps: Optional[Dict[str, App]] = None
    ) -> int:
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

            return self.database_manager.eviction_log.log_eviction(
                app_id=event.app_id,
                algorithm=event.algorithm,
                reason=event.reason,
                lock_checked=event.lock_checked,
                safe_release=event.safe_release,
                result=event.result
            )
