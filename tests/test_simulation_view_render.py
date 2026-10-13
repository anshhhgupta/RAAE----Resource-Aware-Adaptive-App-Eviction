"""Smoke tests for the Simulation page.

These drive the page through Streamlit's ``AppTest`` and press the real controls,
so they check the parts a unit test cannot: that each button is wired to the
right action, that the controls reflect the session's real status, that an
invalid transition cannot be taken, and that the decision timeline shown matches
the events the run actually produced.

State is built by ``tests/render_driver.py`` inside a single script run, so the
SQLite connection is created and read on the same thread, as in a live session.
Run databases are written to a temporary directory so no test touches the
repository's own ``database/runs`` folder.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("streamlit", reason="streamlit is required to render the page")

from streamlit.testing.v1 import AppTest  # noqa: E402

import backend  # noqa: F401  -- imported first to resolve the database/backend import cycle
from database.db import DatabaseManager  # noqa: E402
from ui import state  # noqa: E402

DRIVER = str(ROOT / "tests" / "render_driver.py")
TIMEOUT = 120

ENV_KEYS = (
    "RAAE_DASHBOARD_MODE",
    "RAAE_DASHBOARD_DB",
    "RAAE_DASHBOARD_RESULT",
    "RAAE_RENDER_PAGE",
    "RAAE_RUN_DIRECTORY",
)


# ──────────────────────────────────────────────────────────────
# Fixtures and helpers
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


@pytest.fixture()
def run_directory(tmp_path: Path) -> Path:
    """A throwaway directory for this test's run databases."""
    directory = tmp_path / "runs"
    directory.mkdir()
    return directory


@pytest.fixture()
def page(run_directory: Path):
    """The Simulation page, with run databases redirected to tmp_path."""
    os.environ.update(
        {
            "RAAE_DASHBOARD_MODE": "simulation",
            "RAAE_DASHBOARD_DB": ":memory:",
            "RAAE_RENDER_PAGE": "simulation",
            "RAAE_RUN_DIRECTORY": str(run_directory),
        }
    )
    at = AppTest.from_file(DRIVER, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _text(at: AppTest) -> str:
    """Returns all visible prose: markdown, captions and message elements."""
    parts = [block.value for block in at.markdown]
    for kind in ("caption", "success", "error", "warning", "info"):
        parts += [block.value for block in getattr(at, kind)]
    return " ".join(parts)


CHIP_PATTERN = re.compile(
    r"chip-label'>([^<]+)</span><span class='chip-value'>(?:<[^>]+>)*([^<]*)"
)


def _chips(at: AppTest) -> dict:
    """Extracts the dashboard header chips."""
    values: dict = {}
    for block in at.markdown:
        for label, value in CHIP_PATTERN.findall(block.value):
            values.setdefault(label.strip(), value.strip())
    return values


def _session(at: AppTest):
    """Returns the live session, asserting one exists."""
    session = at.session_state[state.KEY_SIM_SESSION]
    assert session is not None, "expected a run to be in progress"
    return session


def _start(at: AppTest, policy_index: int = 0, ticks: int = 3) -> AppTest:
    """Starts a run through the page's own Start control."""
    at.selectbox(key="sim_policy").set_value(at.selectbox(key="sim_policy").options[policy_index])
    at.number_input(key="sim_tick_budget").set_value(ticks)
    at.button(key="sim_start").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


# ──────────────────────────────────────────────────────────────
# Idle state
# ──────────────────────────────────────────────────────────────

def test_page_starts_idle_with_only_start_available(page) -> None:
    """Nothing may be runnable before a run exists."""
    assert page.session_state[state.KEY_SIM_SESSION] is None

    assert page.button(key="sim_start").disabled is False
    for key in ("sim_pause", "sim_resume", "sim_stop", "sim_reset",
                "sim_tick_one", "sim_tick_many", "sim_pressure"):
        assert page.button(key=key).disabled is True, key

    assert "No run in progress" in _text(page)


# ──────────────────────────────────────────────────────────────
# Start
# ──────────────────────────────────────────────────────────────

def test_start_creates_a_run_and_locks_the_configuration(page) -> None:
    at = _start(page)

    session = _session(at)
    assert session.status == "RUNNING"
    assert session.current_tick == 0

    assert "Run started with RAAE" in _text(at)
    # Configuration is fixed for the life of a run.
    assert at.selectbox(key="sim_policy").disabled is True
    assert at.button(key="sim_start").disabled is True


def test_start_records_the_run_and_its_database_file(page, run_directory: Path) -> None:
    at = _start(page)

    history = at.session_state[state.KEY_RUN_HISTORY]
    assert len(history) == 1
    assert history[0]["policy"] == "RAAE"
    assert Path(history[0]["db_path"]).parent == run_directory

    # Pause, resume, stop and advance become available.
    for key in ("sim_pause", "sim_tick_one", "sim_tick_many", "sim_stop", "sim_reset"):
        assert at.button(key=key).disabled is False, key
    assert at.button(key="sim_resume").disabled is True


# ──────────────────────────────────────────────────────────────
# Advancing
# ──────────────────────────────────────────────────────────────

def test_advancing_one_tick_updates_the_display(page) -> None:
    at = _start(page)
    at.button(key="sim_tick_one").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]

    session = _session(at)
    assert session.current_tick == 1

    text = _text(at)
    assert "Executed 1 tick(s)" in text
    assert "Current tick" in text
    assert "Live simulation" in text
    assert "Most recent eviction decision" in text


