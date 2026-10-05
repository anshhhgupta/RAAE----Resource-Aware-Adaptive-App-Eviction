"""Unit tests for Semaphore-style resource locking."""

import unittest
from backend.models.app import App, AppState
from backend.models.resource import Resource, ResourceStatus
from backend.resource_manager.semaphore import SemaphoreLock


class TestSemaphoreLock(unittest.TestCase):
    """Test suite for SemaphoreLock behavior and app WAITING state transitions."""

    def test_mutex_acquisition(self):
        """Test binary semaphore (mutex) lock acquisition."""
        res = Resource("GPS", "GPS Sensor", capacity=1)
        lock = SemaphoreLock(res)

        appA = App(app_id="A", name="App A")
        
        # Acquire lock
        self.assertTrue(lock.acquire(appA))
        self.assertEqual(res.available_units, 0)
        self.assertIn("GPS", appA.held_resources)
        self.assertEqual(res.status, ResourceStatus.LOCKED)

    def test_blocking_causes_waiting_state(self):
        """Test that attempting to acquire an unavailable resource sets app state to WAITING."""
        res = Resource("GPS", "GPS Sensor", capacity=1)
        lock = SemaphoreLock(res)

        appA = App(app_id="A", name="App A", state=AppState.FOREGROUND)
        appB = App(app_id="B", name="App B", state=AppState.BACKGROUND)

        # App A acquires GPS
        lock.acquire(appA)

        # App B requests GPS while held by App A
        acquired = lock.acquire(appB)
        self.assertFalse(acquired)
        self.assertEqual(appB.state, AppState.WAITING)
        self.assertIn("B", res.waiting_queue)
        self.assertEqual(res.status, ResourceStatus.WAITING)

    def test_release_unblocks_waiting_app(self):
        """Test that releasing a lock unblocks the next app in waiting queue and restores state."""
        res = Resource("GPS", "GPS Sensor", capacity=1)
        lock = SemaphoreLock(res)

        appA = App(app_id="A", name="App A", state=AppState.FOREGROUND)
        appB = App(app_id="B", name="App B", state=AppState.BACKGROUND)
        apps_map = {"A": appA, "B": appB}

        lock.acquire(appA)
        lock.acquire(appB) # App B blocks & enters WAITING

        self.assertEqual(appB.state, AppState.WAITING)

        # App A releases GPS with apps_map passed for wakeup
        released = lock.release(appA, registered_apps=apps_map)
        self.assertTrue(released)

        # App B should now hold the lock and state changed from WAITING
        self.assertNotIn("GPS", appA.held_resources)
        self.assertIn("GPS", appB.held_resources)
        self.assertNotEqual(appB.state, AppState.WAITING)
        self.assertEqual(res.holders.get("B"), 1)
        self.assertEqual(len(res.waiting_queue), 0)

    def test_counting_semaphore(self):
        """Test counting semaphore with capacity > 1."""
        res = Resource("FILE_LOCK", "File Locks", capacity=2)
        lock = SemaphoreLock(res)

        appA = App(app_id="A", name="App A")
        appB = App(app_id="B", name="App B")
        appC = App(app_id="C", name="App C")

        self.assertTrue(lock.acquire(appA))
        self.assertTrue(lock.acquire(appB))
        self.assertEqual(res.available_units, 0)

        # 3rd request blocks
        self.assertFalse(lock.acquire(appC))
        self.assertEqual(appC.state, AppState.WAITING)


if __name__ == "__main__":
    unittest.main()
