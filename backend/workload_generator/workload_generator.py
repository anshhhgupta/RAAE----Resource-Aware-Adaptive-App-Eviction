"""Workload Generator for RAAE system simulation."""

import random
import time
from typing import List, Dict, Any, Optional
from backend.models.app import App, AppState
from backend.memory_manager.memory_manager import MemoryManager
from backend.resource_manager.resource_manager import ResourceManager


class WorkloadEvent:
    """Represents a single simulated workload step or operation."""

    def __init__(self, event_type: str, app_id: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.event_type: str = event_type
        self.app_id: str = app_id
        self.payload: Dict[str, Any] = payload or {}
        self.timestamp: float = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "app_id": self.app_id,
            "payload": self.payload,
            "timestamp": self.timestamp
        }

    def __repr__(self) -> str:
        return f"<WorkloadEvent {self.event_type} app={self.app_id} payload={self.payload}>"


class WorkloadGenerator:
    """Generates synthetic application workloads, resource lock requests, and memory pressure.
    
    Attributes:
        seed (Optional[int]): Random seed for reproducible workload generation.
    """

    DEFAULT_APP_TEMPLATES = [
        {"name": "Maps", "priority": 3, "initial_mem": 250, "preferred_resource": "GPS"},
        {"name": "Music Player", "priority": 2, "initial_mem": 120, "preferred_resource": "AUDIO"},
        {"name": "Camera", "priority": 3, "initial_mem": 300, "preferred_resource": "MIC"},
        {"name": "Database Sync", "priority": 1, "initial_mem": 180, "preferred_resource": "DB_LOCK"},
        {"name": "File Manager", "priority": 1, "initial_mem": 90, "preferred_resource": "FILE_LOCK"},
        {"name": "Fitness Tracker", "priority": 2, "initial_mem": 150, "preferred_resource": "GPS"},
        {"name": "Browser", "priority": 3, "initial_mem": 400, "preferred_resource": None},
        {"name": "Chat App", "priority": 2, "initial_mem": 110, "preferred_resource": None},
        {"name": "Gaming App", "priority": 4, "initial_mem": 500, "preferred_resource": "AUDIO"},
        {"name": "Bluetooth Sync", "priority": 1, "initial_mem": 80, "preferred_resource": "BLUETOOTH"},
    ]

    def __init__(self, seed: Optional[int] = None) -> None:
        self.seed: Optional[int] = seed
        self.rng: random.Random = random.Random(seed)
        self._app_counter: int = 1

    def create_sample_apps(self, count: int = 5) -> List[App]:
        """Creates a batch of sample apps populated from realistic templates.
        
        Args:
            count: Number of sample apps to generate.
            
        Returns:
            List[App]: List of instantiated App objects.
        """
        apps = []
        templates = self.DEFAULT_APP_TEMPLATES.copy()
        self.rng.shuffle(templates)

        for i in range(min(count, len(templates))):
            tmpl = templates[i]
            app_id = f"app_{self._app_counter}"
            self._app_counter += 1

            # Alternate foreground and background states
            initial_state = AppState.FOREGROUND if i == 0 else AppState.BACKGROUND

            app = App(
                app_id=app_id,
                name=tmpl["name"],
                priority=tmpl["priority"],
                state=initial_state,
                memory_footprint=tmpl["initial_mem"],
                reference_bit=1
            )
            apps.append(app)

        return apps

    def generate_random_event(
        self,
        memory_manager: MemoryManager,
        resource_manager: ResourceManager
    ) -> Optional[WorkloadEvent]:
        """Generates a single random action event on current active apps.
        
        Possible actions:
        - state_change: Move foreground <-> background
        - allocate_memory: Expand memory footprint
        - deallocate_memory: Shrink memory footprint
        - acquire_resource: Request a lock on a shared resource
        - release_resource: Release an acquired lock
        """
        active_apps = memory_manager.get_active_apps()
        if not active_apps:
            return None

        app = self.rng.choice(active_apps)
        event_types = ["state_change", "allocate_memory", "deallocate_memory", "acquire_resource", "release_resource"]
        chosen_type = self.rng.choice(event_types)

        if chosen_type == "state_change":
            new_state = AppState.BACKGROUND if app.state == AppState.FOREGROUND else AppState.FOREGROUND
            app.change_state(new_state)
            return WorkloadEvent("STATE_CHANGE", app.app_id, {"new_state": new_state.value})

        elif chosen_type == "allocate_memory":
            amount = self.rng.randint(20, 100)
            success = memory_manager.allocate_memory(app.app_id, amount)
            return WorkloadEvent("ALLOCATE_MEMORY", app.app_id, {"amount": amount, "success": success})

        elif chosen_type == "deallocate_memory":
            amount = self.rng.randint(10, 50)
            freed = memory_manager.deallocate_memory(app.app_id, amount)
            return WorkloadEvent("DEALLOCATE_MEMORY", app.app_id, {"freed": freed})

        elif chosen_type == "acquire_resource":
            resources = resource_manager.get_all_resources()
            if not resources:
                return None
            res = self.rng.choice(resources)
            success = resource_manager.acquire_resource(app, res.resource_id)
            return WorkloadEvent("ACQUIRE_RESOURCE", app.app_id, {"resource_id": res.resource_id, "success": success})

        elif chosen_type == "release_resource":
            if not app.held_resources:
                return None
            res_id = self.rng.choice(list(app.held_resources))
            success = resource_manager.release_resource(app, res_id, registered_apps=memory_manager.apps)
            return WorkloadEvent("RELEASE_RESOURCE", app.app_id, {"resource_id": res_id, "success": success})

        return None

    def trigger_memory_pressure(self, memory_manager: MemoryManager, target_percentage: float = 85.0) -> int:
        """Allocates memory to active apps to drive total system memory usage up to target percentage.
        
        Returns:
            int: Total extra MB allocated to achieve pressure.
        """
        active_apps = memory_manager.get_active_apps()
        if not active_apps:
            return 0

        target_bytes = (target_percentage / 100.0) * memory_manager.total_memory
        current_used = memory_manager.get_used_memory()
        needed = int(target_bytes - current_used)

        if needed <= 0:
            return 0

        allocated_total = 0
        per_app_add = needed // len(active_apps)

        for app in active_apps:
            amount = per_app_add if per_app_add > 0 else 10
            if memory_manager.allocate_memory(app.app_id, amount):
                allocated_total += amount
                if memory_manager.get_usage_percentage() >= target_percentage:
                    break

        return allocated_total
