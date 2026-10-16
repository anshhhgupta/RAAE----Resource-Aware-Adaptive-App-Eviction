"""Semaphore-style resource locking implementation."""

from typing import Any, Optional, Dict, Union
from backend.models.app import App, AppState
from backend.models.resource import Resource, ResourceStatus


class SemaphoreLock:
    """Implements binary semaphore locking and unlocking for a shared Resource.

    Every granted acquisition and every release performed here is reported to the
    database through the ``log_lock_request`` hook, which records both the
    request event and the resulting ResourceLocks ledger row. The decision to
    grant, queue or reject a request is made above this class; persistence only
    records the outcome.

    Attributes:
        resource (Resource): The target resource to control.
        db (Optional[Any]): Optional DatabaseManager repository instance for clean persistence hooks.
    """

    def __init__(self, resource: Resource, db: Optional[Any] = None) -> None:
        self.resource: Resource = resource
        self.db: Optional[Any] = db

    @property
    def resource_id(self) -> str:
        return self.resource.resource_id

    def set_db_manager(self, db: Any) -> None:
        """Sets the DatabaseManager repository instance."""
        self.db = db

    def acquire(
        self,
        app_or_id: Union[App, str],
        units: int = 1,
        apps_map: Optional[Dict[str, App]] = None,
        registered_apps: Optional[Dict[str, App]] = None
    ) -> bool:
        """Attempts to acquire the resource for an application."""
        apps_map = registered_apps if registered_apps is not None else apps_map
        app_id = app_or_id.app_id if isinstance(app_or_id, App) else str(app_or_id)
        app_obj = app_or_id if isinstance(app_or_id, App) else (apps_map.get(app_id) if apps_map else None)

        if app_obj and app_obj.is_evicted:
            return False

        # Prevent acquiring the same resource twice
        if app_id in self.resource.holders or (app_obj and self.resource_id in app_obj.held_resources):
            return False

        # If resource is free (available units >= 1)
        if self.resource.available_units >= 1:
            self.resource.available_units -= 1
            self.resource.holders[app_id] = 1
            if app_obj:
                app_obj.acquire_resource(self.resource_id)
                app_obj.touch()

            if self.db:
                if hasattr(self.db, "save_resource"):
                    self.db.save_resource(self.resource)
                if app_obj and hasattr(self.db, "save_app"):
                    self.db.save_app(app_obj)
                if hasattr(self.db, "log_lock_request"):
                    self.db.log_lock_request(app_id, self.resource_id, "GRANTED")

            return True

        # Resource occupied -> add requester to FIFO waiting queue
        if app_id not in self.resource.waiting_queue:
            self.resource.waiting_queue.append(app_id)

        if app_obj and app_obj.state != AppState.WAITING:
            app_obj.change_state(AppState.WAITING)

        if self.db:
            if hasattr(self.db, "save_resource"):
                self.db.save_resource(self.resource)
            if app_obj and hasattr(self.db, "save_app"):
                self.db.save_app(app_obj)
            if hasattr(self.db, "log_lock_request"):
                self.db.log_lock_request(app_id, self.resource_id, "QUEUED")

        return False

    def release(
        self,
        app_or_id: Union[App, str],
        units: int = 1,
        apps_map: Optional[Dict[str, App]] = None,
        registered_apps: Optional[Dict[str, App]] = None
    ) -> bool:
        """Releases the resource held by an application."""
        apps_map = registered_apps if registered_apps is not None else apps_map
        app_id = app_or_id.app_id if isinstance(app_or_id, App) else str(app_or_id)
        app_obj = app_or_id if isinstance(app_or_id, App) else (apps_map.get(app_id) if apps_map else None)

        if app_id not in self.resource.holders:
            return False

        # Release resource lock
        del self.resource.holders[app_id]
        self.resource.available_units += 1

        if app_obj:
            app_obj.release_resource(self.resource_id)

        if self.db:
            if hasattr(self.db, "save_resource"):
                self.db.save_resource(self.resource)
            if app_obj and hasattr(self.db, "save_app"):
                self.db.save_app(app_obj)
            if hasattr(self.db, "log_lock_request"):
                self.db.log_lock_request(app_id, self.resource_id, "RELEASED")

        # Process waiting queue in FIFO order
        self._process_waiting_queue(apps_map)

        return True

    def _process_waiting_queue(self, apps_map: Optional[Dict[str, App]] = None) -> None:
        """Grants freed resource to the next waiting requester in FIFO order."""
        while self.resource.waiting_queue and self.resource.available_units > 0:
            next_app_id = self.resource.waiting_queue.pop(0)
            next_app_obj = apps_map.get(next_app_id) if apps_map else None

            if next_app_obj and next_app_obj.is_evicted:
                # Skip stale/evicted app from queue
                continue

            self.resource.available_units -= 1
            self.resource.holders[next_app_id] = 1

            if next_app_obj:
                next_app_obj.acquire_resource(self.resource_id)
                next_app_obj.change_state(AppState.BACKGROUND)
                next_app_obj.touch()

            if self.db:
                if hasattr(self.db, "save_resource"):
                    self.db.save_resource(self.resource)
                if next_app_obj and hasattr(self.db, "save_app"):
                    self.db.save_app(next_app_obj)
                if hasattr(self.db, "log_lock_request"):
                    self.db.log_lock_request(next_app_id, self.resource_id, "GRANTED")

            break



