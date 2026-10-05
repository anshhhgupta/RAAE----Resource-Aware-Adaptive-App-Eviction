"""Unit tests for ResourceManager in RAAE."""

import unittest
from backend.models.app import App, AppState
from backend.models.resource import Resource, ResourceStatus
from backend.resource_manager.resource_manager import ResourceManager


class TestResourceManager(unittest.TestCase):
    """Test suite for ResourceManager."""

    def setUp(self):
        self.res_mgr = ResourceManager()
        self.res_mgr.create_default_resources()

    def test_default_resources_created(self):
        """Test that default resources like GPS, MIC, AUDIO exist."""
        resources = self.res_mgr.get_all_resources()
        res_ids = [r.resource_id for r in resources]
        self.assertIn("GPS", res_ids)
        self.assertIn("MIC", res_ids)
        self.assertIn("AUDIO", res_ids)
        self.assertIn("DB_LOCK", res_ids)

    def test_acquire_and_release_resource(self):
        """Test acquiring and releasing a resource."""
        app = App(app_id="app_1", name="Maps")
        
        # 1. Acquire GPS
        success = self.res_mgr.acquire_resource(app, "GPS")
        self.assertTrue(success)
        self.assertIn("GPS", app.held_resources)
        
        gps = self.res_mgr.get_resource("GPS")
        self.assertEqual(gps.status, ResourceStatus.LOCKED)
        self.assertEqual(gps.available_units, 0)
        self.assertEqual(gps.holders.get("app_1"), 1)

        # 2. Release GPS
        released = self.res_mgr.release_resource(app, "GPS")
        self.assertTrue(released)
        self.assertNotIn("GPS", app.held_resources)
        self.assertEqual(gps.status, ResourceStatus.FREE)
        self.assertEqual(gps.available_units, 1)

    def test_release_all_resources_for_app(self):
        """Test releasing all locks held by an app."""
        app = App(app_id="app_2", name="Camera")
        self.res_mgr.acquire_resource(app, "MIC")
        self.res_mgr.acquire_resource(app, "AUDIO")

        self.assertEqual(app.held_resources, {"MIC", "AUDIO"})

        released_ids = self.res_mgr.release_all_resources_for_app(app)
        self.assertEqual(set(released_ids), {"MIC", "AUDIO"})
        self.assertEqual(app.held_resources, set())


if __name__ == "__main__":
    unittest.main()
