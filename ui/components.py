"""Reusable UI components for the RAAE Streamlit dashboard.

All rendering helpers live here so page modules stay focused on layout
and data orchestration.  Every function receives plain Python values —
never a raw backend object — keeping the UI layer decoupled from business logic.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import streamlit as st


# ──────────────────────────────────────────────────────────────
# Metric Cards
# ──────────────────────────────────────────────────────────────

def metric_card(
    label: str,
    value: Any,
    delta: Optional[str] = None,
    delta_sentiment: str = "neutral",
    accent: str = "blue",
    unavailable: bool = False,
) -> None:
    """Renders a styled metric card with optional delta indicator.

    Args:
        label: Metric title shown above the value.
        value: Primary numeric or text value to display.
        delta: Optional change indicator text (e.g. "+3 vs baseline"). May be
            HTML, so a badge can be shown as the sub-line.
        delta_sentiment: One of "positive", "negative", "neutral".
        accent: CSS accent colour used for the left border.
        unavailable: Renders the value in the muted "could not be read" style,
            so an absent metric is never mistaken for a real number.
    """
    accent_colors = {
        "blue": "#58a6ff", "green": "#3fb950", "orange": "#d29922",
        "red": "#f85149", "purple": "#bc8cff",
    }
    border_color = accent_colors.get(accent, accent_colors["blue"])
    if unavailable:
        border_color = "#30363d"
        value = UNAVAILABLE
    value_class = "metric-value unavailable" if unavailable else "metric-value"
    delta_html = ""
    if delta is not None:
        delta_html = f'<div class="metric-delta {delta_sentiment}">{delta}</div>'
    html = f"""
    <div class="metric-card" style="border-left: 3px solid {border_color};">
        <div class="metric-label">{_esc(label)}</div>
        <div class="{value_class}">{_esc(value)}</div>
        {delta_html}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def metric_row(metrics: Sequence[Dict[str, Any]]) -> None:
    """Renders a horizontal row of metric cards.

    Each dict in *metrics* is unpacked as keyword arguments to :func:`metric_card`.
    """
    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        with col:
            metric_card(**m)


# ──────────────────────────────────────────────────────────────
# Status Indicators
# ──────────────────────────────────────────────────────────────

_PRESSURE_CSS = {
    "GREEN": "green", "YELLOW": "yellow", "ORANGE": "orange", "RED": "red",
}

_STATE_CSS = {
    "FOREGROUND": "blue", "BACKGROUND": "purple",
    "WAITING": "orange", "EVICTED": "red",
    "ACTIVE": "green", "FREE": "green",
    "LOCKED": "orange",
    # Simulation lifecycle shown in the dashboard header.
    "IDLE": "blue", "READY": "blue", "COMPLETED": "green",
    "RUNNING": "green", "PAUSED": "orange", "STOPPED": "blue",
    # Wound-Wait decisions shown in the conflict monitor.
    "WOUND": "red", "WOUND_HOLDER": "red",
    "WAIT": "orange", "NO_CONFLICT": "green",
    "UNAVAILABLE": "orange",
}


def status_badge(text: str) -> str:
    """Returns an HTML badge string for *text*, auto-detecting colour."""
    css_class = _STATE_CSS.get(text.upper(), _PRESSURE_CSS.get(text.upper(), "blue"))
    return f'<span class="status-badge {css_class}">{text}</span>'


def pressure_badge(level: str) -> str:
    """Returns an HTML badge tuned for memory pressure levels."""
    css_class = _PRESSURE_CSS.get(level.upper(), "blue")
    return f'<span class="status-badge {css_class}">{level}</span>'


