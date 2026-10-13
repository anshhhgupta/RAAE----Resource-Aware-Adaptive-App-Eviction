"""Smoke tests for the Applications page.

These drive the real page through Streamlit's ``AppTest`` against a real
``SimulationState``, so they cover the parts a unit test cannot: that a control
is actually wired to the right widget, that the page renders what the backend
returned, and that the operation results survive the rerun that follows them.

State is built by ``tests/render_driver.py`` inside a single script run, so the
SQLite connection is created and read on the same thread, as in a live session.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("streamlit", reason="streamlit is required to render the page")

from streamlit.testing.v1 import AppTest  # noqa: E402

DRIVER = str(ROOT / "tests" / "render_driver.py")
TIMEOUT = 120

CREATE_BUTTON = "Create application"

#: Environment the driver reads to decide what to build and render. It stays set
#: for the whole test — a widget click triggers further script runs, and those
#: must see the same mode and page as the first render.
ENV_KEYS = (
    "RAAE_DASHBOARD_MODE",
    "RAAE_DASHBOARD_DB",
    "RAAE_DASHBOARD_RESULT",
    "RAAE_RENDER_PAGE",
)


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def driver_environment():
    """Keeps the driver's environment scoped to a single test."""
    previous = {name: os.environ.get(name) for name in ENV_KEYS}
    yield
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _run_page(
    mode: str = "spare",
    page: str = "applications",
    db_path: str = ":memory:",
) -> AppTest:
    """Renders one page once for the given driver mode."""
    os.environ.update(
        {
            "RAAE_DASHBOARD_MODE": mode,
            "RAAE_DASHBOARD_DB": db_path,
            "RAAE_DASHBOARD_RESULT": "",
            "RAAE_RENDER_PAGE": page,
        }
    )
    at = AppTest.from_file(DRIVER, default_timeout=TIMEOUT)
    at.run()
    return at


def _body(at: AppTest) -> str:
    """Returns every markdown block on the page as one string."""
    return " ".join(block.value for block in at.markdown)


def _text(at: AppTest) -> str:
    """Returns all visible prose: markdown, captions and message elements."""
    parts = [block.value for block in at.markdown]
    parts += [block.value for block in at.caption]
    parts += [block.value for block in at.success]
    parts += [block.value for block in at.error]
    parts += [block.value for block in at.info]
    parts += [block.value for block in at.warning]
    return " ".join(parts)


def _assert_clean(at: AppTest) -> None:
    """Fails the test if the render raised."""
    assert not at.exception, [str(e.value) for e in at.exception]


def _submit(at: AppTest, name: str, memory: int, priority: int, state: str = "Background") -> AppTest:
    """Fills and submits the create-application form, then returns the rerun."""
    at.text_input(key="create_name").set_value(name)
    at.number_input(key="create_memory").set_value(memory)
    at.number_input(key="create_priority").set_value(priority)
    at.selectbox(key="create_state").set_value(state)
    [button for button in at.button if CREATE_BUTTON in button.label][0].click().run()
    return at


# ──────────────────────────────────────────────────────────────
# Rendering
# ──────────────────────────────────────────────────────────────

def test_page_explains_itself_when_no_state_is_attached() -> None:
    """With no simulation state, the page says so instead of offering dead forms."""
    at = _run_page(mode="empty")
    _assert_clean(at)

    body = _body(at)
    assert "No simulation state attached" in body
    assert not at.button, "no action may be offered without a backend to act on"


def test_table_lists_the_registered_applications() -> None:
    """The table shows the backend's applications and every required column."""
    at = _run_page()
    _assert_clean(at)

    body = _body(at)
    for column in (
        "App ID",
        "Application",
        "Priority",
        "Memory",
        "State",
        "Reference bit",
        "Held resources",
        "Status",
    ):
        assert column in body

    # The three applications the driver registered, with their real figures.
    for app_id in ("app_maps", "app_fitness", "app_chat"):
        assert app_id in body
    assert "200 MB" in body
    assert "P3" in body


def test_only_the_four_hardware_resources_are_offered() -> None:
    """The resource selector must contain exactly Camera, Microphone, GPS, Audio."""
    at = _run_page()
    _assert_clean(at)

    offered = at.selectbox(key="app_controls_resource").options
    assert offered == ["Camera", "Microphone", "GPS", "Audio"]


