"""Workload Generator for RAAE system simulation."""

import random
import time
from typing import List, Dict, Any, Optional, Union
from backend.models.app import App, AppState
from backend.memory_manager.memory_manager import MemoryManager
from backend.resource_manager.resource_manager import ResourceManager


class WorkloadEvent:
    """Represents a single simulated workload step or operation.
    
    Attributes:
        event_type (str): Type of event (e.g. 'ALLOCATE_MEMORY', 'ACQUIRE_RESOURCE', 'ACCESS_APP', 'MEMORY_PRESSURE').
        app_id (Optional[str]): Target app_id, or None for system-wide events.
        tick (int): Simulation tick index.
        payload (Dict[str, Any]): Parameters associated with the event.
        timestamp (float): Event creation timestamp.
    """

    def __init__(
        self,
        event_type: str,
        app_id: Optional[str] = None,
        tick: int = 0,
        payload: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None
    ) -> None:
        self.event_type: str = event_type
        self.app_id: Optional[str] = app_id
        self.tick: int = tick
        self.payload: Dict[str, Any] = payload or {}
        self.timestamp: float = float(timestamp) if timestamp is not None else time.time()


    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "app_id": self.app_id,
            "tick": self.tick,
            "payload": self.payload,
            "timestamp": self.timestamp
        }

    def __repr__(self) -> str:
        return f"<WorkloadEvent tick={self.tick} type={self.event_type} app={self.app_id} payload={self.payload}>"


class WorkloadTrace:
    """Container for generated deterministic workload traces."""

    def __init__(
        self,
        seed: int,
        scenario: str,
        total_memory: int,
        initial_apps: List[App],
        events: List[WorkloadEvent]
    ) -> None:
        self.seed: int = seed
        self.scenario: str = scenario
        self.total_memory: int = total_memory
        self.initial_apps: List[App] = initial_apps
        self.events: List[WorkloadEvent] = events

    def __iter__(self):
        return iter(self.events)

    def __len__(self):
        return len(self.events)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seed": self.seed,
            "scenario": self.scenario,
            "total_memory": self.total_memory,
            "initial_apps": [app.to_dict() for app in self.initial_apps],
            "events": [event.to_dict() for event in self.events]
        }


