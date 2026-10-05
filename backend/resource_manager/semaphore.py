"""Semaphore-style resource locking implementation."""

from typing import Optional, Dict, Tuple
from backend.models.app import App, AppState
from backend.models.resource import Resource, ResourceStatus


class SemaphoreLock:
    """Implements semaphore-style locking and unlocking for a shared Resource.
    
    Attributes:
        resource (Resource): The target resource to control.
    """

    def __init__(self, resource: Resource) -> None:
        self.resource: Resource = resource

    @property
    def resource_id(self) -> str:
        return self.resource.resource_id

    def acquire(self, app: App, units: int = 1) -> bool:
        """Attempts to acquire resource units for an application.
        
        If available, grants lock and updates app.held_resources.
        If unavailable, puts app into waiting queue and transitions app state to WAITING.
        
        Args:
            app: The requesting App.
            units: Number of semaphore units requested (default 1).
            
        Returns:
            bool: True if lock acquired immediately, False if blocked/waiting.
        """
        units = max(1, int(units))

        if app.is_evicted:
            return False

        # If units available
        if self.resource.available_units >= units:
            self.resource.available_units -= units
            current_held = self.resource.holders.get(app.app_id, 0)
            self.resource.holders[app.app_id] = current_held + units
            app.acquire_resource(self.resource_id)
            app.touch()
            return True

        # Otherwise block app
        if app.app_id not in self.resource.waiting_queue:
            self.resource.waiting_queue.append(app.app_id)
        
        # Transition app state to WAITING
        if app.state != AppState.WAITING:
            app.change_state(AppState.WAITING)

        return False

    def release(self, app: App, units: int = 1, registered_apps: Optional[Dict[str, App]] = None) -> bool:
        """Releases resource units held by an application.
        
        If other apps are waiting in queue, unblocks the next waiting app(s) in FIFO order.
        
        Args:
            app: The App releasing the lock.
            units: Number of units to release (default 1).
            registered_apps: Optional map of app_id -> App objects to process queue wakeups.
            
        Returns:
            bool: True if release was successful, False if app did not hold resource.
        """
        units = max(1, int(units))
        held = self.resource.holders.get(app.app_id, 0)

        if held <= 0:
            return False

        released_count = min(held, units)
        new_held = held - released_count

        if new_held > 0:
            self.resource.holders[app.app_id] = new_held
        else:
            del self.resource.holders[app.app_id]
            app.release_resource(self.resource_id)

        self.resource.available_units += released_count

        # Process waiting queue if apps are waiting and units available
        self._process_waiting_queue(registered_apps)

        return True

    def _process_waiting_queue(self, registered_apps: Optional[Dict[str, App]] = None) -> None:
        """Grants freed resource units to waiting applications in FIFO order."""
        if not registered_apps:
            return

        while self.resource.waiting_queue and self.resource.available_units > 0:
            next_app_id = self.resource.waiting_queue[0]
            next_app = registered_apps.get(next_app_id)

            if not next_app or next_app.is_evicted:
                # Remove stale or evicted app from wait queue
                self.resource.waiting_queue.pop(0)
                continue

            # Grant 1 unit to waiting app
            self.resource.waiting_queue.pop(0)
            self.resource.available_units -= 1
            self.resource.holders[next_app.app_id] = self.resource.holders.get(next_app.app_id, 0) + 1
            next_app.acquire_resource(self.resource_id)

            # Move app state from WAITING back to BACKGROUND or FOREGROUND
            next_app.change_state(AppState.BACKGROUND)
            next_app.touch()