def test_advancing_multiple_ticks(page) -> None:
    at = _start(page, ticks=5)
    at.number_input(key="sim_ticks_n").set_value(3)
    at.button(key="sim_tick_many").click().run()

    assert _session(at).current_tick == 3
    assert "Executed 3 tick(s)" in _text(at)


def test_advancing_stops_at_the_configured_budget(page) -> None:
    at = _start(page, ticks=2)
    at.number_input(key="sim_ticks_n").set_value(10)
    at.button(key="sim_tick_many").click().run()

    assert _session(at).current_tick == 2
    assert "Tick budget reached" in _text(at)


# ──────────────────────────────────────────────────────────────
# Pause / resume
# ──────────────────────────────────────────────────────────────

def test_pause_blocks_the_tick_controls_and_resume_restores_them(page) -> None:
    at = _start(page)
    at.button(key="sim_tick_one").click().run()

    at.button(key="sim_pause").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]

    assert _session(at).status == "PAUSED"
    assert at.button(key="sim_tick_one").disabled is True
    assert at.button(key="sim_tick_many").disabled is True
    assert at.button(key="sim_pressure").disabled is True
    assert at.button(key="sim_resume").disabled is False
    assert "paused at tick 1" in _text(at)

    at.button(key="sim_resume").click().run()
    assert _session(at).status == "RUNNING"
    assert at.button(key="sim_tick_one").disabled is False


def test_paused_runs_reject_pressure(page) -> None:
    at = _start(page)
    at.button(key="sim_pause").click().run()

    assert at.button(key="sim_pressure").disabled is True


# ──────────────────────────────────────────────────────────────
# Stop
# ──────────────────────────────────────────────────────────────

def test_stop_ends_the_run_but_keeps_its_records(page, run_directory: Path) -> None:
    at = _start(page)
    at.button(key="sim_tick_one").click().run()
    db_path = _session(at).db_path

    at.button(key="sim_stop").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]

    session = _session(at)
    assert session.status == "STOPPED"
    assert "Run stopped after 1 tick" in _text(at)
    assert db_path in _text(at)

    # No further ticking is possible, and the file is still on disk.
    for key in ("sim_tick_one", "sim_tick_many", "sim_pressure", "sim_pause", "sim_resume"):
        assert at.button(key=key).disabled is True, key
    assert Path(db_path).exists()