class WorkloadGenerator:
    """Generates synthetic deterministic application workloads and events for RAAE simulation.
    
    Attributes:
        seed (Optional[int]): Random seed for reproducible workload generation.
    """

    HARDWARE_RESOURCES = ["Camera", "Microphone", "GPS", "Audio"]

    DEFAULT_APP_NAMES = [
        "Navigation", "Camera App", "Voice Recorder", "Music Player", "Fitness Tracker",
        "Social Media", "Web Browser", "Email Client", "Gallery", "Game App",
        "FileManager", "Calendar", "Notes", "Weather", "Bluetooth Sync"
    ]

    def __init__(self, seed: Optional[int] = None) -> None:
        self.seed: Optional[int] = seed
        self.rng: random.Random = random.Random(seed)
        self._app_counter: int = 1

    def set_seed(self, seed: int) -> None:
        """Sets the random seed for deterministic generation."""
        self.seed = seed
        self.rng = random.Random(seed)

    def create_deterministic_apps(
        self,
        count: int = 5,
        min_mem: int = 50,
        max_mem: int = 300
    ) -> List[App]:
        """Generates random simulated apps with deterministic parameters based on current rng seed."""
        apps = []
        names = self.DEFAULT_APP_NAMES.copy()
        self.rng.shuffle(names)

        for i in range(count):
            app_id = f"app_{i + 1}"
            name = names[i % len(names)]
            priority = self.rng.randint(1, 5)
            state = AppState.FOREGROUND if i == 0 else AppState.BACKGROUND
            mem_footprint = self.rng.randint(min_mem, max_mem)
            ref_bit = self.rng.choice([0, 1])

            app = App(
                app_id=app_id,
                name=name,
                priority=priority,
                state=state,
                memory_footprint=mem_footprint,
                reference_bit=ref_bit
            )
            apps.append(app)

        return apps

    def generate_workload(
        self,
        seed: int = 42,
        ticks: int = 20,
        scenario: str = "normal",
        num_apps: int = 5,
        ram_mb: int = 1000
    ) -> WorkloadTrace:
        """Generates a reproducible workload scenario over specified ticks.
        
        Args:
            seed: Random seed for 100% reproducible event generation.
            ticks: Number of simulation ticks.
            scenario: Workload scenario name ('normal', 'high_memory_pressure', 'resource_contention').
            num_apps: Number of simulated applications to generate.
            ram_mb: Total system memory capacity in MB.
            
        Returns:
            WorkloadTrace: Trace object containing setup apps and tick events.
        """
        rng = random.Random(seed)
        scenario_lower = scenario.lower()

        # Initial app footprints based on scenario
        if scenario_lower == "high_memory_pressure":
            min_mem, max_mem = 150, 350
        elif scenario_lower == "resource_contention":
            min_mem, max_mem = 80, 200
        else:  # normal
            min_mem, max_mem = 50, 250

        # Generate initial apps deterministically
        names = self.DEFAULT_APP_NAMES.copy()
        rng.shuffle(names)
        initial_apps = []

        for i in range(num_apps):
            app_id = f"app_{i + 1}"
            name = names[i % len(names)]
            priority = rng.randint(1, 5)
            state = AppState.FOREGROUND if i == 0 else AppState.BACKGROUND
            mem_footprint = rng.randint(min_mem, max_mem)
            ref_bit = rng.choice([0, 1])

            app = App(
                app_id=app_id,
                name=name,
                priority=priority,
                state=state,
                memory_footprint=mem_footprint,
                reference_bit=ref_bit
            )
            initial_apps.append(app)

        events: List[WorkloadEvent] = []
        app_ids = [a.app_id for a in initial_apps]
        active_held_resources: Dict[str, List[str]] = {a_id: [] for a_id in app_ids}

        for tick in range(ticks):
            target_app_id = rng.choice(app_ids)

            if scenario_lower == "high_memory_pressure":
                event_choice = rng.choices(
                    ["ALLOCATE_MEMORY", "MEMORY_PRESSURE", "ACCESS_APP", "ACQUIRE_RESOURCE", "RELEASE_RESOURCE"],
                    weights=[45, 25, 15, 10, 5]
                )[0]
            elif scenario_lower == "resource_contention":
                event_choice = rng.choices(
                    ["ACQUIRE_RESOURCE", "ACCESS_APP", "RELEASE_RESOURCE", "STATE_CHANGE", "ALLOCATE_MEMORY"],
                    weights=[50, 20, 15, 10, 5]
                )[0]
            else:  # normal
                event_choice = rng.choices(
                    ["ACCESS_APP", "ALLOCATE_MEMORY", "DEALLOCATE_MEMORY", "ACQUIRE_RESOURCE", "RELEASE_RESOURCE", "STATE_CHANGE"],
                    weights=[25, 20, 15, 15, 15, 10]
                )[0]

            if event_choice == "ALLOCATE_MEMORY":
                amount = rng.randint(20, 100) if scenario_lower != "high_memory_pressure" else rng.randint(80, 180)
                events.append(WorkloadEvent(
                    event_type="ALLOCATE_MEMORY",
                    app_id=target_app_id,
                    tick=tick,
                    payload={"amount": amount}
                ))

            elif event_choice == "DEALLOCATE_MEMORY":
                amount = rng.randint(10, 50)
                events.append(WorkloadEvent(
                    event_type="DEALLOCATE_MEMORY",
                    app_id=target_app_id,
                    tick=tick,
                    payload={"amount": amount}
                ))

            elif event_choice == "ACCESS_APP":
                ref_bit = rng.choice([1, 1, 1, 0])
                events.append(WorkloadEvent(
                    event_type="ACCESS_APP",
                    app_id=target_app_id,
                    tick=tick,
                    payload={"reference_bit": ref_bit}
                ))

            elif event_choice == "STATE_CHANGE":
                new_state = rng.choice([AppState.FOREGROUND.value, AppState.BACKGROUND.value])
                events.append(WorkloadEvent(
                    event_type="STATE_CHANGE",
                    app_id=target_app_id,
                    tick=tick,
                    payload={"new_state": new_state}
                ))

            elif event_choice == "ACQUIRE_RESOURCE":
                resource_id = rng.choice(self.HARDWARE_RESOURCES)
                events.append(WorkloadEvent(
                    event_type="ACQUIRE_RESOURCE",
                    app_id=target_app_id,
                    tick=tick,
                    payload={"resource_id": resource_id}
                ))
                active_held_resources[target_app_id].append(resource_id)

            elif event_choice == "RELEASE_RESOURCE":
                held = active_held_resources[target_app_id]
                res_id = rng.choice(held) if held else rng.choice(self.HARDWARE_RESOURCES)
                events.append(WorkloadEvent(
                    event_type="RELEASE_RESOURCE",
                    app_id=target_app_id,
                    tick=tick,
                    payload={"resource_id": res_id}
                ))
                if held and res_id in held:
                    held.remove(res_id)

            elif event_choice == "MEMORY_PRESSURE":
                target_pct = rng.uniform(80.0, 95.0)
                events.append(WorkloadEvent(
                    event_type="MEMORY_PRESSURE",
                    app_id=None,
                    tick=tick,
                    payload={"target_percentage": round(target_pct, 1)}
                ))

        return WorkloadTrace(
            seed=seed,
            scenario=scenario,
            total_memory=ram_mb,
            initial_apps=initial_apps,
            events=events
        )

    # Legacy helper methods for backwards compatibility
    def create_sample_apps(self, count: int = 5) -> List[App]:
        return self.create_deterministic_apps(count=count)

    def generate_random_event(
        self,
        memory_manager: MemoryManager,
        resource_manager: ResourceManager
    ) -> Optional[WorkloadEvent]:
        active_apps = memory_manager.get_active_apps()
        if not active_apps:
            return None

        app = self.rng.choice(active_apps)
        event_types = ["state_change", "allocate_memory", "deallocate_memory", "acquire_resource", "release_resource"]
        chosen_type = self.rng.choice(event_types)

        if chosen_type == "state_change":
            new_state = AppState.BACKGROUND if app.state == AppState.FOREGROUND else AppState.FOREGROUND
            app.change_state(new_state)
            return WorkloadEvent("STATE_CHANGE", app.app_id, 0, {"new_state": new_state.value})

        elif chosen_type == "allocate_memory":
            amount = self.rng.randint(20, 100)
            success = memory_manager.allocate_memory(app.app_id, amount)
            return WorkloadEvent("ALLOCATE_MEMORY", app.app_id, 0, {"amount": amount, "success": success})

        elif chosen_type == "deallocate_memory":
            amount = self.rng.randint(10, 50)
            freed = memory_manager.deallocate_memory(app.app_id, amount)
            return WorkloadEvent("DEALLOCATE_MEMORY", app.app_id, 0, {"freed": freed})

        elif chosen_type == "acquire_resource":
            resources = resource_manager.get_all_resources()
            if not resources:
                return None
            res = self.rng.choice(resources)
            success = resource_manager.acquire_resource(app, res.resource_id)
            return WorkloadEvent("ACQUIRE_RESOURCE", app.app_id, 0, {"resource_id": res.resource_id, "success": success})

        elif chosen_type == "release_resource":
            if not app.held_resources:
                return None
            res_id = self.rng.choice(list(app.held_resources))
            success = resource_manager.release_resource(app, res_id, registered_apps=memory_manager.apps)
            return WorkloadEvent("RELEASE_RESOURCE", app.app_id, 0, {"resource_id": res_id, "success": success})

        return None

    def trigger_memory_pressure(self, memory_manager: MemoryManager, target_percentage: float = 85.0) -> int:
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

