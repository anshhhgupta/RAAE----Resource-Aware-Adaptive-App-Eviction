"""Tests for the Applications page actions and their validation.

These exercise :mod:`ui.app_actions` directly, against a real ``SimulationState``
backed by a real SQLite database, so every assertion is about what the backend
actually did rather than about what the UI believes.

Covered:

* input validation — names, memory footprints, priorities, states, reference bits
  and resource ids;
* application creation — the object the backend returns, duplicate protection,
  and that memory accounting stays correct;
* the operational adapters — priority, state, access, reference bit, and the
  request/release semaphore cycle including queueing and promotion;
* the refusals, which must explain what the backend cannot do rather than fail
  silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.memory_manager.memory_manager import MemoryManager  # noqa: E402
from backend.models.app import App, AppState  # noqa: E402
from backend.resource_manager.resource_manager import ResourceManager  # noqa: E402
from backend.simulation.simulation_state import SimulationState  # noqa: E402
from database.db import DatabaseManager  # noqa: E402
from ui import app_actions as actions  # noqa: E402


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────

@pytest.fixture()
def sim(tmp_path: Path):
    """A live SimulationState over 1000 MB, with the default resources."""
    db = DatabaseManager(str(tmp_path / "apps_test.db"))
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


def _add(sim, app_id, name, priority=2, state=AppState.BACKGROUND, memory=100, ref_bit=0):
    """Registers an app directly through the backend."""
    app = App(
        app_id=app_id,
        name=name,
        priority=priority,
        state=state,
        memory_footprint=memory,
        reference_bit=ref_bit,
    )
    sim.add_app(app)
    return app


# ──────────────────────────────────────────────────────────────
# Validation — pure functions
# ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["", "   ", "\t\n", None])
def test_blank_names_are_rejected(value) -> None:
    with pytest.raises(actions.ValidationError, match="required"):
        actions.clean_name(value)


def test_names_are_trimmed() -> None:
    assert actions.clean_name("  Video Player  ") == "Video Player"


def test_overlong_names_are_rejected() -> None:
    with pytest.raises(actions.ValidationError, match="60 characters"):
        actions.clean_name("x" * (actions.MAX_NAME_LENGTH + 1))


@pytest.mark.parametrize("value", [-1, -100])
def test_negative_memory_is_rejected(value) -> None:
    with pytest.raises(actions.ValidationError, match="negative"):
        actions.clean_memory_footprint(value)


@pytest.mark.parametrize("value", ["abc", None, "", 3.5, True])
def test_non_integer_memory_is_rejected(value) -> None:
    with pytest.raises(actions.ValidationError):
        actions.clean_memory_footprint(value)


def test_zero_memory_is_allowed() -> None:
    assert actions.clean_memory_footprint(0) == 0


@pytest.mark.parametrize("value", [0, 6, -1, 99])
def test_priority_outside_the_documented_band_is_rejected(value) -> None:
    with pytest.raises(actions.ValidationError, match="between 1 and 5"):
        actions.clean_priority(value)


@pytest.mark.parametrize("value", [1, 3, 5])
def test_priority_inside_the_documented_band_is_accepted(value) -> None:
    assert actions.clean_priority(value) == value


@pytest.mark.parametrize("value", ["WAITING", "EVICTED", "running", "", None])
def test_invalid_initial_states_are_rejected(value) -> None:
    with pytest.raises(actions.ValidationError):
        actions.clean_initial_state(value)


def test_initial_states_are_normalised() -> None:
    assert actions.clean_initial_state("foreground") == "FOREGROUND"
    assert actions.clean_initial_state(" Background ") == "BACKGROUND"


@pytest.mark.parametrize("value", ["WAITING", "EVICTED", "NONSENSE"])
def test_only_foreground_and_background_are_switchable(value) -> None:
    with pytest.raises(actions.ValidationError):
        actions.clean_target_state(value)


@pytest.mark.parametrize("value", [0, 1])
def test_reference_bit_accepts_zero_and_one(value) -> None:
    assert actions.clean_reference_bit(value) == value


@pytest.mark.parametrize("value", [2, -1, "x"])
def test_reference_bit_rejects_anything_else(value) -> None:
    with pytest.raises(actions.ValidationError):
        actions.clean_reference_bit(value)


def test_offered_resources_are_exactly_the_four_hardware_resources() -> None:
    assert actions.CANONICAL_RESOURCES == ("Camera", "Microphone", "GPS", "Audio")
    assert actions.available_resources(_quick_sim()) == list(actions.CANONICAL_RESOURCES)


def test_alias_resources_are_not_offered() -> None:
    """MIC/AUDIO are backend aliases for Microphone/Audio and must not appear."""
    offered = actions.available_resources(_quick_sim())
    assert "MIC" not in offered
    assert "AUDIO" not in offered
    assert "DB_LOCK" not in offered


def test_unregistered_resource_is_rejected() -> None:
    with pytest.raises(actions.ValidationError, match="not registered"):
        actions.clean_resource_id("Camera", ["GPS", "Audio"])


def test_resource_outside_the_canonical_set_is_rejected() -> None:
    with pytest.raises(actions.ValidationError, match="Choose one of"):
        actions.clean_resource_id("DB_LOCK", ["DB_LOCK"])


def _quick_sim():
    """A throwaway state used only for resource-listing assertions."""
    return SimulationState(
        memory_manager=MemoryManager(total_memory=100),
        resource_manager=ResourceManager(),
    )


# ──────────────────────────────────────────────────────────────
# Application ids
# ──────────────────────────────────────────────────────────────

def test_app_id_is_derived_from_the_name() -> None:
    assert actions.build_app_id("Video Player", []) == "app_video_player"
    assert actions.build_app_id("Chat App", ["app_maps"]) == "app_chat_app"


def test_app_id_never_collides_with_an_existing_one() -> None:
    taken = ["app_maps", "app_maps_2", "app_maps_3"]
    assert actions.build_app_id("Maps", taken) == "app_maps_4"


def test_app_id_falls_back_for_a_name_with_no_usable_characters() -> None:
    assert actions.build_app_id("!!!", []) == "app_app"


# ──────────────────────────────────────────────────────────────
# Creation
# ──────────────────────────────────────────────────────────────

def test_create_returns_the_application_the_backend_registered(sim) -> None:
    result = actions.create_application(sim, "Video Player", 200, 4, "BACKGROUND")

    assert result.ok, result.message
    app = result.app
    assert app is not None
    assert app.app_id == "app_video_player"
    assert app.name == "Video Player"
    assert app.priority == 4
    assert app.memory_footprint == 200
    assert app.state == AppState.BACKGROUND

    # Registered with both managers, and readable back from the backend.
    assert sim.memory_manager.apps["app_video_player"] is app
    assert sim.resource_manager.apps["app_video_player"] is app
    assert sim.memory_manager.apps["app_video_player"].name == "Video Player"


def test_created_application_is_persisted(sim) -> None:
    actions.create_application(sim, "Video Player", 200, 4, "BACKGROUND")

    stored = sim.db.get_app("app_video_player")
    assert stored is not None
    assert stored.name == "Video Player"
    assert stored.memory_footprint == 200
    assert stored.priority == 4


def test_creation_is_refused_before_touching_the_backend_on_bad_input(sim) -> None:
    result = actions.create_application(sim, "   ", 100, 2, "BACKGROUND")

    assert result.ok is False
    assert "required" in result.message
    assert sim.memory_manager.apps == {}


@pytest.mark.parametrize(
    "memory, priority, state, expected",
    [
        (-5, 2, "BACKGROUND", "negative"),
        (100, 9, "BACKGROUND", "between 1 and 5"),
        (100, 2, "EVICTED", "Initial state"),
    ],
)
def test_creation_rejects_invalid_fields(sim, memory, priority, state, expected) -> None:
    result = actions.create_application(sim, "Bad App", memory, priority, state)

    assert result.ok is False
    assert expected in result.message
    assert sim.memory_manager.apps == {}


def test_creation_is_refused_when_it_would_over_commit_memory(sim) -> None:
    result = actions.create_application(sim, "Huge", 1500, 2, "BACKGROUND")

    assert result.ok is False
    assert "only 1000 MB is free" in result.message
    assert sim.memory_manager.get_used_memory() == 0


def test_two_applications_with_the_same_name_do_not_collide(sim) -> None:
    first = actions.create_application(sim, "Music Player", 100, 2, "BACKGROUND")
    second = actions.create_application(sim, "Music Player", 120, 3, "FOREGROUND")

    assert first.ok and second.ok
    assert first.app.app_id == "app_music_player"
    assert second.app.app_id == "app_music_player_2"
    assert len(sim.memory_manager.apps) == 2
    assert sim.memory_manager.apps["app_music_player"].memory_footprint == 100
    assert sim.memory_manager.apps["app_music_player_2"].memory_footprint == 120


def test_creation_without_a_state_is_refused() -> None:
    result = actions.create_application(None, "Orphan", 10, 2, "BACKGROUND")

    assert result.ok is False
    assert "No simulation state" in result.message


# ──────────────────────────────────────────────────────────────
# Memory accounting
# ──────────────────────────────────────────────────────────────

def test_memory_accounting_tracks_created_applications(sim) -> None:
    assert sim.memory_manager.get_used_memory() == 0
    assert sim.memory_manager.get_free_memory() == 1000

    first = actions.create_application(sim, "One", 250, 2, "BACKGROUND")
    second = actions.create_application(sim, "Two", 150, 2, "BACKGROUND")

    assert first.ok and second.ok
    assert sim.memory_manager.get_used_memory() == 400
    assert sim.memory_manager.get_free_memory() == 600
    assert sim.memory_manager.get_usage_percentage() == pytest.approx(40.0)
    assert sim.memory_manager.get_pressure_level().value == "GREEN"


def test_memory_accounting_counts_only_active_applications(sim) -> None:
    _add(sim, "app_one", "One", memory=300)

    assert sim.memory_manager.get_used_memory() == 300

    # Evicting through the backend frees the footprint the UI reported.
    assert sim.memory_manager.evict_app("app_one") is True
    assert sim.memory_manager.get_used_memory() == 0
    assert sim.memory_manager.get_free_memory() == 1000


def test_evicted_application_frees_memory_for_a_new_one(sim) -> None:
    _add(sim, "app_big", "Big", memory=900)
    assert actions.create_application(sim, "Small", 200, 2, "BACKGROUND").ok is False

    sim.memory_manager.evict_app("app_big")

    result = actions.create_application(sim, "Small", 200, 2, "BACKGROUND")
    assert result.ok is True, result.message
    assert sim.memory_manager.get_used_memory() == 200


# ──────────────────────────────────────────────────────────────
# Priority
# ──────────────────────────────────────────────────────────────

def test_priority_change_updates_the_backend_and_the_log(sim) -> None:
    app = _add(sim, "app_maps", "Maps", priority=2)

    result = actions.change_priority(sim, app.app_id, 5)

    assert result.ok is True, result.message
    assert result.app.priority == 5
    assert sim.memory_manager.apps["app_maps"].priority == 5
    assert sim.db.get_app("app_maps").priority == 5


def test_priority_change_rejects_an_out_of_band_value(sim) -> None:
    app = _add(sim, "app_maps", "Maps", priority=2)

    result = actions.change_priority(sim, app.app_id, 9)

    assert result.ok is False
    assert "between 1 and 5" in result.message
    assert sim.memory_manager.apps["app_maps"].priority == 2


def test_priority_change_reports_an_unknown_application(sim) -> None:
    result = actions.change_priority(sim, "app_ghost", 3)

    assert result.ok is False
    assert "No application" in result.message


def test_priority_change_is_rolled_back_when_the_write_fails(sim) -> None:
    """A failed persistence must not leave memory state ahead of the log."""
    app = _add(sim, "app_maps", "Maps", priority=2)

    class FailingBridge:
        def save_app(self, _app):
            return False

    original = sim.memory_manager.db
    sim.memory_manager.db = FailingBridge()
    try:
        result = actions.change_priority(sim, app.app_id, 5)
    finally:
        sim.memory_manager.db = original

    assert result.ok is False
    assert "rolled back" in result.message
    assert sim.memory_manager.apps["app_maps"].priority == 2


# ──────────────────────────────────────────────────────────────
# State changes
# ──────────────────────────────────────────────────────────────

def test_state_change_moves_between_foreground_and_background(sim) -> None:
    app = _add(sim, "app_maps", "Maps", state=AppState.BACKGROUND)

    result = actions.change_state(sim, app.app_id, "FOREGROUND")

    assert result.ok is True, result.message
    assert sim.memory_manager.apps["app_maps"].state == AppState.FOREGROUND
    assert sim.memory_manager.apps["app_maps"].status.value == "ACTIVE"
    assert sim.db.get_app("app_maps").state == "FOREGROUND"


def test_state_change_is_refused_while_the_app_is_queued(sim) -> None:
    """The semaphore only dequeues on release, so the state must not move."""
    _add(sim, "app_maps", "Maps")
    waiter = _add(sim, "app_fitness", "Fitness Tracker")

    assert sim.request_resource("app_maps", "GPS") is True
    assert sim.request_resource("app_fitness", "GPS") is False
    assert sim.memory_manager.apps["app_fitness"].state == AppState.WAITING

    result = actions.change_state(sim, waiter.app_id, "FOREGROUND")

    assert result.ok is False
    assert "queued for GPS" in result.message
    assert "dequeues on release" in result.message
    assert sim.memory_manager.apps["app_fitness"].state == AppState.WAITING


def test_state_change_is_refused_for_an_evicted_application(sim) -> None:
    app = _add(sim, "app_maps", "Maps")
    sim.memory_manager.evict_app(app.app_id)

    result = actions.change_state(sim, app.app_id, "FOREGROUND")

    assert result.ok is False
    assert "evicted" in result.message


def test_state_change_is_refused_for_an_engine_owned_state(sim) -> None:
    app = _add(sim, "app_maps", "Maps")
    result = actions.change_state(sim, app.app_id, "EVICTED")

    assert result.ok is False
    assert "FOREGROUND or BACKGROUND" in result.message


# ──────────────────────────────────────────────────────────────
# Reference bit
# ──────────────────────────────────────────────────────────────

def test_simulated_access_sets_the_reference_bit_through_touch(sim) -> None:
    app = _add(sim, "app_maps", "Maps", ref_bit=0)

    result = actions.simulate_access(sim, app.app_id)

    assert result.ok is True, result.message
    assert sim.memory_manager.apps["app_maps"].reference_bit == 1
    assert sim.db.get_app("app_maps").reference_bit == 1


def test_reference_bit_can_be_set_explicitly_in_both_directions(sim) -> None:
    app = _add(sim, "app_maps", "Maps", ref_bit=1)

    cleared = actions.set_reference_bit(sim, app.app_id, 0)
    assert cleared.ok is True
    assert sim.memory_manager.apps["app_maps"].reference_bit == 0
    assert sim.db.get_app("app_maps").reference_bit == 0

    raised = actions.set_reference_bit(sim, app.app_id, 1)
    assert raised.ok is True
    assert sim.memory_manager.apps["app_maps"].reference_bit == 1


def test_reference_bit_rejects_values_outside_zero_and_one(sim) -> None:
    app = _add(sim, "app_maps", "Maps", ref_bit=1)

    result = actions.set_reference_bit(sim, app.app_id, 7)

    assert result.ok is False
    assert "0 or 1" in result.message
    assert sim.memory_manager.apps["app_maps"].reference_bit == 1


# ──────────────────────────────────────────────────────────────
# Resources
# ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("resource_id", ["Camera", "Microphone", "GPS", "Audio"])
def test_each_offered_resource_can_be_requested_and_released(sim, resource_id) -> None:
    app = _add(sim, "app_maps", "Maps")

    granted = actions.request_resource(sim, app.app_id, resource_id)
    assert granted.ok is True, granted.message
    assert resource_id in sim.memory_manager.apps["app_maps"].held_resources
    assert sim.resource_manager.get_holder(resource_id) == app.app_id

    released = actions.release_resource(sim, app.app_id, resource_id)
    assert released.ok is True, released.message
    assert resource_id not in sim.memory_manager.apps["app_maps"].held_resources
    assert sim.resource_manager.get_holder(resource_id) is None


def test_requesting_a_held_resource_twice_is_refused(sim) -> None:
    app = _add(sim, "app_maps", "Maps")
    assert actions.request_resource(sim, app.app_id, "GPS").ok is True

    again = actions.request_resource(sim, app.app_id, "GPS")

    assert again.ok is False
    assert "already holds GPS" in again.message


def test_contended_request_is_reported_as_queued(sim) -> None:
    holder = _add(sim, "app_maps", "Maps")
    waiter = _add(sim, "app_fitness", "Fitness Tracker")
    assert actions.request_resource(sim, holder.app_id, "GPS").ok is True

    result = actions.request_resource(sim, waiter.app_id, "GPS")

    assert result.ok is False
    assert "queued" in result.message
    assert result.detail["blocked_by"] == holder.app_id
    assert "GPS" in actions.queued_resources(sim, waiter.app_id)
    assert sim.memory_manager.apps["app_fitness"].state == AppState.WAITING


def test_releasing_promotes_the_next_waiter(sim) -> None:
    holder = _add(sim, "app_maps", "Maps")
    waiter = _add(sim, "app_fitness", "Fitness Tracker")
    assert actions.request_resource(sim, holder.app_id, "GPS").ok is True
    assert actions.request_resource(sim, waiter.app_id, "GPS").ok is False

    result = actions.release_resource(sim, holder.app_id, "GPS")

    assert result.ok is True, result.message
    assert "app_fitness → GPS" in result.message
    assert sim.resource_manager.get_holder("GPS") == waiter.app_id
    assert actions.queued_resources(sim, waiter.app_id) == []
    assert sim.memory_manager.apps["app_fitness"].state == AppState.BACKGROUND


def test_releasing_a_resource_that_is_not_held_is_refused(sim) -> None:
    app = _add(sim, "app_maps", "Maps")

    result = actions.release_resource(sim, app.app_id, "Camera")

    assert result.ok is False
    assert "does not hold Camera" in result.message


def test_an_evicted_application_cannot_take_a_lock(sim) -> None:
    app = _add(sim, "app_maps", "Maps")
    sim.memory_manager.evict_app(app.app_id)

    result = actions.request_resource(sim, app.app_id, "GPS")

    assert result.ok is False
    assert "evicted" in result.message
    assert sim.resource_manager.get_holder("GPS") is None


def test_resource_lock_is_recorded_in_the_database(sim) -> None:
    app = _add(sim, "app_maps", "Maps")

    actions.request_resource(sim, app.app_id, "GPS")

    active = sim.db.resource_locks.get_active_locks()
    assert any(
        lock["app_id"] == app.app_id and lock["resource_id"] == "GPS"
        for lock in active
    )


# ──────────────────────────────────────────────────────────────
# Inspection
# ──────────────────────────────────────────────────────────────

def test_detail_view_reports_held_and_queued_resources(sim) -> None:
    holder = _add(sim, "app_maps", "Maps")
    waiter = _add(sim, "app_fitness", "Fitness Tracker")
    actions.request_resource(sim, holder.app_id, "GPS")
    actions.request_resource(sim, waiter.app_id, "GPS")

    holder_detail = actions.describe_application(sim, holder)
    assert holder_detail["held_resources"] == ["GPS"]
    assert holder_detail["queued_resources"] == []
    assert holder_detail["is_active"] is True

    waiter_detail = actions.describe_application(sim, waiter)
    assert waiter_detail["held_resources"] == []
    assert waiter_detail["queued_resources"] == ["GPS"]

    header = [row[0] for row in waiter_detail["resource_rows"]]
    assert header == ["GPS"]


def test_get_application_returns_the_backend_object(sim) -> None:
    app = _add(sim, "app_maps", "Maps")

    assert actions.get_application(sim, "app_maps") is app
    assert actions.get_application(sim, "app_ghost") is None
    assert actions.get_application(None, "app_maps") is None


def test_operations_without_a_state_are_refused_not_crashed() -> None:
    for call in (
        lambda: actions.change_priority(None, "app_maps", 3),
        lambda: actions.change_state(None, "app_maps", "FOREGROUND"),
        lambda: actions.simulate_access(None, "app_maps"),
        lambda: actions.set_reference_bit(None, "app_maps", 1),
        lambda: actions.request_resource(None, "app_maps", "GPS"),
        lambda: actions.release_resource(None, "app_maps", "GPS"),
    ):
        result = call()
        assert result.ok is False
        assert "No simulation state" in result.message


def test_no_database_still_allows_in_memory_operations(tmp_path) -> None:
    """Persistence is optional: the managers work without a DatabaseManager."""
    memory_manager = MemoryManager(total_memory=1000)
    resource_manager = ResourceManager()
    resource_manager.create_default_resources()
    sim = SimulationState(
        memory_manager=memory_manager, resource_manager=resource_manager
    )

    created = actions.create_application(sim, "No DB", 100, 2, "BACKGROUND")
    assert created.ok is True

    priority = actions.change_priority(sim, created.app.app_id, 5)
    assert priority.ok is True
    assert sim.memory_manager.apps[created.app.app_id].priority == 5

    lock = actions.request_resource(sim, created.app.app_id, "Audio")
    assert lock.ok is True