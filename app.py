"""RAAE Streamlit UI — Resource-Aware Adaptive App Eviction Simulation.

Run:
    streamlit run app.py

Layout
------
1. Header / project title
2. Simulation controls  (start / stop / reset / clock tick / random event)
3. App creation & configuration
4. RAM usage panel
5. Active applications table
6. Resource locks table
7. Waiting queues panel
8. Latest eviction / conflict events
"""

import sys
import os
from datetime import datetime

# Make sure the project root is on sys.path so backend/ and database/ imports work
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st

from backend.models.app import AppState
from backend.simulation_controller import SimulationController

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="RAAE Simulation",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session-state bootstrap — one controller instance per browser session
# ---------------------------------------------------------------------------

if "ctrl" not in st.session_state:
    st.session_state.ctrl = SimulationController()

ctrl: SimulationController = st.session_state.ctrl

# ---------------------------------------------------------------------------
# Helper: pressure colour
# ---------------------------------------------------------------------------

PRESSURE_COLOUR = {
    "GREEN":  "🟢",
    "YELLOW": "🟡",
    "ORANGE": "🟠",
    "RED":    "🔴",
}

# ============================================================================
# ① PROJECT TITLE / HEADER
# ============================================================================

st.title("🧠 RAAE — Resource-Aware Adaptive App Eviction")
st.caption(
    "A simulation of Clock-based eviction with Wound-Wait conflict resolution, "
    "semaphore resource locking, and SQLite persistence."
)
st.divider()

# ============================================================================
# ② SIMULATION CONTROLS  (top strip)
# ============================================================================

st.subheader("⚙️ Simulation Controls")
ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4, ctrl_col5 = st.columns(5)

with ctrl_col1:
    if st.button("▶ Start", use_container_width=True, disabled=ctrl.is_running):
        ctrl.start()
        st.rerun()

with ctrl_col2:
    if st.button("⏹ Stop", use_container_width=True, disabled=not ctrl.is_running):
        ctrl.stop()
        st.rerun()

with ctrl_col3:
    if st.button("↺ Reset", use_container_width=True):
        ctrl.reset()
        st.rerun()

with ctrl_col4:
    if st.button("⏱ Clock Tick", use_container_width=True, disabled=not ctrl.is_running):
        ctrl.run_clock_tick()
        st.rerun()

with ctrl_col5:
    if st.button("🎲 Random Event", use_container_width=True, disabled=not ctrl.is_running):
        ctrl.run_random_event()
        st.rerun()

# Running status badge
status_text = "🟢 **Running**" if ctrl.is_running else "🔴 **Stopped**"
st.markdown(f"Simulation status: {status_text}")
refresh_col, updated_col = st.columns([1, 5])
with refresh_col:
    if st.button("↻ Refresh view", use_container_width=True):
        st.rerun()
with updated_col:
    st.caption(f"View refreshed: {datetime.now().strftime('%H:%M:%S')}")

st.divider()

# ============================================================================
# ③ APP CREATION / CONFIGURATION  (sidebar)
# ============================================================================

with st.sidebar:
    st.header("➕ Add Application")
    app_name = st.text_input("App Name", value="New App")
    app_priority = st.slider("Priority", min_value=1, max_value=10, value=2)
    app_memory = st.number_input("Memory (MB)", min_value=10, max_value=1000, value=100, step=10)
    app_state_raw = st.selectbox("Initial State", ["BACKGROUND", "FOREGROUND"])
    app_state = AppState(app_state_raw)

    if st.button("➕ Add App", use_container_width=True):
        ctrl.add_app(
            name=app_name,
            priority=app_priority,
            memory_footprint=app_memory,
            state=app_state,
        )
        st.success(f"App '{app_name}' added!")
        st.rerun()

    st.divider()

    # --- Memory Pressure ---
    st.header("🔴 Trigger Memory Pressure")
    pressure_target = st.slider("Target RAM %", min_value=60, max_value=99, value=85)
    if st.button("⚡ Trigger Pressure", use_container_width=True):
        ctrl.trigger_memory_pressure(target_pct=float(pressure_target))
        st.rerun()

    st.divider()

    # --- Resource request / release ---
    st.header("🔒 Resource Operations")
    available_resources = ctrl.get_available_resource_ids()
    app_ids = ctrl.get_app_ids()

    if app_ids and available_resources:
        sel_app_req = st.selectbox(
            "App (request)",
            options=app_ids,
            format_func=lambda aid: f"{ctrl.mem_mgr.apps[aid].name} ({aid})"
            if aid in ctrl.mem_mgr.apps else aid,
            key="req_app",
        )
        sel_res_req = st.selectbox("Resource to Request", options=available_resources, key="req_res")
        if st.button("🔒 Request Resource", use_container_width=True):
            ctrl.request_resource(sel_app_req, sel_res_req)
            st.rerun()

        st.markdown("---")

        sel_app_rel = st.selectbox(
            "App (release)",
            options=app_ids,
            format_func=lambda aid: f"{ctrl.mem_mgr.apps[aid].name} ({aid})"
            if aid in ctrl.mem_mgr.apps else aid,
            key="rel_app",
        )
        sel_res_rel = st.selectbox("Resource to Release", options=available_resources, key="rel_res")
        if st.button("🔓 Release Resource", use_container_width=True):
            ctrl.release_resource(sel_app_rel, sel_res_rel)
            st.rerun()
    else:
        st.info("Add apps first, then manage resources here.")

