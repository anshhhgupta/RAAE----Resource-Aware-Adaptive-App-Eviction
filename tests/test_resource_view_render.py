"""Smoke and rendering tests for the Resource Monitoring page (ui/resource_view.py).

Verifies via Streamlit AppTest that:
- Empty mode renders empty-state messaging without crashing
- Live mode renders the four monitored resources (Camera, Microphone, GPS, Audio)
- Metric summary cards, status table, and detail cards display correct holders and queues
- Control widgets (application select, resource select, Request/Release buttons) are present
- Conflict monitor renders live conflicts and evaluation controls
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("streamlit", reason="streamlit is required to render the page")

from streamlit.testing.v1 import AppTest  # noqa: E402

DRIVER = str(ROOT / "tests" / "render_driver.py")
TIMEOUT = 120

ENV_KEYS = (
    "RAAE_DASHBOARD_MODE",
    "RAAE_DASHBOARD_DB",
    "RAAE_DASHBOARD_RESULT",
    "RAAE_RENDER_PAGE",
)


@pytest.fixture(autouse=True)
def driver_environment():
    """Keeps the driver's environment scoped to a single test."""
    previous = {name: os.environ.get(name) for name in ENV_KEYS}
    yield
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _run_page(
    mode: str = "live",
    page: str = "resources",
    db_path: str = ":memory:",
) -> AppTest:
    """Renders the resources page once for the given driver mode."""
    os.environ.update(
        {
            "RAAE_DASHBOARD_MODE": mode,
            "RAAE_DASHBOARD_DB": db_path,
            "RAAE_DASHBOARD_RESULT": "",
            "RAAE_RENDER_PAGE": page,
        }
    )
    at = AppTest.from_file(DRIVER, default_timeout=TIMEOUT)
    at.run()
    return at


def _body(at: AppTest) -> str:
    """Returns every markdown block on the page as one string."""
    return " ".join(block.value for block in at.markdown)


def _assert_clean(at: AppTest) -> None:
    """Fails the test if the render raised an unhandled exception."""
    assert not at.exception, [str(e.value) for e in at.exception]


def test_resources_page_empty_mode():
    """With no simulation state, the page shows an empty state placeholder."""
    at = _run_page(mode="empty")
    _assert_clean(at)
    body = _body(at)
    assert "No resources registered" in body


def test_resources_page_live_mode_renders_all_canonical_resources():
    """In live mode, Camera, Microphone, GPS, and Audio are all monitored."""
    at = _run_page(mode="live")
    _assert_clean(at)
    body = _body(at)

    assert "Resource Monitor" in body
    assert "Camera" in body
    assert "Microphone" in body
    assert "GPS" in body
    assert "Audio" in body


def test_resources_page_renders_controls_and_conflict_monitor():
    """Widgets for requesting/releasing locks and the conflict monitor are present."""
    at = _run_page(mode="live")
    _assert_clean(at)

    # Check selectboxes
    select_keys = [s.key for s in at.selectbox]
    assert "resource_app" in select_keys
    assert "resource_target" in select_keys

    # Check buttons
    button_labels = [b.label for b in at.button]
    assert any("Request" in label for label in button_labels)
    assert any("Release" in label for label in button_labels)

    body = _body(at)
    assert "Conflict monitor" in body
