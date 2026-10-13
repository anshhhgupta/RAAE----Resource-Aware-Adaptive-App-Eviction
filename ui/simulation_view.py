"""Simulation page for the RAAE Streamlit app.

Runs the real eviction workflow under the user's control. The page owns no
simulation logic: a :class:`~ui.simulation_session.SimulationSession` holds the
existing ``SimulationController`` and advances it one tick at a time, and every
number and status shown here is read back from that backend.

Controls: Start, Pause, Resume, Stop, Reset, advance one tick, advance several
ticks, trigger memory pressure, policy selection and workload configuration.

The decision timeline shows the eight documented workflow steps for the
selected tick, marking each one confirmed, not executed, not applicable or
unavailable based only on what the tick produced and what the database recorded.

Ticks run synchronously when a control is pressed — there is no background
thread and no sleeping — so a run that says RUNNING is one that will execute its
next tick on demand.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

from ui import state
from ui.components import (
    _esc,
    decision_timeline,
    empty_state,
    metric_row,
    section_header,
    spacer,
    status_badge,
    styled_table,
    unavailable_note,
)
from ui.simulation_session import (
    CONFIRMED,
    POLICY_BASELINE,
    POLICY_RAAE,
    STATUS_PAUSED,
    STATUS_RUNNING,
    STATUS_STOPPED,
    SimulationError,
    SimulationSession,
    build_timeline,
)

#: Where each run's SQLite log is written. Files are kept, never deleted.
RUN_DIRECTORY = Path("database") / "runs"

POLICY_CHOICES = [POLICY_RAAE, POLICY_BASELINE]

#: Shown beside each policy in the selector.
POLICY_NOTES = {
    POLICY_RAAE: "resource-aware, protects contended apps",
    POLICY_BASELINE: "blind Clock eviction",
}

SCENARIOS = ["normal", "high_memory_pressure", "resource_contention"]

#: What each status means for the run, and how the card is coloured.
STATUS_MEANING = {
    STATUS_RUNNING: "The next tick executes when you press Advance",
    STATUS_PAUSED: "Paused — no tick will run until you press Resume",
    STATUS_STOPPED: "Stopped — no further tick is possible; records are kept",
}
STATUS_ACCENT = {
    STATUS_RUNNING: "green",
    STATUS_PAUSED: "orange",
    STATUS_STOPPED: "blue",
}


def render(
    sim_state: Optional[Any] = None,
    db: Optional[Any] = None,
) -> None:
    """Renders the Simulation page.

    Args:
        sim_state: The demo ``SimulationState`` from the sidebar, if any. The
            page does not need it: a run builds its own controller.
        db: Unused here; each run owns its own database file.
    """
    st.markdown("## ⚙️ Simulation Engine")
    st.caption(
        "Drive the real eviction workflow one tick at a time — every figure and "
        "timeline step below comes from the backend."
    )

    session = state.run_session()

    _render_feedback()

    if session is None:
        _render_idle_summary()

    config = _render_configuration(session)
    _render_run_controls(session, config)
    spacer(12)

    if session is None:
        empty_state(
            "⚙️",
            "No run in progress",
            "Configure the run above and press Start. Ticks then execute only "
            "when you ask for them.",
        )
        spacer(12)
        # The history stays visible after a reset: it is what tells the user
        # their previous records were kept rather than discarded.
        _render_run_history()
        return

    _render_live_metrics(session)
    spacer(12)
    _render_last_decision(session)
    spacer(12)
    _render_timeline(session)
    spacer(12)
    _render_registry(session)
    spacer(12)
    _render_run_history()


# ──────────────────────────────────────────────────────────────
# Feedback
# ──────────────────────────────────────────────────────────────

def _push(message: str, level: str = "info") -> None:
    """Stores a message for display on this and the next render."""
    st.session_state["simulation_feedback"] = [
        *st.session_state.get("simulation_feedback", []),
        {"level": level, "message": message},
    ][-4:]


def _render_feedback() -> None:
    """Renders stored messages.

    A message is pushed only after the backend call it describes has returned,
    so nothing is reported before it happened.
    """
    renderers = {
        "success": st.success,
        "error": st.error,
        "warning": st.warning,
        "info": st.info,
    }
    for entry in st.session_state.get("simulation_feedback", []):
        renderers.get(entry.get("level", "info"), st.info)(entry["message"])


# ──────────────────────────────────────────────────────────────
# Idle state
# ──────────────────────────────────────────────────────────────

def _render_idle_summary() -> None:
    """Explains what a run will do and where its records are kept."""
    history = state.run_history()
    if history:
        last = history[-1]
        st.info(
            f"Last run: {last['policy']} · {last['ticks']} tick(s) · log kept at "
            f"`{last['db_path']}`.",
            icon="🗃️",
        )
    else:
        st.caption(
            "No run has been started in this session. Each run writes its own "
            f"SQLite log under `{RUN_DIRECTORY.as_posix()}/`, and those files are kept."
        )


# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────

def _render_configuration(session: Optional[SimulationSession]) -> Dict[str, Any]:
    """Renders the run configuration, locked while a run exists."""
    locked = session is not None
    help_text = (
        "Reset the run to change its configuration."
        if locked
        else "Applied when the run starts."
    )

    section_header("Run configuration", hint="locked while a run exists" if locked else "")
    spacer(6)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        policy = st.selectbox(
            "Eviction policy",
            POLICY_CHOICES,
            format_func=lambda name: f"{name} — {POLICY_NOTES[name]}" if name in POLICY_NOTES else str(name),
            key="sim_policy",
            disabled=locked,
            help=help_text,
        )
    with col2:
        total_memory = st.number_input(
            "Total RAM (MB)", 256, 8192, 1000, step=128, key="sim_total_memory",
            disabled=locked, help=help_text,
        )
    with col3:
        threshold = st.slider(
            "Pressure threshold (%)", 50.0, 95.0, 75.0, 1.0, key="sim_threshold",
            disabled=locked, help=help_text,
        )
    with col4:
        tick_budget = st.number_input(
            "Tick budget", 1, 50, 3, step=1, key="sim_tick_budget", disabled=locked,
            help="The most ticks this run may execute before Advance stops.",
        )

    use_custom = st.checkbox(
        "Use a generated workload instead of the deterministic 5-app workload",
        value=False,
        key="sim_use_custom",
        disabled=locked,
        help=help_text,
    )

    seed = scenario = num_apps = None
    if use_custom:
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            seed = st.number_input(
                "Workload seed", 0, 999999, 42, step=1, key="sim_seed",
                disabled=locked,
                help="The generator is seeded, so the same seed rebuilds the same apps.",
            )
        with col_b:
            scenario = st.selectbox(
                "Scenario", SCENARIOS, key="sim_scenario", disabled=locked
            )
        with col_c:
            num_apps = st.number_input(
                "Applications", 3, 15, 5, key="sim_num_apps", disabled=locked
            )

    return {
        "policy": policy,
        "total_memory": int(total_memory),
        "pressure_threshold": float(threshold),
        "tick_budget": int(tick_budget),
        "use_custom": bool(use_custom),
        "seed": seed,
        "scenario": scenario,
        "num_apps": int(num_apps) if num_apps else 5,
    }


# ──────────────────────────────────────────────────────────────
# Run controls
# ──────────────────────────────────────────────────────────────

def _render_run_controls(
    session: Optional[SimulationSession], config: Dict[str, Any]
) -> None:
    """Renders the lifecycle and tick controls.

    Every button is enabled from the session's own status, so an invalid
    transition cannot be requested in the first place.
    """
    section_header("Controls", hint="status drives what is available")
    spacer(6)

    row1 = st.columns([1, 1, 1, 1, 1])
    with row1[0]:
        if st.button(
            "▶️ Start",
            type="primary",
            use_container_width=True,
            disabled=session is not None,
            help="A run already exists. Reset it to start another.",
            key="sim_start",
        ):
            _start_run(config)
    with row1[1]:
        if st.button(
            "⏸ Pause",
            use_container_width=True,
            disabled=not (session and session.can_pause),
            help="Withhold further ticks; the run is kept.",
            key="sim_pause",
        ):
            _run_transition(session.pause, "paused")
    with row1[2]:
        if st.button(
            "⏵ Resume",
            use_container_width=True,
            disabled=not (session and session.can_resume),
            help="Allow ticking to continue.",
            key="sim_resume",
        ):
            _run_transition(session.resume, "resumed")
    with row1[3]:
        if st.button(
            "⏹ Stop",
            use_container_width=True,
            disabled=not (session and session.can_stop),
            help="Close the controller. The run's database file is kept.",
            key="sim_stop",
        ):
            _stop_run(session)
    with row1[4]:
        if st.button(
            "♻️ Reset",
            use_container_width=True,
            disabled=session is None,
            help="Discard the in-memory run. Database records are kept.",
            key="sim_reset",
        ):
            _reset_run(session)

    spacer(8)

    row2 = st.columns([1, 2, 2, 3])
    advance_hint = (
        "Runs one tick through the engine."
        if session and session.can_advance
        else "Unavailable: start a run and resume it first."
        if session
        else "Unavailable until a run is started."
    )
    with row2[0]:
        if st.button(
            "⏭ Advance one tick",
            use_container_width=True,
            disabled=not (session and session.can_advance),
            help=advance_hint,
            key="sim_tick_one",
        ):
            _advance(session, 1)
    with row2[1]:
        ticks_to_run = st.number_input(
            "Ticks", 1, 50, 3, step=1,
            disabled=not (session and session.can_advance),
            key="sim_ticks_n",
            label_visibility="collapsed",
        )
    with row2[2]:
        if st.button(
            "⏩ Advance ticks",
            use_container_width=True,
            disabled=not (session and session.can_advance),
            help=advance_hint,
            key="sim_tick_many",
        ):
            _advance(session, int(ticks_to_run))
    with row2[3]:
        target = st.slider(
            "Pressure target (%)", 50, 100, 85, 5,
            disabled=not (session and session.is_running),
            key="sim_pressure_target",
            label_visibility="collapsed",
        )

    spacer(4)

    if st.button(
        "🔥 Trigger memory pressure",
        use_container_width=True,
        disabled=not (session and session.is_running),
        help="Allocates memory to active apps until the target utilisation is reached.",
        key="sim_pressure",
    ):
        _trigger_pressure(session, float(target))

    if session is not None:
        budget = getattr(session.workload, "ticks", None)
        if isinstance(budget, int) and session.current_tick >= budget:
            st.caption(
                f"Tick budget reached ({budget}). Reset to start another run, or "
                "raise the budget when configuring a new one."
            )


def _run_transition(action, label: str) -> None:
    """Runs a lifecycle transition and reports what it did."""
    try:
        _push(action(), "success")
    except SimulationError as error:
        _push(str(error), "error")
    st.rerun()


# ──────────────────────────────────────────────────────────────
# Actions
# ──────────────────────────────────────────────────────────────

def _next_db_path() -> str:
    """Returns an unused database path for the next run."""
    counter = int(st.session_state.get("simulation_run_counter", 0)) + 1
    st.session_state["simulation_run_counter"] = counter
    return str(RUN_DIRECTORY / f"run_{counter:03d}.db")


def _build_workload(config: Dict[str, Any], db_path: str):
    """Builds the workload for a run from the page configuration."""
    from backend.simulation_engine.controller import AppSpec, Workload

    if not config["use_custom"]:
        workload = Workload.deterministic_5_apps()
        workload.total_memory = config["total_memory"]
        workload.pressure_threshold = config["pressure_threshold"]
        workload.ticks = config["tick_budget"]
        workload.db_path = db_path
        return workload

    from backend.workload_generator.workload_generator import WorkloadGenerator

    generator = WorkloadGenerator(seed=config["seed"])
    trace = generator.generate_workload(
        seed=config["seed"],
        ticks=config["tick_budget"],
        scenario=config["scenario"],
        num_apps=config["num_apps"],
        ram_mb=config["total_memory"],
    )

    specs = []
    acquisitions: List[Any] = []
    for app in trace.initial_apps:
        specs.append(
            AppSpec(
                app_id=app.app_id,
                name=app.name,
                priority=app.priority,
                memory_footprint=app.memory_footprint,
                state=app.state.value,
                reference_bit=app.reference_bit,
                held_resources=sorted(app.held_resources),
            )
        )
        # A generated app that already holds a resource must have its lock
        # created, otherwise the registry would claim a lock the backend never took.
        acquisitions.extend((app.app_id, r) for r in sorted(app.held_resources))

    return Workload(
        total_memory=config["total_memory"],
        pressure_threshold=config["pressure_threshold"],
        ticks=config["tick_budget"],
        db_path=db_path,
        apps=specs,
        resource_acquisitions=acquisitions,
    )


def _start_run(config: Dict[str, Any]) -> None:
    """Builds the controller and registers the run in the history."""
    db_path = _next_db_path()
    try:
        workload = _build_workload(config, db_path)
        session = SimulationSession(policy=config["policy"], workload=workload)
    except Exception as error:  # noqa: BLE001 - reported to the user
        _push(f"Could not start the run: {error}", "error")
        st.rerun()
        return

    st.session_state[state.KEY_SIM_SESSION] = session
    st.session_state[state.KEY_SELECTED_POLICY] = config["policy"]
    st.session_state[state.KEY_COMPARISON_DONE] = False
    st.session_state[state.KEY_RUN_HISTORY] = [
        *state.run_history(),
        {
            "policy": config["policy"],
            "db_path": session.db_path,
            "ticks": 0,
            "started_at": datetime.datetime.now().strftime("%H:%M:%S"),
            "workload": (
                f"{config['num_apps']} generated apps, seed {config['seed']}, "
                f"{config['scenario']}"
                if config["use_custom"]
                else "deterministic 5-app workload"
            ),
        },
    ]
    _publish_snapshot(session)
    _push(
        f"Run started with {config['policy']} on {workload.total_memory} MB of RAM. "
        f"Log: {session.db_path}",
        "success",
    )
    st.rerun()


def _advance(session: Optional[SimulationSession], count: int) -> None:
    """Advances the run and republishes its cumulative result."""
    if session is None:
        _push("No run is in progress.", "error")
        st.rerun()
        return
    try:
        records = session.advance(count)
    except SimulationError as error:
        _push(str(error), "error")
        st.rerun()
        return
    except Exception as error:  # noqa: BLE001 - a backend failure must not kill the page
        _push(f"The backend failed while executing the tick: {error}", "error")
        st.rerun()
        return

    _publish_snapshot(session)
    _update_history(session)
    statuses = ", ".join(f"tick {r.tick_number}: {r.status}" for r in records)
    _push(
        f"Executed {len(records)} tick(s) — {statuses}. "
        f"Now at tick {session.current_tick}.",
        "success" if any(r.eviction_results for r in records) else "info",
    )
    st.rerun()


def _trigger_pressure(session: Optional[SimulationSession], target: float) -> None:
    """Allocates memory to reach the requested utilisation."""
    if session is None:
        _push("No run is in progress.", "error")
        st.rerun()
        return
    try:
        outcome = session.trigger_pressure(target)
    except SimulationError as error:
        _push(str(error), "error")
        st.rerun()
        return
    except Exception as error:  # noqa: BLE001 - a backend failure must not kill the page
        _push(f"The backend failed while triggering pressure: {error}", "error")
        st.rerun()
        return

    _publish_snapshot(session)
    _push(
        outcome["message"],
        "success" if outcome["allocated_mb"] else "warning",
    )
    st.rerun()


def _stop_run(session: SimulationSession) -> None:
    """Stops the run, keeping its database file."""
    try:
        message = session.stop()
    except SimulationError as error:
        _push(str(error), "error")
        st.rerun()
        return
    _publish_snapshot(session)
    _update_history(session)
    _push(message, "warning")
    st.rerun()


def _reset_run(session: Optional[SimulationSession]) -> None:
    """Drops the in-memory run. Database records and history are kept."""
    if session is None:
        return
    retained = session.db_path
    if session.can_stop:
        # Close the connection so the file is not left open; the rows stay.
        try:
            session.stop()
        except SimulationError:
            pass

    st.session_state[state.KEY_SIM_SESSION] = None
    _update_history(session)
    _push(
        f"Run discarded. Its records are still in {retained} and the run history "
        "below still lists it.",
        "warning",
    )
    st.rerun()


def _publish_snapshot(session: SimulationSession) -> None:
    """Stores the run's cumulative result where the dashboard reads it."""
    key = (
        state.KEY_RUN_RAAE
        if session.policy == POLICY_RAAE
        else state.KEY_RUN_BASELINE
    )
    st.session_state[key] = session.result_snapshot()


