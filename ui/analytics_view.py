"""Analytics & Evaluation page for RAAE Streamlit app.

Compares BASELINE_CLOCK versus RAAE under identical workloads, RAM limits,
and initial application states, rendering:
- Reproducible workload configuration controls (deterministic & synthetic)
- Metric cards for key indicators
- Side-by-side empirical results table reporting actual deltas without bias
- Five Matplotlib dark-themed charts:
    1. Baseline Clock versus RAAE eviction counts
    2. Resource conflict comparison
    3. Freeze incident comparison
    4. Memory utilization over time
    5. Eviction events over time
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import streamlit as st

from backend.simulation_engine.controller import (
    AppSpec,
    SimulationResult,
    Workload,
    run_simulation,
)
from backend.workload_generator.workload_generator import WorkloadGenerator
from ui.components import (
    empty_state,
    metric_card,
    metric_row,
    section_header,
    spacer,
    status_badge,
    styled_table,
)

COMPARISON_KEY = "analytics_comparison"


# ──────────────────────────────────────────────────────────────
# Workload & Simulation Execution
# ──────────────────────────────────────────────────────────────

def create_reproducible_workloads(
    workload_type: str,
    total_memory: int = 1000,
    pressure_threshold: float = 75.0,
    ticks: int = 1,
    seed: int = 42,
    num_apps: int = 5,
) -> Tuple[Workload, Workload]:
    """Builds two completely isolated, identical Workload instances.

    Ensures BASELINE_CLOCK and RAAE start from equivalent initial states,
    RAM limits, and workloads with zero cross-contamination.
    """
    if workload_type == "deterministic":
        base_w = Workload.deterministic_5_apps()
        base_w.total_memory = int(total_memory)
        base_w.pressure_threshold = float(pressure_threshold)
        base_w.ticks = int(ticks)
        base_w.db_path = ":memory:"
        return deepcopy(base_w), deepcopy(base_w)

    # Synthetic reproducible scenarios
    gen = WorkloadGenerator(seed=int(seed))
    scenario_name = workload_type  # "high_memory_pressure", "resource_contention", "normal"
    trace = gen.generate_workload(
        seed=int(seed),
        ticks=int(ticks),
        scenario=scenario_name,
        num_apps=int(num_apps),
        ram_mb=int(total_memory),
    )

    app_specs = [
        AppSpec(
            app_id=a.app_id,
            name=a.name,
            priority=a.priority,
            memory_footprint=a.memory_footprint,
            state=a.state.value,
            reference_bit=a.reference_bit,
            held_resources=sorted(list(a.held_resources)),
        )
        for a in trace.initial_apps
    ]

    resource_acquisitions = []
    for a in trace.initial_apps:
        for r_id in sorted(list(a.held_resources)):
            resource_acquisitions.append((a.app_id, r_id))

    workload = Workload(
        total_memory=int(total_memory),
        pressure_threshold=float(pressure_threshold),
        ticks=int(ticks),
        db_path=":memory:",
        apps=app_specs,
        resource_acquisitions=resource_acquisitions,
        aging_reference_bits={a.app_id: a.reference_bit for a in trace.initial_apps},
    )
    return deepcopy(workload), deepcopy(workload)


def run_comparison(
    workload_type: str,
    total_memory: int = 1000,
    pressure_threshold: float = 75.0,
    ticks: int = 1,
    seed: int = 42,
    num_apps: int = 5,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Executes BASELINE_CLOCK and RAAE on isolated, identical workloads."""
    w_baseline, w_raae = create_reproducible_workloads(
        workload_type=workload_type,
        total_memory=total_memory,
        pressure_threshold=pressure_threshold,
        ticks=ticks,
        seed=seed,
        num_apps=num_apps,
    )

    res_baseline: SimulationResult = run_simulation("BASELINE_CLOCK", w_baseline)
    res_raae: SimulationResult = run_simulation("RAAE", w_raae)
    return res_baseline.to_dict(), res_raae.to_dict()


# ──────────────────────────────────────────────────────────────
# Matplotlib Chart Generators
# ──────────────────────────────────────────────────────────────