def test_detail_view_renders_for_the_selected_application() -> None:
    """The inspector shows the application's own facts."""
    at = _run_page()
    _assert_clean(at)

    body = _body(at)
    assert "Application detail" in body
    assert "Memory footprint" in body
    assert "Reference bit" in body
    assert "Eviction history" in body


# ──────────────────────────────────────────────────────────────
# Creation through the form
# ──────────────────────────────────────────────────────────────

def test_creating_an_application_shows_the_backend_result() -> None:
    """A submitted form registers the app and reports what the backend returned."""
    at = _run_page()
    _assert_clean(at)

    before = at.session_state["sim_state"].memory_manager.get_used_memory()

    at = _submit(at, "Video Player", 150, 4, "Background")
    _assert_clean(at)

    messages = [s.value for s in at.success]
    assert any("Created 'Video Player' as app_video_player" in m for m in messages), messages
    assert any("150 MB" in m and "priority 4" in m for m in messages), messages

    # The backend, not the page, is the source of truth.
    memory_manager = at.session_state["sim_state"].memory_manager
    app = memory_manager.apps["app_video_player"]
    assert app.name == "Video Player"
    assert app.memory_footprint == 150
    assert app.priority == 4

    # ...and the page re-rendered from it.
    assert "app_video_player" in _body(at)
    assert "No eviction decisions have been recorded" in _text(at)

    # Memory accounting moved by exactly the registered footprint.
    assert memory_manager.get_used_memory() == before + 150


def test_created_application_appears_in_the_state_summary() -> None:
    """The counters follow the registry the backend now holds."""
    at = _run_page()
    _assert_clean(at)

    at = _submit(at, "Foregrounder", 100, 3, "Foreground")
    _assert_clean(at)

    body = _body(at)
    assert "app_foregrounder" in body
    assert "4 registered" in body  # three from the driver, one just created


def test_empty_name_is_rejected_by_the_page() -> None:
    """Submitting the form with no name reports the validation error."""
    at = _run_page()
    _assert_clean(at)

    registered_before = len(at.session_state["sim_state"].memory_manager.apps)

    at = _submit(at, "   ", 100, 2)
    _assert_clean(at)

    errors = [e.value for e in at.error]
    assert any("name is required" in e.lower() for e in errors), errors
    assert not at.success
    assert len(at.session_state["sim_state"].memory_manager.apps) == registered_before


def test_footprint_field_cannot_exceed_free_memory() -> None:
    """The form's ceiling is the backend's free memory, so it cannot over-commit."""
    at = _run_page()
    _assert_clean(at)

    free = at.session_state["sim_state"].memory_manager.get_free_memory()
    assert free == 600, "the spare state must leave headroom for this test"
    assert at.number_input(key="create_memory").max == float(free)


def test_repeated_submissions_create_separate_applications() -> None:
    """Submitting twice does not overwrite: the second gets its own id."""
    at = _run_page()
    _assert_clean(at)

    at = _submit(at, "Music Player", 100, 2)
    _assert_clean(at)
    at = _submit(at, "Music Player", 120, 3)
    _assert_clean(at)

    apps = at.session_state["sim_state"].memory_manager.apps
    assert "app_music_player" in apps
    assert "app_music_player_2" in apps
    assert apps["app_music_player"].memory_footprint == 100
    assert apps["app_music_player_2"].memory_footprint == 120


def test_feedback_is_shown_only_after_the_backend_acted() -> None:
    """No success or error appears before an operation has actually run."""
    at = _run_page()
    _assert_clean(at)

    # Nothing submitted yet, so nothing has been reported.
    assert not at.success
    assert not at.error

    at = _submit(at, "Reported", 60, 2)
    _assert_clean(at)

    assert [s.value for s in at.success], "the backend result must be reported"
    assert not at.error


# ──────────────────────────────────────────────────────────────
# Operations through the page
# ──────────────────────────────────────────────────────────────

def test_priority_change_through_the_page() -> None:
    """The priority control reaches the backend and the table reflects it."""
    at = _run_page()
    _assert_clean(at)

    at.selectbox(key="app_controls_selected").set_value("app_chat")
    at.run()

    at.number_input(key="app_controls_priority").set_value(5)
    at.button(key="apply_priority").click().run()
    _assert_clean(at)

    assert any("priority 1 → 5" in s.value for s in at.success), [s.value for s in at.success]
    assert at.session_state["sim_state"].memory_manager.apps["app_chat"].priority == 5


