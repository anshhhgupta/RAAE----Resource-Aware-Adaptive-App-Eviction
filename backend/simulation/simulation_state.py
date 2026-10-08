"""SimulationState and integrated simulation workflow for RAAE system."""

from typing import Dict, List, Optional, Any, Tuple, Union
from backend.models.app import App, AppState
from backend.models.resource import Resource
from backend.memory_manager.memory_manager import MemoryManager, MemoryPressureLevel
from backend.resource_manager.resource_manager import ResourceManager


class EvictionCandidateInfo:
    """Encapsulates an eviction candidate selected by Memory Manager and its resource locks.
    
    Attributes:
        app (App): The selected candidate App object (not yet evicted).
        held_resources (List[str]): List of resource IDs currently held by this candidate.
    """

    def __init__(self, app: App, held_resources: List[str]) -> None:
        self.app: App = app
        self.held_resources: List[str] = list(held_resources)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app_id": self.app.app_id,
            "app_name": self.app.name,
            "state": self.app.state.value,
            "memory_footprint": self.app.memory_footprint,
            "reference_bit": self.app.reference_bit,
            "held_resources": self.held_resources,
            "is_evicted": self.app.is_evicted
        }

    def __repr__(self) -> str:
        return (
            f"<EvictionCandidateInfo app_id={self.app.app_id!r} name={self.app.name!r} "
            f"held_resources={self.held_resources}>"
        )


class SimulationState:
    """Integrates MemoryManager and ResourceManager into a unified simulation state.
    
    Provides standard simulation workflow methods and clean inspection interfaces for RAAE Engine.
    """

    def __init__(
        self,
        memory_manager: Optional[MemoryManager] = None,
        resource_manager: Optional[ResourceManager] = None,
        db: Optional[Any] = None
    ) -> None:
        self.memory_manager: MemoryManager = memory_manager or MemoryManager(total_memory=1000)
        self.resource_manager: ResourceManager = resource_manager or ResourceManager()
        self.db: Optional[Any] = db

        if self.db:
            self.memory_manager.set_db_manager(self.db)
            self.resource_manager.set_db_manager(self.db)

        # Ensure default hardware resources exist
        if not self.resource_manager.get_all_resources():
            self.resource_manager.create_default_resources()

    def set_db_manager(self, db: Any) -> None:
        """Sets DatabaseManager for both memory and resource managers."""
        self.db = db
        self.memory_manager.set_db_manager(db)
        self.resource_manager.set_db_manager(db)


    def add_app(self, app: App) -> None:
        """Registers an application with both MemoryManager and ResourceManager."""
        self.memory_manager.add_app(app)
        self.resource_manager.register_app(app)

    def allocate_memory(self, app_id: str, amount: int) -> bool:
        """Allocates memory to an app via MemoryManager."""
        return self.memory_manager.allocate_memory(app_id, amount)

    def request_resource(self, app_id: str, resource_id: str) -> bool:
        """Requests a resource lock for an app via ResourceManager."""
        app = self.memory_manager.apps.get(str(app_id))
        target = app if app else app_id
        return self.resource_manager.request_resource(target, resource_id)

    def release_resource(self, app_id: str, resource_id: str) -> bool:
        """Releases a resource lock via ResourceManager."""
        app = self.memory_manager.apps.get(str(app_id))
        target = app if app else app_id
        return self.resource_manager.release_resource(target, resource_id)

    def update_reference_bit(self, app_id: str, bit: int) -> bool:
        """Updates an app's reference bit via MemoryManager."""
        return self.memory_manager.update_reference_bit(app_id, bit)

    def trigger_memory_pressure(self, target_percentage: float = 85.0) -> int:
        """Generates memory pressure to reach target percentage usage."""
        active_apps = self.memory_manager.get_active_apps()
        if not active_apps:
            return 0

        target_bytes = (target_percentage / 100.0) * self.memory_manager.total_memory
        current_used = self.memory_manager.get_used_memory()
        needed = int(target_bytes - current_used)

        if needed <= 0:
            return 0

        allocated_total = 0
        per_app_add = max(10, needed // len(active_apps))

        for app in active_apps:
            if self.memory_manager.allocate_memory(app.app_id, per_app_add):
                allocated_total += per_app_add
                if self.memory_manager.get_usage_percentage() >= target_percentage:
                    break

        return allocated_total

    def select_eviction_candidate(self) -> Optional[App]:
        """Selects a Clock eviction candidate from MemoryManager WITHOUT evicting it directly."""
        return self.memory_manager.select_eviction_candidate()

    def get_candidate_resources(self, app_or_id: Union[App, str]) -> List[str]:
        """Exposes the hardware resources currently held by the candidate application."""
        app_id = app_or_id.app_id if isinstance(app_or_id, App) else str(app_or_id)
        app_obj = self.memory_manager.apps.get(app_id)
        if app_obj:
            return sorted(list(app_obj.held_resources))
        
        active_locks = self.resource_manager.get_all_active_locks()
        return sorted([res_id for res_id, holder in active_locks.items() if holder == app_id])

    def get_candidate_info(self) -> Optional[EvictionCandidateInfo]:
        """Selects an eviction candidate and packages it with its held resources without evicting it."""
        candidate = self.select_eviction_candidate()
        if not candidate:
            return None
        held = self.get_candidate_resources(candidate)
        return EvictionCandidateInfo(candidate, held)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes current unified simulation state."""
        return {
            "memory": self.memory_manager.to_dict(),
            "resources": self.resource_manager.to_dict(),
            "active_locks": self.resource_manager.get_all_active_locks()
        }
