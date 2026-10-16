"""Simulation Engine coordinating memory management, resources, and eviction policies."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from backend.models.app import App
from backend.memory_manager.memory_manager import MemoryManager, MemoryPressureLevel
from backend.resource_manager.resource_manager import ResourceManager
from backend.simulation_engine.policies import (
    BaselineClockPolicy,
    EvictionPolicy,
    EvictionPolicyDecision,
    EvictionPolicyResult,
    EvictionPolicyType,
    RAAEEvictionPolicy,
)
from database.db import DatabaseManager


@dataclass
class SimulationConfig:
    """Configuration settings for simulation engine execution."""
    memory_pressure_threshold: float = 75.0
    max_eviction_attempts_per_tick: int = 10
    total_memory: int = 1000


@dataclass
class SimulationMetrics:
    """Comparable telemetry metrics collected across simulation runs."""
    policy: str
    total_ticks: int = 0
    memory_pressure_ticks: int = 0
    total_eviction_attempts: int = 0
    successful_evictions: int = 0
    safe_evictions: int = 0
    unsafe_evictions: int = 0
    safe_releases_performed: int = 0
    conflicts_detected: int = 0
    conflicts_prevented: int = 0
    freeze_incidents: int = 0
    total_memory_freed: int = 0
    peak_memory_used: int = 0
    final_active_apps_count: int = 0
    final_evicted_apps_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy": self.policy,
            "total_ticks": self.total_ticks,
            "memory_pressure_ticks": self.memory_pressure_ticks,
            "total_eviction_attempts": self.total_eviction_attempts,
            "successful_evictions": self.successful_evictions,
            "safe_evictions": self.safe_evictions,
            "unsafe_evictions": self.unsafe_evictions,
            "safe_releases_performed": self.safe_releases_performed,
            "conflicts_detected": self.conflicts_detected,
            "conflicts_prevented": self.conflicts_prevented,
            "freeze_incidents": self.freeze_incidents,
            "total_memory_freed": self.total_memory_freed,
            "peak_memory_used": self.peak_memory_used,
            "final_active_apps_count": self.final_active_apps_count,
            "final_evicted_apps_count": self.final_evicted_apps_count,
        }


@dataclass
class SimulationTickResult:
    """Detailed result of a single simulation tick execution."""
    tick_number: int
    policy: str
    memory_before_mb: int
    memory_after_mb: int
    usage_percentage_before: float
    usage_percentage_after: float
    pressure_level_before: str
    pressure_level_after: str
    is_under_pressure: bool
    clock_inspections: List[Dict[str, Any]] = field(default_factory=list)
    eviction_results: List[EvictionPolicyResult] = field(default_factory=list)
    status: str = "NORMAL"


class SimulationEngine:
    """Coordinates simulation ticks, memory pressure detection, Clock selection,

    and eviction policy execution (BASELINE_CLOCK or RAAE) with SQLite persistence.
    """

    def __init__(
        self,
        memory_manager: MemoryManager,
        resource_manager: ResourceManager,
        db_manager: Optional[DatabaseManager] = None,
        policy: Optional[EvictionPolicy] = None,
        config: Optional[SimulationConfig] = None,
    ) -> None:
        self.memory_manager: MemoryManager = memory_manager
        self.resource_manager: ResourceManager = resource_manager
        self.db_manager: Optional[DatabaseManager] = db_manager
        self.policy: EvictionPolicy = policy or RAAEEvictionPolicy()
        self.config: SimulationConfig = config or SimulationConfig()
        self.current_tick: int = 0
        self.metrics: SimulationMetrics = SimulationMetrics(policy=self.policy.policy_type.value)

    def set_policy(self, policy: EvictionPolicy) -> None:
        """Dynamically switches eviction policy."""
        self.policy = policy
        self.metrics.policy = policy.policy_type.value

    def tick(self) -> SimulationTickResult:
        """Executes one simulation tick.

        Complete Tick Workflow:
        simulation tick
        → memory pressure check
        → Clock candidate
        → RAAE resource check
        → Conflict Manager
        → safe release if required
        → eviction
        → database logging
        """
        self.current_tick += 1
        self.metrics.total_ticks += 1

        used_before = self.memory_manager.get_used_memory()
        pct_before = self.memory_manager.get_usage_percentage()
        pressure_before = self.memory_manager.get_pressure_level().value
        if used_before > self.metrics.peak_memory_used:
            self.metrics.peak_memory_used = used_before

        is_under_pressure = (
            self.memory_manager.is_under_pressure()
            or pct_before >= self.config.memory_pressure_threshold
        )

        if not is_under_pressure:
            self.metrics.final_active_apps_count = len(self.memory_manager.get_active_apps())
            self.metrics.final_evicted_apps_count = len([a for a in self.memory_manager.apps.values() if a.is_evicted])
            return SimulationTickResult(
                tick_number=self.current_tick,
                policy=self.policy.policy_type.value,
                memory_before_mb=used_before,
                memory_after_mb=used_before,
                usage_percentage_before=pct_before,
                usage_percentage_after=pct_before,
                pressure_level_before=pressure_before,
                pressure_level_after=pressure_before,
                is_under_pressure=False,
                status="NO_PRESSURE"
            )

        self.metrics.memory_pressure_ticks += 1

        eviction_results: List[EvictionPolicyResult] = []
        all_clock_inspections: List[Dict[str, Any]] = []
        visited_candidates: Set[str] = set()

        attempts = 0
        max_attempts = min(
            self.config.max_eviction_attempts_per_tick,
            max(1, len(self.memory_manager.get_active_apps()) * 2)
        )

        while (
            (self.memory_manager.is_under_pressure() or self.memory_manager.get_usage_percentage() >= self.config.memory_pressure_threshold)
            and attempts < max_attempts
        ):
            active_apps = self.memory_manager.get_active_apps()
            if not active_apps:
                break

            attempts += 1
            self.metrics.total_eviction_attempts += 1

            # Clock candidate selection
            candidate, clock_logs = self.memory_manager.clock_step()
            all_clock_inspections.extend(clock_logs)

            if candidate is None:
                break

            if candidate.app_id in visited_candidates:
                # Avoid infinite loops if all candidates were visited in this tick
                break

            # Evaluate candidate under the active policy
            # (BASELINE_CLOCK skips resource check; RAAE performs resource check + conflict evaluation)
            decision = self.policy.evaluate_candidate(
                candidate=candidate,
                memory_manager=self.memory_manager,
                resource_manager=self.resource_manager
            )

            if decision.conflict_detected:
                self.metrics.conflicts_detected += 1

            if not decision.allow_eviction:
                # Policy prevents eviction (RAAE protected candidate holding contested resource!)
                visited_candidates.add(candidate.app_id)
                self.metrics.conflicts_prevented += 1

                # Execute policy workflow (which logs protection to database)
                result = self.policy.execute_eviction(
                    candidate=candidate,
                    decision=decision,
                    memory_manager=self.memory_manager,
                    resource_manager=self.resource_manager,
                    db_manager=self.db_manager,
                    pressure_level=pressure_before
                )
                eviction_results.append(result)
                # Clock pointer continues to inspect the next candidate in the ring!
                continue

            # Policy allows eviction!
            # Under BASELINE_CLOCK: candidate is evicted blindly without releasing held resources.
            # Under RAAE: held resources are safely released first, then candidate is evicted.
            result = self.policy.execute_eviction(
                candidate=candidate,
                decision=decision,
                memory_manager=self.memory_manager,
                resource_manager=self.resource_manager,
                db_manager=self.db_manager,
                pressure_level=pressure_before
            )
            eviction_results.append(result)

            if result.evicted:
                self.metrics.successful_evictions += 1
                self.metrics.total_memory_freed += result.memory_freed

                if result.is_unsafe:
                    self.metrics.unsafe_evictions += 1
                    if result.conflict_detected:
                        self.metrics.freeze_incidents += len(result.waiting_app_ids)
                else:
                    self.metrics.safe_evictions += 1

                if result.safe_release_performed:
                    self.metrics.safe_releases_performed += len(result.released_resource_ids)

            # Check if pressure is now resolved
            if not self.memory_manager.is_under_pressure() and self.memory_manager.get_usage_percentage() < self.config.memory_pressure_threshold:
                break

        used_after = self.memory_manager.get_used_memory()
        pct_after = self.memory_manager.get_usage_percentage()
        pressure_after = self.memory_manager.get_pressure_level().value
        self.metrics.final_active_apps_count = len(self.memory_manager.get_active_apps())
        self.metrics.final_evicted_apps_count = len([a for a in self.memory_manager.apps.values() if a.is_evicted])

        status = "RESOLVED" if pct_after < self.config.memory_pressure_threshold else "PRESSURE_REMAINS"
        if any(r.is_unsafe for r in eviction_results):
            status = "UNSAFE_EVICTION_OCCURRED"

        return SimulationTickResult(
            tick_number=self.current_tick,
            policy=self.policy.policy_type.value,
            memory_before_mb=used_before,
            memory_after_mb=used_after,
            usage_percentage_before=pct_before,
            usage_percentage_after=pct_after,
            pressure_level_before=pressure_before,
            pressure_level_after=pressure_after,
            is_under_pressure=is_under_pressure,
            clock_inspections=all_clock_inspections,
            eviction_results=eviction_results,
            status=status
        )
