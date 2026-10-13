"""Render driver used by the Streamlit page smoke tests.

Each mode builds real backend objects and renders a page inside one Streamlit
script run. That matters: the SQLite connection is created and read on the same
thread here, exactly as in a live Streamlit session, so the database-backed code
paths are genuinely exercised rather than being skipped by a cross-thread error
from the test harness.

``RAAE_DASHBOARD_MODE`` (or argv[1]) selects the state to build:

``empty``
    Nothing attached; every metric must report itself unavailable.
``live``
    A live SimulationState with apps and contended resources, built here. Its
    five applications fill the 1000 MB system.
``spare``
    A live SimulationState with 400 MB used of 1000 MB, so an application can
    still be created. GPS is contended, so one app sits in the waiting queue.
``run``
    An already-populated database plus the JSON result of a run the test
    executed. The driver only reads, so the log tables are written exactly once.
``dbonly``
    A database holding the schema but no rows, and no live state.

``RAAE_RENDER_PAGE`` selects which page module is rendered:

``dashboard`` (default)
    ``ui.dashboard`` — the Overview page.
``applications``
    ``ui.app_controls`` — the Applications page.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Environment variables are how the test harness selects a mode, because
# AppTest.from_file() exposes no argv; argv is honoured as well so the script can
# also be driven by hand with `streamlit run`.
MODE = os.environ.get("RAAE_DASHBOARD_MODE") or (
    sys.argv[1] if len(sys.argv) > 1 else "empty"
)
DB_PATH = os.environ.get("RAAE_DASHBOARD_DB") or (
    sys.argv[2] if len(sys.argv) > 2 else ":memory:"
)
RESULT_PATH = os.environ.get("RAAE_DASHBOARD_RESULT", "")
PAGE = os.environ.get("RAAE_RENDER_PAGE", "dashboard")

from ui import configure_page  # noqa: E402

configure_page()

import streamlit as st  # noqa: E402

from ui import analytics_view, app_controls, dashboard, resource_view, simulation_view, state  # noqa: E402

state.initialise()

# The Simulation page writes each run's log under a module-level directory.
# Redirecting it here keeps test runs out of the repository's database folder.
if os.environ.get("RAAE_RUN_DIRECTORY"):
    simulation_view.RUN_DIRECTORY = Path(os.environ["RAAE_RUN_DIRECTORY"])

sim_state = None
db = None


def _build_live_state(database: Any):
    """Creates a live SimulationState with the deterministic 5-app workload."""
    from backend.memory_manager.memory_manager import MemoryManager
    from backend.models.app import App, AppState
    from backend.resource_manager.resource_manager import ResourceManager
    from backend.simulation.simulation_state import SimulationState

    memory_manager = MemoryManager(total_memory=1000)
    resource_manager = ResourceManager()
    resource_manager.create_default_resources()
    live = SimulationState(
        memory_manager=memory_manager,
        resource_manager=resource_manager,
        db=database,
    )

    apps = [
        App("app_maps", "Maps", priority=3, state=AppState.BACKGROUND, memory_footprint=300, reference_bit=0),
        App("app_fitness", "Fitness Tracker", priority=2, state=AppState.BACKGROUND, memory_footprint=150, reference_bit=1),
        App("app_music", "Music Player", priority=2, state=AppState.BACKGROUND, memory_footprint=300, reference_bit=0),
        App("app_chat", "Chat App", priority=1, state=AppState.BACKGROUND, memory_footprint=100, reference_bit=0),
        App("app_browser", "Browser", priority=4, state=AppState.FOREGROUND, memory_footprint=150, reference_bit=1),
    ]
    for app in apps:
        live.add_app(app)

    # GPS is held by Maps, so Fitness Tracker is queued and becomes WAITING.
    live.request_resource("app_maps", "GPS")
    live.request_resource("app_music", "AUDIO")
    live.request_resource("app_fitness", "GPS")

    for resource in resource_manager.get_all_resources():
        database.save_resource(resource)

    return live


def _build_spare_state(database: Any):
    """A live state with headroom: 400 MB used of 1000, GPS contended.

    The Applications page refuses a footprint larger than the free memory the
    MemoryManager reports, so its tests need a state that is not already full.
    """
    from backend.memory_manager.memory_manager import MemoryManager
    from backend.models.app import App, AppState
    from backend.resource_manager.resource_manager import ResourceManager
    from backend.simulation.simulation_state import SimulationState

    resource_manager = ResourceManager()
    resource_manager.create_default_resources()
    live = SimulationState(
        memory_manager=MemoryManager(total_memory=1000),
        resource_manager=resource_manager,
        db=database,
    )

    for app in (
        App("app_maps", "Maps", priority=3, state=AppState.BACKGROUND, memory_footprint=200, reference_bit=0),
        App("app_fitness", "Fitness Tracker", priority=2, state=AppState.BACKGROUND, memory_footprint=100, reference_bit=1),
        App("app_chat", "Chat App", priority=1, state=AppState.BACKGROUND, memory_footprint=100, reference_bit=0),
    ):
        live.add_app(app)

    live.request_resource("app_maps", "GPS")
    live.request_resource("app_fitness", "GPS")

    for resource in resource_manager.get_all_resources():
        database.save_resource(resource)

    return live


# ──────────────────────────────────────────────────────────────
# State assembly
#
# The state is built once per session and reused by later reruns, exactly as
# app.py behaves: Quick Start creates it and every subsequent run reuses it.
# That is what lets a test press a button, trigger the rerun the page issues
# after each operation, and still see the result.
# ──────────────────────────────────────────────────────────────

NEEDS_DB = ("live", "spare", "run", "dbonly")
THREAD_KEY = "render_driver_thread"

sim_state = st.session_state.get(state.KEY_SIM_STATE)
db = st.session_state.get(state.KEY_DB)

# AppTest starts a fresh script thread per run, and SQLite refuses a connection
# created on another thread. Re-opening here is a harness accommodation only:
# the simulation state — and therefore every application the tests created — is
# carried over, and the managers are re-attached to the new connection through
# SimulationState.set_db_manager.
thread = threading.get_ident()
if MODE in NEEDS_DB and (db is None or st.session_state.get(THREAD_KEY) != thread):
    from database.db import DatabaseManager

    if db is not None:
        try:
            db.close()
        except Exception:
            pass
    db = DatabaseManager(DB_PATH)
    st.session_state[state.KEY_DB] = db
    st.session_state[THREAD_KEY] = thread
    if sim_state is not None:
        sim_state.set_db_manager(db)

if sim_state is None:
    if MODE == "live":
        sim_state = _build_live_state(db)
    elif MODE == "spare":
        sim_state = _build_spare_state(db)
    st.session_state[state.KEY_SIM_STATE] = sim_state

if MODE == "run":
    # The test already executed the run and wrote its result; the dashboard is
    # shown against that database exactly as a user would see it afterwards.
    if not RESULT_PATH:
        raise SystemExit("MODE='run' requires RAAE_DASHBOARD_RESULT pointing at the run result.")
    with open(RESULT_PATH, "r", encoding="utf-8") as handle:
        result = json.load(handle)

    key = (
        state.KEY_RUN_RAAE
        if result.get("policy") == state.POLICY_RAAE
        else state.KEY_RUN_BASELINE
    )
    st.session_state[key] = result
    st.session_state[state.KEY_SELECTED_POLICY] = str(result.get("policy"))

st.session_state[state.KEY_SIM_STATE] = sim_state
st.session_state[state.KEY_DB] = db

st.caption(f"driver mode: {MODE}")

PAGE_MODULES = {
    "dashboard": dashboard,
    "applications": app_controls,
    "simulation": simulation_view,
    "resources": resource_view,
    "analytics": analytics_view,
}
PAGE_MODULES.get(PAGE, dashboard).render(sim_state=sim_state, db=db)