"""Dashboard / Overview page for the RAAE Streamlit app.

The page is a read-only monitor over the existing backend. It never builds a
simulation, never mutates an app or a resource, and never keeps a second copy of
simulation state: it reads the live ``SimulationState`` and the attached
``DatabaseManager`` that :mod:`app` passes in, plus the run results that
:mod:`ui.simulation_view` stores in ``st.session_state`` (read through
:mod:`ui.state`).

Sections rendered:

* **Header** — title, simulation status, current tick and selected eviction
  policy, plus a manual refresh control.
* **Metric cards** — total / used / available RAM, memory utilization, active
  applications, total evictions and resource conflicts.
* **Memory monitor** — RAM usage bar, memory-pressure indicator, a used-versus-
  available gauge, and a utilization chart over simulation ticks.
* **Application summary** — foreground / background / waiting / evicted counts
  with a per-app table.
* **Recent event log** — timestamp, event type, application, resource, decision
  and explanation, merged across the three log tables.

Every value is read from the backend or the database. A metric that cannot be
read is rendered as ``Unavailable`` and the reason is reported in the "Data
availability" panel at the foot of the page rather than replaced by a default.
"""

from __future__ import annotations

import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import streamlit as st

from ui import state
from ui.components import (
    UNAVAILABLE,
    _format_timestamp,
    _esc,
    diagnostics_panel,
    empty_state,
    header_chips,
    header_title,
    memory_gauge,
    metric_row,
    pressure_bar,
    pressure_badge,
    section_header,
    spacer,
    state_composition_bar,
    status_badge,
    styled_table,
    unavailable_note,
    utilization_chart,
)

# Rows pulled from each log table before the newest ones are displayed.
LOG_FETCH_LIMIT = 200
EVENT_DISPLAY_LIMIT = 25
PRESSURE_SAMPLE_LIMIT = 200

EVENT_TYPES = ("EVICTION", "CONFLICT", "MEMORY")

#: Colour used for each decision/result value found in the log tables.
DECISION_COLORS = {
    "EVICTED": "#f85149",
    "EVICT": "#f85149",
    "SKIPPED": "#d29922",
    "CONFLICT": "#d29922",
    "WAIT": "#58a6ff",
    "NONE": "#f85149",
    "RAAE_PROTECT": "#3fb950",
    "WOUND_WAIT": "#58a6ff",
    "ALLOCATE": "#58a6ff",
    "DEALLOCATE": "#8b949e",
    "PRESSURE": "#8b949e",
}

EVENT_TYPE_COLORS = {
    "EVICTION": "#f85149",
    "CONFLICT": "#d29922",
    "MEMORY": "#58a6ff",
}

APP_STATES = ("FOREGROUND", "BACKGROUND", "WAITING", "EVICTED")


# ──────────────────────────────────────────────────────────────
# Page entry point
# ──────────────────────────────────────────────────────────────

def render(
    sim_state: Optional[Any] = None,
    db: Optional[Any] = None,
) -> None:
    """Renders the Overview / Dashboard page.

    Args:
        sim_state: Live ``SimulationState``, or None when none is attached.
        db: Attached ``DatabaseManager``, or None when persistence is disabled.
    """
    errors: List[Tuple[str, str]] = []
    sources: List[Tuple[str, str]] = []

    run = state.active_run()
    apps = _read_apps(sim_state, db, errors, sources)
    memory = _read_memory(sim_state, db, run, errors, sources)
    counters = _read_counters(db, run, errors, sources)
    events, event_source = _read_events(db, errors, sources)
    utilization = _utilization_series(run, memory, db, errors, sources)

    _render_header()
    _render_metric_cards(memory, counters, apps)
    _render_memory_monitor(memory, utilization)
    _render_application_summary(apps)
    _render_event_log(events, event_source, db)
    _render_availability(sources, errors)