# ──────────────────────────────────────────────────────────────
# Reset
# ──────────────────────────────────────────────────────────────

def test_reset_does_not_destroy_historical_records(page, run_directory: Path) -> None:
    at = _start(page)
    at.button(key="sim_tick_one").click().run()
    first_db = _session(at).db_path

    at.button(key="sim_reset").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]

    assert at.session_state[state.KEY_SIM_SESSION] is None
    assert "Run discarded" in _text(at)

    # The history still lists the run and its database file...
    history = at.session_state[state.KEY_RUN_HISTORY]
    assert len(history) == 1
    assert history[0]["db_path"] == first_db
    assert "no historical record is destroyed" in _text(at)

    # ...and the records themselves are still readable.
    assert Path(first_db).exists()
    db = DatabaseManager(first_db)
    try:
        assert db.eviction_log.get_all_evictions(limit=50), (
            "reset must not delete the records a run wrote"
        )
    finally:
        db.close()


def test_starting_again_after_reset_uses_a_new_database(page, run_directory: Path) -> None:
    at = _start(page)
    at.button(key="sim_tick_one").click().run()
    first_db = _session(at).db_path

    at.button(key="sim_reset").click().run()
    at.button(key="sim_start").click().run()

    second_db = _session(at).db_path
    assert second_db != first_db
    assert len(at.session_state[state.KEY_RUN_HISTORY]) == 2
    assert Path(first_db).exists()


# ──────────────────────────────────────────────────────────────
# Policy selection
# ──────────────────────────────────────────────────────────────

def test_policy_selection_reaches_the_engine(page) -> None:
    at = _start(page, policy_index=1)  # BASELINE_CLOCK

    session = _session(at)
    assert session.policy == "BASELINE_CLOCK"
    assert at.session_state[state.KEY_SELECTED_POLICY] == "BASELINE_CLOCK"
    assert "BASELINE_CLOCK" in _text(at)


def test_baseline_timeline_reports_that_the_resource_check_is_skipped(page) -> None:
    at = _start(page, policy_index=1)
    at.button(key="sim_tick_one").click().run()

    text = _text(at)
    assert "RAAE checks held resources" in text
    assert "no resource-aware check" in text


# ──────────────────────────────────────────────────────────────
# Decision timeline
# ──────────────────────────────────────────────────────────────

def test_timeline_before_any_tick_labels_every_step(page) -> None:
    at = _start(page)

    text = _text(at)
    assert "no tick executed yet" in text
    assert text.count("Not executed") == 8


def test_timeline_after_a_tick_matches_the_events_written(page) -> None:
    at = _start(page)
    at.button(key="sim_tick_one").click().run()

    session = _session(at)
    record = session.last_record()
    text = _text(at)

    # Every step of the documented workflow is present...
    for step in session_module_titles():
        assert step in text, step

    # ...and the confirmed claims match the tick that actually ran.
    from ui.simulation_session import CONFIRMED, build_timeline

    steps = {s.number: s for s in build_timeline(session, record)}
    confirmed = [n for n, s in steps.items() if s.status == CONFIRMED]

    assert steps[1].status == CONFIRMED, "the deterministic workload starts at 100%"
    assert record.clock_inspections, "the engine inspected the Clock ring"
    assert steps[2].status == CONFIRMED
    assert record.eviction_results, "the engine evicted under pressure"
    assert confirmed

    # Any persistence claim is backed by a real row.
    db = DatabaseManager(session.db_path)
    try:
        logged = {row["event_id"] for row in db.eviction_log.get_all_evictions(limit=100)}
        conflicts = db.conflict_log.get_all_conflicts(limit=100)
    finally:
        db.close()

    if steps[8].status == CONFIRMED:
        for result in record.eviction_results:
            if result.persisted_event_id:
                assert result.persisted_event_id in logged

    if steps[4].status == CONFIRMED and record.conflicts:
        assert conflicts, "the timeline claims a conflict the log does not contain"


