"""Deterministic simulation runner comparing BASELINE_CLOCK vs RAAE."""

from typing import Any, Dict, List, Optional, Tuple

from backend.models.app import App, AppState
from backend.models.resource import Resource
from backend.memory_manager.memory_manager import MemoryManager
from backend.resource_manager.resource_manager import ResourceManager
from backend.simulation_engine.policies import (
    BaselineClockPolicy,
    EvictionPolicyType,
    RAAEEvictionPolicy,
)
from backend.simulation_engine.simulation_engine import (
    SimulationConfig,
    SimulationEngine,
    SimulationTickResult,
)
from database.db import DatabaseManager


def setup_deterministic_simulation(
    policy_type: EvictionPolicyType,
    db_path: str = ":memory:",
    total_memory: int = 1000,
    pressure_threshold: float = 75.0,
) -> Tuple[SimulationEngine, DatabaseManager]:
    """Creates a deterministic simulation environment with at least 5 apps and at least 2 apps holding resources.

    Environment setup:
    1. Total Memory: 1000 MB.
    2. Apps (5 apps total, initially consuming 1000 MB -> 100% memory usage / RED pressure):
       - App 1 (app_maps): 'Maps' (300 MB, ref_bit=0, holding GPS). Contested by Fitness.
       - App 2 (app_fitness): 'Fitness Tracker' (150 MB, ref_bit=1, WAITING for GPS).
       - App 3 (app_music): 'Music Player' (200 MB, ref_bit=0, holding AUDIO). Uncontested.
       - App 4 (app_chat): 'Chat App' (150 MB, ref_bit=0, holds no resources).
       - App 5 (app_browser): 'Browser' (200 MB, ref_bit=1, holds no resources).
    3. Shared Resources:
       - 'GPS': held by app_maps (app_fitness waiting in queue).
       - 'AUDIO': held by app_music (uncontested).
    """
    # 1. Database Manager
    db = DatabaseManager(db_path)

    # 2. Managers
    mem_mgr = MemoryManager(total_memory=total_memory)
    res_mgr = ResourceManager()
    res_mgr.create_default_resources()

    # Pre-save resources in DB
    for res in res_mgr.get_all_resources():
        db.save_resource(res)

    # 3. Create 5 deterministic apps
    app_maps = App(
        app_id="app_maps",
        name="Maps",
        priority=3,
        state=AppState.BACKGROUND,
        memory_footprint=300,
        reference_bit=0  # Next candidate selected by Clock
    )
    app_fitness = App(
        app_id="app_fitness",
        name="Fitness Tracker",
        priority=2,
        state=AppState.BACKGROUND,
        memory_footprint=150,
        reference_bit=1
    )
    app_music = App(
        app_id="app_music",
        name="Music Player",
        priority=2,
        state=AppState.BACKGROUND,
        memory_footprint=300,
        reference_bit=0
    )
    app_chat = App(
        app_id="app_chat",
        name="Chat App",
        priority=1,
        state=AppState.BACKGROUND,
        memory_footprint=100,
        reference_bit=0
    )
    app_browser = App(
        app_id="app_browser",
        name="Browser",
        priority=4,
        state=AppState.FOREGROUND,
        memory_footprint=150,
        reference_bit=1
    )

    apps = [app_maps, app_fitness, app_music, app_chat, app_browser]
    for app in apps:
        mem_mgr.register_app(app)
        db.save_app(app)

    # 4. Resource Allocation & Contention Setup
    # - App 1 (Maps) acquires GPS
    res_mgr.acquire_resource(app_maps, "GPS")
    db.resource_locks.acquire_lock(app_maps.app_id, "GPS")
    db.save_app(app_maps)
    db.save_resource(res_mgr.get_resource("GPS"))

    # - App 2 (Fitness) requests GPS -> BLOCKED, transitions to WAITING state
    res_mgr.acquire_resource(app_fitness, "GPS")
    db.save_app(app_fitness)
    db.save_resource(res_mgr.get_resource("GPS"))

    # - App 3 (Music Player) acquires AUDIO -> uncontested
    res_mgr.acquire_resource(app_music, "AUDIO")
    db.resource_locks.acquire_lock(app_music.app_id, "AUDIO")
    db.save_app(app_music)
    db.save_resource(res_mgr.get_resource("AUDIO"))

    # Aging: Simulated background idle state sets reference bits for Clock evaluation.
    # Note: acquire_resource() calls app.touch() which sets ref_bit=1.
    # We explicitly simulate that background apps have been idle, making app_maps the first Clock candidate.
    app_maps.reference_bit = 0
    app_music.reference_bit = 0
    app_chat.reference_bit = 0
    app_fitness.reference_bit = 1
    app_browser.reference_bit = 1
    db.save_app(app_maps)
    db.save_app(app_music)
    db.save_app(app_chat)
    db.save_app(app_fitness)
    db.save_app(app_browser)

    # 5. Configure Policy
    if policy_type == EvictionPolicyType.BASELINE_CLOCK:
        policy = BaselineClockPolicy()
    else:
        policy = RAAEEvictionPolicy()

    engine = SimulationEngine(
        memory_manager=mem_mgr,
        resource_manager=res_mgr,
        db_manager=db,
        policy=policy,
        config=SimulationConfig(
            memory_pressure_threshold=pressure_threshold,
            total_memory=total_memory
        )
    )

    return engine, db


