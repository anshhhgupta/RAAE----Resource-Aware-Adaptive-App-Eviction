"""Unit and integration tests for the Resource Monitoring page and actions.

Tests:
- Free-resource acquisition through ResourceManager
- Competing requests, queue insertion, and WAITING state transitions
- Resource release and subsequent FIFO queue handling
- Single-holder invariant enforcement
- Conflict evaluation supporting WOUND_HOLDER, WAIT, and NO_CONFLICT
- Conflict preemption application (apply_wound_wait) and outcome reporting
- Evicted app acquisition refusal
"""

from __future__ import annotations

import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend  # noqa: E402
from backend.models.app import App, AppState  # noqa: E402
from backend.memory_manager.memory_manager import MemoryManager  # noqa: E402
from backend.resource_manager.resource_manager import ResourceManager  # noqa: E402
from backend.simulation.simulation_state import SimulationState  # noqa: E402
from database.db import DatabaseManager  # noqa: E402
from ui import resource_actions as actions  # noqa: E402


@pytest.fixture()
def sim(tmp_path: Path):
    """Sets up a clean in-memory SimulationState with 1000 MB RAM and default resources."""
    db = DatabaseManager(str(tmp_path / "resource_test.db"))
    memory_manager = MemoryManager(total_memory=1000)
    resource_manager = ResourceManager()
    resource_manager.create_default_resources()
    state = SimulationState(
        memory_manager=memory_manager,
        resource_manager=resource_manager,
        db=db,
    )
    yield state
    db.close()


def _create_app(sim: SimulationState, app_id: str, name: str, priority: int = 2) -> App:
    app = App(
        app_id=app_id,
        name=name,
        priority=priority,
        state=AppState.BACKGROUND,
        memory_footprint=100,
    )
    sim.add_app(app)
    return app


def test_canonical_resources_present(sim: SimulationState):
    """The four canonical resources (Camera, Microphone, GPS, Audio) are registered."""
    avail = actions.available_resources(sim)
    assert set(avail) == {"Camera", "Microphone", "GPS", "Audio"}


def test_free_resource_acquisition(sim: SimulationState):
    """Free resource acquisition through ResourceManager succeeds and reflects in UI rows."""
    app = _create_app(sim, "app_maps", "Maps", priority=3)

    # Initially GPS is free
    rows = {r.resource_id: r for r in actions.resource_rows(sim)}
    assert rows["GPS"].status == "FREE"
    assert rows["GPS"].holder_id is None
    assert rows["GPS"].available_units == 1
    assert rows["GPS"].waiting == ()
    assert rows["GPS"].waiting_count == 0
    assert actions.integrity_problems(sim.resource_manager.get_resource("GPS")) == []

    # Request GPS through the resource action (which uses ResourceManager)
    res = actions.request_resource(sim, app.app_id, "GPS")
    assert res.ok is True
    assert "granted GPS" in res.message

    # Re-read through UI rows and verify displayed state matches backend
    rows = {r.resource_id: r for r in actions.resource_rows(sim)}
    assert rows["GPS"].status == "LOCKED"
    assert rows["GPS"].holder_id == "app_maps"
    assert rows["GPS"].holder_name == "Maps"
    assert rows["GPS"].available_units == 0
    assert rows["GPS"].waiting == ()
    assert rows["GPS"].waiting_count == 0
    assert actions.integrity_problems(sim.resource_manager.get_resource("GPS")) == []


def test_competing_requests_and_queue_insertion(sim: SimulationState):
    """When a resource is held, a competing request queues the app in WAITING state."""
    app1 = _create_app(sim, "app_maps", "Maps", priority=3)
    app2 = _create_app(sim, "app_fit", "Fitness Tracker", priority=2)

    # App1 acquires GPS
    actions.request_resource(sim, app1.app_id, "GPS")

    # App2 requests GPS (competing request)
    res2 = actions.request_resource(sim, app2.app_id, "GPS")
    assert res2.ok is False
    assert "queued" in res2.message

    # Backend state: app2 is WAITING
    assert app2.state == AppState.WAITING

    # Displayed resource rows reflect queue insertion
    rows = {r.resource_id: r for r in actions.resource_rows(sim)}
    assert rows["GPS"].status == "WAITING"
    assert rows["GPS"].holder_id == "app_maps"
    assert rows["GPS"].waiting == (("app_fit", "Fitness Tracker"),)
    assert rows["GPS"].waiting_count == 1

    # Conflict monitor detects the live conflict
    conflicts = actions.live_conflicts(sim)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.resource_id == "GPS"
    assert c.holder_id == "app_maps"
    assert c.requester_id == "app_fit"
    assert c.queue_length == 1


def test_resource_release_and_subsequent_queue_handling(sim: SimulationState):
    """Releasing a resource promotes the queued requester in FIFO order."""
    app1 = _create_app(sim, "app_maps", "Maps", priority=3)
    app2 = _create_app(sim, "app_fit", "Fitness Tracker", priority=2)

    actions.request_resource(sim, app1.app_id, "GPS")
    actions.request_resource(sim, app2.app_id, "GPS")

    # Verify app1 holds GPS and app2 is queued
    assert "GPS" in app1.held_resources
    assert "GPS" not in app2.held_resources
    assert app2.state == AppState.WAITING

    # Release GPS from app1
    rel = actions.release_resource(sim, app1.app_id, "GPS")
    assert rel.ok is True

    # App1 released; semaphore popped app2 from queue and granted GPS
    assert "GPS" not in app1.held_resources
    assert "GPS" in app2.held_resources
    assert app2.state == AppState.BACKGROUND

    # Verify displayed state
    rows = {r.resource_id: r for r in actions.resource_rows(sim)}
    assert rows["GPS"].status == "LOCKED"
    assert rows["GPS"].holder_id == "app_fit"
    assert rows["GPS"].waiting == ()
    assert rows["GPS"].waiting_count == 0

    # No more live conflicts
    assert actions.live_conflicts(sim) == []


