"""Resource Manager module for RAAE simulation."""

from typing import Dict, List, Optional, Any
from backend.models.app import App
from backend.models.resource import Resource, ResourceStatus
from backend.resource_manager.semaphore import SemaphoreLock


class ResourceManager:
    """Manages shared system resources, semaphore locking, and waiting queues.
    
    Attributes:
        resources (Dict[str, Resource]): Registry of managed resource_id -> Resource.
        locks (Dict[str, SemaphoreLock]): Registry of resource_id -> SemaphoreLock.
    """

    def __init__(self) -> None:
        self.resources: Dict[str, Resource] = {}
        self.locks: Dict[str, SemaphoreLock] = {}

    def register_resource(self, resource: Resource) -> None:
        """Registers a new resource with the manager."""
        self.resources[resource.resource_id] = resource
        self.locks[resource.resource_id] = SemaphoreLock(resource)

    def get_resource(self, resource_id: str) -> Optional[Resource]:
        """Retrieves a resource by ID."""
        return self.resources.get(str(resource_id))

    def create_default_resources(self) -> None:
        """Helper to pre-populate common system resources defined in RAAE Readme."""
        default_list = [
            Resource("GPS", "GPS Location Sensor", capacity=1),
            Resource("MIC", "Microphone Audio Capture", capacity=1),
            Resource("AUDIO", "Audio Output Stream", capacity=1),
            Resource("DB_LOCK", "Shared Database Lock", capacity=1),
            Resource("FILE_LOCK", "Shared File Handle Lock", capacity=2),
            Resource("BLUETOOTH", "Bluetooth Peripheral", capacity=1)
        ]
        for res in default_list:
            if res.resource_id not in self.resources:
                self.register_resource(res)

    def acquire_resource(
        self,
        app: App,
        resource_id: str,
        units: int = 1
    ) -> bool:
        """Attempts to acquire a lock on a specified resource for an app.
        
        Args:
            app: The requesting App instance.
            resource_id: Resource identifier.
            units: Number of semaphore units requested.
            
        Returns:
            bool: True if lock was acquired, False if blocked/waiting or error.
        """
        lock = self.locks.get(str(resource_id))
        if not lock:
            raise KeyError(f"Resource '{resource_id}' is not registered in ResourceManager.")

        return lock.acquire(app, units)

    def release_resource(
        self,
        app: App,
        resource_id: str,
        units: int = 1,
        registered_apps: Optional[Dict[str, App]] = None
    ) -> bool:
        """Releases a lock on a specified resource held by an app.
        
        Args:
            app: The releasing App instance.
            resource_id: Resource identifier.
            units: Number of semaphore units to release.
            registered_apps: Map of registered apps to notify/wake up waiting processes.
            
        Returns:
            bool: True if release succeeded, False otherwise.
        """
        lock = self.locks.get(str(resource_id))
        if not lock:
            return False

        return lock.release(app, units, registered_apps)

    def release_all_resources_for_app(
        self,
        app: App,
        registered_apps: Optional[Dict[str, App]] = None
    ) -> List[str]:
        """Releases all resources currently held by the specified application.
        
        Useful during eviction or app shutdown.
        
        Returns:
            List[str]: List of resource_ids that were released.
        """
        held_list = list(app.held_resources)
        released = []

        for res_id in held_list:
            lock = self.locks.get(res_id)
            if lock:
                lock.release(app, units=lock.resource.holders.get(app.app_id, 1), registered_apps=registered_apps)
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
            "resources": [res.to_dict() for res in self.resources.values()]
        }
