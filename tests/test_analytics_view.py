"""Unit, integration, and rendering tests for ui/analytics_view.py.

Verifies:
- Reproducible workload creation ensuring isolated, identical initial states
- Comparison run between BASELINE_CLOCK and RAAE
- Every Matplotlib chart (1-5) generates cleanly and matches simulation telemetry
- Side-by-side data consistency and neutrality
- Streamlit empty-state rendering
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui import analytics_view as av  # noqa: E402


# ──────────────────────────────────────────────────────────────
# Workload Isolation & Reproducibility Tests
# ──────────────────────────────────────────────────────────────

def test_reproducible_workloads_identical_and_isolated():
    """Both policies receive identical specs but completely isolated instances."""
    w_b, w_r = av.create_reproducible_workloads(
        workload_type="deterministic",
        total_memory=1000,
        pressure_threshold=75.0,
        ticks=2,
    )

    # Identical configurations
    assert w_b.total_memory == w_r.total_memory == 1000
    assert w_b.pressure_threshold == w_r.pressure_threshold == 75.0
    assert w_b.ticks == w_r.ticks == 2
    assert len(w_b.apps) == len(w_r.apps) == 5

    # True isolation: mutating w_b does not mutate w_r
    w_b.total_memory = 2000
    assert w_r.total_memory == 1000


def test_synthetic_workload_reproducibility():
    """Given the same seed, synthetic traces generate identical workloads for both."""
    w_b, w_r = av.create_reproducible_workloads(
        workload_type="high_memory_pressure",
        total_memory=1500,
        pressure_threshold=80.0,
        ticks=3,
        seed=123,
        num_apps=6,
    )

    assert w_b.total_memory == w_r.total_memory == 1500
    assert len(w_b.apps) == len(w_r.apps) == 6
    for app_b, app_r in zip(w_b.apps, w_r.apps):
        assert app_b.app_id == app_r.app_id
        assert app_b.memory_footprint == app_r.memory_footprint
        assert app_b.priority == app_r.priority


def test_run_comparison_deterministic():
    """Runs BASELINE_CLOCK vs RAAE and verifies structured comparable results."""
    b_res, r_res = av.run_comparison(
        workload_type="deterministic",
        total_memory=1000,
        pressure_threshold=75.0,
        ticks=1,
    )

    assert b_res["policy"] == "BASELINE_CLOCK"
    assert r_res["policy"] == "RAAE"

    # In deterministic 5-apps workload:
    # Baseline blindly evicts Maps holding GPS -> causes freeze incident
    assert b_res["freeze_incidents"] == 1
    # RAAE detects contention on GPS, defers/releases safely -> 0 freeze incidents
    assert r_res["freeze_incidents"] == 0
    assert r_res["blocked_waiting_evictions"] >= 1
    assert r_res["resource_releases"] >= 1


# ──────────────────────────────────────────────────────────────
# Matplotlib Charts Tests
# ──────────────────────────────────────────────────────────────

def test_chart_1_eviction_counts():
    """Chart 1 accurately renders eviction counts and blocked attempts."""
    b_res = {"eviction_count": 2, "blocked_waiting_evictions": 0}
    r_res = {"eviction_count": 1, "blocked_waiting_evictions": 1}

    fig = av.plot_eviction_counts(b_res, r_res)
    assert isinstance(fig, plt.Figure)
    ax = fig.axes[0]
    # Check that bars were created
    assert len(ax.patches) == 4  # 2 categories x 2 policies
    plt.close(fig)


def test_chart_2_resource_conflicts():
    """Chart 2 accurately renders detected conflicts for both policies."""
    b_res = {"resource_conflicts": 1}
    r_res = {"resource_conflicts": 1}

    fig = av.plot_resource_conflicts(b_res, r_res)
    assert isinstance(fig, plt.Figure)
    ax = fig.axes[0]
    assert len(ax.patches) == 2
    # Verify patch heights match simulation values
    heights = [p.get_height() for p in ax.patches]
    assert heights == [1, 1]
    plt.close(fig)


def test_chart_3_freeze_incidents():
    """Chart 3 accurately plots freeze incidents."""
    b_res = {"freeze_incidents": 1}
    r_res = {"freeze_incidents": 0}

    fig = av.plot_freeze_incidents(b_res, r_res)
    assert isinstance(fig, plt.Figure)
    ax = fig.axes[0]
    heights = [p.get_height() for p in ax.patches]
    assert heights == [1, 0]
    plt.close(fig)


def test_chart_4_memory_utilization_timeline():
    """Chart 4 renders memory utilization lines over ticks."""
    b_res = {
        "ticks_detail": [
            {"tick": 1, "memory_before": 1000, "memory_after": 700},
            {"tick": 2, "memory_before": 700, "memory_after": 700},
        ]
    }
    r_res = {
        "ticks_detail": [
            {"tick": 1, "memory_before": 1000, "memory_after": 700},
            {"tick": 2, "memory_before": 700, "memory_after": 700},
        ]
    }

    fig = av.plot_memory_utilization_timeline(b_res, r_res, total_memory=1000)
    assert isinstance(fig, plt.Figure)
    ax = fig.axes[0]
    # Check lines (baseline, raae, threshold)
    lines = ax.get_lines()
    assert len(lines) >= 3
    plt.close(fig)


def test_chart_5_eviction_events_timeline():
    """Chart 5 renders per-tick eviction events bar chart."""
    b_res = {
        "ticks_detail": [
            {"tick": 1, "evictions": [{"evicted": True}]},
            {"tick": 2, "evictions": []},
        ]
    }
    r_res = {
        "ticks_detail": [
            {"tick": 1, "evictions": [{"evicted": True}, {"evicted": False}]},
            {"tick": 2, "evictions": []},
        ]
    }

    fig = av.plot_eviction_events_timeline(b_res, r_res)
    assert isinstance(fig, plt.Figure)
    ax = fig.axes[0]
    assert len(ax.patches) == 4  # 2 ticks x 2 policies
    plt.close(fig)


# ──────────────────────────────────────────────────────────────
# Streamlit AppTest Smoke Tests
# ──────────────────────────────────────────────────────────────

pytest.importorskip("streamlit", reason="streamlit is required to render the page")
from streamlit.testing.v1 import AppTest  # noqa: E402

DRIVER = str(ROOT / "tests" / "render_driver.py")


def test_analytics_page_empty_state_rendering():
    """When no comparison is executed, displays the empty state placeholder."""
    os.environ.update(
        {
            "RAAE_DASHBOARD_MODE": "live",
            "RAAE_DASHBOARD_DB": ":memory:",
            "RAAE_DASHBOARD_RESULT": "",
            "RAAE_RENDER_PAGE": "analytics",
        }
    )
    at = AppTest.from_file(DRIVER, default_timeout=120)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]

    body = " ".join(block.value for block in at.markdown)
    assert "Policy Analytics & Evaluation" in body
    assert "No Policy Comparison Executed" in body

    # Control widgets present
    assert any("Run Comparison" in b.label for b in at.button)