# ──────────────────────────────────────────────────────────────
# Data access — every read is guarded
# ──────────────────────────────────────────────────────────────

def _guard(
    errors: List[Tuple[str, str]],
    operation: str,
    call: Callable[[], Any],
    default: Any = None,
) -> Any:
    """Runs a backend read, recording rather than raising a failure.

    A missing table, a closed connection or a broken column must not take the
    whole page down, so the error is appended to *errors* and *default* is
    returned instead.
    """
    try:
        return call()
    except Exception as error:  # noqa: BLE001 - a monitor must survive bad reads
        errors.append((operation, f"{type(error).__name__}: {error}"))
        return default


def _app_state_name(app: Any) -> str:
    """Returns an app's execution state as a plain string."""
    state_value = getattr(app, "state", None)
    value = getattr(state_value, "value", state_value)
    return str(value).upper() if value else "UNKNOWN"


def _read_apps(
    sim_state: Optional[Any],
    db: Optional[Any],
    errors: List[Tuple[str, str]],
    sources: List[Tuple[str, str]],
) -> List[Any]:
    """Returns registered applications from the live registry, else the database."""
    memory_manager = getattr(sim_state, "memory_manager", None)
    if memory_manager is not None:
        apps = _guard(
            errors,
            "MemoryManager.apps",
            lambda: list(memory_manager.apps.values()),
            [],
        ) or []
        sources.append(("Applications", "Live MemoryManager registry"))
        return apps

    if db is not None:
        apps = _guard(errors, "Apps.get_all_apps", lambda: db.apps.get_all_apps(), []) or []
        sources.append(
            ("Applications", f"SQLite Apps table ({len(apps)} row(s))")
        )
        return apps

    sources.append(("Applications", "None attached"))
    return []


def _read_memory(
    sim_state: Optional[Any],
    db: Optional[Any],
    run: Optional[Dict[str, Any]],
    errors: List[Tuple[str, str]],
    sources: List[Tuple[str, str]],
) -> Dict[str, Any]:
    """Reads RAM totals from the live MemoryManager, else from telemetry.

    Falls back to the most recent persisted memory-pressure sample, which is the
    only place the total RAM size survives once the live state is gone, and then
    to the final reading the run itself reported. Returns a mapping whose values
    are None when nothing could be read.
    """
    blank: Dict[str, Any] = {
        "total_mb": None,
        "used_mb": None,
        "available_mb": None,
        "utilization_pct": None,
        "pressure": None,
        "under_pressure": None,
        "source": "None attached",
    }

    memory_manager = getattr(sim_state, "memory_manager", None)
    if memory_manager is not None:
        total = _guard(errors, "MemoryManager.total_memory", lambda: int(memory_manager.total_memory))
        used = _guard(errors, "MemoryManager.get_used_memory", lambda: int(memory_manager.get_used_memory()))
        available = _guard(errors, "MemoryManager.get_free_memory", lambda: int(memory_manager.get_free_memory()))
        pct = _guard(errors, "MemoryManager.get_usage_percentage", lambda: float(memory_manager.get_usage_percentage()))
        level = _guard(errors, "MemoryManager.get_pressure_level", lambda: memory_manager.get_pressure_level().value)
        under = _guard(errors, "MemoryManager.is_under_pressure", lambda: memory_manager.is_under_pressure())

        sources.append(("RAM", "Live MemoryManager"))
        return {
            "total_mb": total,
            "used_mb": used,
            "available_mb": available,
            "utilization_pct": pct,
            "pressure": str(level) if level is not None else None,
            "under_pressure": under,
            "source": "Live MemoryManager",
        }

    if db is not None:
        sample = _guard(
            errors,
            "SystemEvents.get_pressure_samples",
            lambda: (db.system_events.get_pressure_samples(limit=1) or [None])[0],
        )
        if sample:
            total = _int_or_none(sample.get("total_memory"))
            used = _int_or_none(sample.get("used_memory"))
            pct = (used / total * 100.0) if (total and used is not None) else None
            available = max(0, total - used) if (total is not None and used is not None) else None
            sources.append(
                (
                    "RAM",
                    f"SystemEvents sample @ {_format_timestamp(sample.get('timestamp'))}",
                )
            )
            return {
                "total_mb": total,
                "used_mb": used,
                "available_mb": available,
                "utilization_pct": pct,
                "pressure": str(sample.get("pressure_level")) if sample.get("pressure_level") else None,
                "under_pressure": None,
                "source": "SystemEvents pressure sample (no live state)",
            }

        sources.append(("RAM", "SystemEvents (no samples recorded)"))

    # Last resort: the final reading the run itself reported. The result carries
    # the ratio and the memory still held, which between them give the total.
    if run:
        total = _infer_total_memory(run, blank)
        used = _int_or_none(run.get("final_memory_used"))
        if total and used is not None:
            pct = used / total * 100.0
            sources.append(
                ("RAM", f"Final reading of the last {run.get('policy', 'run')}")
            )
            return {
                "total_mb": total,
                "used_mb": used,
                "available_mb": max(0, total - used),
                "utilization_pct": pct,
                "pressure": None,
                "under_pressure": None,
                "source": f"Final reading of the last {run.get('policy', 'run')}",
            }
        sources.append(("RAM", "Run reported no usable final reading"))

    return blank