def run_deterministic_comparison(
    total_memory: int = 1000,
    pressure_threshold: float = 75.0,
    ticks_to_run: int = 1
) -> Dict[str, Any]:
    """Runs identical deterministic simulation under BASELINE_CLOCK and RAAE policies

    and returns side-by-side comparable metrics.
    """
    # -------------------------------------------------------------
    # RUN 1: BASELINE_CLOCK
    # -------------------------------------------------------------
    engine_baseline, db_baseline = setup_deterministic_simulation(
        policy_type=EvictionPolicyType.BASELINE_CLOCK,
        total_memory=total_memory,
        pressure_threshold=pressure_threshold
    )

    baseline_ticks: List[Dict[str, Any]] = []
    for _ in range(ticks_to_run):
        res = engine_baseline.tick()
        baseline_ticks.append({
            "tick": res.tick_number,
            "memory_before": res.memory_before_mb,
            "memory_after": res.memory_after_mb,
            "usage_pct_before": res.usage_percentage_before,
            "usage_pct_after": res.usage_percentage_after,
            "status": res.status,
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
                for r in res.eviction_results
            ]
        })

    baseline_metrics = engine_baseline.metrics.to_dict()
    baseline_eviction_logs = db_baseline.eviction_log.get_all_evictions()
    baseline_conflict_logs = db_baseline.conflict_log.get_all_conflicts()
    baseline_active_locks = db_baseline.resource_locks.get_active_locks()
    baseline_maps_app = engine_baseline.memory_manager.apps["app_maps"]
    baseline_fitness_app = engine_baseline.memory_manager.apps["app_fitness"]

    # -------------------------------------------------------------
    # RUN 2: RAAE
    # -------------------------------------------------------------
    engine_raae, db_raae = setup_deterministic_simulation(
        policy_type=EvictionPolicyType.RAAE,
        total_memory=total_memory,
        pressure_threshold=pressure_threshold
    )

    raae_ticks: List[Dict[str, Any]] = []
    for _ in range(ticks_to_run):
        res = engine_raae.tick()
        raae_ticks.append({
            "tick": res.tick_number,
            "memory_before": res.memory_before_mb,
            "memory_after": res.memory_after_mb,
            "usage_pct_before": res.usage_percentage_before,
            "usage_pct_after": res.usage_percentage_after,
            "status": res.status,
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
                for r in res.eviction_results
            ]
        })

    raae_metrics = engine_raae.metrics.to_dict()
    raae_eviction_logs = db_raae.eviction_log.get_all_evictions()
    raae_conflict_logs = db_raae.conflict_log.get_all_conflicts()
    raae_active_locks = db_raae.resource_locks.get_active_locks()
    raae_maps_app = engine_raae.memory_manager.apps["app_maps"]
    raae_fitness_app = engine_raae.memory_manager.apps["app_fitness"]

    # Verification of key hypotheses
    maps_blindly_killed_in_baseline = baseline_maps_app.is_evicted
    maps_protected_in_raae = not raae_maps_app.is_evicted

    summary = {
        "hypothesis_verified": (
            maps_blindly_killed_in_baseline
            and maps_protected_in_raae
            and baseline_metrics["unsafe_evictions"] > 0
            and raae_metrics["unsafe_evictions"] == 0
        ),
        "app_holding_resource_blindly_killed_in_baseline": maps_blindly_killed_in_baseline,
        "app_holding_resource_protected_in_raae": maps_protected_in_raae,
        "baseline_unsafe_evictions": baseline_metrics["unsafe_evictions"],
        "raae_unsafe_evictions": raae_metrics["unsafe_evictions"],
        "baseline_freeze_incidents": baseline_metrics["freeze_incidents"],
        "raae_freeze_incidents": raae_metrics["freeze_incidents"],
        "raae_conflicts_prevented": raae_metrics["conflicts_prevented"],
        "raae_safe_releases": raae_metrics["safe_releases_performed"],
    }

    return {
        "summary": summary,
        "baseline": {
            "metrics": baseline_metrics,
            "ticks": baseline_ticks,
            "maps_state": {
                "is_evicted": baseline_maps_app.is_evicted,
                "state": baseline_maps_app.state.value,
                "memory": baseline_maps_app.memory_footprint,
                "held_resources": sorted(list(baseline_maps_app.held_resources))
            },
            "fitness_state": {
                "is_evicted": baseline_fitness_app.is_evicted,
                "state": baseline_fitness_app.state.value,
            },
            "db_logs": {
                "eviction_log_count": len(baseline_eviction_logs),
                "conflict_log_count": len(baseline_conflict_logs),
                "active_locks_count": len(baseline_active_locks)
            }
        },
        "raae": {
            "metrics": raae_metrics,
            "ticks": raae_ticks,
            "maps_state": {
                "is_evicted": raae_maps_app.is_evicted,
                "state": raae_maps_app.state.value,
                "memory": raae_maps_app.memory_footprint,
                "held_resources": sorted(list(raae_maps_app.held_resources))
            },
            "fitness_state": {
                "is_evicted": raae_fitness_app.is_evicted,
                "state": raae_fitness_app.state.value,
            },
            "db_logs": {
                "eviction_log_count": len(raae_eviction_logs),
                "conflict_log_count": len(raae_conflict_logs),
                "active_locks_count": len(raae_active_locks)
            }
        }
    }