def _update_history(session: SimulationSession) -> None:
    """Refreshes the tick count recorded for the current run."""
    history = state.run_history()
    if history:
        history[-1]["ticks"] = int(session.current_tick)
        history[-1]["status"] = session.status
        st.session_state[state.KEY_RUN_HISTORY] = history


# ──────────────────────────────────────────────────────────────
# Live metrics
# ──────────────────────────────────────────────────────────────

def _render_live_metrics(session: SimulationSession) -> None:
    """Renders the live run figures read from the backend."""
    memory = session.memory_snapshot()
    section_header("Live simulation", hint=f"{session.status} · {session.policy}")
    spacer(6)

    metric_row(
        [
            {
                "label": "Current tick",
                "value": session.current_tick,
                "delta": f"of {getattr(session.workload, 'ticks', '?')} budgeted",
                "accent": "blue",
            },
            {
                "label": "Simulation status",
                "value": session.status,
                "delta": STATUS_MEANING.get(session.status, ""),
                "accent": STATUS_ACCENT.get(session.status, "blue"),
            },
            {
                "label": "Eviction policy",
                "value": session.policy,
                "delta": "Applied by the engine",
                "accent": "purple",
            },
            {
                "label": "Active applications",
                "value": memory["active_apps"],
                "delta": f"{memory['registered_apps']} registered",
                "accent": "green",
                "unavailable": memory["active_apps"] is None,
            },
            {
                "label": "Total RAM",
                "value": f"{memory['total_mb']} MB" if memory["total_mb"] else None,
                "accent": "blue",
                "unavailable": memory["total_mb"] is None,
            },
            {
                "label": "Used RAM",
                "value": f"{memory['used_mb']} MB" if memory["used_mb"] is not None else None,
                "delta": (
                    f"{memory['utilization_pct']:.1f}% · {memory['pressure']}"
                    if memory["utilization_pct"] is not None
                    else None
                ),
                "accent": "orange"
                if (memory["utilization_pct"] or 0) >= 75
                else "blue",
                "unavailable": memory["used_mb"] is None,
            },
            {
                "label": "Available RAM",
                "value": (
                    f"{memory['available_mb']} MB"
                    if memory["available_mb"] is not None
                    else None
                ),
                "accent": "green",
                "unavailable": memory["available_mb"] is None,
            },
        ]
    )