# ============================================================================
# MAIN CONTENT
# ============================================================================

# ============================================================================
# ④ SYSTEM METRICS
# ============================================================================

metrics = ctrl.get_system_metrics()
pressure_icon = PRESSURE_COLOUR.get(metrics["pressure"], "⚪")

metric_rows = [
    [
        ("Total RAM", f"{metrics['total_mb']} MB"),
        ("Used RAM", f"{metrics['used_mb']} MB"),
        ("Available RAM", f"{metrics['free_mb']} MB"),
        ("Memory pressure", f"{pressure_icon} {metrics['pressure']}"),
    ],
    [
        ("Total apps", metrics["total_apps"]),
        ("Active apps", metrics["active_apps"]),
        ("Eviction count", metrics["eviction_count"]),
        ("Conflict count", metrics["conflict_count"]),
    ],
]
for metric_row in metric_rows:
    columns = st.columns(4)
    for column, (label, value) in zip(columns, metric_row):
        column.metric(label, "Unavailable" if value is None else value)

usage_pct = metrics["usage_pct"]
st.progress(min(usage_pct / 100.0, 1.0), text=f"{usage_pct:.1f}% RAM used")
st.caption(f"Clock hand: {metrics['clock_hand']}")
st.divider()

# ============================================================================
# ⑤ APPLICATIONS
# ============================================================================

st.subheader("📱 Applications")
apps = ctrl.get_active_apps()
app_names = {app.app_id: app.name for app in apps}
if not apps:
    st.info("No apps registered yet. Add one in the sidebar.")
else:
    app_rows = []
    for app in apps:
        app_rows.append({
            "App ID": app.app_id,
            "Name": app.name,
            "Priority": app.priority,
            "State": app.state.value if app.state else "Unavailable",
            "Memory usage (MB)": app.memory_footprint,
            "Reference bit": app.reference_bit,
            "Held resources": ", ".join(sorted(app.held_resources)) or "—",
        })
    st.dataframe(
        app_rows,
        use_container_width=True,
        hide_index=True,
        height=min(55 + len(app_rows) * 38, 400),
    )

# ============================================================================
# ⑥ RESOURCE STATE
# ============================================================================

st.subheader("🔒 Resources")
locks = ctrl.get_resource_locks()
queues_by_resource = {
    queue["resource_id"]: queue["waiting"]
    for queue in ctrl.get_waiting_queues()
}
if not locks:
    st.info("No resources registered.")
else:
    resource_rows = []
    for lock in locks:
        holders = [
            f"{app_names.get(app_id, app_id)} ({app_id})"
            for app_id in lock["holders"]
        ]
        waiters = [
            f"{app_names.get(app_id, app_id)} ({app_id})"
            for app_id in queues_by_resource.get(lock["resource_id"], [])
        ]
        resource_rows.append({
            "Resource name": lock["name"],
            "Status": lock["status"],
            "Current holder": ", ".join(holders) or "—",
            "Waiting queue": ", ".join(waiters) or "—",
            "Capacity": lock["capacity"],
            "Available": lock["available"],
        })
    st.dataframe(resource_rows, use_container_width=True, hide_index=True)

st.divider()

# ============================================================================
# ⑦ EVENT LOG
# ============================================================================

st.subheader("📋 Event log")
event_rows = ctrl.get_event_log(n=50)
if not event_rows:
    st.info("No events recorded yet.")
else:
    for row in event_rows:
        row["Timestamp"] = datetime.fromtimestamp(row.pop("timestamp")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        row["Event type"] = row.pop("event_type")
        row["App"] = row.pop("app") or "—"
        row["Resource"] = row.pop("resource") or "—"
        row["Decision"] = row.pop("decision") or "—"
        row["Details"] = row.pop("message")
    st.dataframe(event_rows, use_container_width=True, hide_index=True)

st.divider()

# DB log expanders (collapsed by default)
with st.expander("🗄️ DB: Conflict Log (last 10)"):
    clog = ctrl.get_conflict_log(limit=10)
    if clog:
        st.dataframe(clog, use_container_width=True)
    else:
        st.caption("No conflict events logged yet.")

with st.expander("🗄️ DB: Eviction Log (last 10)"):
    elog = ctrl.get_eviction_log(limit=10)
    if elog:
        st.dataframe(elog, use_container_width=True)
    else:
        st.caption("No eviction events logged yet.")

# ---------------------------------------------------------------------------
# Auto-refresh hint
# ---------------------------------------------------------------------------
st.caption(
    "💡 Use **Refresh view** to reload the displayed state without advancing the simulation. "
    "Use **Clock Tick** to advance one step or **Random Event** to inject a workload event."
)