def test_conflict_evaluation_wound_holder(sim: SimulationState):
    """Higher priority requester queued behind lower priority holder produces WOUND_HOLDER."""
    app1 = _create_app(sim, "app_chat", "Chat App", priority=1)
    app2 = _create_app(sim, "app_nav", "Navigator", priority=4)

    # Lower priority app1 acquires Camera
    actions.request_resource(sim, app1.app_id, "Camera")

    # Higher priority app2 requests Camera -> queued
    actions.request_resource(sim, app2.app_id, "Camera")

    verdict = actions.evaluate_conflict(sim, sim.db, "Camera")
    assert verdict is not None
    assert verdict.decision == "WOUND_HOLDER"
    assert verdict.is_wound is True
    assert verdict.requester_id == "app_nav"
    assert verdict.holder_id == "app_chat"
    assert verdict.requester_priority == 4
    assert verdict.holder_priority == 1
    assert "preempt" in verdict.reason.lower() or "wound" in verdict.reason.lower() or "higher priority" in verdict.reason.lower()


def test_conflict_evaluation_wait(sim: SimulationState):
    """Lower priority requester queued behind higher priority holder produces WAIT."""
    app1 = _create_app(sim, "app_maps", "Maps", priority=4)
    app2 = _create_app(sim, "app_notes", "Notes", priority=1)

    # Higher priority app1 acquires Microphone
    actions.request_resource(sim, app1.app_id, "Microphone")

    # Lower priority app2 requests Microphone -> queued
    actions.request_resource(sim, app2.app_id, "Microphone")

    verdict = actions.evaluate_conflict(sim, sim.db, "Microphone")
    assert verdict is not None
    assert verdict.decision == "WAIT"
    assert verdict.is_wound is False
    assert verdict.requester_id == "app_notes"
    assert verdict.holder_id == "app_maps"
    assert "wait" in verdict.reason.lower()


def test_conflict_evaluation_no_conflict(sim: SimulationState):
    """Evaluating a free resource or an app that already holds it produces NO_CONFLICT."""
    app1 = _create_app(sim, "app_maps", "Maps", priority=3)

    # Audio is free
    verdict_free = actions.evaluate_conflict(sim, sim.db, "Audio", requester_id=app1.app_id)
    assert verdict_free is not None
    assert verdict_free.decision == "NO_CONFLICT"
    assert "free" in verdict_free.reason.lower()

    # Now acquire Audio
    actions.request_resource(sim, app1.app_id, "Audio")

    # Evaluating app1 requesting Audio which it already holds
    verdict_held = actions.evaluate_conflict(sim, sim.db, "Audio", requester_id=app1.app_id)
    assert verdict_held is not None
    assert verdict_held.decision == "NO_CONFLICT"
    assert "already" in verdict_held.reason.lower()


def test_apply_wound_wait_preemption_and_outcome(sim: SimulationState):
    """apply_wound_wait releases the wounded holder and promotes the queued requester."""
    app_low = _create_app(sim, "app_low", "LowApp", priority=1)
    app_high = _create_app(sim, "app_high", "HighApp", priority=5)

    actions.request_resource(sim, app_low.app_id, "Audio")
    actions.request_resource(sim, app_high.app_id, "Audio")

    # Adjudicate and apply WOUND_HOLDER
    adjudication = actions.apply_wound_wait(sim, sim.db, "Audio")
    assert adjudication is not None
    assert adjudication.decision == "WOUND_HOLDER"
    assert adjudication.released is True

    # Re-check outcome
    outcome = actions.outcome_for(sim, adjudication)
    assert outcome["state"] == "Released"
    assert "app_high now holds it" in outcome["detail"]

    # Verify backend state: app_high now holds Audio, app_low has no lock
    assert "Audio" not in app_low.held_resources
    assert "Audio" in app_high.held_resources


def test_single_holder_invariant(sim: SimulationState):
    """Every resource has at most one active holder at any time."""
    app1 = _create_app(sim, "a1", "App1", priority=3)
    app2 = _create_app(sim, "a2", "App2", priority=2)
    app3 = _create_app(sim, "a3", "App3", priority=1)

    for res in ["Camera", "Microphone", "GPS", "Audio"]:
        actions.request_resource(sim, app1.app_id, res)
        actions.request_resource(sim, app2.app_id, res)
        actions.request_resource(sim, app3.app_id, res)

    for row in actions.resource_rows(sim):
        assert row.capacity == 1
        assert row.holder_id == "a1"
        assert row.problems == ()
        assert len(row.waiting) == 2


def test_evicted_app_cannot_acquire_resource(sim: SimulationState):
    """An evicted app is refused by the semaphore."""
    app = _create_app(sim, "app_dead", "DeadApp", priority=1)
    app.change_state(AppState.EVICTED)

    res = actions.request_resource(sim, app.app_id, "Camera")
    assert res.ok is False
    assert "evicted" in res.message.lower()
