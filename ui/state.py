"""Single source of truth for simulation state held in ``st.session_state``.

Several pages need the same three facts — which eviction policy is selected, and
what the last simulation run produced. Rather than each page inventing its own
copy of those values, every page reads them through the accessors here, and the
pages that *produce* them (:mod:`ui.simulation_view`, :mod:`app`) write through
the same key constants.

This module owns session keys and nothing else: it never runs a simulation,
never touches the database, and never derives a metric. A value it cannot find
is reported as ``None`` so the UI can say "unavailable" instead of inventing a
value.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

# ── Session keys ───────────────────────────────────────────────
KEY_NAV_PAGE = "nav_page"
KEY_SIM_STATE = "sim_state"
KEY_DB = "db"
KEY_RUN_RAAE = "sim_raae"
KEY_RUN_BASELINE = "sim_baseline"
KEY_COMPARISON_DONE = "sim_comparison_done"
KEY_SELECTED_POLICY = "selected_policy"
KEY_SIM_SESSION = "simulation_session"
KEY_RUN_HISTORY = "simulation_run_history"

#: Every session key the app owns, with the value used when it is absent.
DEFAULTS: Dict[str, Any] = {
    KEY_NAV_PAGE: "Overview",
    KEY_SIM_STATE: None,          # live SimulationState
    KEY_DB: None,                 # DatabaseManager
    KEY_RUN_RAAE: None,           # last RAAE SimulationResult dict
    KEY_RUN_BASELINE: None,       # last BASELINE_CLOCK SimulationResult dict
    KEY_COMPARISON_DONE: False,
    KEY_SELECTED_POLICY: "RAAE",  # pre-selected in the Simulation page control
    KEY_SIM_SESSION: None,        # live ui.simulation_session.SimulationSession
    KEY_RUN_HISTORY: [],          # one entry per run; database records are kept
}

POLICY_RAAE = "RAAE"
POLICY_BASELINE = "BASELINE_CLOCK"


def initialise(defaults: Optional[Dict[str, Any]] = None) -> None:
    """Ensures every app session key exists, without overwriting live values."""
    for key, value in (defaults or DEFAULTS).items():
        if key not in st.session_state:
            st.session_state[key] = value


# ── Read accessors ─────────────────────────────────────────────

def live_simulation() -> Optional[Any]:
    """Returns the live ``SimulationState``, or None when none is attached."""
    return st.session_state.get(KEY_SIM_STATE)


def database() -> Optional[Any]:
    """Returns the attached ``DatabaseManager``, or None when persistence is off."""
    return st.session_state.get(KEY_DB)


def run_session() -> Optional[Any]:
    """Returns the live Simulation page session, or None when no run exists.

    Duck-typed on purpose: this module must stay free of backend imports so it
    can be read by every page, including ones with no simulation attached.
    """
    return st.session_state.get(KEY_SIM_SESSION)


def run_history() -> List[Dict[str, Any]]:
    """Returns the record of runs started in this session.

    Entries are appended on Start and never removed, so resetting a run leaves
    the list — and the database files it points at — intact.
    """
    return list(st.session_state.get(KEY_RUN_HISTORY) or [])


def selected_policy() -> Optional[str]:
    """Returns the eviction policy the user selected, or None if unset."""
    value = st.session_state.get(KEY_SELECTED_POLICY)
    return str(value) if value else None


def raae_run() -> Optional[Dict[str, Any]]:
    """Returns the most recent RAAE result mapping, or None."""
    return st.session_state.get(KEY_RUN_RAAE)


def baseline_run() -> Optional[Dict[str, Any]]:
    """Returns the most recent BASELINE_CLOCK result mapping, or None."""
    return st.session_state.get(KEY_RUN_BASELINE)


def comparison_done() -> bool:
    """Returns True when both policies have been run in the current session."""
    return bool(st.session_state.get(KEY_COMPARISON_DONE, False))


def active_run() -> Optional[Dict[str, Any]]:
    """Returns the run the header should describe.

    The run matching the selected policy is preferred, so switching the selector
    switches the reported policy. When no policy is selected the RAAE run is
    used, then BASELINE_CLOCK. Returns None when no run exists at all.
    """
    policy = selected_policy()
    if policy == POLICY_BASELINE and baseline_run():
        return baseline_run()
    raae = raae_run()
    if raae:
        return raae
    return baseline_run()


def run_for_policy(policy: Optional[str]) -> Optional[Dict[str, Any]]:
    """Returns the stored run for a specific policy name, or None."""
    if policy == POLICY_RAAE:
        return raae_run()
    if policy == POLICY_BASELINE:
        return baseline_run()
    return None


def run_policies() -> List[str]:
    """Returns the policies that have actually been run this session."""
    policies: List[str] = []
    if raae_run():
        policies.append(POLICY_RAAE)
    if baseline_run():
        policies.append(POLICY_BASELINE)
    return policies


# ── Derived header facts ───────────────────────────────────────

def ticks_run() -> Optional[int]:
    """Returns the number of ticks executed, or None.

    A live run reports the engine's own counter; otherwise the stored result is
    used so a finished run still reports what it executed.
    """
    session = run_session()
    if session is not None:
        return int(getattr(session, "current_tick", 0) or 0)

    run = active_run()
    if not run:
        return None
    total = run.get("total_ticks")
    if isinstance(total, int):
        return total
    detail = run.get("ticks_detail")
    return len(detail) if isinstance(detail, list) else None


def simulation_status() -> Tuple[str, str]:
    """Returns ``(status, explanation)`` describing the simulation.

    Status is one of:

    * ``RUNNING`` — a run is live and will execute the next tick on demand.
    * ``PAUSED`` — a run exists but ticking is withheld until resumed.
    * ``STOPPED`` — the run's controller has been closed.
    * ``IDLE`` — no run, no live state, no database; nothing has been loaded.
    * ``READY`` — a live ``SimulationState`` or database is attached, but no
      simulation has been executed yet.
    * ``COMPLETED`` — a run finished earlier and its results are stored.

    The explanation names the source of the claim so the header never asserts a
    state the backend did not report.
    """
    session = run_session()
    if session is not None:
        status = str(getattr(session, "status", "IDLE"))
        policy = getattr(session, "policy", "unknown policy")
        ticks = getattr(session, "current_tick", 0)
        if status == "RUNNING":
            return status, f"{policy} · {ticks} tick(s) executed · next tick runs on demand"
        if status == "PAUSED":
            return status, f"{policy} · paused at tick {ticks}"
        return status, f"{policy} · stopped after {ticks} tick(s)"

    run = active_run()
    if run is not None:
        ticks = ticks_run()
        detail = f"{ticks} tick(s) executed" if ticks is not None else "run completed"
        return "COMPLETED", f"{run.get('policy', 'Unknown policy')} · {detail}"

    if live_simulation() is not None or database() is not None:
        return "READY", "State loaded · no simulation executed yet"

    return "IDLE", "No simulation state or database attached"


def effective_policy() -> Optional[str]:
    """Returns the policy name to display in the header.

    A live run is authoritative, then the comparison, then the user's selection.
    """
    session = run_session()
    if session is not None:
        return getattr(session, "policy", None)

    if comparison_done() and raae_run() and baseline_run():
        return f"{POLICY_BASELINE} vs {POLICY_RAAE}"
    return selected_policy()