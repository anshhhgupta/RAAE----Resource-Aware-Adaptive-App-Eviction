"""Applications page for the RAAE Streamlit app.

Create, inspect and operate on the applications the simulation has registered.
Every control is wired to the existing backend through :mod:`ui.app_actions`:
this module holds no simulation state, calls no repository directly, and never
mutates a backend object itself — it renders what the backend reports after each
operation.

Layout:
    * Summary counters (foreground / background / waiting / evicted)
    * Create-application form
    * Application table
    * Operation panel for the selected application
    * Detail inspector with lock, queue and eviction-history data

When no simulation state is attached, the forms are disabled and the page says
which backend object is missing rather than pretending an action is available.
"""

from __future__ import annotations

import datetime
from typing import Any, List, Optional, Sequence

import streamlit as st

from ui import app_actions as actions
from ui.components import (
    _esc,
    empty_state,
    metric_card,
    metric_row,
    section_header,
    spacer,
    status_badge,
    styled_table,
    unavailable_note,
)

#: How many feedback messages to keep, so a rerun never replays old results.
MESSAGE_HISTORY = 4


def render(
    sim_state: Optional[Any] = None,
    db: Optional[Any] = None,
) -> None:
    """Renders the Applications page.

    Args:
        sim_state: Live ``SimulationState``, or None.
        db: Attached ``DatabaseManager``, used only to read eviction history.
    """
    st.markdown("## 📱 Application Management")
    st.caption(
        "Create applications and drive the resources the backend exposes for them"
    )

    _render_feedback()

    if sim_state is None:
        empty_state(
            "📱",
            "No simulation state attached",
            "Applications are created through the backend, so a live simulation "
            "state is required. Use “🚀 Initialise Demo State” in the sidebar.",
        )
        return

    apps = _live_apps(sim_state)

    _render_summary(apps)
    spacer(12)
    _render_create_form(sim_state, apps)
    spacer(16)
    _render_table(apps)
    spacer(16)
    _render_operations(sim_state, db, apps)
    spacer(16)
    _render_inspector(sim_state, db, apps)


# ──────────────────────────────────────────────────────────────
# Feedback
# ──────────────────────────────────────────────────────────────

def _push_feedback(result: actions.ActionResult) -> None:
    """Stores an operation outcome for display on this and the next render.

    The message is only pushed once the backend operation has returned, so the
    user never sees a success or an error for work that has not happened yet.
    """
    st.session_state["app_actions_feedback"] = [
        *st.session_state.get("app_actions_feedback", []),
        {"ok": result.ok, "message": result.message},
    ][-MESSAGE_HISTORY:]


def _render_feedback() -> None:
    """Renders the stored outcomes, newest last."""
    for entry in st.session_state.get("app_actions_feedback", []):
        if entry.get("ok"):
            st.success(entry["message"], icon="✅")
        else:
            st.error(entry["message"], icon="🚫")


# ──────────────────────────────────────────────────────────────
# Data
# ──────────────────────────────────────────────────────────────

def _live_apps(sim_state: Any) -> List[Any]:
    """Returns the backend's registered applications, or an empty list."""
    manager = getattr(sim_state, "memory_manager", None)
    if manager is None:
        return []
    try:
        return list(manager.apps.values())
    except Exception:
        return []


def _render_summary(apps: Sequence[Any]) -> None:
    """Renders the per-state application counters."""
    counts = {"FOREGROUND": 0, "BACKGROUND": 0, "WAITING": 0, "EVICTED": 0}
    for app in apps:
        state_name = str(getattr(getattr(app, "state", ""), "value", ""))
        if state_name in counts:
            counts[state_name] += 1

    section_header("Application summary", hint=f"{len(apps)} registered")
    spacer(6)

    metric_row(
        [
            {
                "label": "Foreground",
                "value": counts["FOREGROUND"],
                "delta": "Protected from eviction",
                "accent": "blue",
                "unavailable": not apps,
            },
            {
                "label": "Background",
                "value": counts["BACKGROUND"],
                "delta": "Eligible for eviction",
                "accent": "purple",
                "unavailable": not apps,
            },
            {
                "label": "Waiting",
                "value": counts["WAITING"],
                "delta": "Queued for a resource",
                "accent": "orange",
                "unavailable": not apps,
            },
            {
                "label": "Evicted",
                "value": counts["EVICTED"],
                "delta": "Memory reclaimed",
                "accent": "red",
                "unavailable": not apps,
            },
        ]
    )


# ──────────────────────────────────────────────────────────────
# Create application
# ──────────────────────────────────────────────────────────────

