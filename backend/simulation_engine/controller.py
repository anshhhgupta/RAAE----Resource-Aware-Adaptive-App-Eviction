"""Simulation Controller and unified run_simulation interface.

Provides:
- Workload specification and reproducible factory methods.
- SimulationController integrating MemoryManager, ResourceManager, RAAEEngine, and SQLite.
- run_simulation(policy, workload) returning structured, comparable results.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from backend.models.app import App, AppState
from backend.memory_manager.memory_manager import MemoryManager
from backend.resource_manager.resource_manager import ResourceManager
from backend.simulation_engine.policies import (
    BaselineClockPolicy,
    EvictionPolicy,
    EvictionPolicyType,
    RAAEEvictionPolicy,
)
from backend.simulation_engine.simulation_engine import (
    SimulationConfig,
    SimulationEngine,
    SimulationTickResult,
)
from database.db import DatabaseManager


@dataclass
class AppSpec:
    """Specification for initializing an application in a reproducible workload."""
    app_id: str
    name: str
    priority: int = 1
    memory_footprint: int = 100
    state: str = "BACKGROUND"
    reference_bit: int = 1
    held_resources: List[str] = field(default_factory=list)

    def to_app(self) -> App:
        return App(
            app_id=self.app_id,
            name=self.name,
            priority=self.priority,
            state=AppState(self.state),
            memory_footprint=self.memory_footprint,
            reference_bit=self.reference_bit,
            held_resources=set(self.held_resources)
        )


@dataclass
class Workload:
    """Encapsulates reproducible workload specifications.

    Ensures both BASELINE_CLOCK and RAAE start from the exact same initial state.
    """
    total_memory: int = 1000
    pressure_threshold: float = 75.0
    ticks: int = 1
    db_path: str = ":memory:"
    apps: List[AppSpec] = field(default_factory=list)
    resource_acquisitions: List[Tuple[str, str]] = field(default_factory=list)
    resource_requests_blocked: List[Tuple[str, str]] = field(default_factory=list)
    aging_reference_bits: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def deterministic_5_apps(cls) -> "Workload":
        """Creates the canonical deterministic workload with 5 apps and 2 resource holders.

        - Maps (300 MB): Holds GPS (Contested by Fitness Tracker)
        - Fitness Tracker (150 MB): Blocked in waiting queue for GPS
        - Music Player (300 MB): Holds AUDIO (Uncontested)
        - Chat App (100 MB): Holds no resources
        - Browser (150 MB): Holds no resources
        Total memory: 1000 MB (100% usage -> RED pressure)
        """
        return cls(
            total_memory=1000,
            pressure_threshold=75.0,
            ticks=1,
            db_path=":memory:",
            apps=[
                AppSpec(app_id="app_maps", name="Maps", priority=3, memory_footprint=300, state="BACKGROUND", reference_bit=0),
                AppSpec(app_id="app_fitness", name="Fitness Tracker", priority=2, memory_footprint=150, state="BACKGROUND", reference_bit=1),
                AppSpec(app_id="app_music", name="Music Player", priority=2, memory_footprint=300, state="BACKGROUND", reference_bit=0),
                AppSpec(app_id="app_chat", name="Chat App", priority=1, memory_footprint=100, state="BACKGROUND", reference_bit=0),
                AppSpec(app_id="app_browser", name="Browser", priority=4, memory_footprint=150, state="FOREGROUND", reference_bit=1),
            ],
            resource_acquisitions=[
                ("app_maps", "GPS"),
                ("app_music", "AUDIO"),
            ],
            resource_requests_blocked=[
                ("app_fitness", "GPS"),
            ],
            aging_reference_bits={
                "app_maps": 0,
                "app_fitness": 1,
                "app_music": 0,
                "app_chat": 0,
                "app_browser": 1,
            }
        )


@dataclass
class SimulationResult:
    """Structured telemetry results returned by run_simulation()."""
    policy: str
    eviction_count: int
    blocked_waiting_evictions: int
    resource_conflicts: int
    freeze_incidents: int
    conflict_resolution_time: float  # In deterministic logical time units
    resource_releases: int
    total_ticks: int = 1
    memory_freed: int = 0
    peak_memory_used: int = 1000
    final_memory_used: int = 0
    final_usage_percentage: float = 0.0
    active_apps: List[str] = field(default_factory=list)
    evicted_apps: List[str] = field(default_factory=list)
    ticks_detail: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy": self.policy,
            "eviction_count": self.eviction_count,
            "blocked_waiting_evictions": self.blocked_waiting_evictions,
            "resource_conflicts": self.resource_conflicts,
            "freeze_incidents": self.freeze_incidents,
            "conflict_resolution_time": self.conflict_resolution_time,
            "resource_releases": self.resource_releases,
            "total_ticks": self.total_ticks,
            "memory_freed": self.memory_freed,
            "peak_memory_used": self.peak_memory_used,
            "final_memory_used": self.final_memory_used,
            "final_usage_percentage": self.final_usage_percentage,
            "active_apps": list(self.active_apps),
            "evicted_apps": list(self.evicted_apps),
            "ticks_detail": self.ticks_detail,
        }

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


class SimulationController:
    """Controller integrating MemoryManager, ResourceManager, RAAEEngine, and SQLite.

    Orchestrates workload execution and collects required metrics.
    """

    def __init__(
        self,
        policy: Union[str, EvictionPolicyType, EvictionPolicy],
        workload: Optional[Workload] = None,
        db_path: Optional[str] = None,
    ) -> None:
        self.workload = deepcopy(workload) if workload else Workload.deterministic_5_apps()
        self.policy_instance = self._resolve_policy(policy)
        self.db = DatabaseManager(
            db_path if db_path is not None else self.workload.db_path
        )
        self.mem_mgr = MemoryManager(total_memory=self.workload.total_memory)
        self.res_mgr = ResourceManager()
        self.res_mgr.create_default_resources()

        # Seed resources into DB
        for r in self.res_mgr.get_all_resources():
            self.db.save_resource(r)

        self._initialize_workload()

        self.engine = SimulationEngine(
            memory_manager=self.mem_mgr,
            resource_manager=self.res_mgr,
            db_manager=self.db,
            policy=self.policy_instance,
            config=SimulationConfig(
                memory_pressure_threshold=self.workload.pressure_threshold,
                total_memory=self.workload.total_memory
            )
        )

        self.conflict_resolution_time_total: float = 0.0

    def _resolve_policy(
        self,
        policy: Union[str, EvictionPolicyType, EvictionPolicy]
    ) -> EvictionPolicy:
        if isinstance(policy, EvictionPolicy):
            return policy

        try:
            policy_type = (
                policy
                if isinstance(policy, EvictionPolicyType)
                else EvictionPolicyType(str(policy).upper())
            )
        except ValueError as error:
            raise ValueError(
                f"Unknown policy '{policy}'. Choose BASELINE_CLOCK or RAAE."
            ) from error

        if policy_type is EvictionPolicyType.BASELINE_CLOCK:
            return BaselineClockPolicy()
        if policy_type is EvictionPolicyType.RAAE:
            return RAAEEvictionPolicy()
        raise ValueError(f"Unknown policy '{policy}'. Choose BASELINE_CLOCK or RAAE.")

    def _initialize_workload(self) -> None:
        """Instantiates pristine apps, acquisitions, and waiting queues from workload spec."""
        # 1. Register applications
        for app_spec in self.workload.apps:
            app = app_spec.to_app()
            self.mem_mgr.register_app(app)
            self.db.save_app(app)

        # 2. Acquire resources
        for app_id, resource_id in self.workload.resource_acquisitions:
            app = self.mem_mgr.apps.get(app_id)
            if app:
                self.res_mgr.acquire_resource(app, resource_id)
                self.db.resource_locks.acquire_lock(app.app_id, resource_id)
                self.db.save_app(app)
                res = self.res_mgr.get_resource(resource_id)
                if res:
                    self.db.save_resource(res)

        # 3. Blocked requests (waiting queue)
        for app_id, resource_id in self.workload.resource_requests_blocked:
            app = self.mem_mgr.apps.get(app_id)
            if app:
                self.res_mgr.acquire_resource(app, resource_id)
                self.db.save_app(app)
                res = self.res_mgr.get_resource(resource_id)
                if res:
                    self.db.save_resource(res)

        # 4. Aging reference bits
        for app_id, ref_bit in self.workload.aging_reference_bits.items():
            app = self.mem_mgr.apps.get(app_id)
            if app:
                app.reference_bit = ref_bit
                self.db.save_app(app)

    def run(self, ticks: Optional[int] = None) -> SimulationResult:
        """Runs the simulation for the specified number of ticks and returns structured metrics."""
        ticks_to_run = self.workload.ticks if ticks is None else ticks
        if (
            not isinstance(ticks_to_run, int)
            or isinstance(ticks_to_run, bool)
            or ticks_to_run <= 0
        ):
            raise ValueError("ticks must be a positive integer")
        ticks_detail: List[Dict[str, Any]] = []

        for _ in range(ticks_to_run):
            tick_res: SimulationTickResult = self.engine.tick()

            # Use logical simulation time so identical workloads produce identical metrics.
            if self.policy_instance.policy_type == EvictionPolicyType.RAAE:
                self.conflict_resolution_time_total += sum(
                    1.0 for result in tick_res.eviction_results if result.conflict_detected
                )

            ticks_detail.append({
                "tick": tick_res.tick_number,
                "status": tick_res.status,
                "memory_before": tick_res.memory_before_mb,
                "memory_after": tick_res.memory_after_mb,
                "evictions": [
                    {
                        "app_id": r.candidate.app_id,
                        "name": r.candidate.name,
                        "evicted": r.evicted,
                        "is_unsafe": r.is_unsafe,
                        "safe_release": r.safe_release_performed,
                        "memory_freed": r.memory_freed,
                        "reason": r.reason
                    }
                    for r in tick_res.eviction_results
                ]
            })

        m = self.engine.metrics
        active_apps = [a.name for a in self.mem_mgr.get_active_apps()]
        evicted_apps = [a.name for a in self.mem_mgr.apps.values() if a.is_evicted]

        # In BASELINE_CLOCK, no conflict resolution is performed (time = 0.0)
        resolution_time = round(self.conflict_resolution_time_total, 6) if self.policy_instance.policy_type == EvictionPolicyType.RAAE else 0.0

        return SimulationResult(
            policy=self.policy_instance.policy_type.value,
            eviction_count=m.successful_evictions,
            blocked_waiting_evictions=m.conflicts_prevented,
            resource_conflicts=m.conflicts_detected or (1 if m.freeze_incidents > 0 else 0),
            freeze_incidents=m.freeze_incidents,
            conflict_resolution_time=resolution_time,
            resource_releases=m.safe_releases_performed,
            total_ticks=m.total_ticks,
            memory_freed=m.total_memory_freed,
            peak_memory_used=m.peak_memory_used,
            final_memory_used=self.mem_mgr.get_used_memory(),
            final_usage_percentage=round(self.mem_mgr.get_usage_percentage(), 2),
            active_apps=active_apps,
            evicted_apps=evicted_apps,
            ticks_detail=ticks_detail
        )

    def close(self) -> None:
        """Closes open database resources."""
        self.db.close()


def run_simulation(
    policy: Union[str, EvictionPolicyType, EvictionPolicy],
    workload: Optional[Union[Workload, Mapping[str, Any]]] = None
) -> SimulationResult:
    """Primary simple function executing a reproducible simulation and returning structured results.

    Args:
        policy: Eviction policy ('BASELINE_CLOCK' or 'RAAE' / EvictionPolicyType / EvictionPolicy).
        workload: Workload specification instance or dictionary. If None, uses canonical deterministic workload.

    Returns:
        SimulationResult: Structured results recording:
            - eviction_count
            - blocked_waiting_evictions
            - resource_conflicts
            - freeze_incidents
            - conflict_resolution_time
            - resource_releases
    """
    workload_obj: Workload
    if workload is None:
        workload_obj = Workload.deterministic_5_apps()
    elif isinstance(workload, Workload):
        workload_obj = deepcopy(workload)
    elif isinstance(workload, Mapping):
        workload_values = dict(workload)
        if "apps" in workload_values:
            workload_values["apps"] = [
                AppSpec(**app) if isinstance(app, Mapping) else app
                for app in workload_values["apps"]
            ]
        workload_obj = Workload(**workload_values)
    else:
        raise TypeError("workload must be a Workload, mapping, or None")

    controller = SimulationController(policy=policy, workload=workload_obj)
    try:
        return controller.run()
    finally:
        controller.close()
