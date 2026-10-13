"""Smoke tests for the RAAE Streamlit dashboard.

The dashboard is a read-only monitor, so these tests assert two things:

1. It never raises — with nothing attached, with a live simulation state, and
   after a real simulation run whose logs are read back out of SQLite.
2. Every number it shows is the backend's number. The assertions compare
   rendered values against the figures the simulation itself produced, so a
   hard-coded or defaulted metric fails the test rather than passing silently.

The DB-backed scenarios are driven through ``tests/render_driver.py``, which
builds the state and renders the page inside a single script run so the SQLite
connection is created and read on the same thread, as in a live session.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("streamlit", reason="streamlit is required to render the dashboard")

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(ROOT / "app.py")
DRIVER = str(ROOT / "tests" / "render_driver.py")

TIMEOUT = 120

CARD_PATTERN = re.compile(
    r"metric-label\">([^<]+)</div>\s*<div class=\"metric-value[^\"]*\">([^<]*)<"
)
CHIP_PATTERN = re.compile(
    r"chip-label'>([^<]+)</span><span class='chip-value'>(?:<[^>]+>)*([^<]*)"
)


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
    """Keeps the driver's environment scoped to a single test.

    The driver reads these at import time, and a widget click triggers further
    script runs, so the values must stay set for the whole test rather than only
    for its first render.
    """
    previous = {name: os.environ.get(name) for name in ENV_KEYS}
    yield
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _body(at: AppTest) -> str:
    """Returns every markdown block on the page as one string."""
    return " ".join(block.value for block in at.markdown)


def _run_driver(
    mode: str,
    db_path: str = ":memory:",
    result_path: str = "",
) -> AppTest:
    """Renders the dashboard once for the given driver mode.

    The mode is passed through the environment because ``AppTest.from_file``
    exposes no argv hook; the driver reads it in a single script run.
    """
    os.environ.update(
        {
            "RAAE_DASHBOARD_MODE": mode,
            "RAAE_DASHBOARD_DB": db_path,
            "RAAE_DASHBOARD_RESULT": result_path,
            "RAAE_RENDER_PAGE": "dashboard",
        }
    )
    at = AppTest.from_file(DRIVER, default_timeout=TIMEOUT)
    at.run()
    return at


def _seed_run(tmp_path: Path, policy: str, ticks: int):
    """Executes one real simulation into a fresh SQLite file.

    Returns ``(db_path, result_path, result)``. The log tables are written
    exactly once, here; the dashboard run that follows only reads them, so no
    second workload is initialised against the same ledger.
    """
    from backend.simulation_engine.controller import SimulationController, Workload

    db_path = str(tmp_path / f"raae_{policy.lower()}_{ticks}t.db")
    result_path = str(tmp_path / f"raae_{policy.lower()}_{ticks}t.json")

    workload = Workload.deterministic_5_apps()
    workload.db_path = db_path
    workload.total_memory = 1000
    workload.ticks = ticks

    controller = SimulationController(policy=policy, workload=workload)
    result = controller.run()
    controller.close()

    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle)

    return db_path, result_path, result


def _cards(at: AppTest) -> dict:
    """Extracts ``{label: value}`` pairs from the rendered metric cards."""
    values: dict = {}
    for block in at.markdown:
        for label, value in CARD_PATTERN.findall(block.value):
            values[label.strip()] = value.strip()
    return values


def _chips(at: AppTest) -> dict:
    """Extracts ``{label: value}`` pairs from the rendered header chips."""
    values: dict = {}
    for block in at.markdown:
        for label, value in CHIP_PATTERN.findall(block.value):
            values.setdefault(label.strip(), value.strip())
    return values


def _assert_clean(at: AppTest) -> None:
    """Fails the test if the render raised."""
    assert not at.exception, [str(e.value) for e in at.exception]


# ──────────────────────────────────────────────────────────────
# Empty simulation
# ──────────────────────────────────────────────────────────────

def test_empty_simulation_fabricates_nothing() -> None:
    """With nothing attached, the page renders and admits every gap."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    _assert_clean(at)

    body = _body(at)
    assert "RAAE \u2014 Resource-Aware Adaptive App Eviction" in body
    for section in ("Memory monitor", "Application summary", "Recent event log"):
        assert section in body

    chips = _chips(at)
    assert chips["Simulation status"] == "IDLE"
    assert chips["Simulation tick"] == "Unavailable"
    assert chips["Eviction policy"] == "RAAE"

    cards = _cards(at)
    for label in (
        "Total RAM",
        "Used RAM",
        "Available RAM",
        "Memory Utilization",
        "Active Applications",
        "Total Evictions",
        "Resource Conflicts",
    ):
        assert cards[label] == "Unavailable", f"{label} was fabricated: {cards[label]!r}"


