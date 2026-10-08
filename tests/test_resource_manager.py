"""Unit tests for ResourceManager in RAAE."""

import unittest
from backend.models.app import App, AppState
from backend.models.resource import Resource, ResourceStatus
from backend.resource_manager.resource_manager import ResourceManager


class TestResourceManager(unittest.TestCase):
    """Test suite for ResourceManager binary semaphore locking and queueing."""

    def setUp(self):
        self.res_mgr = ResourceManager()
        self.res_mgr.create_default_resources()

    def test_simulated_hardware_resources_exist(self):
        """Test that the four simulated hardware resources (Camera, Microphone, GPS, Audio) exist."""
        resources = self.res_mgr.get_all_resources()
        res_ids = [r.resource_id for r in resources]
        self.assertIn("Camera", res_ids)
        self.assertIn("Microphone", res_ids)
        self.assertIn("GPS", res_ids)
        self.assertIn("Audio", res_ids)

    def test_successful_acquisition(self):
        """Test successful acquisition of a free resource."""
        appA = App(app_id="appA", name="Camera App")
        success = self.res_mgr.request_resource(appA, "Camera")
        
        self.assertTrue(success)
        self.assertEqual(self.res_mgr.get_holder("Camera"), "appA")
        self.assertIn("Camera", appA.held_resources)

    def test_failed_acquisition_and_queue_insertion(self):
        """Test failed acquisition when occupied and verification of FIFO queue insertion."""
        appA = App(app_id="appA", name="App A")
        appB = App(app_id="appB", name="App B")
        appC = App(app_id="appC", name="App C")

        # App A acquires Camera
        self.assertTrue(self.res_mgr.request_resource(appA, "Camera"))

        # App B requests occupied Camera -> fails & enters waiting queue
        success_b = self.res_mgr.request_resource(appB, "Camera")
        self.assertFalse(success_b)
        self.assertEqual(appB.state, AppState.WAITING)

        # App C requests occupied Camera -> fails & enters waiting queue
        success_c = self.res_mgr.request_resource(appC, "Camera")
        self.assertFalse(success_c)

        # Verify waiting queue contains [appB, appC] in FIFO order
        queue = self.res_mgr.get_waiting_queue("Camera")
        self.assertEqual(queue, ["appB", "appC"])
        self.assertEqual(self.res_mgr.get_holder("Camera"), "appA")

    def test_release_and_next_requester(self):
        """Test resource release and automatic grant to next FIFO requester."""
        appA = App(app_id="appA", name="App A")
        appB = App(app_id="appB", name="App B")

        self.res_mgr.request_resource(appA, "Microphone")
        self.res_mgr.request_resource(appB, "Microphone")

        self.assertEqual(self.res_mgr.get_holder("Microphone"), "appA")
        self.assertEqual(self.res_mgr.get_waiting_queue("Microphone"), ["appB"])

        # App A releases Microphone
        released = self.res_mgr.release_resource(appA, "Microphone")
        self.assertTrue(released)
        self.assertNotIn("Microphone", appA.held_resources)

        # Next requester (appB) should now be holder
        self.assertEqual(self.res_mgr.get_holder("Microphone"), "appB")
        self.assertIn("Microphone", appB.held_resources)
        self.assertEqual(self.res_mgr.get_waiting_queue("Microphone"), [])

    def test_invalid_release(self):
        """Test that releasing a resource not held by an app returns False."""
        appA = App(app_id="appA", name="App A")
        appC = App(app_id="appC", name="App C")

        self.res_mgr.request_resource(appA, "GPS")

        # App C attempts invalid release
        released = self.res_mgr.release_resource(appC, "GPS")
        self.assertFalse(released)
        self.assertEqual(self.res_mgr.get_holder("GPS"), "appA")

    def test_prevent_acquiring_same_resource_twice(self):
        """Test that an app cannot acquire the same resource twice."""
        appA = App(app_id="appA", name="App A")
        
        self.assertTrue(self.res_mgr.request_resource(appA, "Audio"))
        
        # Second acquisition attempt by appA on Audio should fail
        self.assertFalse(self.res_mgr.request_resource(appA, "Audio"))
        self.assertEqual(self.res_mgr.get_holder("Audio"), "appA")

    def test_get_all_active_locks(self):
        """Test retrieving all active locks in the system."""
        appA = App(app_id="appA", name="App A")
        appB = App(app_id="appB", name="App B")

        self.res_mgr.request_resource(appA, "GPS")
        self.res_mgr.request_resource(appB, "Camera")

        active_locks = self.res_mgr.get_all_active_locks()
        self.assertEqual(active_locks.get("GPS"), "appA")
        self.assertEqual(active_locks.get("Camera"), "appB")

    def test_release_all_resources_for_app(self):
        """Test releasing all locks held by an app."""
        app = App(app_id="app_2", name="Camera App")
        self.res_mgr.request_resource(app, "Microphone")
        self.res_mgr.request_resource(app, "Audio")

        self.assertEqual(app.held_resources, {"Microphone", "Audio"})

        released_ids = self.res_mgr.release_all_resources_for_app(app)
        self.assertEqual(set(released_ids), {"Microphone", "Audio"})
        self.assertEqual(app.held_resources, set())
        self.assertEqual(self.res_mgr.get_all_active_locks(), {})


if __name__ == "__main__":
    unittest.main()

