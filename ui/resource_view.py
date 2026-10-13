"""Resource Monitoring page for the RAAE Streamlit app.

Watches the four monitored hardware resources — Camera, Microphone, GPS and
Audio — and the contention around them.

What the page shows:

* **Resource status** — name, current holder, free/locked status, waiting queue
  and how many applications are queued, plus a single-holder integrity check on
  every resource.
* **Controls** — request or release a lock for a selected application. Both go
  through :mod:`ui.app_actions`, which is the only path to the ResourceManager,
  so the semaphore's own checks always run.
* **Conflict monitor** — every conflict the semaphore has actually produced
  (a holder plus at least one queued application), the backend's Wound-Wait
  decision and reason, and the outcome read back from the backend afterwards,
  alongside the recent ConflictLog rows.

Holding a resource never implies an eviction here: this page grants and queues
locks only. Deciding which application to evict, and whether its locks may be
dropped, is the RAAE engine's job on the Simulation page.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from ui import resource_actions as actions
from ui.components import (
    _esc,
    empty_state,
    metric_row,
    section_header,
    spacer,
    status_badge,
    styled_table,
    unavailable_note,
)

#: Key holding the adjudications shown for the current render.
ADJUDICATION_KEY = "resource_adjudications"


def render(
    sim_state: Optional[Any] = None,
    db: Optional[Any] = None,
) -> None:
    """Renders the Resource Monitoring page.

    Args:
        sim_state: Live ``SimulationState`` holding the ResourceManager.
        db: ``DatabaseManager`` used to read recorded conflict events.
    """
    st.markdown("## 🔒 Resource Monitor")
    st.caption(
        "Camera, Microphone, GPS and Audio — ownership, waiting queues and "
        "Wound-Wait decisions, read live from the ResourceManager."
    )

    _render_feedback()

    if sim_state is None or actions.available_resources(sim_state) == []:
        empty_state(
            "🔒",
            "No resources registered",
            "Resources are created by the ResourceManager. Use “🚀 Initialise "
            "Demo State” in the sidebar to attach a simulation state.",
        )
        return

    rows = actions.resource_rows(sim_state)
    _render_summary(rows)
    spacer(14)
    _render_status_table(rows)
    spacer(14)
    _render_cards(rows)
    spacer(16)
    _render_controls(sim_state)
    spacer(16)
    _render_conflict_monitor(sim_state, db)
    spacer(16)
    _render_conflict_events(sim_state, db)


# ──────────────────────────────────────────────────────────────
# Feedback
# ──────────────────────────────────────────────────────────────

def _push(message: str, level: str = "info") -> None:
    """Stores a message for display on this and the next render."""
    st.session_state["resource_feedback"] = [
        *st.session_state.get("resource_feedback", []),
        {"level": level, "message": message},
    ][-4:]


def _render_feedback() -> None:
    """Renders stored messages, pushed only after the backend has acted."""
    renderers = {"success": st.success, "error": st.error, "warning": st.warning, "info": st.info}
    for entry in st.session_state.get("resource_feedback", []):
        renderers.get(entry.get("level", "info"), st.info)(entry["message"])


# ──────────────────────────────────────────────────────────────
# Status
# ──────────────────────────────────────────────────────────────

def _render_summary(rows: List[Any]) -> None:
    """Renders the resource-wide counters."""
    locked = [r for r in rows if r.holder_id]
    queued = [r for r in rows if r.waiting_count]
    problems = [r for r in rows if r.problems]

    metric_row(
        [
            {
                "label": "Resources monitored",
                "value": len(rows),
                "delta": ", ".join(r.resource_id for r in rows) or "none registered",
                "accent": "blue",
                "unavailable": not rows,
            },
            {
                "label": "Locked",
                "value": len(locked),
                "delta": ", ".join(r.resource_id for r in locked) or "all free",
                "accent": "orange",
                "unavailable": not rows,
            },
            {
                "label": "Free",
                "value": len(rows) - len(locked),
                "delta": "available to acquire",
                "accent": "green",
                "unavailable": not rows,
            },
            {
                "label": "Contended",
                "value": len(queued),
                "delta": (
                    f"{sum(r.waiting_count for r in queued)} application(s) queued"
                    if queued
                    else "no waiting queues"
                ),
                "accent": "red" if queued else "green",
                "unavailable": not rows,
            },
            {
                "label": "Integrity",
                "value": "OK" if not problems else "Check",
                "delta": (
                    "every resource has at most one holder"
                    if not problems
                    else f"{len(problems)} resource(s) break the model"
                ),
                "accent": "green" if not problems else "red",
                "unavailable": not rows,
            },
        ]
    )


def _status_row(row: Any) -> List[str]:
    """Formats one resource status row."""
    waiting = ", ".join(_esc(name) for _, name in row.waiting) if row.waiting else "—"
    return [
        f"<strong>{_esc(row.name)}</strong>",
        _esc(row.resource_id),
        status_badge(row.status),
        (
            f"{_esc(row.holder_name)}<br/><span class='event-muted'>{_esc(row.holder_id)}</span>"
            if row.holder_id
            else "<span class='event-muted'>—</span>"
        ),
        f"{row.available_units}/{row.capacity}",
        waiting,
        str(row.waiting_count) if row.waiting_count is not None else "Unavailable",
    ]


def _render_status_table(rows: List[Any]) -> None:
    """Renders the ownership table."""
    section_header("Resource status", hint="from the ResourceManager")
    spacer(6)

    if not rows:
        unavailable_note("No resource could be read from the ResourceManager.")
        return

    styled_table(
        ["Resource", "ID", "Status", "Current holder", "Units", "Waiting queue", "Queued apps"],
        [_status_row(row) for row in rows],
    )


def _render_cards(rows: List[Any]) -> None:
    """Renders one card per resource, including its integrity check."""
    section_header("Ownership detail")
    spacer(6)

    if not rows:
        return

    columns = st.columns(min(len(rows), 4))
    for column, row in zip(columns, rows):
        with column:
            status_class = {"FREE": "#3fb950", "LOCKED": "#d29922", "WAITING": "#f85149"}.get(
                row.status, "#8b949e"
            )
            holder = (
                f"{_esc(row.holder_name)}<br/><span class='event-muted'>{_esc(row.holder_id)}</span>"
                if row.holder_id
                else "<span class='event-muted'>No holder</span>"
            )
            queue = (
                ", ".join(_esc(name) for _, name in row.waiting)
                if row.waiting
                else "<span class='event-muted'>Empty</span>"
            )
            integrity = (
                "<div class='gauge-note' style='color:#3fb950;'>✔ Single holder holds</div>"
                if not row.problems
                else "<div class='gauge-note' style='color:#f85149;'>⚠ "
                + "; ".join(_esc(p) for p in row.problems)
                + "</div>"
            )
            st.markdown(
                f"""
                <div class="metric-card" style="border-left:3px solid {status_class};">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <span style="font-weight:600;font-size:0.95rem;">{_esc(row.resource_id)}</span>
                        {status_badge(row.status)}
                    </div>
                    <div style="font-size:0.78rem;color:#8b949e;margin-top:0.4rem;">{_esc(row.name)}</div>
                    <div style="font-size:0.82rem;margin-top:0.3rem;">
                        Units: {row.available_units}/{row.capacity}
                    </div>
                    <div style="font-size:0.82rem;margin-top:0.5rem;">Holder: {holder}</div>
                    <div style="font-size:0.82rem;margin-top:0.5rem;">Queue: {queue}</div>
                    {integrity}
                </div>
                """,
                unsafe_allow_html=True,
            )


# ──────────────────────────────────────────────────────────────
# Controls
# ──────────────────────────────────────────────────────────────

def _render_controls(sim_state: Any) -> None:
    """Renders the request and release controls for a selected application."""
    section_header(
        "Acquire or release",
        hint="every action goes through ResourceManager",
    )
    spacer(6)

    st.caption(
        "Locks are granted only when the semaphore allows it. Holding a resource "
        "never means the application should be evicted — that decision belongs to "
        "the RAAE engine."
    )

    apps = _registry(sim_state)
    if not apps:
        unavailable_note("No applications are registered, so no lock can be requested.")
        return

    resources = actions.available_resources(sim_state)
    labels = {app_id: f"{app.name} ({app_id})" for app_id, app in apps.items()}

    app_col, resource_col, request_col, release_col = st.columns([2, 2, 1, 1])
    with app_col:
        app_id = st.selectbox(
            "Application",
            list(labels.keys()),
            format_func=lambda x: labels[x],
            key="resource_app",
        )
    with resource_col:
        resource_id = st.selectbox(
            "Resource",
            resources,
            key="resource_target",
        )

    app = apps[app_id]
    holds = resource_id in (getattr(app, "held_resources", set()) or set())
    row = next((r for r in actions.resource_rows(sim_state) if r.resource_id == resource_id), None)
    held_elsewhere = row.holder_id is not None and row.holder_id != app_id

    with request_col:
        if st.button(
            "🔒 Request",
            key="resource_request",
            use_container_width=True,
            disabled=holds,
            help=(
                "This application already holds it."
                if holds
                else "Requests the lock; the semaphore may queue it instead."
            ),
        ):
            result = actions.request_resource(sim_state, app_id, resource_id)
            _push(result.message, "success" if result.ok else "warning")
            st.rerun()

    with release_col:
        if st.button(
            "🔓 Release",
            key="resource_release",
            use_container_width=True,
            disabled=not holds,
            help=(
                "Release the held lock and let the semaphore re-process the queue."
                if holds
                else "This application does not hold it."
            ),
        ):
            result = actions.release_resource(sim_state, app_id, resource_id)
            _push(result.message, "success" if result.ok else "error")
            st.rerun()

    if held_elsewhere:
        st.caption(
            f"{_esc(row.holder_id)} currently holds {_esc(resource_id)}; a request "
            "will be queued rather than granted."
        )


def _registry(sim_state: Any) -> Dict[str, Any]:
    """Returns the registered applications, for the selector."""
    manager = getattr(sim_state, "memory_manager", None)
    if manager is None:
        return {}
    try:
        return dict(manager.apps)
    except Exception:
        return {}


# ──────────────────────────────────────────────────────────────
# Conflict monitor
# ──────────────────────────────────────────────────────────────

def _render_conflict_monitor(sim_state: Any, db: Optional[Any]) -> None:
    """Renders live conflicts with the backend's Wound-Wait adjudication."""
    conflicts = actions.live_conflicts(sim_state)
    stored: Dict[str, Any] = st.session_state.get(ADJUDICATION_KEY) or {}

    section_header(
        "Conflict monitor",
        hint=f"{len(conflicts)} live conflict(s)" if conflicts else "no live conflict",
    )
    spacer(6)

    if conflicts:
        rows = []
        for conflict in conflicts:
            rows.append(
                [
                    f"{_esc(conflict.requester_name)}<br/><span class='event-muted'>{_esc(conflict.requester_id)}</span>",
                    f"P{_esc(conflict.requester_priority)}",
                    _esc(_ts(conflict.requester_last_access)),
                    f"{_esc(conflict.holder_name)}<br/><span class='event-muted'>{_esc(conflict.holder_id)}</span>",
                    f"P{_esc(conflict.holder_priority)}",
                    _esc(_ts(conflict.holder_last_access)),
                    _esc(conflict.resource_id),
                    str(conflict.queue_length),
                ]
            )
        styled_table(
            [
                "Requesting app",
                "Req. priority",
                "Req. last access",
                "Current holder",
                "Holder priority",
                "Holder last access",
                "Contested resource",
                "Queued",
            ],
            rows,
        )

        spacer(10)

        for conflict in conflicts:
            with st.expander(
                f"⚖️ Wound-Wait decision — {conflict.resource_id} "
                f"({conflict.requester_name} vs {conflict.holder_name})",
                expanded=conflict.resource_id in stored,
            ):
                button_col, apply_col = st.columns(2)
                with button_col:
                    if st.button(
                        "Evaluate decision",
                        key=f"evaluate_{conflict.resource_id}",
                        use_container_width=True,
                        help="Runs the backend's Wound-Wait manager without changing any lock.",
                    ):
                        verdict = actions.evaluate_conflict(sim_state, db, conflict.resource_id)
                        if verdict is None:
                            _push(
                                f"The backend reported no conflict for {_esc(conflict.resource_id)}.",
                                "warning",
                            )
                        else:
                            stored[conflict.resource_id] = verdict
                            st.session_state[ADJUDICATION_KEY] = stored
                            _push(
                                f"Wound-Wait returned {verdict.decision} for "
                                f"{_esc(conflict.resource_id)}.",
                                "info",
                            )
                        st.rerun()

                verdict = stored.get(conflict.resource_id)
                with apply_col:
                    if st.button(
                        "Apply WOUND_HOLDER",
                        key=f"apply_{conflict.resource_id}",
                        use_container_width=True,
                        disabled=verdict is None or not verdict.is_wound,
                        help=(
                            "Asks the backend to release the wounded holder's lock "
                            "through the ResourceManager."
                            if verdict is not None and verdict.is_wound
                            else "Only available after an evaluation returns WOUND_HOLDER."
                        ),
                    ):
                        applied = actions.apply_wound_wait(sim_state, db, conflict.resource_id)
                        if applied is None:
                            _push("No conflict remained to apply.", "warning")
                        else:
                            stored[conflict.resource_id] = applied
                            st.session_state[ADJUDICATION_KEY] = stored
                            _push(
                                f"Applied {applied.decision}: "
                                + actions.outcome_for(sim_state, applied)["detail"],
                                "success" if applied.released else "warning",
                            )
                        st.rerun()

                if verdict is not None:
                    _render_verdict(sim_state, verdict)
    else:
        st.caption(
            "A conflict exists only where a resource is held and another "
            "application is queued behind it. None is queued right now."
        )

    # ── On-demand contention evaluator ─────────────────────────
    apps = _registry(sim_state)
    if apps:
        spacer(8)
        with st.expander(
            "🔍 Adjudicate Contention / Pre-Request Analysis",
            expanded=not conflicts,
        ):
            st.caption(
                "Test the backend's Wound-Wait conflict resolution between any requesting application "
                "and resource holder. Supports WOUND_HOLDER, WAIT, and NO_CONFLICT outcomes."
            )
            labels = {
                app_id: f"{app.name} ({app_id}) [P{getattr(app, 'priority', '?')}]"
                for app_id, app in apps.items()
            }
            c1, c2, c3 = st.columns([2, 2, 1])
            with c1:
                eval_app_id = st.selectbox(
                    "Requesting application",
                    list(labels.keys()),
                    format_func=lambda x: labels[x],
                    key="eval_adhoc_app",
                )
            with c2:
                eval_res_id = st.selectbox(
                    "Target resource",
                    actions.available_resources(sim_state),
                    key="eval_adhoc_res",
                )
            with c3:
                spacer(24)
                if st.button("⚖️ Evaluate", key="eval_adhoc_btn", use_container_width=True):
                    adhoc_v = actions.evaluate_conflict(
                        sim_state, db, eval_res_id, requester_id=eval_app_id
                    )
                    if adhoc_v:
                        stored[f"adhoc_{eval_res_id}_{eval_app_id}"] = adhoc_v
                        st.session_state[ADJUDICATION_KEY] = stored
                        _push(
                            f"Wound-Wait returned {adhoc_v.decision} for {eval_res_id}.",
                            "info",
                        )
                    st.rerun()

            current_adhoc = stored.get(f"adhoc_{eval_res_id}_{eval_app_id}")
            if current_adhoc is not None:
                _render_verdict(sim_state, current_adhoc)


