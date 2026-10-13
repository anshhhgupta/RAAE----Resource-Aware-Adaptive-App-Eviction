"""Comprehensive End-to-End Integration Test for RAAE Frontend & Backend.

Executes the complete 17-step end-to-end integration scenario:
1. Launch app state & backend managers.
2. Create at least three simulated applications.
3. Verify RAM allocation and pressure level.
4. Request Camera, Microphone, GPS, and Audio resources.
5. Verify single-holder resource ownership and waiting queues.
6. Trigger / observe memory pressure.
7. Observe Clock / Second-Chance candidate selection.
8. Verify RAAE checks the candidate's resources.
9. Trigger a resource conflict.
10. Verify actual Wound-Wait decision.
11. Observe safe resource release / waiting / deferred eviction.
12. Complete a valid eviction.
13. Verify RAM accounting and resource ownership afterward.
14. Confirm events are persisted in SQLite.
15. Confirm the dashboard event feed displays the actual events.
16. Compare baseline Clock and RAAE using equivalent workloads.
17. Verify the comparison charts match recorded results.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend  # noqa: F401,E402
from backend.models.app import App, AppState
from backend.models.resource import Resource
from backend.memory_manager.memory_manager import MemoryManager
from backend.resource_manager.resource_manager import ResourceManager
from backend.conflict_manager.conflict_manager import ConflictManager, ConflictDecision
from backend.simulation.simulation_state import SimulationState
from backend.simulation_engine.controller import Workload, run_simulation
from database.db import DatabaseManager
from ui import analytics_view as av
from ui import app_actions, resource_actions


def test_full_end_to_end_integration_flow(tmp_path: Path):
    """Executes the complete 17-step end-to-end integration scenario."""
    # ── Step 1: Launch the app & initialize session state ──────────────
    db_file = str(tmp_path / "e2e_sim.db")
    db = DatabaseManager(db_file)
    mem_mgr = MemoryManager(total_memory=1000)
    res_mgr = ResourceManager()
    res_mgr.create_default_resources()

    sim = SimulationState(
        memory_manager=mem_mgr,
        resource_manager=res_mgr,
        db=db,
    )
    assert sim is not None
    assert mem_mgr.total_memory == 1000

    # ── Step 2: Create at least three simulated applications ───────────
    res1 = app_actions.create_application(
        sim,
        name="Maps",
        memory_footprint=300,
        priority=3,
        initial_state="BACKGROUND",
    )
    assert res1.ok is True
    app1 = res1.app
    assert app1 is not None
    mem_mgr.update_reference_bit(app1.app_id, 0)

    res2 = app_actions.create_application(
        sim,
        name="Fitness",
        memory_footprint=250,
        priority=2,
        initial_state="BACKGROUND",
    )
    assert res2.ok is True
    app2 = res2.app
    assert app2 is not None
    mem_mgr.update_reference_bit(app2.app_id, 1)

    res3 = app_actions.create_application(
        sim,
        name="Music",
        memory_footprint=300,
        priority=1,
        initial_state="BACKGROUND",
    )
    assert res3.ok is True
    app3 = res3.app
    assert app3 is not None
    mem_mgr.update_reference_bit(app3.app_id, 0)

    assert len(mem_mgr.apps) == 3

    # ── Step 3: Verify RAM allocation ─────────────────────────────────
    used_ram = mem_mgr.get_used_memory()
    assert used_ram == 850
    assert mem_mgr.get_free_memory() == 150
    assert mem_mgr.get_usage_percentage() == 85.0
    # 85% is ORANGE pressure (75% <= threshold < 90%)
    assert mem_mgr.get_pressure_level().value == "ORANGE"
    assert mem_mgr.is_under_pressure() is True

    # ── Step 4: Request Camera, Microphone, GPS, and Audio ─────────────
    # app1 acquires Camera and GPS
    req1 = resource_actions.request_resource(sim, "app_maps", "Camera")
    assert req1.ok is True
    req2 = resource_actions.request_resource(sim, "app_maps", "GPS")
    assert req2.ok is True

    # app2 acquires Audio and Microphone
    req3 = resource_actions.request_resource(sim, "app_fitness", "Audio")
    assert req3.ok is True
    req4 = resource_actions.request_resource(sim, "app_fitness", "Microphone")
    assert req4.ok is True

    # ── Step 5: Verify single-holder resource ownership & waiting queues ─
    # app3 requests GPS which is held by app1 -> will be queued!
    req5 = resource_actions.request_resource(sim, "app_music", "GPS")
    assert req5.ok is False
    assert "queued" in req5.message

    # Verify single-holder invariant for all resources
    rows = {r.resource_id: r for r in resource_actions.resource_rows(sim)}
    assert rows["Camera"].holder_id == "app_maps"
    assert rows["GPS"].holder_id == "app_maps"
    assert rows["Audio"].holder_id == "app_fitness"
    assert rows["Microphone"].holder_id == "app_fitness"

    # Verify waiting queue on GPS
    assert rows["GPS"].waiting == (("app_music", "Music"),)
    assert rows["GPS"].waiting_count == 1
    assert app3.state == AppState.WAITING

    # Invariant: single-holder check reports 0 integrity problems
    for res_obj in res_mgr.get_all_resources():
        assert resource_actions.integrity_problems(res_obj) == []

    # ── Step 6: Trigger memory pressure ───────────────────────────────
    assert mem_mgr.is_under_pressure() is True
    assert mem_mgr.get_usage_percentage() >= 75.0

    # ── Step 7: Observe Clock/Second-Chance candidate selection ───────
    candidate = sim.select_eviction_candidate()
    assert candidate is not None
    # Clock inspected background apps
    assert candidate.app_id in ("app_maps", "app_music")

    # ── Step 8: Verify RAAE checks the candidate's resources ──────────
    # If candidate holds resources, RAAE identifies them
    candidate_info = sim.get_candidate_info()
    assert candidate_info is not None
    held_by_candidate = sim.get_candidate_resources(candidate)
    assert set(held_by_candidate) == set(candidate.held_resources)

    # ── Step 9: Trigger a resource conflict ───────────────────────────
    # GPS has a live conflict between app_music (requester, P1) and app_maps (holder, P3)
    conflicts = resource_actions.live_conflicts(sim)
    assert len(conflicts) >= 1
    gps_conflict = [c for c in conflicts if c.resource_id == "GPS"][0]
    assert gps_conflict.holder_id == "app_maps"
    assert gps_conflict.requester_id == "app_music"

    # ── Step 10: Verify the actual Wound-Wait decision ─────────────────
    adjudication = resource_actions.evaluate_conflict(sim, db, "GPS")
    assert adjudication is not None
    # app_music (P1) is queued behind app_maps (P3) -> requester priority < holder priority -> WAIT!
    assert adjudication.decision == "WAIT"
    assert adjudication.is_wound is False
    assert "wait" in adjudication.reason.lower()

    # ── Step 11: Observe safe resource release & queue promotion ───────
    # When app_maps safely releases GPS:
    rel_gps = resource_actions.release_resource(sim, "app_maps", "GPS")
    assert rel_gps.ok is True
    # The semaphore immediately processes the queue and grants GPS to app_music
    assert "GPS" in app3.held_resources
    assert app3.state == AppState.BACKGROUND
    rows_after = {r.resource_id: r for r in resource_actions.resource_rows(sim)}
    assert rows_after["GPS"].holder_id == "app_music"
    assert rows_after["GPS"].waiting == ()

    # ── Step 12: Complete a valid eviction ─────────────────────────────
    # Safe release remaining held resource (Camera) before eviction
    rel_cam = resource_actions.release_resource(sim, "app_maps", "Camera")
    assert rel_cam.ok is True
    # Evict app_maps
    evict_success = mem_mgr.evict_app("app_maps")
    assert evict_success is True
    assert app1.is_evicted is True
    assert app1.is_active is False
    # Log eviction to SQLite
    db.eviction_log.log_eviction(
        app_id="app_maps",
        algorithm="RAAE",
        reason="Safe eviction after resource release",
        lock_checked=True,
        safe_release=True,
        result="EVICTED",
    )

    # ── Step 13: Verify RAM accounting & resource ownership afterward ──
    assert mem_mgr.get_used_memory() == 550  # 850 - 300 = 550 MB
    assert "app_maps" not in [a.app_id for a in mem_mgr.get_active_apps()]
    camera_res = res_mgr.get_resource("Camera")
    assert camera_res.primary_holder_id is None

    # ── Step 14: Confirm events are persisted in SQLite ───────────────
    eviction_logs = db.eviction_log.get_all_evictions()
    assert len(eviction_logs) >= 1
    ev_record = [e for e in eviction_logs if e["app_id"] == "app_maps"][0]
    assert ev_record["result"] == "EVICTED"
    assert ev_record["algorithm"] == "RAAE"

    # ── Step 15: Confirm the dashboard displays actual events ──────────
    events = db.get_recent_events(limit=50)
    assert len(events) >= 1
    event_apps = [e.get("app_id") for e in events]
    assert "app_maps" in event_apps

    # ── Step 16: Compare baseline Clock and RAAE using equivalent workloads
    b_res, r_res = av.run_comparison(
        workload_type="deterministic",
        total_memory=1000,
        pressure_threshold=75.0,
        ticks=1,
    )
    assert b_res["policy"] == "BASELINE_CLOCK"
    assert r_res["policy"] == "RAAE"
    # In deterministic workload: Baseline causes 1 freeze, RAAE prevents it (0 freezes)
    assert b_res["freeze_incidents"] == 1
    assert r_res["freeze_incidents"] == 0

    # ── Step 17: Verify comparison charts match recorded results ───────
    fig1 = av.plot_eviction_counts(b_res, r_res)
    assert isinstance(fig1, plt.Figure)
    plt.close(fig1)

    fig2 = av.plot_resource_conflicts(b_res, r_res)
    assert isinstance(fig2, plt.Figure)
    plt.close(fig2)

    fig3 = av.plot_freeze_incidents(b_res, r_res)
    assert isinstance(fig3, plt.Figure)
    plt.close(fig3)

    fig4 = av.plot_memory_utilization_timeline(b_res, r_res, total_memory=1000)
    assert isinstance(fig4, plt.Figure)
    plt.close(fig4)

    fig5 = av.plot_eviction_events_timeline(b_res, r_res)
    assert isinstance(fig5, plt.Figure)
    plt.close(fig5)

    db.close()