def test_every_navigation_page_renders() -> None:
    """Each sidebar page loads without raising."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    _assert_clean(at)

    for page in ("Applications", "Simulation", "Resources", "Analytics", "Overview"):
        at.sidebar.radio[0].set_value(page).run()
        _assert_clean(at)


def test_quick_start_bootstraps_a_live_state() -> None:
    """The Quick Start button attaches a real state and reruns the page."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    _assert_clean(at)

    [button] = [b for b in at.sidebar.button if "Initialise" in b.label]
    button.click().run()
    _assert_clean(at)

    assert at.session_state["sim_state"] is not None
    assert at.session_state["db"] is not None
    assert _chips(at)["Simulation status"] == "READY"
    assert "no simulation executed yet" in _body(at)


# ──────────────────────────────────────────────────────────────
# Initialised simulation (live state, no run)
# ──────────────────────────────────────────────────────────────

def test_live_simulation_state_shows_backend_figures() -> None:
    """Metric cards must carry the MemoryManager's own numbers."""
    at = _run_driver("live")
    _assert_clean(at)

    cards = _cards(at)
    assert cards["Total RAM"] == "1000 MB"
    assert cards["Used RAM"] == "1000 MB"
    assert cards["Available RAM"] == "0 MB"
    assert cards["Memory Utilization"] == "100.0%"
    assert cards["Active Applications"] == "5"

    # State counts come from the app registry, not from a fixed set.
    assert cards["Foreground"] == "1"
    assert cards["Background"] == "3"
    assert cards["Waiting"] == "1"
    assert cards["Evicted"] == "0"

    body = _body(at)
    chips = _chips(at)
    assert chips["Simulation status"] == "READY"
    assert "no simulation executed yet" in body

    # Live pressure and the backend's own under-pressure verdict.
    assert ">RED<" in body
    assert "is_under_pressure() \u2192 True" in body


# ──────────────────────────────────────────────────────────────
# Completed simulation (metrics read back from SQLite)
# ──────────────────────────────────────────────────────────────

def test_dashboard_reads_a_completed_run_back_from_the_database(tmp_path: Path) -> None:
    """After a real run, counters, apps and events come from the log tables."""
    db_path, result_path, expected = _seed_run(tmp_path, "RAAE", ticks=3)

    at = _run_driver("run", db_path, result_path)
    _assert_clean(at)

    cards = _cards(at)
    assert cards["Total RAM"] == "1000 MB"
    assert cards["Total Evictions"] == str(expected.eviction_count)
    assert cards["Resource Conflicts"] == str(expected.resource_conflicts)

    body = _body(at)
    chips = _chips(at)

    # Header facts come from the run that actually happened.
    assert chips["Simulation status"] == "COMPLETED"
    assert chips["Simulation tick"] == "3"
    assert "3 tick(s) executed" in chips["Status detail"]
    assert chips["Eviction policy"] == "RAAE"

    # The event log is populated from the three log tables.
    assert "EvictionLog" in body
    assert "ConflictLog" in body
    assert "MemoryEvents" in body

    # A chart was drawn rather than an empty frame.
    assert at.get("plotly_chart"), "no utilization chart was rendered"
    assert "3 tick(s)" in body


def test_event_log_lists_the_records_the_run_wrote(tmp_path: Path) -> None:
    """The rendered event log carries the required columns and real rows."""
    from database.db import DatabaseManager

    db_path, result_path, _ = _seed_run(tmp_path, "BASELINE_CLOCK", ticks=2)

    db = DatabaseManager(db_path)
    logged = db.get_recent_events(limit=200)
    db.close()
    assert logged, "the run wrote no events to inspect"

    at = _run_driver("run", db_path, result_path)
    _assert_clean(at)
    body = _body(at)

    # The header row proves the required columns are present.
    for column in (
        "Timestamp",
        "Event type",
        "Application",
        "Resource",
        "Decision",
        "Explanation",
    ):
        assert column in body

    # An app name from the Apps table reaches the Application column, and the
    # logged reason reaches the Explanation column.
    assert "Maps" in body
    assert "Baseline Clock eviction without resource-aware check" in body


def test_empty_database_distinguishes_zero_from_missing(tmp_path: Path) -> None:
    """A connected but empty database reports real zeros, not 'Unavailable'."""
    from database.db import DatabaseManager

    db_path = str(tmp_path / "raae_empty_test.db")
    DatabaseManager(db_path).close()  # schema created, no log rows written

    at = _run_driver("dbonly", db_path)
    _assert_clean(at)

    body = _body(at)
    cards = _cards(at)

    # No rows exist, so zero is the truthful count...
    assert cards["Total Evictions"] == "0"
    assert cards["Resource Conflicts"] == "0"

    # ...but nothing claimed to have happened, so the dependent panels say so.
    assert "No events recorded" in body
    assert "No simulation ticks have been executed" in body

    # Without a live state or a sample there is no RAM figure to report.
    assert cards["Total RAM"] == "Unavailable"