def pressure_bar(
    used: int, total: int, level: str,
) -> None:
    """Renders a colour-coded memory pressure progress bar."""
    pct = (used / total * 100) if total > 0 else 0
    bar_colors = {
        "GREEN": "#3fb950", "YELLOW": "#d29922",
        "ORANGE": "#db8729", "RED": "#f85149",
    }
    color = bar_colors.get(level.upper(), "#58a6ff")
    html = f"""
    <div class="pressure-bar-container">
        <div style="display:flex; justify-content:space-between; align-items:baseline;">
            <span style="font-weight:600; font-size:0.88rem;">Memory Usage</span>
            <span style="font-size:0.82rem; color:#8b949e;">
                {used} MB / {total} MB &nbsp; {pressure_badge(level)}
            </span>
        </div>
        <div class="pressure-bar-track">
            <div class="pressure-bar-fill"
                 style="width:{min(pct, 100):.1f}%; background:{color};"></div>
        </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# Tables
# ──────────────────────────────────────────────────────────────

def styled_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    caption: Optional[str] = None,
) -> None:
    """Renders an HTML table with custom dark styling.

    Each element in *rows* may contain raw HTML (e.g. status badges).
    """
    if not rows:
        empty_state("📋", "No data", "There are no records to display yet.")
        return

    cap = f"<caption style='caption-side:top;text-align:left;font-weight:600;padding:0.5rem 1rem;color:#8b949e;font-size:0.78rem;'>{caption}</caption>" if caption else ""
    thead = "".join(f"<th>{h}</th>" for h in headers)
    tbody = ""
    for row in rows:
        tds = "".join(f"<td>{cell}</td>" for cell in row)
        tbody += f"<tr>{tds}</tr>"
    html = f"""
    <table class="styled-table">
        {cap}
        <thead><tr>{thead}</tr></thead>
        <tbody>{tbody}</tbody>
    </table>
    """
    st.markdown(html, unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────
# Event Log
# ──────────────────────────────────────────────────────────────

_EVENT_ICONS = {
    "MEMORY": "💾", "EVICTION": "⚡", "CONFLICT": "⚠️",
}
_EVENT_COLORS = {
    "MEMORY": "#58a6ff", "EVICTION": "#f85149", "CONFLICT": "#d29922",
}


def event_log(events: Sequence[Dict[str, Any]], max_items: int = 30) -> None:
    """Renders a chronological event feed from the unified event timeline.

    Each *event* dict is expected to carry at least ``event_type``, ``app_id``,
    ``code``, ``detail``, and ``timestamp`` keys — the shape returned by
    :meth:`database.event_feed.EventRepository.get_recent_events`.
    """
    if not events:
        empty_state("📜", "No Events", "Run a simulation to generate event logs.")
        return

    for ev in events[:max_items]:
        etype = str(ev.get("event_type", ""))
        icon = _EVENT_ICONS.get(etype, "📌")
        color = _EVENT_COLORS.get(etype, "#8b949e")
        app_id = ev.get("app_id", "—")
        code = ev.get("code", "")
        detail = ev.get("detail", "")
        ts = ev.get("timestamp")
        ts_str = _format_timestamp(ts)
        detail_text = f"{code}" + (f" — {detail}" if detail else "")
        html = f"""
        <div class="event-item">
            <span style="font-size:1.1rem;">{icon}</span>
            <span class="event-type" style="color:{color};">{etype}</span>
            <span style="font-weight:500; min-width:80px;">{app_id}</span>
            <span class="event-detail">{detail_text}</span>
            <span class="event-time">{ts_str}</span>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)