def _render_create_form(sim_state: Any, apps: Sequence[Any]) -> None:
    """Renders the create-application form and runs it on submit."""
    section_header("Create application", hint="Registered with MemoryManager and ResourceManager")
    spacer(6)

    free_memory = _free_memory(sim_state)
    used_memory = _used_memory(sim_state)
    total_memory = _total_memory(sim_state)
    if total_memory is not None:
        st.caption(
            f"Memory in use: {used_memory} MB of {total_memory} MB · "
            f"{free_memory} MB available"
        )

    # The ceiling follows the backend: the MemoryManager cannot back a footprint
    # larger than the memory it reports as free.
    max_mb = free_memory if free_memory is not None else 0

    # A form submits once per press, so a repeated click cannot register the
    # same application twice; the backend also refuses a duplicate id.
    with st.form("create_application_form", clear_on_submit=True):
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            name = st.text_input(
                "Application name",
                placeholder="e.g. Video Player",
                max_chars=actions.MAX_NAME_LENGTH,
                key="create_name",
            )
        with col2:
            memory_mb = st.number_input(
                "Memory footprint (MB)",
                min_value=0,
                max_value=max(max_mb, 1),
                value=min(100, max_mb),
                step=10,
                key="create_memory",
                help="Cannot exceed the free memory reported by the MemoryManager.",
            )
        with col3:
            priority = st.number_input(
                "Priority",
                min_value=actions.PRIORITY_MIN,
                max_value=actions.PRIORITY_MAX,
                value=2,
                step=1,
                key="create_priority",
                help="Documented band: 1 (lowest) to 5 (highest).",
            )

        initial_state = st.selectbox(
            "Initial state",
            list(actions.INITIAL_STATES),
            key="create_state",
            format_func=lambda s: s.capitalize(),
        )

        submitted = st.form_submit_button("➕ Create application", type="primary")

    if submitted:
        result = actions.create_application(
            sim_state,
            name=name,
            memory_footprint=memory_mb,
            priority=priority,
            initial_state=initial_state,
        )
        _push_feedback(result)
        st.rerun()


def _total_memory(sim_state: Any) -> Optional[int]:
    """Returns the MemoryManager's RAM limit, or None."""
    manager = getattr(sim_state, "memory_manager", None)
    if manager is None:
        return None
    try:
        return int(manager.total_memory)
    except Exception:
        return None


def _used_memory(sim_state: Any) -> Optional[int]:
    """Returns the memory the backend counts as in use, or None."""
    manager = getattr(sim_state, "memory_manager", None)
    if manager is None:
        return None
    try:
        return int(manager.get_used_memory())
    except Exception:
        return None


def _free_memory(sim_state: Any) -> Optional[int]:
    """Returns the memory the backend reports as free, or None."""
    manager = getattr(sim_state, "memory_manager", None)
    if manager is None:
        return None
    try:
        return int(manager.get_free_memory())
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────
# Application table
# ──────────────────────────────────────────────────────────────

def _app_rows(apps: Sequence[Any]) -> List[List[str]]:
    """Formats the application table rows."""
    rows: List[List[str]] = []
    for app in sorted(apps, key=lambda a: str(getattr(a, "app_id", ""))):
        held = sorted(getattr(app, "held_resources", set()) or set())
        status = str(getattr(getattr(app, "status", ""), "value", ""))
        rows.append(
            [
                _esc(getattr(app, "app_id", "—")),
                f"<strong>{_esc(getattr(app, 'name', '—'))}</strong>",
                f"P{_esc(getattr(app, 'priority', '—'))}",
                f"{_esc(getattr(app, 'memory_footprint', '—'))} MB",
                status_badge(str(getattr(getattr(app, "state", ""), "value", "—"))),
                "🟢 1" if getattr(app, "reference_bit", 0) else "⚪ 0",
                ", ".join(_esc(r) for r in held) if held else "<span class='event-muted'>—</span>",
                status_badge(status),
            ]
        )
    return rows


def _render_table(apps: Sequence[Any]) -> None:
    """Renders every registered application."""
    section_header("Applications", hint="Live MemoryManager registry")
    spacer(6)

    if not apps:
        empty_state(
            "📱",
            "No applications registered",
            "Create one above, or load the demo state from the sidebar.",
        )
        return

    styled_table(
        [
            "App ID",
            "Application",
            "Priority",
            "Memory",
            "State",
            "Reference bit",
            "Held resources",
            "Status",
        ],
        _app_rows(apps),
    )


# ──────────────────────────────────────────────────────────────
# Operations
# ──────────────────────────────────────────────────────────────