def _render_last_decision(session: SimulationSession) -> None:
    """Renders the most recent eviction decision the engine produced."""
    section_header("Most recent eviction decision")
    spacer(6)

    decision = session.last_decision()
    if decision is None:
        empty_state(
            "🧭",
            "No eviction decision yet",
            "The engine has not produced an eviction result. Advance a tick, or "
            "trigger memory pressure to force one.",
        )
        return

    outcome = "Evicted" if decision["evicted"] else "Deferred"
    rows = [
        ["Tick", f"{decision['tick']}"],
        ["Application", f"{decision['app_name']} ({decision['app_id']})"],
        ["Policy", status_badge(decision["policy"])],
        ["Outcome", status_badge(outcome)],
        ["Memory freed", f"{decision['memory_freed']} MB"],
        ["Conflict detected", "Yes" if decision["conflict_detected"] else "No"],
        [
            "Safe release",
            "Yes" if decision["safe_release"] else "No",
        ],
        [
            "Released resources",
            ", ".join(decision["released_resources"]) or "—",
        ],
        ["Waiting applications", ", ".join(decision["waiting_apps"]) or "—"],
        [
            "Persisted event",
            f"EvictionLog #{decision['persisted_event_id']}"
            if decision["persisted_event_id"]
            else "—",
        ],
        ["Reason", decision["reason"] or "—"],
    ]
    styled_table(["Field", "Value"], rows)


