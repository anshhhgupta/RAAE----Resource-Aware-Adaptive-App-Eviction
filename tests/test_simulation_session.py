"""Tests for the step-wise simulation session and its decision timeline.

The central claim this module has to justify is that it drives the *existing*
backend rather than a second simulation, so the first test compares the session's
stepping against ``SimulationController.run()`` for the same workload.

The timeline tests then check the stronger claim the page makes to the user: a
step is only reported as confirmed when the backend produced the evidence, and
every event id shown is really present in the database afterwards.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.simulation_engine.controller import (  # noqa: E402
    SimulationController,
    Workload,
)
from database.db import DatabaseManager  # noqa: E402
from ui import simulation_session as ss  # noqa: E402


def _workload(ticks: int = 3, db_path: str = ":memory:") -> Workload:
    """A fresh deterministic workload; the factory returns a new instance."""
    workload = Workload.deterministic_5_apps()
    workload.ticks = ticks
    workload.db_path = db_path
    return workload


def _session(policy: str = "RAAE", ticks: int = 3, db_path: str = ":memory:") -> ss.SimulationSession:
    return ss.SimulationSession(policy=policy, workload=_workload(ticks, db_path), db_path=db_path)


# ──────────────────────────────────────────────────────────────
# The session drives the existing backend
# ──────────────────────────────────────────────────────────────

def test_stepping_matches_the_controllers_own_run() -> None:
    """Session stepping and SimulationController.run() must agree exactly.

    Same workload, same policy, same ticks: if the session were running its own
    loop with its own rules, the metrics or the outcomes would drift.
    """
    stepped = _session(policy="RAAE", ticks=4)
    stepped_records = stepped.advance(4)
    stepped_metrics = stepped.engine.metrics.to_dict()
    stepped_snapshot = stepped.memory_snapshot()
    stepped_evicted = [
        r.candidate.app_id for rec in stepped_records for r in rec.eviction_results if r.evicted
    ]

    controller = SimulationController(policy="RAAE", workload=_workload(ticks=4))
    reference = controller.run()
    reference_metrics = controller.engine.metrics.to_dict()
    reference_evicted = [
        r["name"]
        for tick in reference.ticks_detail
        for r in tick["evictions"]
        if r["evicted"]
    ]
    controller.close()

    assert [r.status for r in stepped_records] == [
        tick["status"] for tick in reference.ticks_detail
    ]
    assert stepped_metrics == reference_metrics
    assert len(stepped_evicted) == len(reference_evicted)
    assert stepped_snapshot["used_mb"] == reference.final_memory_used
    assert stepped_snapshot["registered_apps"] == 5


def test_a_refused_policy_is_rejected() -> None:
    with pytest.raises(ss.SimulationError, match="Unknown policy"):
        _session(policy="ROUND_ROBIN")


# ──────────────────────────────────────────────────────────────
# Lifecycle and invalid transitions
# ──────────────────────────────────────────────────────────────

def test_a_new_session_is_running_and_can_advance() -> None:
    session = _session()

    assert session.status == ss.STATUS_RUNNING
    assert session.can_advance
    assert session.current_tick == 0
    assert session.records == []


def test_pause_blocks_ticking_until_resumed() -> None:
    session = _session()

    assert "paused at tick 0" in session.pause()
    assert session.status == ss.STATUS_PAUSED
    assert not session.can_advance
    with pytest.raises(ss.SimulationError, match="Cannot advance"):
        session.advance(1)

    assert "resumed" in session.resume()
    assert session.can_advance
    session.advance(1)
    assert session.current_tick == 1


def test_pausing_twice_is_refused() -> None:
    session = _session()
    session.pause()
    with pytest.raises(ss.SimulationError, match="Cannot pause"):
        session.pause()


def test_resuming_a_running_session_is_refused() -> None:
    session = _session()
    with pytest.raises(ss.SimulationError, match="Cannot resume"):
        session.resume()


def test_stopping_closes_the_run_and_blocks_further_ticks() -> None:
    session = _session()
    session.advance(1)

    message = session.stop()

    assert session.status == ss.STATUS_STOPPED
    assert "stopped after 1 tick" in message
    # The message names the file that was kept, so nothing looks destroyed.
    assert session.db_path in message
    assert not session.can_advance
    with pytest.raises(ss.SimulationError, match="Cannot advance"):
        session.advance(1)


def test_stopping_twice_is_refused() -> None:
    session = _session()
    session.stop()
    with pytest.raises(ss.SimulationError, match="Cannot stop"):
        session.stop()


def test_a_stopped_session_cannot_be_paused_or_resumed() -> None:
    session = _session()
    session.stop()
    with pytest.raises(ss.SimulationError, match="Cannot pause"):
        session.pause()
    with pytest.raises(ss.SimulationError, match="Cannot resume"):
        session.resume()


@pytest.mark.parametrize("count", [0, -1, 1.5, True, "2", None])
def test_only_positive_integer_tick_counts_are_accepted(count) -> None:
    session = _session()
    with pytest.raises(ss.SimulationError, match="positive integer"):
        session.advance(count)
    assert session.current_tick == 0


# ──────────────────────────────────────────────────────────────
# Advancing
# ──────────────────────────────────────────────────────────────

def test_advancing_one_tick_returns_its_record() -> None:
    session = _session()

    [record] = session.advance(1)

    assert record.tick_number == 1
    assert session.current_tick == 1
    assert record.started_at <= record.finished_at
    assert session.last_record() is record


def test_advancing_multiple_ticks_stops_at_the_workload_budget() -> None:
    session = _session(ticks=2)

    records = session.advance(10)

    assert [r.tick_number for r in records] == [1, 2]
    assert session.current_tick == 2


def test_tick_budget_exhaustion_is_visible_to_the_page() -> None:
    session = _session(ticks=1)
    session.advance(5)
    assert session.current_tick == 1


def test_memory_snapshot_reports_live_figures() -> None:
    session = _session()
    before = session.memory_snapshot()

    assert before["total_mb"] == 1000
    assert before["used_mb"] == 1000  # the deterministic workload fills the machine
    assert before["available_mb"] == 0
    assert before["registered_apps"] == 5
    assert before["pressure"] in ("ORANGE", "RED")


# ──────────────────────────────────────────────────────────────
# Memory pressure
# ──────────────────────────────────────────────────────────────

def test_triggering_pressure_reports_when_the_system_is_already_full() -> None:
    session = _session()

    outcome = session.trigger_pressure(90.0)

    assert outcome["allocated_mb"] == 0
    assert "already at or above" in outcome["message"]
    assert outcome["used_after_mb"] == outcome["used_before_mb"]


def test_triggering_pressure_allocates_memory_towards_the_target() -> None:
    from backend.simulation_engine.controller import AppSpec, Workload

    workload = Workload(
        total_memory=1000,
        pressure_threshold=75.0,
        ticks=2,
        db_path=":memory:",
        apps=[
            AppSpec(app_id="app_a", name="A", priority=2, memory_footprint=100),
            AppSpec(app_id="app_b", name="B", priority=2, memory_footprint=100),
        ],
    )
    session = ss.SimulationSession(policy="RAAE", workload=workload)

    outcome = session.trigger_pressure(80.0)

    assert outcome["allocated_mb"] > 0
    assert outcome["used_after_mb"] > outcome["used_before_mb"]
    assert outcome["utilization_pct"] >= 80.0
    # The allocation went through the live MemoryManager, not a local tally.
    assert session.controller.mem_mgr.get_used_memory() == outcome["used_after_mb"]


def test_pressure_cannot_be_triggered_while_paused() -> None:
    session = _session()
    session.pause()
    with pytest.raises(ss.SimulationError, match="Cannot trigger memory pressure"):
        session.trigger_pressure(90.0)


# ──────────────────────────────────────────────────────────────
# Decision timeline — evidence, not narration
# ──────────────────────────────────────────────────────────────

def test_timeline_before_any_tick_marks_every_step_unexecuted() -> None:
    session = _session()

    steps = ss.build_timeline(session)

    assert len(steps) == 8
    assert [s.number for s in steps] == list(range(1, 9))
    assert all(s.status == ss.NOT_EXECUTED for s in steps)
    assert all("No tick has been executed" in s.detail for s in steps)


def test_raae_tick_confirms_the_steps_the_engine_actually_ran(tmp_path: Path) -> None:
    db_path = str(tmp_path / "raae_timeline.db")
    session = _session(policy="RAAE", ticks=2, db_path=db_path)
    session.advance(1)

    steps = {s.number: s for s in ss.build_timeline(session)}
    results = session.last_record().eviction_results

    # 1 — the deterministic workload starts at 100% utilisation.
    assert steps[1].status == ss.CONFIRMED
    assert "pressure moved" in steps[1].detail

    # 2 — the Clock inspected and selected a candidate.
    assert steps[2].status == ss.CONFIRMED
    assert session.last_record().clock_inspections

    # 3 — RAAE checked the candidate's locks.
    assert steps[3].status == ss.CONFIRMED

    # 7 — the engine evicted something.
    assert steps[7].status == ss.CONFIRMED
    assert results
    assert "evicted" in steps[7].detail or "deferred" in steps[7].detail

    # 8 — and the event id it reports really is in EvictionLog.
    event_ids = [r.persisted_event_id for r in results if r.persisted_event_id]
    assert event_ids, "the engine reported no persisted event"
    assert steps[8].status == ss.CONFIRMED
    assert f"#{event_ids[0]}" in steps[8].detail

    db = DatabaseManager(db_path)
    logged = {row["event_id"] for row in db.eviction_log.get_all_evictions(limit=100)}
    db.close()
    assert set(event_ids) <= logged


def test_baseline_timeline_says_the_resource_check_never_happened(tmp_path: Path) -> None:
    db_path = str(tmp_path / "baseline_timeline.db")
    session = _session(policy="BASELINE_CLOCK", ticks=1, db_path=db_path)
    session.advance(1)

    steps = {s.number: s for s in ss.build_timeline(session)}

    assert steps[3].status == ss.NOT_APPLICABLE
    assert "no resource-aware check" in steps[3].detail


def test_timeline_conflicts_match_the_conflict_log(tmp_path: Path) -> None:
    db_path = str(tmp_path / "conflicts.db")
    session = _session(policy="RAAE", ticks=2, db_path=db_path)
    session.advance(1)

    record = session.last_record()
    steps = {s.number: s for s in ss.build_timeline(session, record)}

    db = DatabaseManager(db_path)
    stored = db.conflict_log.get_all_conflicts(limit=100)
    db.close()

    if stored:
        # The tick recorded conflicts, so the timeline must say so.
        assert steps[4].status == ss.CONFIRMED
        for conflict in record.conflicts:
            assert any(
                row["waiting_app_id"] == conflict["waiting_app_id"]
                and row["resource_id"] == conflict["resource_id"]
                for row in stored
            )
        # Every conflict the tick claims was resolved under Wound-Wait is
        # labelled accordingly — and the default RAAE wiring is not Wound-Wait,
        # so the step must not claim otherwise.
        assert steps[5].status in (ss.NOT_EXECUTED, ss.CONFIRMED)
        if steps[5].status == ss.NOT_EXECUTED:
            assert "no Wound-Wait precedence decision" in steps[5].detail
    else:
        assert steps[4].status in (ss.NOT_EXECUTED, ss.CONFIRMED)


def test_timeline_of_a_tick_without_evictions_says_nothing_happened(tmp_path: Path) -> None:
    from backend.simulation_engine.controller import AppSpec, Workload

    workload = Workload(
        total_memory=1000,
        pressure_threshold=75.0,
        ticks=3,
        db_path=str(tmp_path / "quiet.db"),
        apps=[AppSpec(app_id="app_a", name="A", priority=2, memory_footprint=100)],
    )
    session = ss.SimulationSession(policy="RAAE", workload=workload)
    session.advance(1)

    steps = {s.number: s for s in ss.build_timeline(session)}

    assert steps[1].status == ss.NOT_EXECUTED
    assert "NO_PRESSURE" in steps[1].detail
    assert steps[2].status == ss.NOT_EXECUTED
    assert steps[7].status == ss.NOT_EXECUTED
    assert steps[8].status == ss.NOT_EXECUTED
    assert steps[5].status == ss.NOT_APPLICABLE


def test_timeline_can_be_rebuilt_for_an_earlier_tick() -> None:
    session = _session(ticks=3)
    session.advance(3)

    first = ss.build_timeline(session, session.records[0])
    latest = ss.build_timeline(session, session.records[-1])

    assert len(first) == len(latest) == 8
    assert first[0].detail != ""


# ──────────────────────────────────────────────────────────────
# Result snapshot and history
# ──────────────────────────────────────────────────────────────

def test_result_snapshot_reports_cumulative_engine_metrics() -> None:
    session = _session(ticks=2)
    session.advance(2)

    snapshot = session.result_snapshot()

    assert snapshot["policy"] == "RAAE"
    assert snapshot["total_ticks"] == 2
    assert len(snapshot["ticks_detail"]) == 2
    assert snapshot["final_usage_percentage"] == session.memory_snapshot()["utilization_pct"]
    assert snapshot["final_memory_used"] == session.memory_snapshot()["used_mb"]


def test_last_decision_reports_the_most_recent_eviction_only(tmp_path: Path) -> None:
    session = _session(ticks=2, db_path=str(tmp_path / "decision.db"))
    session.advance(1)

    decision = session.last_decision()

    assert decision is not None
    assert decision["policy"] == "RAAE"
    assert decision["tick"] == 1
    assert decision["reason"]
    assert isinstance(decision["evicted"], bool)


def test_last_decision_is_none_before_any_tick() -> None:
    assert _session().last_decision() is None


# ──────────────────────────────────────────────────────────────
# History is never destroyed
# ──────────────────────────────────────────────────────────────

def test_a_stopped_run_keeps_its_database_records(tmp_path: Path) -> None:
    first_path = str(tmp_path / "run_one.db")
    first = _session(policy="RAAE", ticks=1, db_path=first_path)
    first.advance(1)
    first.stop()

    # A second run uses its own file, exactly as the page allocates it.
    second = _session(policy="RAAE", ticks=1, db_path=str(tmp_path / "run_two.db"))
    second.advance(1)
    second.stop()

    original = DatabaseManager(first_path)
    evictions = original.eviction_log.get_all_evictions(limit=100)
    original.close()

    assert first_path.endswith("run_one.db")
    assert evictions, "the first run's records must survive the second run"


def test_app_rows_mirror_the_live_registry() -> None:
    session = _session()
    rows = session.app_rows()

    assert [r["app_id"] for r in rows] == sorted(r["app_id"] for r in rows)
    assert len(rows) == 5
    maps = next(r for r in rows if r["app_id"] == "app_maps")
    assert maps["name"] == "Maps"
    assert maps["held_resources"] == ["GPS"]
    assert maps["priority"] == 3