def _render_operations(sim_state: Any, db: Optional[Any], apps: Sequence[Any]) -> None:
    """Renders the action panel for one selected application."""
    section_header("Operations", hint="Executed by the backend, then reported")
    spacer(6)

    if not apps:
        unavailable_note("Select an application to enable its operations.")
        return

    labels = {app.app_id: f"{app.name} ({app.app_id})" for app in apps}
    app_id = st.selectbox(
        "Application",
        list(labels.keys()),
        format_func=lambda x: labels[x],
        key="app_controls_selected",
    )
    app = actions.get_application(sim_state, app_id)
    if app is None:
        unavailable_note(
            f"The backend no longer holds an application with id '{app_id}'."
        )
        return

    col_state, col_priority = st.columns(2)

    # ── Priority ──────────────────────────────────────────────
    with col_priority:
        st.markdown("**Change priority**")
        st.caption(
            f"Current: P{app.priority} · documented band "
            f"{actions.PRIORITY_MIN}–{actions.PRIORITY_MAX}"
        )
        priority_col, priority_btn = st.columns([2, 1])
        with priority_col:
            new_priority = st.number_input(
                "New priority",
                min_value=actions.PRIORITY_MIN,
                max_value=actions.PRIORITY_MAX,
                value=int(app.priority),
                step=1,
                key="app_controls_priority",
                label_visibility="collapsed",
            )
        with priority_btn:
            if st.button("Apply", key="apply_priority", use_container_width=True):
                _run(actions.change_priority(sim_state, app_id, new_priority))

    # ── State ─────────────────────────────────────────────────
    with col_state:
        st.markdown("**Change state**")
        queued = actions.queued_resources(sim_state, app_id)
        if queued:
            st.caption(
                f"Queued for {', '.join(queued)} — the backend only "
                "dequeues on release, so the state is locked until then."
            )
        current = str(app.state.value)
        options = [
            s for s in actions.SWITCHABLE_STATES if s != current
        ] or list(actions.SWITCHABLE_STATES)
        target = st.selectbox(
            "Move to",
            options,
            format_func=lambda s: s.capitalize(),
            key="app_controls_target_state",
            disabled=bool(queued),
        )
        if st.button(
            "Apply state",
            key="apply_state",
            use_container_width=True,
            disabled=bool(queued) or app.is_evicted,
            help=(
                "Disabled while the application is queued for a resource."
                if queued
                else "Unavailable for an evicted application."
                if app.is_evicted
                else None
            ),
        ):
            _run(actions.change_state(sim_state, app_id, target))

    spacer(8)

    # ── Reference bit / access ────────────────────────────────
    st.markdown("**Reference bit & simulated access**")
    st.caption(
        f"Current reference bit: {app.reference_bit} · last access "
        f"{_format_ts(app.last_access_time)}"
    )
    access_col, bit_col, bit_btn = st.columns([2, 2, 1])
    with access_col:
        st.markdown(
            "<div class='field-label'>Simulate access</div>",
            unsafe_allow_html=True,
        )
        st.caption("Calls MemoryManager.touch_app(), which sets the bit to 1.")
        if st.button("👆 Simulate access", key="simulate_access", use_container_width=True):
            _run(actions.simulate_access(sim_state, app_id))
    with bit_col:
        bit = st.selectbox(
            "Set reference bit",
            [0, 1],
            format_func=lambda b: f"{b} — {'recently used' if b else 'eviction candidate'}",
            key="app_controls_ref_bit",
        )
    with bit_btn:
        st.markdown("<div style='height:1.8rem'></div>", unsafe_allow_html=True)
        if st.button("Set bit", key="apply_ref_bit", use_container_width=True):
            _run(actions.set_reference_bit(sim_state, app_id, bit))

    spacer(10)

    # ── Resources ─────────────────────────────────────────────
    _render_resource_controls(sim_state, app)


def _render_resource_controls(sim_state: Any, app: Any) -> None:
    """Renders the resource request and release controls."""
    st.markdown("**Resources**")
    offered = actions.available_resources(sim_state)

    if not offered:
        unavailable_note(
            "None of Camera, Microphone, GPS or Audio is registered with the "
            "ResourceManager, so locks cannot be requested."
        )
        return

    held = sorted(app.held_resources)
    request_options = [r for r in offered if r not in held] or offered
    selectable = list(offered)
    resource_col, request_col, release_col = st.columns([2, 1, 1])

    with resource_col:
        resource_id = st.selectbox(
            "Resource",
            selectable,
            key="app_controls_resource",
        )
    with request_col:
        if st.button(
            "🔒 Request",
            key="request_resource",
            use_container_width=True,
            disabled=resource_id in held or app.is_evicted,
            help=(
                "This application already holds it."
                if resource_id in held
                else "An evicted application cannot acquire a lock."
                if app.is_evicted
                else "Requests the semaphore lock."
            ),
        ):
            _run(actions.request_resource(sim_state, app.app_id, resource_id))
    with release_col:
        if st.button(
            "🔓 Release",
            key="release_resource",
            use_container_width=True,
            disabled=resource_id not in held,
            help=(
                "Release the held lock."
                if resource_id in held
                else "This application does not hold it."
            ),
        ):
            _run(actions.release_resource(sim_state, app.app_id, resource_id))

    if held:
        st.caption(
            f"{app.name} holds: {', '.join(held)} · "
            f"available to request: {', '.join(request_options)}"
        )


