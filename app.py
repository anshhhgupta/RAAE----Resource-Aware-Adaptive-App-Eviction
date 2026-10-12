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

STATE_BADGE = {
    "FOREGROUND": "🌟 FOREGROUND",
    "BACKGROUND": "🔅 BACKGROUND",
    "WAITING":    "⏳ WAITING",
    "EVICTED":    "💀 EVICTED",
}

EVENT_ICON = {
    "eviction": "✅",
    "conflict": "⚠️",
    "resource": "🔒",
    "info":     "ℹ️",
    "error":    "❌",
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
# MAIN CONTENT — three-column layout
# ============================================================================

left_col, mid_col, right_col = st.columns([1.4, 1.6, 1.6])

# ============================================================================
# ④ RAM USAGE
# ============================================================================

with left_col:
    st.subheader("💾 RAM Usage")
    ram = ctrl.get_ram_snapshot()
    pressure_icon = PRESSURE_COLOUR.get(ram["pressure"], "⚪")

    st.metric("Used", f"{ram['used_mb']} MB")
    st.metric("Free", f"{ram['free_mb']} MB")
    st.metric("Total", f"{ram['total_mb']} MB")

    pct = ram["usage_pct"]
    st.progress(
        min(pct / 100.0, 1.0),
        text=f"{pct:.1f}% used"
    )

    st.markdown(f"**Pressure:** {pressure_icon} {ram['pressure']}")
    st.markdown(f"**Clock Hand:** `{ram['clock_hand']}`")

# ============================================================================
# ⑤ ACTIVE APPLICATIONS
# ============================================================================

with mid_col:
    st.subheader("📱 Applications")
    apps = ctrl.get_active_apps()
    if not apps:
        st.info("No apps registered yet. Add one in the sidebar.")
    else:
        rows = []
        for a in apps:
            held = ", ".join(sorted(a.held_resources)) or "—"
            rows.append({
                "ID": a.app_id,
                "Name": a.name,
                "Priority": a.priority,
                "State": STATE_BADGE.get(a.state.value, a.state.value),
                "RAM (MB)": a.memory_footprint,
                "Ref Bit": a.reference_bit,
                "Holds": held,
            })
        st.dataframe(
            rows,
            use_container_width=True,
            height=min(55 + len(rows) * 38, 400),
        )

# ============================================================================
# ⑥ RESOURCE LOCKS
# ============================================================================

with right_col:
    st.subheader("🔒 Resource Locks")
    locks = ctrl.get_resource_locks()
    if not locks:
        st.info("No resources registered.")
    else:
        for lock in locks:
            status_icon = "🟢" if lock["status"] == "FREE" else ("🔴" if lock["status"] == "LOCKED" else "🟡")
            holders_str = ", ".join(lock["holders"]) if lock["holders"] else "—"
            with st.container():
                c1, c2, c3 = st.columns([2, 1, 2])
                c1.markdown(f"**{lock['resource_id']}**")
                c2.markdown(f"{status_icon} {lock['status']}")
                c3.markdown(f"Held by: `{holders_str}`")
                st.caption(
                    f"Capacity: {lock['capacity']} | Available: {lock['available']}"
                )
        st.markdown("")  # spacer

st.divider()

# ============================================================================
# ⑦ WAITING QUEUES
# ============================================================================

bottom_left, bottom_right = st.columns(2)

with bottom_left:
    st.subheader("⏳ Waiting Queues")
    queues = ctrl.get_waiting_queues()
    if not queues:
        st.success("No apps currently waiting for any resource.")
    else:
        for q in queues:
            # Resolve app names where possible
            def _name(aid):
                a = ctrl.mem_mgr.apps.get(aid)
                return f"{a.name} ({aid})" if a else aid

            waiter_labels = [_name(w) for w in q["waiting"]]
            st.markdown(f"**{q['resource_id']}** — {q['queue_depth']} waiting:")
            for label in waiter_labels:
                st.markdown(f"  - {label}")

# ============================================================================
# ⑧ LATEST EVICTION / CONFLICT EVENTS
# ============================================================================

with bottom_right:
    st.subheader("📋 Latest Events")
    events = ctrl.get_recent_events(n=15)
    if not events:
        st.info("No events yet. Start the simulation and run a Clock Tick.")
    else:
        for ev in events:
            icon = EVENT_ICON.get(ev.kind, "•")
            ts = __import__("datetime").datetime.fromtimestamp(ev.timestamp).strftime("%H:%M:%S")
            colour = {
                "eviction": "green",
                "conflict": "orange",
                "error":    "red",
                "resource": "blue",
                "info":     "gray",
            }.get(ev.kind, "gray")
            st.markdown(
                f":{colour}[{icon} `{ts}` {ev.message}]"
            )

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
    "💡 Tip: Use the **Clock Tick** button to advance the simulation one step, "
    "or **Random Event** to inject a workload event. "
    "Install `streamlit-autorefresh` for timed auto-ticking."
)
