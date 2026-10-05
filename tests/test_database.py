"""Unit tests for SQLite persistence in RAAE."""

import unittest
from backend.models.app import App, AppState, AppStatus
from backend.models.resource import Resource
from database.db import DatabaseManager


class TestDatabaseManager(unittest.TestCase):
    """Test suite for DatabaseManager SQLite operations."""

    def setUp(self):
        # Use in-memory SQLite database for tests
        self.db = DatabaseManager(db_path=":memory:")

    def test_save_and_get_app(self):
        """Test persisting an App instance to SQLite and retrieving it."""
        app = App(
            app_id="app_db_1",
            name="Maps App",
            priority=3,
            state=AppState.FOREGROUND,
            memory_footprint=250,
            reference_bit=1,
            held_resources={"GPS", "MIC"}
        )

        self.db.save_app(app)

        retrieved = self.db.get_app("app_db_1")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.app_id, "app_db_1")
        self.assertEqual(retrieved.name, "Maps App")
        self.assertEqual(retrieved.priority, 3)
        self.assertEqual(retrieved.state, AppState.FOREGROUND)
        self.assertEqual(retrieved.memory_footprint, 250)
        self.assertEqual(retrieved.held_resources, {"GPS", "MIC"})
        self.assertEqual(retrieved.status, AppStatus.ACTIVE)

    def test_update_app_in_db(self):
        """Test updating an existing App in SQLite (state transition & eviction)."""
        app = App(app_id="app_db_2", name="Camera", state=AppState.BACKGROUND)
        self.db.save_app(app)

        # Evict app and save
        app.evict()
        self.db.save_app(app)

        retrieved = self.db.get_app("app_db_2")
        self.assertEqual(retrieved.state, AppState.EVICTED)
        self.assertEqual(retrieved.status, AppStatus.EVICTED)
        self.assertTrue(retrieved.is_evicted)

    def test_get_all_apps_and_delete(self):
        """Test fetching all apps and deleting an app."""
        app1 = App(app_id="app_1", name="App 1")
        app2 = App(app_id="app_2", name="App 2")

        self.db.save_app(app1)
        self.db.save_app(app2)

        all_apps = self.db.get_all_apps()
        self.assertEqual(len(all_apps), 2)

        deleted = self.db.delete_app("app_1")
        self.assertTrue(deleted)
        self.assertIsNone(self.db.get_app("app_1"))

    def test_resource_persistence(self):
        """Test saving and getting a Resource record."""
        res = Resource("GPS", "GPS Sensor", capacity=1, waiting_queue=["app_2", "app_3"])
        self.db.save_resource(res)

        retrieved = self.db.get_resource("GPS")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.resource_id, "GPS")
        self.assertEqual(retrieved.capacity, 1)
        self.assertEqual(retrieved.waiting_queue, ["app_2", "app_3"])


if __name__ == "__main__":
    unittest.main()
