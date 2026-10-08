"""Resource Manager module for RAAE simulation."""

from typing import Dict, List, Optional, Any, Union, Tuple
from backend.models.app import App
from backend.models.resource import Resource, ResourceStatus
from backend.resource_manager.semaphore import SemaphoreLock


class ResourceManager:
    """Manages system hardware resources, binary semaphore locking, and waiting queues.
    
    Attributes:
        resources (Dict[str, Resource]): Registry of managed resource_id -> Resource.
        locks (Dict[str, SemaphoreLock]): Registry of resource_id -> SemaphoreLock.
        apps (Dict[str, App]): Registry of managed app_id -> App instances.
    """

    def __init__(self, db: Optional[Any] = None) -> None:
        self.resources: Dict[str, Resource] = {}
        self.locks: Dict[str, SemaphoreLock] = {}
        self.apps: Dict[str, App] = {}
        self.db: Optional[Any] = db

    def set_db_manager(self, db: Any) -> None:
        """Sets the DatabaseManager repository instance."""
        self.db = db
        for lock in self.locks.values():
            lock.set_db_manager(db)

    def register_app(self, app: App) -> None:
        """Registers an App object with ResourceManager."""
        self.apps[app.app_id] = app
        if self.db and hasattr(self.db, "save_app"):
            self.db.save_app(app)

    def register_resource(self, resource: Resource) -> None:
        """Registers a new resource with the manager."""
        self.resources[resource.resource_id] = resource
        self.locks[resource.resource_id] = SemaphoreLock(resource, db=self.db)
        if self.db and hasattr(self.db, "save_resource"):
            self.db.save_resource(resource)

    def get_resource(self, res_or_id: Union[Resource, str]) -> Optional[Resource]:
        """Retrieves a resource by ID or alias."""
        if isinstance(res_or_id, Resource):
            return res_or_id
        res_id = str(res_or_id)
        if res_id in self.resources:
            return self.resources[res_id]
        
        # Alias mappings
        alias_map = {
            "MIC": "Microphone",
            "AUDIO": "Audio",
            "CAMERA": "Camera",
            "GPS": "GPS",
            "microphone": "Microphone",
            "camera": "Camera",
            "audio": "Audio",
            "gps": "GPS"
        }
        mapped_id = alias_map.get(res_id, res_id)
        return self.resources.get(mapped_id)

    def create_default_resources(self) -> None:
        """Pre-populates system hardware resources for RAAE (Camera, Microphone, GPS, Audio)."""
        default_list = [
            Resource("Camera", "Camera Hardware Sensor", capacity=1),
            Resource("Microphone", "Microphone Audio Sensor", capacity=1),
            Resource("GPS", "GPS Location Sensor", capacity=1),
            Resource("Audio", "Audio Output Stream", capacity=1),
            # Backwards compatibility aliases/resources
            Resource("MIC", "Microphone Audio Sensor", capacity=1),
            Resource("AUDIO", "Audio Output Stream", capacity=1),
            Resource("DB_LOCK", "Shared Database Lock", capacity=1),
            Resource("FILE_LOCK", "Shared File Handle Lock", capacity=1),
            Resource("BLUETOOTH", "Bluetooth Peripheral", capacity=1),
        ]
        for res in default_list:
            if res.resource_id not in self.resources:
                self.register_resource(res)

    def _resolve_app(self, app_or_id: Union[App, str]) -> Tuple[str, Optional[App]]:
        if isinstance(app_or_id, App):
            self.apps[app_or_id.app_id] = app_or_id
            return app_or_id.app_id, app_or_id
        app_id = str(app_or_id)
        app_obj = self.apps.get(app_id)
        return app_id, app_obj

    def request_resource(
        self,
        app_id: Union[App, str],
        resource: Union[Resource, str]
    ) -> bool:
        """Requests acquisition of a binary semaphore resource."""
        app_str_id, app_obj = self._resolve_app(app_id)
        res_obj = self.get_resource(resource)
        if not res_obj:
            raise KeyError(f"Resource '{resource}' is not registered in ResourceManager.")

        lock = self.locks.get(res_obj.resource_id)
        if not lock:
            lock = SemaphoreLock(res_obj, db=self.db)
            self.locks[res_obj.resource_id] = lock

        target_app = app_obj if app_obj else app_str_id
        return lock.acquire(target_app, apps_map=self.apps)

    def acquire_resource(
        self,
        app: Union[App, str],
        resource_id: Union[Resource, str],
        units: int = 1
    ) -> bool:
        """Alias for request_resource for backward compatibility."""
        return self.request_resource(app, resource_id)

    def release_resource(
        self,
        app_id: Union[App, str],
        resource: Union[Resource, str],
        units: int = 1,
        registered_apps: Optional[Dict[str, App]] = None
    ) -> bool:
        """Releases a resource held by an application.
        
        Args:
            app_id: Application instance or app_id string.
            resource: Resource instance or resource_id string.
            units: Ignored for binary semaphore.
            registered_apps: Optional dictionary of apps to notify/unblock.
            
        Returns:
            bool: True if release succeeded, False if app did not hold resource.
        """
        if registered_apps:
            self.apps.update(registered_apps)

        app_str_id, app_obj = self._resolve_app(app_id)
        res_obj = self.get_resource(resource)
        if not res_obj:
            return False

        lock = self.locks.get(res_obj.resource_id)
        if not lock:
            return False

        target_app = app_obj if app_obj else app_str_id
        return lock.release(target_app, apps_map=self.apps)

    def get_holder(self, resource: Union[Resource, str]) -> Optional[str]:
        """Returns the app_id of the current holder of the resource, or None if free."""
        res_obj = self.get_resource(resource)
        if not res_obj:
            return None
        return res_obj.primary_holder_id

    def get_waiting_queue(self, resource: Union[Resource, str]) -> List[str]:
        """Returns a copy of the FIFO waiting queue for the resource."""
        res_obj = self.get_resource(resource)
        if not res_obj:
            return []
        return list(res_obj.waiting_queue)

    def get_all_active_locks(self) -> Dict[str, str]:
        """Returns a mapping of resource_id -> holder_app_id for all currently locked resources."""
        active_locks = {}
        for res_id, res in self.resources.items():
            holder = res.primary_holder_id
            if holder:
                active_locks[res_id] = holder
        return active_locks

    def release_all_resources_for_app(
        self,
        app: Union[App, str],
        registered_apps: Optional[Dict[str, App]] = None
    ) -> List[str]:
        """Releases all resources currently held by the specified application."""
        if registered_apps:
            self.apps.update(registered_apps)

        app_str_id, app_obj = self._resolve_app(app)
        held_list = list(app_obj.held_resources) if app_obj else [
            r_id for r_id, r in self.resources.items() if app_str_id in r.holders
        ]
        released = []

        for res_id in held_list:
            if self.release_resource(app_str_id, res_id):
                released.append(res_id)

        return released

    def get_all_resources(self) -> List[Resource]:
        """Returns all registered resources."""
        return list(self.resources.values())

    def get_locked_resources(self) -> List[Resource]:
        """Returns all currently locked resources."""
        return [res for res in self.resources.values() if res.is_locked]

    def to_dict(self) -> Dict[str, Any]:
        """Serializes current resource manager state."""
        return {
            "total_resources": len(self.resources),
            "resources": [res.to_dict() for res in self.resources.values()],
            "active_locks": self.get_all_active_locks()
        }