# ──────────────────────────────────────────────────────────────
# Decision timeline
# ──────────────────────────────────────────────────────────────

def _render_timeline(session: SimulationSession) -> None:
    """Renders the eight-step timeline for a chosen tick."""
    records = session.records
    header_hint = f"{len(records)} tick(s) executed" if records else "no tick executed yet"
    section_header("Decision timeline", hint=header_hint)
    spacer(6)

    if not records:
        steps = build_timeline(session)
        decision_timeline(steps, "Timeline — no tick executed yet")
        return

    options = list(range(len(records)))
    index = st.selectbox(
        "Tick",
        options,
        index=len(options) - 1,
        format_func=lambda i: f"Tick {records[i].tick_number} — {records[i].status}",
        key="sim_timeline_tick",
    )
    record = records[index]
    steps = build_timeline(session, record)

    confirmed = sum(1 for s in steps if s.status == CONFIRMED)
    st.caption(
        f"{confirmed} of {len(steps)} steps were confirmed by backend events for "
        f"tick {record.tick_number}. Steps that did not run are labelled as such."
    )
    decision_timeline(
        steps, f"Tick {record.tick_number} · {record.status} · {session.policy}"
    )

    if record.read_errors:
        with st.expander("Evidence that could not be read", expanded=False):
            for message in record.read_errors:
                st.warning(message)


