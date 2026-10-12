"""Isolated, repeatable comparison of Clock and RAAE eviction policies."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Optional, Sequence

from backend.memory_manager.memory_manager import MemoryManager
from backend.models.app import App
from backend.models.resource import Resource
from backend.raae_engine.conflict_manager import ResourceConflictManager
from backend.raae_engine.models import ConflictStatus, EvictionDecisionType
from backend.raae_engine.raae_engine import RAAEEngine
from backend.resource_manager.resource_manager import ResourceManager
from database.db import DatabaseManager


class UnsupportedWorkloadError(ValueError):
    """Raised when the supplied snapshot cannot be compared reliably."""


@dataclass(frozen=True)
class PolicyMetrics:
    policy: str
    eviction_count: int
    simulated_wait_freeze_proxy_count: int
    logged_conflict_outcomes: int
    passive_contention_observations: int
    conflict_case_orchestration_latency_ms: Optional[float]
    resource_blocked_evictions: int
    attempts: int


@dataclass(frozen=True)
class PolicyComparison:
    plain_clock: PolicyMetrics
    raae: PolicyMetrics
    workload_fingerprint: str
    initial_app_count: int
    initial_active_app_count: int
    initial_used_memory_mb: int
    max_attempts: int


def compare_eviction_policies(
    apps: Sequence[App],
    resources: Sequence[Resource],
    total_memory: int,
    clock_hand: int,
    max_attempts: int = 10,
) -> PolicyComparison:
    """Run both policies from independent clones of the same state snapshot.

    The workload consists of up to ``max_attempts`` consecutive Clock victim
    selections. No random or external workload events are injected.
    """
    app_ids = [app.app_id for app in apps]
    resource_ids = [resource.resource_id for resource in resources]
    if len(app_ids) != len(set(app_ids)):
        raise UnsupportedWorkloadError("The snapshot contains duplicate app IDs.")
    if len(resource_ids) != len(set(resource_ids)):
        raise UnsupportedWorkloadError("The snapshot contains duplicate resource IDs.")
    if total_memory <= 0:
        raise UnsupportedWorkloadError("Total memory must be greater than zero.")
    if max_attempts < 1:
        raise UnsupportedWorkloadError("The eviction attempt limit must be at least one.")
    if not any(app.is_active for app in apps):
        raise UnsupportedWorkloadError("Add at least one active app before comparing policies.")

    registered_ids = set(app_ids)
    for resource in resources:
        referenced_ids = set(resource.holders) | set(resource.waiting_queue)
        missing_ids = referenced_ids - registered_ids
        if missing_ids:
            missing = ", ".join(sorted(missing_ids))
            raise UnsupportedWorkloadError(
                f"Resource '{resource.resource_id}' references unknown app(s): {missing}."
            )

    fingerprint = _snapshot_fingerprint(apps, resources, total_memory, clock_hand)
    initial_active_apps = [app for app in apps if app.is_active]
    initial_used_memory = sum(app.memory_footprint for app in initial_active_apps)

    return PolicyComparison(
        plain_clock=_run_policy(
            "Plain Clock", apps, resources, total_memory, clock_hand, max_attempts
        ),
        raae=_run_policy(
            "RAAE", apps, resources, total_memory, clock_hand, max_attempts
        ),
        workload_fingerprint=fingerprint,
        initial_app_count=len(apps),
        initial_active_app_count=len(initial_active_apps),
        initial_used_memory_mb=initial_used_memory,
        max_attempts=max_attempts,
    )


def _run_policy(
    policy: str,
    source_apps: Sequence[App],
    source_resources: Sequence[Resource],
    total_memory: int,
    clock_hand: int,
    max_attempts: int,
) -> PolicyMetrics:
    memory_manager = MemoryManager(total_memory=total_memory)
    for source_app in source_apps:
        memory_manager.register_app(deepcopy(source_app))
    memory_manager.clock_hand = clock_hand

    resource_manager = ResourceManager()
    for source_resource in source_resources:
        resource_manager.register_resource(deepcopy(source_resource))

    db = DatabaseManager(":memory:")
    try:
        for app in memory_manager.apps.values():
            db.save_app(app)
        for resource in resource_manager.get_all_resources():
            db.save_resource(resource)

        engine = None
        if policy == "RAAE":
            conflict_manager = ResourceConflictManager(
                resource_manager=resource_manager,
                registered_apps=memory_manager.apps,
                db=db,
            )
            engine = RAAEEngine(conflict_manager=conflict_manager)

        logged_conflict_outcomes = 0
        passive_contention_observations = 0
        simulated_wait_freeze_proxy_count = 0
        blocked_evictions = 0
        conflict_case_orchestration_latencies = []
        attempts = 0

        for _ in range(max_attempts):
            candidate, _ = memory_manager.clock_step()
            if candidate is None:
                break
            attempts += 1
            contention = _candidate_contentions(
                candidate, resource_manager, memory_manager
            )
            passive_contention_observations += contention

            if engine is None:
                can_evict = True
                conflict_status = ConflictStatus.NO_CONFLICT
                decision_reason = "Plain Clock evicts the selected candidate."
            else:
                conflicts_before = db.conflict_log.count_conflicts()
                started_at = time.perf_counter_ns()
                result = engine.orchestrate_safe_eviction(candidate)
                elapsed_ms = (time.perf_counter_ns() - started_at) / 1_000_000
                conflicts_after = db.conflict_log.count_conflicts()
                can_evict = result.can_evict
                conflict_status = result.conflict_decision.status
                logged_conflict_outcomes += conflicts_after - conflicts_before
                decision_reason = result.eviction_decision.reason
                if (
                    contention
                    or conflicts_after > conflicts_before
                    or conflict_status != ConflictStatus.NO_CONFLICT
                ):
                    conflict_case_orchestration_latencies.append(elapsed_ms)
                if _is_simulated_wait_freeze_proxy(conflict_status):
                    simulated_wait_freeze_proxy_count += 1
                if _is_resource_blocked_decision(
                    conflict_status,
                    result.conflict_decision.resource_ids,
                    can_evict,
                ):
                    blocked_evictions += 1

            if can_evict and memory_manager.evict_app(candidate.app_id):
                db.save_app(candidate)
                db.eviction_log.log_eviction(
                    app_id=candidate.app_id,
                    algorithm="CLOCK" if engine is None else "CLOCK+RAAE",
                    reason=decision_reason,
                    lock_checked=engine is not None,
                    safe_release=engine is not None or not candidate.held_resources,
                    result=EvictionDecisionType.SAFE_TO_EVICT.value,
                )

        orchestration_latency = (
            sum(conflict_case_orchestration_latencies)
            / len(conflict_case_orchestration_latencies)
            if conflict_case_orchestration_latencies else None
        )
        return PolicyMetrics(
            policy=policy,
            eviction_count=db.eviction_log.count_evictions(),
            simulated_wait_freeze_proxy_count=simulated_wait_freeze_proxy_count,
            logged_conflict_outcomes=logged_conflict_outcomes,
            passive_contention_observations=passive_contention_observations,
            conflict_case_orchestration_latency_ms=orchestration_latency,
            resource_blocked_evictions=blocked_evictions,
            attempts=attempts,
        )
    finally:
        db.close()


def _is_simulated_wait_freeze_proxy(status: ConflictStatus) -> bool:
    return status == ConflictStatus.WAIT


def _is_resource_blocked_decision(
    status: ConflictStatus,
    resource_ids: Sequence[str],
    can_evict: bool,
) -> bool:
    resource_blocking_statuses = {
        ConflictStatus.WAIT,
        ConflictStatus.RESOLVE_REQUIRED,
        ConflictStatus.BLOCKED,
        ConflictStatus.NOT_SAFE,
    }
    return (
        not can_evict
        and bool(resource_ids)
        and status in resource_blocking_statuses
    )


def _candidate_contentions(
    candidate: App,
    resource_manager: ResourceManager,
    memory_manager: MemoryManager,
) -> int:
    """Count contested resources involving the current candidate, once each."""
    active_ids = {
        app_id for app_id, app in memory_manager.apps.items() if app.is_active
    }
    contention_count = 0
    for resource in resource_manager.get_all_resources():
        active_holders = (set(resource.holders) & active_ids) - {candidate.app_id}
        active_waiters = (set(resource.waiting_queue) & active_ids) - {candidate.app_id}
        candidate_is_holder = candidate.app_id in resource.holders
        candidate_is_waiting = candidate.app_id in resource.waiting_queue
        if (
            (candidate_is_waiting and active_holders)
            or (candidate_is_holder and active_waiters)
        ):
            contention_count += 1
    return contention_count


def _snapshot_fingerprint(
    apps: Sequence[App],
    resources: Sequence[Resource],
    total_memory: int,
    clock_hand: int,
) -> str:
    snapshot = {
        "total_memory": total_memory,
        "clock_hand": clock_hand,
        "apps": [
            {
                "app_id": app.app_id,
                "name": app.name,
                "priority": app.priority,
                "state": app.state.value,
                "status": app.status.value,
                "memory_footprint": app.memory_footprint,
                "reference_bit": app.reference_bit,
                "last_access_time": app.last_access_time,
                "held_resources": sorted(app.held_resources),
            }
            for app in sorted(apps, key=lambda item: item.app_id)
        ],
        "resources": [
            {
                "resource_id": resource.resource_id,
                "name": resource.name,
                "capacity": resource.capacity,
                "available_units": resource.available_units,
                "holders": sorted(resource.holders.items()),
                "waiting_queue": list(resource.waiting_queue),
            }
            for resource in sorted(resources, key=lambda item: item.resource_id)
        ],
    }
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