def _read_counters(
    db: Optional[Any],
    run: Optional[Dict[str, Any]],
    errors: List[Tuple[str, str]],
    sources: List[Tuple[str, str]],
) -> Dict[str, Optional[int]]:
    """Reads eviction and conflict counters from the analytics repository.

    With no database attached the counters come from the last run's own result,
    which is real engine telemetry rather than a default; the origin is recorded
    so the card can say where the number came from.
    """
    counters: Dict[str, Optional[int]] = {
        "evictions": None,
        "blocked_evictions": None,
        "freezes": None,
        "conflicts": None,
        "acquisitions": None,
        "releases": None,
        "queued_requests": None,
    }

    if db is not None:
        summary = _guard(errors, "AnalyticsRepository.summary", lambda: db.analytics.summary())
        if summary:
            # The repository names its keys `total_*`; the card labels are
            # mapped explicitly rather than by name, so the two cannot drift.
            counters.update(
                {
                    "evictions": _int_or_none(summary.get("total_evictions")),
                    "blocked_evictions": _int_or_none(summary.get("blocked_evictions")),
                    "freezes": _int_or_none(summary.get("total_freezes")),
                    "conflicts": _int_or_none(summary.get("total_conflicts")),
                    "acquisitions": _int_or_none(summary.get("successful_acquisitions")),
                    "releases": _int_or_none(summary.get("resource_releases")),
                    "queued_requests": _int_or_none(summary.get("queued_requests")),
                }
            )
            sources.append(
                ("Evictions & conflicts", "SQLite analytics (EvictionLog, ConflictLog)")
            )
            return counters
        sources.append(("Evictions & conflicts", "SQLite analytics returned no summary"))

    if run:
        counters["evictions"] = _int_or_none(run.get("eviction_count"))
        counters["blocked_evictions"] = _int_or_none(run.get("blocked_waiting_evictions"))
        counters["freezes"] = _int_or_none(run.get("freeze_incidents"))
        counters["conflicts"] = _int_or_none(run.get("resource_conflicts"))
        counters["releases"] = _int_or_none(run.get("resource_releases"))
        policy = run.get("policy", "run")
        sources.append(
            ("Evictions & conflicts", f"Last {policy} result (no database attached)")
        )
        return counters

    sources.append(("Evictions & conflicts", "None attached"))
    return counters