def _render_verdict(sim_state: Any, verdict: Any) -> None:
    """Renders one adjudication and the outcome read back from the backend."""
    outcome = actions.outcome_for(sim_state, verdict)

    styled_table(
        ["Field", "Value"],
        [
            ["Requesting application", _esc(verdict.requester_id or "—")],
            ["Current holder", _esc(verdict.holder_id or "—")],
            ["Contested resource", _esc(verdict.resource_id)],
            ["Requester priority / access", f"P{_esc(verdict.requester_priority)} · {_esc(_ts(verdict.requester_timestamp))}"],
            ["Holder priority / access", f"P{_esc(verdict.holder_priority)} · {_esc(_ts(verdict.holder_timestamp))}"],
            ["Wound-Wait decision", status_badge(verdict.decision)],
            ["Strategy", _esc(verdict.strategy or "—")],
            ["Resolved by", _esc(verdict.resolved_by or "—")],
            ["Reason for the decision", _esc(verdict.reason or "—")],
            ["Release requested", "Yes" if verdict.released else "No"],
            [
                "Conflict row persisted",
                "Yes" if verdict.persisted else "No",
            ],
            ["Actual outcome", f"<strong>{_esc(outcome['state'])}</strong> — {_esc(outcome['detail'])}"],
        ],
        caption="Reported by backend/conflict_manager",
    )

    if verdict.note:
        st.caption(verdict.note)


def _ts(timestamp: Any) -> str:
    """Formats a UNIX timestamp for the conflict table."""
    return actions._clock(timestamp)


# ──────────────────────────────────────────────────────────────
# Recent conflict events
# ──────────────────────────────────────────────────────────────

def _render_conflict_events(sim_state: Any, db: Optional[Any]) -> None:
    """Renders the recent ConflictLog rows."""
    events = actions.recent_conflict_events(db, limit=20)
    section_header(
        "Recent conflict events",
        hint=f"{len(events)} row(s) in ConflictLog",
    )
    spacer(6)

    if not events:
        if db is None:
            unavailable_note(
                "No database is attached, so recorded conflicts cannot be read."
            )
        else:
            st.caption(
                "ConflictLog holds no rows. Events appear here only once the "
                "backend has recorded one — nothing is generated for display."
            )
        return

    styled_table(
        ["Time", "Waiting app", "Blocking app", "Resource", "Strategy", "Resolved by", "Details"],
        actions.event_rows(events, actions.app_name_map(sim_state)),
    )