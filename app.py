"""RAAE — Resource-Aware Adaptive App Eviction · Streamlit Entry Point.

Launches the main dashboard with sidebar navigation across five pages:
    1. Overview     — System snapshot, memory monitor and event timeline
    2. Applications — App registry, state, detail inspector
    3. Simulation   — Run BASELINE_CLOCK vs RAAE comparisons
    4. Resources    — Hardware resource / lock monitor
    5. Analytics    — Aggregated charts and policy comparison

Run with:
    streamlit run app.py

Session state is owned by :mod:`ui.state`: this module initialises it, the
Quick Start button populates it, and each page module reads it. No page keeps its
own copy of the simulation.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is importable so ``backend`` and ``database``
# packages resolve correctly regardless of how Streamlit launches.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Streamlit page config (must be first Streamlit call) ──────
from ui import configure_page  # noqa: E402

configure_page()

import streamlit as st  # noqa: E402

from ui import dashboard, app_controls, simulation_view, resource_view, analytics_view, state  # noqa: E402

# ── Backend imports ───────────────────────────────────────────
import backend  # noqa: F401,E402  -- pre-initialise backend to resolve circular dependency with database.db
from database.db import DatabaseManager  # noqa: E402
from backend.simulation.simulation_state import SimulationState  # noqa: E402
from backend.memory_manager.memory_manager import MemoryManager  # noqa: E402
from backend.resource_manager.resource_manager import ResourceManager  # noqa: E402


# ──────────────────────────────────────────────────────────────
# Session State Initialisation
# ──────────────────────────────────────────────────────────────

state.initialise()


# ──────────────────────────────────────────────────────────────
# Demo bootstrapper
# ──────────────────────────────────────────────────────────────

def _reset_run_results() -> None:
    """Drops stored run results so the header never reports a stale run."""
    for key in (state.KEY_RUN_RAAE, state.KEY_RUN_BASELINE, state.KEY_COMPARISON_DONE):
        st.session_state.pop(key, None)


def _boot_demo_state() -> None:
    """Creates a live in-memory ``SimulationState`` for the Overview / Apps /
    Resources pages.

    This builds real backend objects — a MemoryManager, a ResourceManager and
    their SQLite persistence — rather than placeholder values, so every number
    the dashboard shows afterwards is read back out of the backend. It is not a
    simulation run, so any previous run results are cleared and the header
    correctly reports "no simulation executed yet".
    """
    from backend.models.app import App, AppState

    _reset_run_results()

    db = DatabaseManager(":memory:")
    memory_manager = MemoryManager(total_memory=1000)
    resource_manager = ResourceManager()
    resource_manager.create_default_resources()
    sim = SimulationState(
        memory_manager=memory_manager,
        resource_manager=resource_manager,
        db=db,
    )

    demo_apps = [
        App("app_maps", "Maps", priority=3, state=AppState.BACKGROUND, memory_footprint=300, reference_bit=0),
        App("app_fitness", "Fitness Tracker", priority=2, state=AppState.BACKGROUND, memory_footprint=150, reference_bit=1),
        App("app_music", "Music Player", priority=2, state=AppState.BACKGROUND, memory_footprint=300, reference_bit=0),
        App("app_chat", "Chat App", priority=1, state=AppState.BACKGROUND, memory_footprint=100, reference_bit=0),
        App("app_browser", "Browser", priority=4, state=AppState.FOREGROUND, memory_footprint=150, reference_bit=1),
    ]

    for app in demo_apps:
        sim.add_app(app)

    # Acquire some resources to show contention: the third request cannot be
    # granted, so the semaphore queues it and the app becomes WAITING.
    sim.request_resource("app_maps", "GPS")
    sim.request_resource("app_music", "AUDIO")
    sim.request_resource("app_fitness", "GPS")

    # Persist the resource registry so the Resources and Logs pages can read it.
    for resource in resource_manager.get_all_resources():
        db.save_resource(resource)

    st.session_state[state.KEY_SIM_STATE] = sim
    st.session_state[state.KEY_DB] = db


# ──────────────────────────────────────────────────────────────
# Sidebar Navigation
# ──────────────────────────────────────────────────────────────

_PAGES = {
    "Overview":     ("🏠", dashboard),
    "Applications": ("📱", app_controls),
    "Simulation":   ("⚙️", simulation_view),
    "Resources":    ("🔒", resource_view),
    "Analytics":    ("📈", analytics_view),
}

with st.sidebar:
    # ── Branding ──────────────────────────────────────────────
    st.markdown(
        """
        <div style="text-align:center; padding:1rem 0 0.6rem;">
            <span style="font-size:2.2rem;">🧠</span>
            <div style="font-size:1.15rem; font-weight:700; margin-top:0.3rem;
                        background:linear-gradient(90deg,#58a6ff,#bc8cff);
                        -webkit-background-clip:text; -webkit-text-fill-color:transparent;">
                RAAE Dashboard
            </div>
            <div style="font-size:0.7rem; color:#8b949e; margin-top:0.15rem;">
                Resource-Aware Adaptive App Eviction
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("---")

    # ── Navigation radio ──────────────────────────────────────
    selected_page = st.radio(
        "Navigation",
        list(_PAGES.keys()),
        format_func=lambda p: f"{_PAGES[p][0]}  {p}",
        index=list(_PAGES.keys()).index(st.session_state[state.KEY_NAV_PAGE]),
        label_visibility="collapsed",
    )
    st.session_state[state.KEY_NAV_PAGE] = selected_page

    st.markdown("---")

    # ── Quick-start helper ────────────────────────────────────
    st.markdown(
        "<div style='font-size:0.78rem; color:#8b949e; font-weight:600;'>QUICK START</div>",
        unsafe_allow_html=True,
    )
    if st.button("🚀 Initialise Demo State", use_container_width=True):
        _boot_demo_state()
        st.rerun()

    status, detail = state.simulation_status()
    st.markdown(
        f"""
        <div style="font-size:0.76rem; color:#8b949e; margin-top:0.8rem;">
            <strong style="color:#e6edf3;">Simulation</strong><br/>
            {detail}
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.caption("RAAE · PBL Project · 2026")


# ──────────────────────────────────────────────────────────────
# Page Router
# ──────────────────────────────────────────────────────────────

_icon, page_module = _PAGES[st.session_state[state.KEY_NAV_PAGE]]
page_module.render(
    sim_state=st.session_state.get(state.KEY_SIM_STATE),
    db=st.session_state.get(state.KEY_DB),
)