def _read_events(
    db: Optional[Any],
    errors: List[Tuple[str, str]],
    sources: List[Tuple[str, str]],
) -> Tuple[List[Dict[str, Any]], str]:
    """Merges the three log tables into one newest-first timeline.

    Returns ``(events, origin)``. Each table is queried independently so a single
    missing table degrades that row type instead of the whole log.
    """
    if db is None:
        sources.append(("Event log", "None attached"))
        return [], "No database attached — the event log needs persisted log tables."

    evictions = _guard(
        errors,
        "EvictionLog.get_all_evictions",
        lambda: db.eviction_log.get_all_evictions(LOG_FETCH_LIMIT) or [],
        [],
    ) or []
    conflicts = _guard(
        errors,
        "ConflictLog.get_all_conflicts",
        lambda: db.conflict_log.get_all_conflicts(LOG_FETCH_LIMIT) or [],
        [],
    ) or []
    memory_events = _guard(
        errors,
        "MemoryEvents.get_all_events",
        lambda: db.memory_events.get_all_events(LOG_FETCH_LIMIT) or [],
        [],
    ) or []

    sources.append(
        (
            "Event log",
            f"SQLite EvictionLog ({len(evictions)}), ConflictLog ({len(conflicts)}), "
            f"MemoryEvents ({len(memory_events)})",
        )
    )

    events: List[Dict[str, Any]] = []
    for row in evictions:
        events.append(
            {
                "timestamp": row.get("timestamp"),
                "event_type": "EVICTION",
                "app_id": row.get("app_id"),
                "resource": None,
                "decision": row.get("result"),
                "explanation": row.get("reason"),
            }
        )
    for row in conflicts:
        explanation = row.get("details")
        if not explanation:
            resolved_by = row.get("resolved_by")
            explanation = f"Resolved by {resolved_by}" if resolved_by else None
        events.append(
            {
                "timestamp": row.get("timestamp"),
                "event_type": "CONFLICT",
                "app_id": row.get("blocking_app_id"),
                "resource": row.get("resource_id"),
                "decision": row.get("resolution_strategy"),
                "explanation": explanation,
            }
        )
    for row in memory_events:
        events.append(
            {
                "timestamp": row.get("timestamp"),
                "event_type": "MEMORY",
                "app_id": row.get("app_id"),
                "resource": None,
                "decision": row.get("action"),
                "explanation": (
                    f"{row.get('memory_before')} MB → {row.get('memory_after')} MB "
                    f"· {row.get('pressure_level')} pressure"
                ),
            }
        )

    events.sort(
        key=lambda e: (e["timestamp"] is not None, e["timestamp"] or 0.0),
        reverse=True,
    )
    return events, "SQLite log tables"


