"""Streamlit configuration and theming for RAAE dashboard."""

import streamlit as st


def configure_page():
    """Configures the Streamlit page with RAAE branding and dark theme."""
    st.set_page_config(
        page_title="RAAE — Resource-Aware Adaptive App Eviction",
        page_icon="🧠",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


_GLOBAL_CSS = """
<style>
/* ---- Dark-themed professional overrides ---- */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

:root {
    --bg-primary: #0e1117;
    --bg-secondary: #161b22;
    --bg-card: #1c2333;
    --border-subtle: #30363d;
    --text-primary: #e6edf3;
    --text-secondary: #8b949e;
    --accent-blue: #58a6ff;
    --accent-green: #3fb950;
    --accent-orange: #d29922;
    --accent-red: #f85149;
    --accent-purple: #bc8cff;
    --gradient-blue: linear-gradient(135deg, #1a3a5c 0%, #0d2137 100%);
    --gradient-green: linear-gradient(135deg, #1a3c2a 0%, #0d2118 100%);
    --gradient-orange: linear-gradient(135deg, #3c2e1a 0%, #21180d 100%);
    --gradient-red: linear-gradient(135deg, #3c1a1a 0%, #210d0d 100%);
}

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

/* Metric cards */
.metric-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 12px;
    padding: 1.2rem 1.4rem;
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.metric-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.3);
}
.metric-card .metric-label {
    font-size: 0.78rem;
    font-weight: 500;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 0.3rem;
}
.metric-card .metric-value {
    font-size: 1.9rem;
    font-weight: 700;
    color: var(--text-primary);
    line-height: 1.1;
}
.metric-card .metric-delta {
    font-size: 0.78rem;
    font-weight: 500;
    margin-top: 0.2rem;
}
.metric-card .metric-delta.positive { color: var(--accent-green); }
.metric-card .metric-delta.negative { color: var(--accent-red); }
.metric-card .metric-delta.neutral  { color: var(--text-secondary); }

/* Status badges */
.status-badge {
    display: inline-block;
    padding: 0.2rem 0.65rem;
    border-radius: 20px;
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.status-badge.green   { background: rgba(63,185,80,0.15);  color: #3fb950; border: 1px solid rgba(63,185,80,0.3); }
.status-badge.yellow  { background: rgba(210,153,34,0.15); color: #d29922; border: 1px solid rgba(210,153,34,0.3); }
.status-badge.orange  { background: rgba(219,135,41,0.15); color: #db8729; border: 1px solid rgba(219,135,41,0.3); }
.status-badge.red     { background: rgba(248,81,73,0.15);  color: #f85149; border: 1px solid rgba(248,81,73,0.3); }
.status-badge.blue    { background: rgba(88,166,255,0.15); color: #58a6ff; border: 1px solid rgba(88,166,255,0.3); }
.status-badge.purple  { background: rgba(188,140,255,0.15);color: #bc8cff; border: 1px solid rgba(188,140,255,0.3); }

/* Section headers */
.section-header {
    font-size: 1.15rem;
    font-weight: 600;
    color: var(--text-primary);
    padding-bottom: 0.5rem;
    margin-bottom: 1rem;
    border-bottom: 2px solid var(--border-subtle);
}

/* Empty state */
.empty-state {
    text-align: center;
    padding: 3rem 2rem;
    color: var(--text-secondary);
}
.empty-state .empty-icon {
    font-size: 3rem;
    margin-bottom: 0.8rem;
    opacity: 0.5;
}
.empty-state .empty-title {
    font-size: 1.1rem;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 0.4rem;
}
.empty-state .empty-description {
    font-size: 0.88rem;
    max-width: 400px;
    margin: 0 auto;
}

/* Sidebar styling */
section[data-testid="stSidebar"] {
    background: var(--bg-secondary) !important;
    border-right: 1px solid var(--border-subtle) !important;
}
section[data-testid="stSidebar"] .stRadio > label {
    font-weight: 500;
}

/* Table styling */
.styled-table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid var(--border-subtle);
    font-size: 0.85rem;
}
.styled-table thead th {
    background: var(--bg-secondary);
    color: var(--text-secondary);
    font-weight: 600;
    text-transform: uppercase;
    font-size: 0.72rem;
    letter-spacing: 0.5px;
    padding: 0.8rem 1rem;
    text-align: left;
    border-bottom: 1px solid var(--border-subtle);
}
.styled-table tbody td {
    padding: 0.7rem 1rem;
    border-bottom: 1px solid rgba(48,54,61,0.5);
    color: var(--text-primary);
}
.styled-table tbody tr:last-child td { border-bottom: none; }
.styled-table tbody tr:hover { background: rgba(88,166,255,0.04); }

/* Event log */
.event-item {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 0.7rem 1rem;
    margin-bottom: 0.5rem;
    font-size: 0.82rem;
    display: flex;
    align-items: center;
    gap: 0.8rem;
}
.event-item .event-type {
    font-weight: 600;
    font-size: 0.72rem;
    text-transform: uppercase;
    min-width: 70px;
}
.event-item .event-detail {
    color: var(--text-secondary);
    flex: 1;
}
.event-item .event-time {
    color: var(--text-secondary);
    font-size: 0.72rem;
    white-space: nowrap;
}

/* Pressure bar */
.pressure-bar-container {
    background: var(--bg-secondary);
    border-radius: 8px;
    padding: 1rem 1.2rem;
    border: 1px solid var(--border-subtle);
}
.pressure-bar-track {
    height: 12px;
    background: rgba(255,255,255,0.06);
    border-radius: 6px;
    overflow: hidden;
    margin-top: 0.6rem;
}
.pressure-bar-fill {
    height: 100%;
    border-radius: 6px;
    transition: width 0.6s ease;
}

/* ---- Page header ---- */
.raae-header {
    border-bottom: 1px solid var(--border-subtle);
    padding-bottom: 0.8rem;
    margin-bottom: 0.9rem;
}
.raae-title {
    font-size: 1.6rem;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.01em;
    line-height: 1.2;
}
.raae-subtitle {
    font-size: 0.85rem;
    color: var(--text-secondary);
    margin-top: 0.3rem;
}

/* ---- Header chips ---- */
.raae-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    margin-bottom: 0.4rem;
}
.raae-chip {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    padding: 0.55rem 0.9rem;
    min-width: 150px;
    flex: 1 1 150px;
}
.raae-chip .chip-label {
    display: block;
    font-size: 0.68rem;
    font-weight: 500;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.6px;
}
.raae-chip .chip-value {
    display: block;
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--text-primary);
    margin-top: 0.2rem;
}
.raae-chip .chip-unavailable {
    color: var(--text-secondary);
    font-weight: 500;
    font-style: italic;
}

/* ---- Section header hint ---- */
.section-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 1rem;
}
.section-hint {
    font-size: 0.75rem;
    font-weight: 500;
    color: var(--text-secondary);
    text-transform: none;
    letter-spacing: 0;
}

/* ---- Unavailable metric styling ---- */
.metric-value.unavailable {
    color: var(--text-secondary);
    font-size: 1.1rem;
    font-weight: 600;
    font-style: italic;
    padding-top: 0.35rem;
}
.unavailable-note {
    display: flex;
    align-items: flex-start;
    gap: 0.6rem;
    background: var(--bg-secondary);
    border: 1px dashed var(--border-subtle);
    border-radius: 8px;
    padding: 0.8rem 1rem;
    font-size: 0.82rem;
    color: var(--text-secondary);
}
.unavailable-note strong { color: var(--text-primary); }
.unavailable-mark {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    flex: 0 0 18px;
    border-radius: 50%;
    background: rgba(210,153,34,0.18);
    color: var(--accent-orange);
    font-size: 0.72rem;
    font-weight: 700;
}

/* ---- Gauges ---- */
.gauge {
    background: var(--bg-secondary);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 1rem 1.2rem;
}
.gauge-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 1rem;
    margin-bottom: 0.6rem;
}
.gauge-title {
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--text-primary);
}
.gauge-total {
    font-size: 0.8rem;
    color: var(--text-secondary);
    white-space: nowrap;
}
.gauge-track {
    display: flex;
    height: 12px;
    background: rgba(255,255,255,0.06);
    border-radius: 6px;
    overflow: hidden;
}
.gauge-seg { height: 100%; }
.gauge-used { border-radius: 6px 0 0 6px; transition: width 0.6s ease; }
.gauge-free { background: rgba(110,118,129,0.25); border-radius: 0 6px 6px 0; }
.gauge-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 1.2rem;
    margin-top: 0.7rem;
    font-size: 0.78rem;
    color: var(--text-secondary);
}
.gauge-key { display: inline-flex; align-items: center; gap: 0.4rem; }
.gauge-key strong { color: var(--text-primary); font-weight: 600; }
.gauge-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
}
.gauge-note {
    font-size: 0.76rem;
    color: var(--text-secondary);
    margin-top: 0.6rem;
}

/* ---- Charts & diagnostics ---- */
.chart-caption {
    font-size: 0.74rem;
    color: var(--text-secondary);
    margin-top: 0.2rem;
}
.diag-title {
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--text-secondary);
    margin-bottom: 0.4rem;
}

/* ---- Event log table ---- */
.event-decision {
    font-weight: 600;
    font-size: 0.74rem;
    text-transform: uppercase;
    letter-spacing: 0.4px;
}
.event-muted { color: var(--text-secondary); }

/* ---- Decision timeline ---- */
.timeline-header {
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.6px;
    margin-bottom: 0.6rem;
}
.timeline {
    list-style: none;
    margin: 0;
    padding: 0;
    counter-reset: none;
}
.timeline-step {
    display: flex;
    gap: 0.9rem;
    align-items: flex-start;
    background: var(--bg-secondary);
    border: 1px solid var(--border-subtle);
    border-left-width: 3px;
    border-radius: 8px;
    padding: 0.8rem 1rem;
    margin-bottom: 0.5rem;
}
.timeline-step.confirmed      { border-left-color: var(--accent-green); }
.timeline-step.not-executed   { border-left-color: #6e7681; }
.timeline-step.not-applicable { border-left-color: var(--accent-purple); }
.timeline-step.unavailable    { border-left-color: var(--accent-orange); }

.timeline-index {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 26px;
    height: 26px;
    flex: 0 0 26px;
    border-radius: 50%;
    background: rgba(110,118,129,0.18);
    color: var(--text-secondary);
    font-size: 0.78rem;
    font-weight: 700;
}
.timeline-step.confirmed .timeline-index {
    background: rgba(63,185,80,0.16);
    color: var(--accent-green);
}
.timeline-body { flex: 1; min-width: 0; }
.timeline-title {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.6rem;
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--text-primary);
}
.timeline-state {
    display: inline-block;
    padding: 0.1rem 0.55rem;
    border-radius: 20px;
    font-size: 0.66rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    background: rgba(110,118,129,0.18);
    color: var(--text-secondary);
}
.timeline-step.confirmed .timeline-state {
    background: rgba(63,185,80,0.15);
    color: var(--accent-green);
}
.timeline-step.not-applicable .timeline-state {
    background: rgba(188,140,255,0.15);
    color: var(--accent-purple);
}
.timeline-step.unavailable .timeline-state {
    background: rgba(210,153,34,0.15);
    color: var(--accent-orange);
}
.timeline-detail {
    font-size: 0.82rem;
    color: var(--text-secondary);
    margin-top: 0.3rem;
}
.timeline-evidence {
    font-size: 0.72rem;
    color: #6e7681;
    margin-top: 0.25rem;
}
</style>
"""