def _apply_dark_theme(fig: plt.Figure, ax: plt.Axes, title: str, xlabel: str = "", ylabel: str = "") -> None:
    """Applies a dark aesthetic matching the Streamlit interface."""
    fig.patch.set_facecolor("#161b22")
    ax.set_facecolor("#0e1117")
    for spine in ax.spines.values():
        spine.set_color("#30363d")
        spine.set_linewidth(1.0)
    ax.tick_params(colors="#8b949e", labelsize=9)
    ax.xaxis.label.set_color("#e6edf3")
    ax.yaxis.label.set_color("#e6edf3")
    ax.set_title(title, color="#e6edf3", fontsize=11, fontweight="bold", pad=12)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=9, labelpad=8)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=9, labelpad=8)
    ax.grid(True, linestyle="--", alpha=0.25, color="#30363d")


def plot_eviction_counts(baseline: Dict[str, Any], raae: Dict[str, Any]) -> plt.Figure:
    """Chart 1: Baseline Clock versus RAAE eviction counts."""
    fig, ax = plt.subplots(figsize=(6, 3.8))
    labels = ["Successful Evictions", "Deferred / Blocked"]
    b_vals = [baseline.get("eviction_count", 0), baseline.get("blocked_waiting_evictions", 0)]
    r_vals = [raae.get("eviction_count", 0), raae.get("blocked_waiting_evictions", 0)]

    x = range(len(labels))
    width = 0.35

    rects1 = ax.bar([i - width / 2 for i in x], b_vals, width, label="BASELINE_CLOCK", color="#58a6ff")
    rects2 = ax.bar([i + width / 2 for i in x], r_vals, width, label="RAAE", color="#3fb950")

    for rect in (*rects1, *rects2):
        h = rect.get_height()
        ax.annotate(
            str(h),
            xy=(rect.get_x() + rect.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            color="#e6edf3",
            fontsize=9,
            fontweight="bold",
        )

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#e6edf3", fontsize=8.5)
    _apply_dark_theme(fig, ax, "Eviction Counts: BASELINE_CLOCK vs RAAE", ylabel="Application Count")
    fig.tight_layout()
    return fig


def plot_resource_conflicts(baseline: Dict[str, Any], raae: Dict[str, Any]) -> plt.Figure:
    """Chart 2: Resource conflict comparison."""
    fig, ax = plt.subplots(figsize=(5.5, 3.8))
    policies = ["BASELINE_CLOCK", "RAAE"]
    counts = [baseline.get("resource_conflicts", 0), raae.get("resource_conflicts", 0)]
    colors = ["#58a6ff", "#d29922"]

    bars = ax.bar(policies, counts, width=0.45, color=colors)
    for bar in bars:
        h = bar.get_height()
        ax.annotate(
            str(h),
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            color="#e6edf3",
            fontsize=10,
            fontweight="bold",
        )

    _apply_dark_theme(fig, ax, "Resource Conflicts Detected", ylabel="Conflict Incidents")
    fig.tight_layout()
    return fig


def plot_freeze_incidents(baseline: Dict[str, Any], raae: Dict[str, Any]) -> plt.Figure:
    """Chart 3: Freeze incident comparison (stranded waiters)."""
    fig, ax = plt.subplots(figsize=(5.5, 3.8))
    policies = ["BASELINE_CLOCK", "RAAE"]
    freezes = [baseline.get("freeze_incidents", 0), raae.get("freeze_incidents", 0)]
    colors = ["#f85149", "#3fb950" if freezes[1] == 0 else "#f85149"]

    bars = ax.bar(policies, freezes, width=0.45, color=colors)
    for bar in bars:
        h = bar.get_height()
        ax.annotate(
            str(h),
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            color="#e6edf3",
            fontsize=10,
            fontweight="bold",
        )

    _apply_dark_theme(fig, ax, "Freeze Incidents (Stranded Waiters)", ylabel="Freeze Count")
    fig.tight_layout()
    return fig


def plot_memory_utilization_timeline(baseline: Dict[str, Any], raae: Dict[str, Any], total_memory: int = 1000) -> plt.Figure:
    """Chart 4: Memory utilization over simulation ticks."""
    fig, ax = plt.subplots(figsize=(7, 3.8))

    b_ticks = baseline.get("ticks_detail", [])
    r_ticks = raae.get("ticks_detail", [])

    # Initial memory at tick 0 is memory_before of first tick, or peak memory
    b_init = b_ticks[0].get("memory_before", total_memory) if b_ticks else total_memory
    r_init = r_ticks[0].get("memory_before", total_memory) if r_ticks else total_memory

    x_b = [0] + [t.get("tick", i + 1) for i, t in enumerate(b_ticks)]
    y_b = [b_init] + [t.get("memory_after", b_init) for t in b_ticks]

    x_r = [0] + [t.get("tick", i + 1) for i, t in enumerate(r_ticks)]
    y_r = [r_init] + [t.get("memory_after", r_init) for t in r_ticks]

    ax.plot(x_b, y_b, marker="o", linewidth=2, label="BASELINE_CLOCK", color="#58a6ff")
    ax.plot(x_r, y_r, marker="s", linewidth=2, label="RAAE", color="#3fb950")

    # Pressure threshold reference line (e.g. 75%)
    thresh_val = total_memory * 0.75
    ax.axhline(thresh_val, color="#d29922", linestyle=":", label="Pressure Threshold (75%)", alpha=0.8)

    ax.set_ylim(0, total_memory * 1.08)
    ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#e6edf3", fontsize=8.5)
    _apply_dark_theme(fig, ax, "Memory Utilization Over Time", xlabel="Simulation Tick", ylabel="RAM Used (MB)")
    fig.tight_layout()
    return fig


def plot_eviction_events_timeline(baseline: Dict[str, Any], raae: Dict[str, Any]) -> plt.Figure:
    """Chart 5: Eviction events occurred per simulation tick."""
    fig, ax = plt.subplots(figsize=(7, 3.8))

    b_ticks = baseline.get("ticks_detail", [])
    r_ticks = raae.get("ticks_detail", [])

    max_t = max(len(b_ticks), len(r_ticks), 1)
    tick_indices = list(range(1, max_t + 1))

    b_evictions = []
    for t in b_ticks:
        ev_list = t.get("evictions", [])
        b_evictions.append(len([e for e in ev_list if e.get("evicted")]))
    while len(b_evictions) < max_t:
        b_evictions.append(0)

    r_evictions = []
    for t in r_ticks:
        ev_list = t.get("evictions", [])
        r_evictions.append(len([e for e in ev_list if e.get("evicted")]))
    while len(r_evictions) < max_t:
        r_evictions.append(0)

    width = 0.35
    ax.bar([i - width / 2 for i in tick_indices], b_evictions, width, label="BASELINE_CLOCK", color="#58a6ff")
    ax.bar([i + width / 2 for i in tick_indices], r_evictions, width, label="RAAE", color="#3fb950")

    ax.set_xticks(tick_indices)
    ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#e6edf3", fontsize=8.5)
    _apply_dark_theme(fig, ax, "Eviction Events Over Time", xlabel="Simulation Tick", ylabel="Evictions per Tick")
    fig.tight_layout()
    return fig


# ──────────────────────────────────────────────────────────────
# Main Page Render
# ──────────────────────────────────────────────────────────────

def render(
    sim_state: Optional[Any] = None,
    db: Optional[Any] = None,
) -> None:
    """Renders the Analytics & Evaluation page."""
    st.markdown("## 📈 Policy Analytics & Evaluation")
    st.caption(
        "Empirical side-by-side comparison of BASELINE_CLOCK versus RAAE under "
        "reproducible workloads, equivalent initial states, and Matplotlib visualizations."
    )

    # ── Configuration Controls ────────────────────────────────
    section_header("Workload & Evaluation Parameters")
    spacer(4)

    col1, col2, col3 = st.columns(3)
    with col1:
        workload_type = st.selectbox(
            "Workload Scenario",
            [
                ("deterministic", "Deterministic (5 Apps, GPS Contended)"),
                ("high_memory_pressure", "High Memory Pressure (Synthetic)"),
                ("resource_contention", "Resource Contention (Synthetic)"),
                ("normal", "Normal Workload (Synthetic)"),
            ],
            format_func=lambda x: x[1],
            key="analytics_workload",
        )[0]
    with col2:
        total_memory = st.number_input("Total Memory (MB)", 256, 8192, 1000, step=128, key="analytics_mem")
    with col3:
        pressure_threshold = st.slider("Pressure Threshold (%)", 50.0, 95.0, 75.0, 1.0, key="analytics_thresh")

    col_a, col_b = st.columns(2)
    with col_a:
        default_ticks = 1 if workload_type == "deterministic" else 5
        ticks = st.number_input("Simulation Ticks", 1, 50, default_ticks, step=1, key="analytics_ticks")
    with col_b:
        seed = 42
        num_apps = 5
        if workload_type != "deterministic":
            s_col, a_col = st.columns(2)
            with s_col:
                seed = st.number_input("Random Seed", 1, 9999, 42, step=1, key="analytics_seed")
            with a_col:
                num_apps = st.number_input("Number of Apps", 3, 15, 5, step=1, key="analytics_apps")

    spacer(6)

    # ── Execution Buttons ─────────────────────────────────────
    btn_run, btn_clear = st.columns([1, 4])
    with btn_run:
        run_clicked = st.button("▶️ Run Comparison", type="primary", use_container_width=True)
    with btn_clear:
        if st.button("🗑️ Clear Results", use_container_width=False):
            st.session_state.pop(COMPARISON_KEY, None)
            st.rerun()

    if run_clicked:
        with st.spinner("Executing BASELINE_CLOCK vs RAAE under identical workload..."):
            try:
                b_res, r_res = run_comparison(
                    workload_type=workload_type,
                    total_memory=int(total_memory),
                    pressure_threshold=float(pressure_threshold),
                    ticks=int(ticks),
                    seed=int(seed),
                    num_apps=int(num_apps),
                )
                st.session_state[COMPARISON_KEY] = {
                    "baseline": b_res,
                    "raae": r_res,
                    "workload_type": workload_type,
                    "total_memory": int(total_memory),
                    "ticks": int(ticks),
                }
                st.success("Comparison completed successfully!")
            except Exception as e:
                st.error(f"Simulation execution error: {e}")
                return

    spacer(12)

    # ── Telemetry & Evaluation Results ────────────────────────
    comparison = st.session_state.get(COMPARISON_KEY)
    if comparison is None:
        empty_state(
            "📈",
            "No Policy Comparison Executed",
            "Configure a workload scenario above and click 'Run Comparison' to generate "
            "side-by-side empirical metrics and Matplotlib visualizations.",
        )
        return

    baseline = comparison["baseline"]
    raae = comparison["raae"]
    tot_mem = comparison["total_memory"]

    # ── Headline Metric Tiles ─────────────────────────────────
    section_header("Empirical Metrics Summary")
    b_ev, r_ev = baseline.get("eviction_count", 0), raae.get("eviction_count", 0)
    b_bl, r_bl = baseline.get("blocked_waiting_evictions", 0), raae.get("blocked_waiting_evictions", 0)
    b_cf, r_cf = baseline.get("resource_conflicts", 0), raae.get("resource_conflicts", 0)
    b_fz, r_fz = baseline.get("freeze_incidents", 0), raae.get("freeze_incidents", 0)
    b_lat = baseline.get("conflict_resolution_time", 0.0)
    r_lat = raae.get("conflict_resolution_time", 0.0)
    b_rel, r_rel = baseline.get("resource_releases", 0), raae.get("resource_releases", 0)
    b_pct = baseline.get("final_usage_percentage", 0.0)
    r_pct = raae.get("final_usage_percentage", 0.0)

    metric_row([
        {
            "label": "Eviction Count",
            "value": f"B: {b_ev} | R: {r_ev}",
            "delta": f"{r_ev - b_ev:+d} RAAE vs Baseline",
            "accent": "blue",
        },
        {
            "label": "Blocked Evictions",
            "value": f"B: {b_bl} | R: {r_bl}",
            "delta": f"{r_bl - b_bl:+d} deferred for safety",
            "accent": "orange" if r_bl > 0 else "blue",
        },
        {
            "label": "Resource Conflicts",
            "value": f"B: {b_cf} | R: {r_cf}",
            "delta": f"{r_cf - b_cf:+d} difference",
            "accent": "orange",
        },
        {
            "label": "Freeze Incidents",
            "value": f"B: {b_fz} | R: {r_fz}",
            "delta": f"{r_fz - b_fz:+d} freeze incidents",
            "accent": "red" if (b_fz > 0 or r_fz > 0) else "green",
        },
    ])
    spacer(8)

    metric_row([
        {
            "label": "Resolution Latency",
            "value": f"B: {b_lat:.2f}s | R: {r_lat:.2f}s",
            "delta": f"{r_lat - b_lat:+.2f}s logic overhead",
            "accent": "purple",
        },
        {
            "label": "Memory Utilization",
            "value": f"B: {b_pct}% | R: {r_pct}%",
            "delta": f"{r_pct - b_pct:+.1f}% final footprint",
            "accent": "green" if r_pct <= b_pct else "orange",
        },
        {
            "label": "Safe Releases",
            "value": f"B: {b_rel} | R: {r_rel}",
            "delta": f"{r_rel - b_rel:+d} locks freed",
            "accent": "green" if r_rel > 0 else "blue",
        },
    ])
    spacer(14)

    # ── Side-by-Side Results Table ────────────────────────────
    section_header("Side-by-Side Telemetry Table")
    st.caption("Raw empirical metrics recorded under identical initial workloads. Deliberately unbiased.")

    table_headers = ["Metric", "BASELINE_CLOCK", "RAAE", "Difference (RAAE − Baseline)"]
    table_rows = [
        ["Successful Eviction Count", str(b_ev), str(r_ev), f"{r_ev - b_ev:+d}"],
        ["Deferred / Blocked Evictions", str(b_bl), str(r_bl), f"{r_bl - b_bl:+d}"],
        ["Resource Conflicts Detected", str(b_cf), str(r_cf), f"{r_cf - b_cf:+d}"],
        ["Freeze Incidents (Stranded)", str(b_fz), str(r_fz), f"{r_fz - b_fz:+d}"],
        ["Conflict Resolution Latency", f"{b_lat:.4f}s", f"{r_lat:.4f}s", f"{r_lat - b_lat:+.4f}s"],
        ["Resource Release Count", str(b_rel), str(r_rel), f"{r_rel - b_rel:+d}"],
        ["Final Memory Used", f"{baseline.get('final_memory_used', 0)} MB", f"{raae.get('final_memory_used', 0)} MB", f"{raae.get('final_memory_used', 0) - baseline.get('final_memory_used', 0):+d} MB"],
        ["Memory Utilization", f"{b_pct}%", f"{r_pct}%", f"{r_pct - b_pct:+.2f}%"],
        ["Memory Freed by Evictions", f"{baseline.get('memory_freed', 0)} MB", f"{raae.get('memory_freed', 0)} MB", f"{raae.get('memory_freed', 0) - baseline.get('memory_freed', 0):+d} MB"],
        ["Active Applications", str(len(baseline.get("active_apps", []))), str(len(raae.get("active_apps", []))), f"{len(raae.get('active_apps', [])) - len(baseline.get('active_apps', [])):+d}"],
        ["Evicted Applications", str(len(baseline.get("evicted_apps", []))), str(len(raae.get("evicted_apps", []))), f"{len(raae.get('evicted_apps', [])) - len(baseline.get('evicted_apps', [])):+d}"],
    ]
    styled_table(table_headers, table_rows)
    spacer(14)

    # ── Matplotlib Charts ─────────────────────────────────────
    section_header("Matplotlib Visualizations")
    spacer(6)

    # Row 1: Evictions & Conflicts
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        fig1 = plot_eviction_counts(baseline, raae)
        st.pyplot(fig1)
        plt.close(fig1)
    with col_c2:
        fig2 = plot_resource_conflicts(baseline, raae)
        st.pyplot(fig2)
        plt.close(fig2)

    spacer(8)

    # Row 2: Freezes & Memory Timeline
    col_c3, col_c4 = st.columns(2)
    with col_c3:
        fig3 = plot_freeze_incidents(baseline, raae)
        st.pyplot(fig3)
        plt.close(fig3)
    with col_c4:
        fig4 = plot_memory_utilization_timeline(baseline, raae, total_memory=tot_mem)
        st.pyplot(fig4)
        plt.close(fig4)

    spacer(8)

    # Row 3: Eviction Events Timeline
    st.markdown("#### Eviction Events Across Simulation Ticks")
    fig5 = plot_eviction_events_timeline(baseline, raae)
    st.pyplot(fig5)
    plt.close(fig5)