def _int_or_none(value: Any) -> Optional[int]:
    """Coerces a value to int, mapping anything unusable onto None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _infer_total_memory(
    run: Optional[Dict[str, Any]],
    memory: Dict[str, Any],
) -> Optional[int]:
    """Recovers the run's total RAM in MB.

    The live MemoryManager knows its own limit, and the database stores it on
    every pressure sample, so both are used directly. A ``SimulationResult``
    reports only the final ratio, so its total is reconstructed from that ratio
    and the memory still held — which is exact to the rounding the result applies
    (2 decimal places on the percentage).
    """
    total = _int_or_none(memory.get("total_mb"))
    if total:
        return total
    if not run:
        return None

    pct = run.get("final_usage_percentage")
    used = run.get("final_memory_used")
    try:
        pct_value = float(pct)
        used_value = float(used)
    except (TypeError, ValueError):
        return None
    if pct_value <= 0 or used_value < 0:
        return None
    return int(round(used_value * 100.0 / pct_value))


def _utilization_series(
    run: Optional[Dict[str, Any]],
    memory: Dict[str, Any],
    db: Optional[Any],
    errors: List[Tuple[str, str]],
    sources: List[Tuple[str, str]],
) -> Optional[Dict[str, Any]]:
    """Builds the memory-utilization series for the chart.

    Prefers per-tick detail from the last run — that is literally "utilization
    over simulation ticks". With no run, falls back to the persisted
    memory-pressure samples, which are recorded per observation rather than per
    tick. Returns None when neither source holds data.
    """
    ticks = (run or {}).get("ticks_detail") or []
    total = _infer_total_memory(run, memory)

    if ticks:
        points: List[Tuple[str, float]] = []
        for entry in ticks:
            used = _int_or_none(entry.get("memory_after"))
            if used is None:
                continue
            label = f"Tick {entry.get('tick', '?')}"
            points.append((label, (used / total * 100.0) if total else float(used)))
        if points:
            values = [p[1] for p in points]
            sources.append(
                ("Utilization chart", f"Last run tick detail ({len(points)} tick(s))")
            )
            return {
                "points": points,
                "ylabel": "Memory utilization (%)" if total else "Memory used (MB)",
                "caption": (
                    f"{len(points)} tick(s) · peak {max(values):.1f}"
                    f"{'%' if total else ' MB'} · final {values[-1]:.1f}"
                    f"{'%' if total else ' MB'}"
                    + ("" if total else " · total RAM unavailable, showing MB")
                ),
            }

    if db is not None:
        samples = _guard(
            errors,
            "SystemEvents.get_pressure_samples",
            lambda: db.system_events.get_pressure_samples(PRESSURE_SAMPLE_LIMIT) or [],
            [],
        ) or []
        points = []
        for sample in reversed(samples):
            sample_total = _int_or_none(sample.get("total_memory"))
            sample_used = _int_or_none(sample.get("used_memory"))
            if not sample_total or sample_used is None:
                continue
            points.append(
                (
                    _format_timestamp(sample.get("timestamp")),
                    sample_used / sample_total * 100.0,
                )
            )
        if points:
            values = [p[1] for p in points]
            sources.append(
                (
                    "Utilization chart",
                    f"SystemEvents pressure samples ({len(points)} sample(s))",
                )
            )
            return {
                "points": points,
                "ylabel": "Memory utilization (%)",
                "caption": (
                    f"{len(points)} persisted sample(s) · peak {max(values):.1f}%"
                    f" · final {values[-1]:.1f}% · no tick-level run data available"
                ),
            }

    sources.append(
        ("Utilization chart", "None recorded (no run and no pressure samples)")
    )
    return None


# ──────────────────────────────────────────────────────────────
# Header
# ──────────────────────────────────────────────────────────────

def _render_header() -> None:
    """Renders the title, status chips and refresh control."""
    header_title(
        "RAAE — Resource-Aware Adaptive App Eviction",
        "System monitoring · live backend state and SQLite telemetry",
    )

    status, explanation = state.simulation_status()
    ticks = state.ticks_run()
    header_chips(
        [
            {"label": "Simulation status", "value": status, "badge": True},
            {"label": "Status detail", "value": explanation},
            {"label": "Simulation tick", "value": ticks},
            {"label": "Eviction policy", "value": state.effective_policy()},
        ]
    )

    action_col, note_col = st.columns([1, 5])
    with action_col:
        if st.button(
            "🔄 Refresh",
            use_container_width=True,
            help="Re-read every metric from the backend and database.",
        ):
            st.rerun()
    with note_col:
        st.caption(
            f"Telemetry read at {datetime.datetime.now().strftime('%H:%M:%S')} · "
            "no value on this page is hard-coded."
        )


# ──────────────────────────────────────────────────────────────
# Metric cards
# ──────────────────────────────────────────────────────────────

def _render_metric_cards(
    memory: Dict[str, Any],
    counters: Dict[str, Optional[int]],
    apps: Sequence[Any],
) -> None:
    """Renders the headline metric cards in two responsive rows."""
    section_header("Key metrics", hint="Read on every render")
    spacer(6)

    total = memory["total_mb"]
    used = memory["used_mb"]
    available = memory["available_mb"]
    pct = memory["utilization_pct"]
    pressure = memory["pressure"]
    source = memory["source"]

    no_ram = "No memory manager attached and no pressure sample recorded."
    pct_text = f"{pct:.1f}%" if pct is not None else UNAVAILABLE

    metric_row(
        [
            {
                "label": "Total RAM",
                "value": f"{total} MB" if total is not None else None,
                "delta": source,
                "accent": "blue",
                "unavailable": total is None,
            },
            {
                "label": "Used RAM",
                "value": f"{used} MB" if used is not None else None,
                "delta": (
                    f"{pct_text} of total" if pct is not None else no_ram
                ),
                "delta_sentiment": (
                    "negative" if pct is not None and pct >= 75 else "neutral"
                ),
                "accent": "orange" if (pct is not None and pct >= 75) else "blue",
                "unavailable": used is None,
            },
            {
                "label": "Available RAM",
                "value": f"{available} MB" if available is not None else None,
                "delta": (
                    f"{100.0 - pct:.1f}% headroom"
                    if pct is not None
                    else no_ram
                ),
                "accent": "green",
                "unavailable": available is None,
            },
            {
                "label": "Memory Utilization",
                "value": pct_text if pct is not None else None,
                "delta": (
                    pressure_badge(pressure) if pressure else no_ram
                ),
                "accent": _pressure_accent(pressure),
                "unavailable": pct is None,
            },
        ]
    )
    spacer(12)

    registered = len(apps)
    active = len([a for a in apps if getattr(a, "is_active", False)])
    evictions = counters["evictions"]
    conflicts = counters["conflicts"]
    freezes = counters["freezes"]

    app_note = (
        f"{registered} registered in total"
        if registered
        else "No application registry is attached."
    )

    metric_row(
        [
            {
                "label": "Active Applications",
                "value": active if registered else None,
                "delta": app_note,
                "accent": "green",
                "unavailable": not registered,
            },
            {
                "label": "Total Evictions",
                "value": evictions,
                "delta": (
                    "From SQLite EvictionLog"
                    if evictions is not None
                    else "No eviction log available"
                ),
                "accent": "red",
                "unavailable": evictions is None,
            },
            {
                "label": "Resource Conflicts",
                "value": conflicts,
                "delta": (
                    f"{freezes} freeze incident(s)"
                    if freezes is not None
                    else "No conflict log available"
                ),
                "accent": "orange",
                "unavailable": conflicts is None,
            },
        ]
    )
    spacer(8)


def _pressure_accent(level: Optional[str]) -> str:
    """Maps a memory-pressure level onto a card accent colour."""
    return {
        "GREEN": "green",
        "YELLOW": "orange",
        "ORANGE": "orange",
        "RED": "red",
    }.get(str(level).upper() if level else "", "blue")


# ──────────────────────────────────────────────────────────────
# Memory monitor
# ──────────────────────────────────────────────────────────────

def _render_memory_monitor(
    memory: Dict[str, Any],
    utilization: Optional[Dict[str, Any]],
) -> None:
    """Renders the RAM bar, pressure indicator, gauge and utilization chart."""
    section_header("Memory monitor", hint=memory["source"])
    spacer(6)

    left, right = st.columns([1, 1])

    with left:
        total = memory["total_mb"]
        used = memory["used_mb"]
        available = memory["available_mb"]
        pct = memory["utilization_pct"]
        pressure = memory["pressure"]

        if total is None or used is None or available is None or pct is None:
            unavailable_note(
                "RAM figures need a live MemoryManager or a persisted "
                "SystemEvents memory-pressure sample."
            )
        else:
            pressure_bar(used, total, pressure or "GREEN")
            spacer(10)
            memory_gauge(used, available, total, pct, pressure or "GREEN")
            spacer(10)
            _render_pressure_indicator(memory)

    with right:
        section_header("Memory utilization over ticks")
        spacer(6)
        if utilization:
            utilization_chart(
                utilization["points"],
                utilization["ylabel"],
                "Memory utilization",
                utilization["caption"],
            )
        else:
            unavailable_note(
                "No simulation ticks have been executed and no memory-pressure "
                "samples are stored, so there is no utilization history to plot."
            )


def _render_pressure_indicator(memory: Dict[str, Any]) -> None:
    """Renders the memory-pressure badge with the backend's own verdict."""
    pressure = memory["pressure"]
    under = memory["under_pressure"]
    pct = memory["utilization_pct"]

    if pressure is None:
        unavailable_note("The backend reported no memory-pressure level.")
        return

    verdict = (
        "Eviction permitted"
        if under
        else "Eviction not required"
    )
    if under is None:
        verdict = "Eviction threshold: unknown"
    elif not under:
        verdict = "Below eviction threshold"

    note = (
        f"MemoryManager.is_under_pressure() → {under}"
        if under is not None
        else "No live MemoryManager to report the eviction threshold."
    )
    st.markdown(
        f"""
        <div class="gauge">
            <div class="gauge-head">
                <span class="gauge-title">Memory pressure</span>
                {pressure_badge(pressure)}
            </div>
            <div class="gauge-legend">
                <span class="gauge-key">Utilization&nbsp;<strong>{pct:.1f}%</strong></span>
                <span class="gauge-key">{_esc(verdict)}</span>
            </div>
            <div class="gauge-note">{_esc(note)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────
# Application summary
# ──────────────────────────────────────────────────────────────

def _render_application_summary(apps: Sequence[Any]) -> None:
    """Renders per-state counts, the composition bar and the app table."""
    counts: Dict[str, int] = {name: 0 for name in APP_STATES}
    for app in apps:
        name = _app_state_name(app)
        counts[name] = counts.get(name, 0) + 1

    section_header(
        "Application summary",
        hint=f"{len(apps)} application(s) registered",
    )
    spacer(6)

    metric_row(
        [
            {
                "label": "Foreground",
                "value": counts.get("FOREGROUND", 0),
                "delta": "Running in the foreground",
                "accent": "blue",
                "unavailable": not apps,
            },
            {
                "label": "Background",
                "value": counts.get("BACKGROUND", 0),
                "delta": "Eligible for eviction",
                "accent": "purple",
                "unavailable": not apps,
            },
            {
                "label": "Waiting",
                "value": counts.get("WAITING", 0),
                "delta": "Queued for a contended resource",
                "accent": "orange",
                "unavailable": not apps,
            },
            {
                "label": "Evicted",
                "value": counts.get("EVICTED", 0),
                "delta": "Memory reclaimed",
                "accent": "red",
                "unavailable": not apps,
            },
        ]
    )
    spacer(10)

    if not apps:
        empty_state(
            "📱",
            "No applications registered",
            "Initialise the simulation state or run a simulation to populate "
            "the application registry.",
        )
        return

    state_composition_bar(counts, len(apps))
    spacer(12)

    rows = []
    for app in sorted(apps, key=lambda a: str(getattr(a, "app_id", ""))):
        held = sorted(getattr(app, "held_resources", set()) or set())
        rows.append(
            [
                f"<strong>{_esc(getattr(app, 'name', '—'))}</strong>",
                _esc(getattr(app, "app_id", "—")),
                f"P{_esc(getattr(app, 'priority', '—'))}",
                status_badge(_app_state_name(app)),
                f"{_esc(getattr(app, 'memory_footprint', '—'))} MB",
                "🟢 1" if getattr(app, "reference_bit", 0) else "⚪ 0",
                ", ".join(_esc(r) for r in held) if held else "<span class='event-muted'>—</span>",
            ]
        )
    styled_table(
        ["Application", "ID", "Priority", "State", "Memory", "Ref bit", "Held resources"],
        rows,
        caption="Registered applications",
    )


# ──────────────────────────────────────────────────────────────
# Recent event log
# ──────────────────────────────────────────────────────────────

def _decision_cell(decision: Optional[str]) -> str:
    """Renders a coloured decision / result value."""
    if not decision:
        return "<span class='event-muted'>—</span>"
    color = DECISION_COLORS.get(str(decision).upper(), "#8b949e")
    return f"<span class='event-decision' style='color:{color};'>{_esc(decision)}</span>"


def _event_rows(
    events: Sequence[Dict[str, Any]],
    names: Dict[str, str],
) -> List[List[str]]:
    """Formats timeline entries into table rows."""
    rows: List[List[str]] = []
    for event in events:
        app_id = str(event.get("app_id") or "—")
        app_name = names.get(app_id)
        application = f"{_esc(app_id)}" + (f" · {_esc(app_name)}" if app_name else "")
        resource = event.get("resource")
        rows.append(
            [
                _format_timestamp(event.get("timestamp")),
                f"<span class='event-decision' style='color:"
                f"{EVENT_TYPE_COLORS.get(str(event.get('event_type')), '#8b949e')};'>"
                f"{_esc(event.get('event_type') or '—')}</span>",
                application,
                _esc(resource) if resource else "<span class='event-muted'>—</span>",
                _decision_cell(event.get("decision")),
                _esc(event.get("explanation") or "—"),
            ]
        )
    return rows


def _render_event_log(
    events: Sequence[Dict[str, Any]],
    origin: str,
    db: Optional[Any],
) -> None:
    """Renders the merged, filterable event timeline."""
    section_header("Recent event log", hint=origin)
    spacer(6)

    if not events:
        if db is None:
            unavailable_note(origin)
        else:
            empty_state(
                "📜",
                "No events recorded",
                "The log tables are empty. Run a simulation to generate "
                "eviction, conflict and memory events.",
            )
        return

    selected = st.multiselect(
        "Event types",
        list(EVENT_TYPES),
        default=list(EVENT_TYPES),
        format_func=lambda t: t.capitalize(),
    )
    filtered = [e for e in events if e.get("event_type") in selected]

    if not filtered:
        unavailable_note("No events match the selected event types.")
        return

    names = _app_names(events, db)
    headers = [
        "Timestamp",
        "Event type",
        "Application",
        "Resource",
        "Decision",
        "Explanation",
    ]
    visible = filtered[:EVENT_DISPLAY_LIMIT]
    styled_table(headers, _event_rows(visible, names))

    hidden = len(filtered) - len(visible)
    if hidden > 0:
        with st.expander(f"Show {hidden} older event(s)", expanded=False):
            styled_table(headers, _event_rows(filtered[EVENT_DISPLAY_LIMIT:], names))


def _app_names(
    events: Sequence[Dict[str, Any]],
    db: Optional[Any],
) -> Dict[str, str]:
    """Maps app_id → app name for the event log.

    Names are only used for display; an app that has been deleted simply shows
    its identifier instead of being dropped from the log. The registry is read
    once through the app repository — no SQL lives in the UI layer, and the
    lookup does not grow with the number of events.
    """
    names: Dict[str, str] = {}
    for event in events:
        app_id = event.get("app_id")
        if app_id:
            names[str(app_id)] = str(app_id)

    if db is None or not names:
        return names

    try:
        for app in db.apps.get_all_apps() or []:
            app_id = str(getattr(app, "app_id", ""))
            if app_id in names:
                names[app_id] = str(getattr(app, "name", app_id))
    except Exception:
        # A missing Apps table simply leaves the identifiers in place.
        return names
    return names


# ──────────────────────────────────────────────────────────────
# Data availability
# ──────────────────────────────────────────────────────────────

def _render_availability(
    sources: Sequence[Tuple[str, str]],
    errors: Sequence[Tuple[str, str]],
) -> None:
    """Renders the provenance and error panel."""
    spacer(12)
    diagnostics_panel(sources, errors)