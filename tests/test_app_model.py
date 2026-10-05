"""Unit tests for App model in RAAE."""

import unittest
import time
from backend.models.app import App, AppState, AppStatus


class TestAppModel(unittest.TestCase):
    """Test suite for App class attributes and state management."""

    def test_app_creation_default_values(self):
        """Test creating an app with default attributes."""
        app = App(app_id="app_101", name="Maps")
        
        self.assertEqual(app.app_id, "app_101")
        self.assertEqual(app.name, "Maps")
        self.assertEqual(app.priority, 1)
        self.assertEqual(app.state, AppState.BACKGROUND)
        self.assertEqual(app.memory_footprint, 0)
        self.assertEqual(app.reference_bit, 1)
        self.assertIsInstance(app.last_access_time, float)
        self.assertEqual(app.held_resources, set())
        self.assertEqual(app.status, AppStatus.ACTIVE)
        self.assertTrue(app.is_active)
        self.assertFalse(app.is_evicted)

    def test_app_creation_custom_values(self):
        """Test creating an app with explicit custom parameters."""
        now = time.time()
        app = App(
            app_id="app_102",
            name="Camera",
            priority=3,
            state=AppState.FOREGROUND,
            memory_footprint=250,
            reference_bit=1,
            last_access_time=now,
            held_resources={"GPS", "MIC"}
        )
        
        self.assertEqual(app.app_id, "app_102")
        self.assertEqual(app.name, "Camera")
        self.assertEqual(app.priority, 3)
        self.assertEqual(app.state, AppState.FOREGROUND)
        self.assertEqual(app.memory_footprint, 250)
        self.assertEqual(app.reference_bit, 1)
        self.assertEqual(app.last_access_time, now)
        self.assertEqual(app.held_resources, {"GPS", "MIC"})
        self.assertEqual(app.status, AppStatus.ACTIVE)
        self.assertTrue(app.is_active)
        self.assertFalse(app.is_evicted)

    def test_app_state_changes(self):
        """Test state transitions between FOREGROUND, BACKGROUND, WAITING, and EVICTED."""
        app = App(app_id="app_103", name="Browser", state=AppState.BACKGROUND)
        initial_time = app.last_access_time

        time.sleep(0.01)
        # 1. Transition to FOREGROUND
        app.change_state(AppState.FOREGROUND)
        self.assertEqual(app.state, AppState.FOREGROUND)
        self.assertEqual(app.status, AppStatus.ACTIVE)
        self.assertTrue(app.is_active)
        self.assertGreater(app.last_access_time, initial_time)

        # 2. Transition to WAITING
        app.change_state(AppState.WAITING)
        self.assertEqual(app.state, AppState.WAITING)
        self.assertEqual(app.status, AppStatus.ACTIVE)
        self.assertTrue(app.is_active)

        # 3. Transition to EVICTED
        app.evict()
        self.assertEqual(app.state, AppState.EVICTED)
        self.assertEqual(app.status, AppStatus.EVICTED)
        self.assertFalse(app.is_active)
        self.assertTrue(app.is_evicted)
        self.assertEqual(app.reference_bit, 0)

        # 4. Reactivate from EVICTED
        app.activate(AppState.FOREGROUND)
        self.assertEqual(app.state, AppState.FOREGROUND)
        self.assertEqual(app.status, AppStatus.ACTIVE)
        self.assertTrue(app.is_active)
        self.assertEqual(app.reference_bit, 1)

    def test_touch_updates_activity(self):
        """Test touch method updates timestamp and reference bit."""
        app = App(app_id="app_104", name="Chat", reference_bit=0)
        old_time = app.last_access_time - 10

        app.last_access_time = old_time
        self.assertEqual(app.reference_bit, 0)

        app.touch()
        self.assertEqual(app.reference_bit, 1)
        self.assertGreater(app.last_access_time, old_time)

    def test_resource_acquisition_and_release(self):
        """Test acquiring and releasing resource IDs on App model."""
        app = App(app_id="app_105", name="Music Player")
        self.assertEqual(app.held_resources, set())

        app.acquire_resource("AUDIO")
        app.acquire_resource("BLUETOOTH")
        self.assertEqual(app.held_resources, {"AUDIO", "BLUETOOTH"})

        app.release_resource("AUDIO")
        self.assertEqual(app.held_resources, {"BLUETOOTH"})

        app.release_resource("NON_EXISTENT")
        self.assertEqual(app.held_resources, {"BLUETOOTH"})

    def test_serialization_dict(self):
        """Test converting App to_dict and restoring from_dict."""
        app = App(
            app_id="app_106",
            name="Fitness",
            priority=2,
            state=AppState.FOREGROUND,
            memory_footprint=180,
            held_resources={"GPS"}
        )

        serialized = app.to_dict()
        self.assertEqual(serialized["app_id"], "app_106")
        self.assertEqual(serialized["name"], "Fitness")
        self.assertEqual(serialized["priority"], 2)
        self.assertEqual(serialized["state"], "FOREGROUND")
        self.assertEqual(serialized["memory_footprint"], 180)
        self.assertEqual(serialized["held_resources"], ["GPS"])
        self.assertEqual(serialized["status"], "ACTIVE")
        self.assertTrue(serialized["is_active"])

        restored = App.from_dict(serialized)
        self.assertEqual(restored.app_id, app.app_id)
        self.assertEqual(restored.name, app.name)
        self.assertEqual(restored.priority, app.priority)
        self.assertEqual(restored.state, app.state)
        self.assertEqual(restored.memory_footprint, app.memory_footprint)
        self.assertEqual(restored.held_resources, app.held_resources)
        self.assertEqual(restored.status, app.status)


if __name__ == "__main__":
    unittest.main()