# ──────────────────────────────────────────────────────────────
# Registry and history
# ──────────────────────────────────────────────────────────────

def _render_registry(session: SimulationSession) -> None:
    """Renders the applications as the backend currently holds them."""
    rows = session.app_rows()
    section_header("Application registry", hint="live MemoryManager registry")
    spacer(6)

    if not rows:
        unavailable_note("The run has no applications registered.")
        return

    styled_table(
        ["Application", "ID", "Priority", "Memory", "State", "Ref bit", "Held resources"],
        [
            [
                f"<strong>{_esc(r['name'])}</strong>",
                _esc(r["app_id"]),
                f"P{r['priority']}",
                f"{r['memory']} MB",
                status_badge(r["state"]),
                "🟢 1" if r["reference_bit"] else "⚪ 0",
                ", ".join(_esc(h) for h in r["held_resources"]) or "—",
            ]
            for r in rows
        ],
    )


def _render_run_history() -> None:
    """Lists every run started in this session and where its records live."""
    history = state.run_history()
    section_header("Run history", hint=f"{len(history)} run(s) this session")
    spacer(6)

    if not history:
        st.caption("No run has been started yet.")
        return

    styled_table(
        ["Started", "Policy", "Workload", "Ticks", "Status", "Database file (kept)"],
        [
            [
                entry.get("started_at", "—"),
                status_badge(entry["policy"]),
                entry.get("workload", "—"),
                str(entry.get("ticks", 0)),
                entry.get("status", "—"),
                f"`{entry['db_path']}`",
            ]
            for entry in history
        ],
    )
    st.caption(
        "Resetting a run clears only the in-memory session. Every log file listed "
        "above is left on disk, so no historical record is destroyed."
    )