def _run(result: actions.ActionResult) -> None:
    """Records an operation outcome and refreshes the page.

    The rerun re-reads the registry, so every table and counter on the page
    reflects the backend state the operation produced rather than a local guess.
    """
    _push_feedback(result)
    st.rerun()


# ──────────────────────────────────────────────────────────────
# Detail inspector
# ──────────────────────────────────────────────────────────────

def _render_inspector(sim_state: Any, db: Optional[Any], apps: Sequence[Any]) -> None:
    """Renders the full detail view for the selected application."""
    section_header("Application detail", hint="Read-only view")
    spacer(6)

    if not apps:
        unavailable_note("There is no application to inspect.")
        return

    # The operation panel above owns the selection; fall back to the first
    # application so the inspector is never blank on a fresh page.
    app = None
    app_id = st.session_state.get("app_controls_selected")
    if app_id:
        app = actions.get_application(sim_state, app_id)
    if app is None:
        app = apps[0]

    detail = actions.describe_application(sim_state, app)

    col1, col2, col3 = st.columns(3)
    with col1:
        _tile("Memory footprint", f"{app.memory_footprint} MB", "blue")
    with col2:
        _tile("Priority", f"P{app.priority}", "purple")
    with col3:
        _tile(
            "Status",
            str(app.status.value),
            "green" if app.is_active else "red",
        )

    spacer(8)

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"**App ID:** `{app.app_id}`")
        st.markdown(f"**State:** {status_badge(app.state.value)}")
        st.markdown(
            f"**Reference bit:** {'🟢 1 (recently used)' if app.reference_bit else '⚪ 0 (eviction candidate)'}"
        )
        st.markdown(f"**Last access:** {_format_ts(app.last_access_time)}")
        if app.is_evicted:
            st.caption(
                "Eviction is performed by the simulation engine (RAAE or "
                "BASELINE_CLOCK), not from this page."
            )
    with col_b:
        st.markdown("**Held resources:**")
        if detail["held_resources"]:
            for resource_id in detail["held_resources"]:
                st.markdown(f"&nbsp;&nbsp;🔒 `{resource_id}`")
        else:
            st.markdown("&nbsp;&nbsp;_None_")

        if detail["queued_resources"]:
            st.markdown("**Queued for:**")
            for resource_id in detail["queued_resources"]:
                st.markdown(f"&nbsp;&nbsp;⏳ `{resource_id}`")

    spacer(8)

    if detail["resource_rows"]:
        styled_table(
            ["Resource", "Status", "Holder", "Waiting queue"],
            detail["resource_rows"],
            caption="Lock state from the ResourceManager",
        )
        spacer(8)

    _render_eviction_history(db, app)


def _tile(label: str, value: str, accent: str) -> None:
    """Renders a small metric tile using the shared metric card."""
    metric_card(label, value, accent=accent)


def _render_eviction_history(db: Optional[Any], app: Any) -> None:
    """Renders the eviction log rows recorded for this app, if any."""
    st.markdown("**Eviction history**")
    if db is None:
        unavailable_note(
            "No database is attached, so the eviction log cannot be read."
        )
        return

    try:
        rows = db.eviction_log.get_evictions_by_app(app.app_id) or []
    except Exception as error:  # noqa: BLE001 - the page stays usable
        unavailable_note(f"The eviction log could not be read: {error}")
        return

    if not rows:
        st.caption("No eviction decisions have been recorded for this application.")
        return

    table_rows = [
        [
            _esc(row.get("algorithm", "—")),
            _esc(str(row.get("reason", "—"))[:70]),
            "✅" if row.get("lock_checked") else "❌",
            "✅" if row.get("safe_release") else "—",
            status_badge(str(row.get("result", "—"))),
            _format_ts(row.get("timestamp")),
        ]
        for row in rows[:10]
    ]
    styled_table(
        ["Algorithm", "Reason", "Lock checked", "Safe release", "Result", "Logged"],
        table_rows,
    )


def _format_ts(timestamp: Any) -> str:
    """Formats a UNIX timestamp, or says so when it cannot be read."""
    if timestamp is None:
        return "—"
    try:
        return datetime.datetime.fromtimestamp(float(timestamp)).strftime("%H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(timestamp)