def _format_timestamp(ts: Any) -> str:
    """Converts a UNIX timestamp to a human-readable local-time string."""
    if ts is None:
        return "—"
    try:
        dt = datetime.datetime.fromtimestamp(float(ts))
        return dt.strftime("%H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(ts)


# ──────────────────────────────────────────────────────────────
# Empty / Info States
# ──────────────────────────────────────────────────────────────

def empty_state(icon: str, title: str, description: str) -> None:
    """Renders a centred empty-state placeholder."""
    html = f"""
    <div class="empty-state">
        <div class="empty-icon">{icon}</div>
        <div class="empty-title">{title}</div>
        <div class="empty-description">{description}</div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def section_header(title: str, hint: str = "") -> None:
    """Renders a styled section divider, with an optional right-aligned hint."""
    trail = f'<span class="section-hint">{hint}</span>' if hint else ""
    st.markdown(
        f'<div class="section-header">{title}{trail}</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────
# Text helpers
# ──────────────────────────────────────────────────────────────

#: Rendered wherever a value could not be read from the backend or database.
UNAVAILABLE = "Unavailable"


def _esc(text: Any) -> str:
    """Escapes *text* so backend data can be embedded in dashboard HTML."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def spacer(height_px: float = 12.0) -> None:
    """Inserts vertical whitespace between dashboard sections."""
    st.markdown(
        f"<div style='height:{height_px}px'></div>", unsafe_allow_html=True
    )


def _stretch_kwargs(target: Any) -> Dict[str, Any]:
    """Returns the full-width keyword the installed Streamlit expects.

    ``use_container_width`` was replaced by ``width='stretch'`` in newer
    releases and removed from some, so the keyword is resolved by inspecting the
    installed signature rather than hard-coded to one generation.
    """
    import inspect

    try:
        parameters = inspect.signature(target).parameters
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return {"use_container_width": True}
    if "width" in parameters:
        return {"width": "stretch"}
    return {"use_container_width": True}


def unavailable_note(message: str) -> None:
    """Renders an explicit 'this could not be read' note."""
    st.markdown(
        f"""
        <div class="unavailable-note">
            <span class="unavailable-mark">!</span>
            <div><strong>{_esc(UNAVAILABLE)}</strong> — {_esc(message)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────
# Header
# ──────────────────────────────────────────────────────────────

def header_title(title: str, subtitle: str = "") -> None:
    """Renders the page title block."""
    sub = f"<div class='raae-subtitle'>{_esc(subtitle)}</div>" if subtitle else ""
    st.markdown(
        f"""
        <div class="raae-header">
            <div class="raae-title">{_esc(title)}</div>
            {sub}
        </div>
        """,
        unsafe_allow_html=True,
    )


def header_chips(chips: Sequence[Dict[str, Any]]) -> None:
    """Renders the header's key-value chips.

    Each chip is ``{"label": str, "value": Any, "badge": bool}``. ``badge`` renders
    the value through :func:`status_badge` so pressure levels get their colour;
    a missing or empty value is reported as :data:`UNAVAILABLE` rather than
    rendered blank, so an absent metric is always visible as absent.
    """
    cells: List[str] = []
    for chip in chips:
        label = _esc(chip.get("label", ""))
        value = chip.get("value")
        if chip.get("badge"):
            body = (
                status_badge(str(value)) if value else status_badge("UNAVAILABLE")
            )
        elif value in (None, "", UNAVAILABLE):
            body = f"<span class='chip-unavailable'>{UNAVAILABLE}</span>"
        else:
            body = _esc(value)
        cells.append(
            f"<div class='raae-chip'><span class='chip-label'>{label}</span>"
            f"<span class='chip-value'>{body}</span></div>"
        )
    st.markdown(
        f"<div class='raae-chips'>{''.join(cells)}</div>", unsafe_allow_html=True
    )


# ──────────────────────────────────────────────────────────────
# Gauges
# ──────────────────────────────────────────────────────────────

_GAUGE_COLORS = {
    "used": "#58a6ff",
    "available": "#30363d",
    "GREEN": "#3fb950",
    "YELLOW": "#d29922",
    "ORANGE": "#db8729",
    "RED": "#f85149",
}

_STATE_SEGMENT_COLORS = {
    "FOREGROUND": "#58a6ff",
    "BACKGROUND": "#8b949e",
    "WAITING": "#d29922",
    "EVICTED": "#f85149",
}


def memory_gauge(
    used: int,
    available: int,
    total: int,
    used_pct: float,
    level: str,
    extra_note: str = "",
) -> None:
    """Renders the used-versus-available memory bar with a legend."""
    pct = max(0.0, min(100.0, float(used_pct)))
    level_color = _GAUGE_COLORS.get(str(level).upper(), _GAUGE_COLORS["used"])
    note = (
        f"<div class='gauge-note'>{_esc(extra_note)}</div>" if extra_note else ""
    )
    html = f"""
    <div class="gauge">
        <div class="gauge-head">
            <span class="gauge-title">Used vs available RAM</span>
            <span class="gauge-total">{_esc(used + available)} / {_esc(total)} MB</span>
        </div>
        <div class="gauge-track">
            <div class="gauge-seg gauge-used"
                 style="width:{pct:.1f}%; background:{level_color};"></div>
            <div class="gauge-seg gauge-free"
                 style="width:{max(0.0, 100.0 - pct):.1f}%;"></div>
        </div>
        <div class="gauge-legend">
            <span class="gauge-key">
                <span class="gauge-dot" style="background:{level_color};"></span>
                Used&nbsp;<strong>{_esc(used)} MB</strong>&nbsp;({pct:.1f}%)
            </span>
            <span class="gauge-key">
                <span class="gauge-dot" style="background:{_GAUGE_COLORS['available']};"></span>
                Available&nbsp;<strong>{_esc(available)} MB</strong>&nbsp;
                ({max(0.0, 100.0 - pct):.1f}%)
            </span>
        </div>
        {note}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def state_composition_bar(counts: Dict[str, int], total: int) -> None:
    """Renders a stacked bar of application states, skipping empty segments."""
    if not total:
        st.markdown(
            "<div class='gauge-note'>No applications registered.</div>",
            unsafe_allow_html=True,
        )
        return

    segments: List[str] = []
    for state, color in _STATE_SEGMENT_COLORS.items():
        count = int(counts.get(state, 0) or 0)
        if count <= 0:
            continue
        width = count / total * 100.0
        segments.append(
            f"<div class='gauge-seg' style='width:{width:.2f}%; background:{color};' "
            f"title='{_esc(state)}: {count}'></div>"
        )

    legend = "".join(
        f"<span class='gauge-key'><span class='gauge-dot' "
        f"style='background:{color};'></span>{_esc(state)} "
        f"<strong>{int(counts.get(state, 0) or 0)}</strong></span>"
        for state, color in _STATE_SEGMENT_COLORS.items()
        if int(counts.get(state, 0) or 0) > 0
    )
    st.markdown(
        f"""
        <div class="gauge">
            <div class="gauge-head">
                <span class="gauge-title">Application state composition</span>
                <span class="gauge-total">{_esc(total)} registered</span>
            </div>
            <div class="gauge-track">{''.join(segments)}</div>
            <div class="gauge-legend">{legend}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def utilization_chart(
    points: Sequence[Tuple[str, float]],
    ylabel: str,
    title: str,
    caption: str = "",
) -> None:
    """Plots a utilisation series, or explains why it cannot be plotted.

    *points* is a sequence of ``(x_label, y_value)`` in display order. When Plotly
    is installed an interactive chart is drawn; otherwise the same numbers are
    rendered with Streamlit's built-in chart so the data is never hidden behind a
    missing dependency.
    """
    if not points:
        unavailable_note("No memory samples have been recorded yet.")
        return

    labels = [p[0] for p in points]
    values = [float(p[1]) for p in points]
    cap = f"<div class='chart-caption'>{_esc(caption)}</div>" if caption else ""

    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=labels,
                y=values,
                mode="lines+markers",
                line=dict(color="#58a6ff", width=2),
                marker=dict(color="#58a6ff", size=6),
                fill="tozeroy",
                fillcolor="rgba(88,166,255,0.10)",
                hovertemplate="%{x}<br>%{y:.2f}<extra></extra>",
                name=title,
            )
        )
        fig.update_layout(
            template="plotly_dark",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=10, b=10),
            height=260,
            font=dict(family="Inter", size=11),
            xaxis=dict(title=None, showgrid=False),
            yaxis=dict(title=ylabel, rangemode="tozero", gridcolor="rgba(110,118,129,0.18)"),
            hovermode="x unified",
            showlegend=False,
        )
        st.plotly_chart(
            fig,
            **_stretch_kwargs(st.plotly_chart),
            config={"displayModeBar": False},
        )
        st.markdown(cap, unsafe_allow_html=True)
    except ImportError:
        st.warning(
            "Plotly is not installed, so a simplified chart is shown. "
            "Run `pip install plotly` for the full dashboard styling.",
            icon="⚠️",
        )
        st.line_chart({title: values}, **_stretch_kwargs(st.line_chart))
        st.markdown(cap, unsafe_allow_html=True)

    with st.expander("Chart data", expanded=False):
        styled_table(
            ["Point", ylabel],
            [[label, f"{value:.2f}"] for label, value in zip(labels, values)],
        )


# ──────────────────────────────────────────────────────────────
# Diagnostics
# ──────────────────────────────────────────────────────────────

def diagnostics_panel(
    sources: Sequence[Tuple[str, str]],
    errors: Sequence[Tuple[str, str]],
) -> None:
    """Reports where each value came from and what could not be read.

    *sources* is ``(metric, origin)``; *errors* is ``(operation, message)``. Both
    are rendered inside one expander so a healthy dashboard stays uncluttered,
    while a broken data source can never hide silently.
    """
    label = (
        f"Data availability — {len(sources)} source(s)"
        f"{f', {len(errors)} error(s)' if errors else ''}"
    )
    with st.expander(label, expanded=bool(errors)):
        if sources:
            st.markdown(
                "<div class='diag-title'>Metric origins</div>", unsafe_allow_html=True
            )
            styled_table(["Metric", "Read from"], [[m, o] for m, o in sources])
        if errors:
            st.markdown(
                "<div style='height:0.6rem'></div>", unsafe_allow_html=True
            )
            st.markdown(
                "<div class='diag-title'>Backend errors</div>", unsafe_allow_html=True
            )
            styled_table(["Operation", "Error"], [[op, msg] for op, msg in errors])
        if not sources and not errors:
            st.caption("No backend source was queried on this render.")

# ──────────────────────────────────────────────────────────────
# Decision timeline
# ──────────────────────────────────────────────────────────────

#: Step state → (css class, human label). A step is never silently ticked off.
STEP_STATES = {
    "confirmed": ("confirmed", "Confirmed"),
    "not_executed": ("not-executed", "Not executed"),
    "not_applicable": ("not-applicable", "Not applicable"),
    "unavailable": ("unavailable", "Unavailable"),
}


def decision_timeline(steps: Sequence[Any], title: str = "") -> None:
    """Renders the numbered eviction-workflow timeline.

    Each step is rendered with the state the backend supports: confirmed steps
    name their evidence, and a step that did not run says so instead of being
    presented as part of the flow. *steps* is a sequence of objects exposing
    ``number``, ``title``, ``status``, ``detail`` and optionally ``evidence``.
    """
    if not steps:
        unavailable_note("No timeline is available for this run.")
        return

    header = f"<div class='timeline-header'>{_esc(title)}</div>" if title else ""
    rows: List[str] = []

    for step in steps:
        status = str(getattr(step, "status", "unavailable"))
        css_class, label = STEP_STATES.get(status, STEP_STATES["unavailable"])
        evidence = getattr(step, "evidence", "") or ""
        evidence_html = (
            f"<div class='timeline-evidence'>Source: {_esc(evidence)}</div>"
            if evidence
            else ""
        )
        rows.append(
            f"""
            <li class="timeline-step {css_class}">
                <span class="timeline-index">{_esc(getattr(step, 'number', '?'))}</span>
                <div class="timeline-body">
                    <div class="timeline-title">
                        {_esc(getattr(step, 'title', ''))}
                        <span class="timeline-state">{label}</span>
                    </div>
                    <div class="timeline-detail">{_esc(getattr(step, 'detail', ''))}</div>
                    {evidence_html}
                </div>
            </li>
            """
        )

    st.markdown(
        f"{header}<ol class='timeline'>{''.join(rows)}</ol>",
        unsafe_allow_html=True,
    )
