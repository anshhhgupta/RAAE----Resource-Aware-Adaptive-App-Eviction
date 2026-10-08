"""Unit tests for SQLite persistence and relational schema in RAAE."""

import unittest
import sqlite3
from backend.models.app import App, AppState, AppStatus
from backend.models.resource import Resource, ResourceStatus
from database.db import DatabaseManager


class TestDatabaseManager(unittest.TestCase):
    """Test suite for DatabaseManager SQLite operations, schema integrity, and repositories."""

    def setUp(self):
        # Use in-memory SQLite database for tests
        self.db = DatabaseManager(db_path=":memory:")

    def tearDown(self):
        self.db.close()

    def test_all_six_tables_exist(self):
        """Test that all 6 required tables exist in the initialized database."""
        required_tables = {"Apps", "Resources", "ResourceLocks", "MemoryEvents", "EvictionLog", "ConflictLog"}
        existing_tables = set(self.db.get_table_names())
        # SQLite table names are case-insensitive, let's verify case-insensitively and directly
        existing_tables_lower = {t.lower() for t in existing_tables}
        required_tables_lower = {t.lower() for t in required_tables}

        self.assertTrue(
            required_tables_lower.issubset(existing_tables_lower),
            f"Missing tables: {required_tables_lower - existing_tables_lower}"
        )

    def test_default_resources_seeded(self):
        """Test that initial seed data contains Camera, Microphone, GPS, and Audio."""
        all_res = self.db.resources.get_all()
        res_names = {r.name for r in all_res}
        for expected in ["Camera", "Microphone", "GPS", "Audio"]:
            self.assertIn(expected, res_names)

    def test_idempotent_initialization(self):
        """Test that running init_db multiple times does not duplicate or erase data."""
        app = App(app_id="app_idempotent", name="Idempotency Test", priority=2)
        self.db.save_app(app)

        # Call init_db again
        self.db.init_db()

        # Check that original record is still preserved
        retrieved = self.db.get_app("app_idempotent")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.name, "Idempotency Test")

        # Check that resource count did not duplicate
        all_res = self.db.resources.get_all()
        res_names = [r.name for r in all_res]
        self.assertEqual(len(res_names), len(set(res_names)))

    def test_foreign_key_enforcement(self):
        """Test that foreign key constraints are enabled and enforced."""
        conn = self.db.get_connection()
        # Verify PRAGMA foreign_keys is ON
        fk_check = conn.execute("PRAGMA foreign_keys;").fetchone()
        self.assertEqual(fk_check[0], 1)

        # Attempt to insert a ResourceLock with a non-existent app_id -> should raise IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ResourceLocks (app_id, resource_id, units, status, acquired_at) VALUES (?, ?, ?, ?, ?)",
                ("non_existent_app", "GPS", 1, "HELD", 12345.0)
            )

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
        app = App(app_id="app_db_2", name="Camera App", state=AppState.BACKGROUND)
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
        self.assertGreaterEqual(len(all_apps), 2)

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

    def test_log_memory_action_uses_provided_timestamp(self):
        """Test that a supplied memory action timestamp is persisted."""
        self.db.log_memory_action("app_1", "ALLOCATE", 100, 150, "GREEN", timestamp=123.5)

        logs = self.db.get_all_memory_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["timestamp"], 123.5)

    def test_resource_locks_repository(self):
        """Test acquiring and releasing resource locks via ResourceLockRepository."""
        app = App(app_id="app_lock_1", name="Navigator")
        self.db.save_app(app)

        lock_id = self.db.resource_locks.acquire_lock("app_lock_1", "GPS", units=1)
        self.assertGreater(lock_id, 0)

        active = self.db.resource_locks.get_active_locks()
        self.assertTrue(any(l["app_id"] == "app_lock_1" and l["resource_id"] == "GPS" for l in active))

        # Release lock
        released = self.db.resource_locks.release_lock(lock_id)
        self.assertTrue(released)
        active_after = self.db.resource_locks.get_active_locks()
        self.assertFalse(any(l["lock_id"] == lock_id for l in active_after))

    def test_memory_events_repository(self):
        """Test logging memory allocation / deallocation events."""
        app = App(app_id="app_mem_1", name="Browser")
        self.db.save_app(app)

        event_id = self.db.memory_events.log_event(
            app_id="app_mem_1",
            action="ALLOCATE",
            memory_before=100,
            memory_after=250,
            pressure_level="YELLOW"
        )
        self.assertGreater(event_id, 0)

        events = self.db.memory_events.get_events_by_app("app_mem_1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "ALLOCATE")
        self.assertEqual(events[0]["pressure_level"], "YELLOW")

    def test_eviction_log_repository(self):
        """Test logging eviction decisions."""
        app = App(app_id="app_evict_1", name="Game")
        self.db.save_app(app)

        log_id = self.db.eviction_log.log_eviction(
            app_id="app_evict_1",
            algorithm="RAAE_CLOCK",
            reason="MEMORY_PRESSURE_CRITICAL",
            lock_checked=True,
            safe_release=True,
            result="EVICTED"
        )
        self.assertGreater(log_id, 0)

        logs = self.db.eviction_log.get_evictions_by_app("app_evict_1")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["algorithm"], "RAAE_CLOCK")
        self.assertEqual(logs[0]["lock_checked"], 1)
        self.assertEqual(logs[0]["result"], "EVICTED")

    def test_conflict_log_repository(self):
        """Test logging resource conflicts and resolution."""
        app1 = App(app_id="app_conf_1", name="Holder")
        app2 = App(app_id="app_conf_2", name="Requester")
        self.db.save_app(app1)
        self.db.save_app(app2)

        conflict_id = self.db.conflict_log.log_conflict(
            waiting_app_id="app_conf_2",
            blocking_app_id="app_conf_1",
            resource_id="GPS",
            resolution_strategy="WOUND_WAIT",
            resolved_by="WOUND",
            details="Higher priority app 2 preempted app 1"
        )
        self.assertGreater(conflict_id, 0)

        conflicts = self.db.conflict_log.get_conflicts_by_resource("GPS")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["waiting_app_id"], "app_conf_2")
        self.assertEqual(conflicts[0]["blocking_app_id"], "app_conf_1")



if __name__ == "__main__":
    unittest.main()