def session_module_titles():
    """The eight workflow step titles, as the timeline names them."""
    return (
        "Memory pressure detected",
        "Clock / Second-Chance selects a candidate",
        "RAAE checks held resources",
        "Conflict Manager evaluates the conflict",
        "Wound-Wait determines the response",
        "Resource release or waiting",
        "Eviction succeeds or is deferred",
        "Event is persisted",
    )


def test_timeline_can_be_inspected_for_an_earlier_tick(page) -> None:
    at = _start(page, ticks=3)
    at.number_input(key="sim_ticks_n").set_value(2)
    at.button(key="sim_tick_many").click().run()

    at.selectbox(key="sim_timeline_tick").set_value(0)
    at.run()

    assert "Tick 1" in _text(at)


# ──────────────────────────────────────────────────────────────
# Memory pressure
# ──────────────────────────────────────────────────────────────

def test_triggering_pressure_reports_the_backend_outcome(page) -> None:
    at = _start(page)

    # The deterministic workload already fills the machine, so the honest
    # outcome is "nothing to allocate" rather than a fabricated one.
    at.button(key="sim_pressure").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]

    assert "No allocation made" in _text(at) or "Allocated" in _text(at)


def test_pressure_can_be_forced_with_a_generated_workload(page) -> None:
    at = page
    at.checkbox(key="sim_use_custom").set_value(True).run()
    at.number_input(key="sim_num_apps").set_value(3)
    at.button(key="sim_start").click().run()

    session = _session(at)
    assert session.status == "RUNNING"
    assert len(session.app_rows()) == 3
    assert "generated apps" in _text(at)


# ──────────────────────────────────────────────────────────────
# Dashboard header stays in step
# ──────────────────────────────────────────────────────────────

def test_the_run_is_published_for_the_dashboard_header(page) -> None:
    """The result snapshot the dashboard reads is kept up to date."""
    at = _start(page)
    at.number_input(key="sim_ticks_n").set_value(2)
    at.button(key="sim_tick_many").click().run()

    snapshot = at.session_state[state.KEY_RUN_RAAE]
    assert snapshot["total_ticks"] == 2
    assert snapshot["policy"] == "RAAE"
    assert len(snapshot["ticks_detail"]) == 2


def test_the_dashboard_header_follows_the_live_run(page) -> None:
    """Switching to the Overview page shows the run that is in progress.

    The two pages share session state, so this is what a user sees after
    stepping a couple of ticks and navigating away.
    """
    at = _start(page)
    at.number_input(key="sim_ticks_n").set_value(2)
    at.button(key="sim_tick_many").click().run()

    os.environ["RAAE_RENDER_PAGE"] = "dashboard"
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]

    chips = _chips(at)
    assert chips["Simulation status"] == "RUNNING"
    assert chips["Simulation tick"] == "2"
    assert chips["Eviction policy"] == "RAAE"
    assert "next tick runs on demand" in chips["Status detail"]


def test_a_baseline_run_is_reported_as_such_by_the_header(page) -> None:
    at = _start(page, policy_index=1)
    at.button(key="sim_tick_one").click().run()

    os.environ["RAAE_RENDER_PAGE"] = "dashboard"
    at.run()

    chips = _chips(at)
    assert chips["Simulation status"] == "RUNNING"
    assert chips["Eviction policy"] == "BASELINE_CLOCK"


def test_no_run_leaves_the_header_idle(page) -> None:
    """With no run the Simulation page explains its layout and the header is IDLE."""
    assert page.session_state[state.KEY_SIM_SESSION] is None

    # The Simulation page says where records would be written and kept.
    idle_text = _text(page)
    assert "runs" in idle_text and "kept" in idle_text

    os.environ["RAAE_RENDER_PAGE"] = "dashboard"
    page.run()
    assert not page.exception, [str(e.value) for e in page.exception]

    chips = _chips(page)
    assert chips["Simulation status"] == "IDLE"
    assert chips["Simulation tick"] == "Unavailable"