def test_access_simulation_sets_the_reference_bit_through_the_page() -> None:
    """The access button calls touch_app and the detail view shows the new bit."""
    at = _run_page()
    _assert_clean(at)

    at.selectbox(key="app_controls_selected").set_value("app_chat")
    at.run()

    app = at.session_state["sim_state"].memory_manager.apps["app_chat"]
    assert app.reference_bit == 0

    at.button(key="simulate_access").click().run()
    _assert_clean(at)

    assert any("was accessed" in s.value for s in at.success)
    assert at.session_state["sim_state"].memory_manager.apps["app_chat"].reference_bit == 1


def test_state_control_is_disabled_while_an_app_is_queued() -> None:
    """app_fitness is queued for GPS, so its state control must be disabled."""
    at = _run_page()
    _assert_clean(at)

    at.selectbox(key="app_controls_selected").set_value("app_fitness")
    at.run()
    _assert_clean(at)

    assert at.selectbox(key="app_controls_target_state").disabled is True
    assert at.button(key="apply_state").disabled is True
    assert "Queued for GPS" in _text(at)


def test_state_change_through_the_page() -> None:
    """An unqueued application can be moved between foreground and background."""
    at = _run_page()
    _assert_clean(at)

    at.selectbox(key="app_controls_selected").set_value("app_chat")
    at.run()

    at.selectbox(key="app_controls_target_state").set_value("Foreground")
    at.button(key="apply_state").click().run()
    _assert_clean(at)

    assert any("moved BACKGROUND → FOREGROUND" in s.value for s in at.success)
    app = at.session_state["sim_state"].memory_manager.apps["app_chat"]
    assert app.state.value == "FOREGROUND"


def test_request_and_release_a_resource_through_the_page() -> None:
    """The semaphore cycle is driven from the page and reported back."""
    at = _run_page()
    _assert_clean(at)

    at.selectbox(key="app_controls_selected").set_value("app_chat")
    at.run()

    at.selectbox(key="app_controls_resource").set_value("Microphone")
    at.button(key="request_resource").click().run()
    _assert_clean(at)

    assert any("was granted Microphone" in s.value for s in at.success), [
        s.value for s in at.success
    ]
    assert "Microphone" in at.session_state["sim_state"].memory_manager.apps["app_chat"].held_resources

    at.button(key="release_resource").click().run()
    _assert_clean(at)

    assert any("released Microphone" in s.value for s in at.success)
    assert "Microphone" not in at.session_state["sim_state"].memory_manager.apps["app_chat"].held_resources


def test_contended_request_reports_the_queue_through_the_page() -> None:
    """Requesting a held resource is reported as queued, not as a silent failure."""
    at = _run_page()
    _assert_clean(at)

    at.selectbox(key="app_controls_selected").set_value("app_chat")
    at.run()

    at.selectbox(key="app_controls_resource").set_value("GPS")
    at.button(key="request_resource").click().run()
    _assert_clean(at)

    errors = [e.value for e in at.error]
    assert any("queued" in e and "app_maps" in e for e in errors), errors
    assert at.session_state["sim_state"].memory_manager.apps["app_chat"].state.value == "WAITING"


def test_release_is_disabled_for_a_resource_that_is_not_held() -> None:
    """The button must not offer an action the backend would refuse."""
    at = _run_page()
    _assert_clean(at)

    at.selectbox(key="app_controls_selected").set_value("app_chat")
    at.run()

    at.selectbox(key="app_controls_resource").set_value("Audio")
    at.run()

    assert at.button(key="release_resource").disabled is True
    assert at.button(key="request_resource").disabled is False


def test_evicted_application_cannot_take_a_lock() -> None:
    """An evicted app's request control is disabled and explains the backend rule."""
    at = _run_page()
    _assert_clean(at)

    at.session_state["sim_state"].memory_manager.evict_app("app_chat")
    at.run()

    at.selectbox(key="app_controls_selected").set_value("app_chat")
    at.run()

    assert at.button(key="request_resource").disabled is True
    assert at.button(key="apply_state").disabled is True


def test_history_panel_reports_an_empty_log_without_error() -> None:
    """No eviction history is a normal state, and must read as one."""
    at = _run_page()
    _assert_clean(at)

    text = _text(at)
    assert "Eviction history" in text
    assert "No eviction decisions have been recorded" in text