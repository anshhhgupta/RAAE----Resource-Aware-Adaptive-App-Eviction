"""Unit tests for the RAAE repository / data-access CRUD layer."""

import sqlite3
import unittest

from backend.models.app import App, AppState, AppStatus
from backend.models.resource import Resource
from database.db import DatabaseManager
from database.event_feed import CONFLICT_EVENT, EVICTION_EVENT, MEMORY_EVENT


class RepositoryTestCase(unittest.TestCase):
    """Base test case providing a fresh in-memory database per test."""

    def setUp(self):
        self.db = DatabaseManager(db_path=":memory:")

    def tearDown(self):
        self.db.close()


class TestAppCrud(RepositoryTestCase):
    """Test suite for create/update/get/delete operations on Apps."""

    def test_create_app_persists_all_columns(self):
        """Test that create_app persists every Apps column and is retrievable."""
        app = App(
            app_id="app_crud_1",
            name="Maps",
            priority=4,
            state=AppState.FOREGROUND,
            memory_footprint=250,
            reference_bit=0,
            held_resources={"GPS", "Camera"},
        )

        self.db.create_app(app)
        stored = self.db.get_app("app_crud_1")

        self.assertIsNotNone(stored)
        self.assertEqual(stored.name, "Maps")
        self.assertEqual(stored.priority, 4)
        self.assertEqual(stored.state, AppState.FOREGROUND)
        self.assertEqual(stored.memory_footprint, 250)
        self.assertEqual(stored.reference_bit, 0)
        self.assertEqual(stored.held_resources, {"GPS", "Camera"})
        self.assertEqual(stored.status, AppStatus.ACTIVE)

    def test_create_app_rejects_duplicate_id(self):
        """Test that create_app surfaces a unique constraint violation."""
        self.db.create_app(App(app_id="app_dup", name="First"))

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.create_app(App(app_id="app_dup", name="Second"))

    def test_update_app_modifies_existing_row(self):
        """Test that update_app persists new column values."""
        self.db.create_app(App(app_id="app_upd", name="Old Name", priority=1))

        app = self.db.get_app("app_upd")
        app.name = "New Name"
        app.priority = 5
        app.memory_footprint = 128
        self.assertTrue(self.db.update_app(app))

        stored = self.db.get_app("app_upd")
        self.assertEqual(stored.name, "New Name")
        self.assertEqual(stored.priority, 5)
        self.assertEqual(stored.memory_footprint, 128)

    def test_update_app_returns_false_for_unknown_id(self):
        """Test that update_app reports no match for a missing app_id."""
        self.assertFalse(self.db.update_app(App(app_id="app_missing", name="Ghost")))

    def test_get_all_apps_orders_by_priority_desc(self):
        """Test that get_all_apps returns highest priority first."""
        self.db.create_app(App(app_id="app_low", name="Low", priority=1))
        self.db.create_app(App(app_id="app_high", name="High", priority=9))

        ids = [app.app_id for app in self.db.get_all_apps()]
        self.assertEqual(ids, ["app_high", "app_low"])

    def test_delete_app_removes_row_and_cascades(self):
        """Test that delete_app removes the app and its dependent log rows."""
        self.db.create_app(App(app_id="app_del", name="Doomed"))
        self.db.insert_memory_event("app_del", "ALLOCATE", 10, 20, "GREEN")
        self.db.insert_eviction_log("app_del", "RAAE", "PRESSURE", True, True, "EVICTED")

        self.assertTrue(self.db.delete_app("app_del"))

        self.assertIsNone(self.db.get_app("app_del"))
        self.assertEqual(self.db.memory_events.get_events_by_app("app_del"), [])
        self.assertEqual(self.db.eviction_log.get_evictions_by_app("app_del"), [])

    def test_delete_app_returns_false_when_absent(self):
        """Test that deleting an unknown app_id reports no row removed."""
        self.assertFalse(self.db.delete_app("app_never_existed"))

    def test_app_id_is_treated_as_data_not_sql(self):
        """Test that a quote-bearing app_id is stored literally rather than executed."""
        hostile_id = "app_1'); DROP TABLE Apps;--"
        self.db.create_app(App(app_id=hostile_id, name="Injection Attempt"))

        self.assertIsNotNone(self.db.get_app(hostile_id))
        self.assertIn("Apps", self.db.get_table_names())


class TestResourceCrud(RepositoryTestCase):
    """Test suite for create/get/delete operations on Resources."""

    def test_create_resource_and_read_back(self):
        """Test that create_resource persists a new resource."""
        resource = Resource("DB_LOCK", "Shared Database Lock", capacity=4)

        self.db.create_resource(resource)
        stored = self.db.get_resource("DB_LOCK")

        self.assertIsNotNone(stored)
        self.assertEqual(stored.name, "Shared Database Lock")
        self.assertEqual(stored.capacity, 4)
        self.assertEqual(stored.available_units, 4)
        self.assertEqual(stored.waiting_queue, [])

    def test_get_resources_filters_by_status_and_holder(self):
        """Test that get_resources narrows results by status and holder."""
        self.db.create_app(App(app_id="app_holder", name="Holder"))
        self.db.create_resource(Resource("DB_LOCK", "Shared DB Lock", capacity=1))

        held = Resource("FILE_LOCK", "Shared File Lock", capacity=1, available_units=0)
        held.holders["app_holder"] = 1
        self.db.create_resource(held)

        free_ids = [r.resource_id for r in self.db.get_resources(status="FREE")]
        self.assertIn("DB_LOCK", free_ids)
        self.assertNotIn("FILE_LOCK", free_ids)
        self.assertEqual(
            [r.resource_id for r in self.db.get_resources(held_by_app_id="app_holder")],
            ["FILE_LOCK"],
        )
        self.assertEqual(
            [r.resource_id for r in self.db.get_resources(resource_id="db_lock")],
            ["DB_LOCK"],
        )

    def test_resource_holder_survives_round_trip(self):
        """Test that held_by_app_id is reconstructed into the holders map on read."""
        self.db.create_app(App(app_id="app_rt", name="Round Trip"))
        held = Resource("DB_LOCK", "Shared DB Lock", capacity=1, available_units=0)
        held.holders["app_rt"] = 1

        self.db.create_resource(held)
        stored = self.db.get_resource("DB_LOCK")

        self.assertEqual(stored.primary_holder_id, "app_rt")
        self.assertTrue(stored.is_locked)

    def test_delete_resource_removes_row(self):
        """Test that delete_resource removes the resource."""
        self.db.create_resource(Resource("FILE_LOCK", "File Lock", capacity=1))

        self.assertTrue(self.db.resources.delete_resource("FILE_LOCK"))
        self.assertIsNone(self.db.get_resource("FILE_LOCK"))

    def test_create_resource_rejects_unknown_holder(self):
        """Test that held_by_app_id foreign key is enforced."""
        orphan = Resource("BLUETOOTH", "Bluetooth", capacity=1, available_units=0)
        orphan.holders["app_ghost"] = 1

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.create_resource(orphan)


class TestResourceLockCrud(RepositoryTestCase):
    """Test suite for acquire/release operations on ResourceLocks."""

    def setUp(self):
        super().setUp()
        self.db.create_app(App(app_id="app_lock", name="Locker"))
        self.db.create_app(App(app_id="app_other", name="Other"))

    def test_acquire_and_release_resource_lock(self):
        """Test the acquire/release lifecycle of a single resource lock."""
        lock_id = self.db.acquire_resource_lock("app_lock", "GPS", units=1)
        self.assertGreater(lock_id, 0)

        active = self.db.resource_locks.get_active_locks()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["app_id"], "app_lock")
        self.assertEqual(active[0]["resource_id"], "GPS")
        self.assertEqual(active[0]["units"], 1)
        self.assertEqual(active[0]["app_name"], "Locker")

        self.assertTrue(self.db.release_resource_lock(lock_id))

        self.assertEqual(self.db.resource_locks.get_active_locks(), [])
        history = self.db.resource_locks.get_locks_by_resource("GPS")
        self.assertEqual(history[0]["status"], "RELEASED")
        self.assertIsNotNone(history[0]["released_at"])

    def test_release_is_not_idempotent(self):
        """Test that releasing an already released lock reports no change."""
        lock_id = self.db.acquire_resource_lock("app_lock", "GPS")

        self.assertTrue(self.db.release_resource_lock(lock_id))
        self.assertFalse(self.db.release_resource_lock(lock_id))

    def test_acquire_lock_rejects_unknown_app(self):
        """Test that acquiring a lock for an unregistered app violates the foreign key."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.acquire_resource_lock("app_missing", "GPS")

    def test_release_all_for_app_returns_count(self):
        """Test that releasing an app's locks returns how many were transitioned."""
        first = self.db.acquire_resource_lock("app_lock", "GPS")
        second = self.db.acquire_resource_lock("app_lock", "Camera")
        self.db.acquire_resource_lock("app_other", "Audio")

        self.assertEqual(self.db.resource_locks.release_all_for_app("app_lock"), 2)

        released = {row["lock_id"] for row in self.db.resource_locks.get_active_locks()}
        self.assertEqual(released, {self.db.resource_locks.get_locks_by_app("app_other")[0]["lock_id"]})
        self.assertNotIn(first, released)
        self.assertNotIn(second, released)


class TestLogInsertCrud(RepositoryTestCase):
    """Test suite for insert operations on the three log tables."""

    def setUp(self):
        super().setUp()
        self.db.create_app(App(app_id="app_log_a", name="Logger A"))
        self.db.create_app(App(app_id="app_log_b", name="Logger B"))

    def test_insert_memory_event_persists_supplied_timestamp(self):
        """Test that insert_memory_event honors an explicit timestamp."""
        event_id = self.db.insert_memory_event(
            "app_log_a", "ALLOCATE", 100, 250, "YELLOW", timestamp=123.5
        )
        self.assertGreater(event_id, 0)

        events = self.db.memory_events.get_events_by_app("app_log_a")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "ALLOCATE")
        self.assertEqual(events[0]["memory_before"], 100)
        self.assertEqual(events[0]["memory_after"], 250)
        self.assertEqual(events[0]["pressure_level"], "YELLOW")
        self.assertEqual(events[0]["timestamp"], 123.5)

    def test_insert_memory_event_defaults_timestamp_to_now(self):
        """Test that insert_memory_event fills in a timestamp when omitted."""
        self.db.insert_memory_event("app_log_a", "DEALLOCATE", 250, 100, "GREEN")

        events = self.db.memory_events.get_all_events()
        self.assertEqual(len(events), 1)
        self.assertGreater(events[0]["timestamp"], 0)

    def test_insert_eviction_log_stores_flags_as_integers(self):
        """Test that insert_eviction_log persists booleans as 0/1 integers."""
        event_id = self.db.insert_eviction_log(
            "app_log_a", "RAAE_CLOCK", "PRESSURE_CRITICAL", True, False, "SKIPPED"
        )
        self.assertGreater(event_id, 0)

        logs = self.db.eviction_log.get_evictions_by_app("app_log_a")
        self.assertEqual(logs[0]["algorithm"], "RAAE_CLOCK")
        self.assertEqual(logs[0]["reason"], "PRESSURE_CRITICAL")
        self.assertEqual(logs[0]["lock_checked"], 1)
        self.assertEqual(logs[0]["safe_release"], 0)
        self.assertEqual(logs[0]["result"], "SKIPPED")

    def test_insert_conflict_log_persists_optional_fields(self):
        """Test that insert_conflict_log stores strategy, resolver and details."""
        conflict_id = self.db.insert_conflict_log(
            waiting_app_id="app_log_b",
            blocking_app_id="app_log_a",
            resource_id="GPS",
            resolution_strategy="WOUND_WAIT",
            resolved_by="WOUND",
            details="Higher priority app preempted",
        )
        self.assertGreater(conflict_id, 0)

        conflicts = self.db.conflict_log.get_conflicts_by_resource("GPS")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["waiting_app_id"], "app_log_b")
        self.assertEqual(conflicts[0]["blocking_app_id"], "app_log_a")
        self.assertEqual(conflicts[0]["resolved_by"], "WOUND")
        self.assertEqual(conflicts[0]["details"], "Higher priority app preempted")

    def test_insert_conflict_log_defaults_to_null_optional_fields(self):
        """Test that omitted resolver and details are stored as NULL."""
        self.db.insert_conflict_log("app_log_b", "app_log_a", "Camera")

        conflicts = self.db.conflict_log.get_conflicts_by_resource("Camera")
        self.assertIsNone(conflicts[0]["resolved_by"])
        self.assertIsNone(conflicts[0]["details"])
        self.assertEqual(conflicts[0]["resolution_strategy"], "WOUND_WAIT")

    def test_insert_eviction_log_rejects_unknown_app(self):
        """Test that log tables enforce the Apps foreign key."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.insert_eviction_log("app_ghost", "RAAE", "WHY", False, False, "EVICTED")


class TestGetRecentEvents(RepositoryTestCase):
    """Test suite for the merged cross-table event timeline."""

    def setUp(self):
        super().setUp()
        self.db.create_app(App(app_id="app_feed", name="Feeder"))
        self.db.create_app(App(app_id="app_wait", name="Waiter"))
        self.db.create_app(App(app_id="app_other", name="Unrelated"))

        self.db.insert_memory_event("app_feed", "ALLOCATE", 10, 20, "GREEN", timestamp=100.0)
        self.db.insert_eviction_log("app_feed", "RAAE", "PRESSURE", True, True, "EVICTED", timestamp=200.0)
        self.db.insert_conflict_log(
            "app_wait", "app_feed", "GPS", details="contended", timestamp=300.0
        )
        self.db.insert_memory_event("app_other", "ALLOCATE", 5, 9, "GREEN", timestamp=400.0)

    def test_get_recent_events_merges_all_tables_newest_first(self):
        """Test that events from all three log tables are merged in timestamp order."""
        events = self.db.get_recent_events()

        self.assertEqual(len(events), 4)
        self.assertEqual(
            [event["timestamp"] for event in events], [400.0, 300.0, 200.0, 100.0]
        )
        self.assertEqual(
            [event["event_type"] for event in events],
            [MEMORY_EVENT, CONFLICT_EVENT, EVICTION_EVENT, MEMORY_EVENT],
        )

    def test_get_recent_events_exposes_normalized_columns(self):
        """Test that each merged row carries the documented common shape."""
        events = self.db.get_recent_events(limit=1)

        self.assertEqual(
            set(events[0].keys()),
            {
                "event_type",
                "id",
                "app_id",
                "resource_id",
                "code",
                "detail",
                "value_before",
                "value_after",
                "timestamp",
            },
        )

    def test_get_recent_events_filters_by_app(self):
        """Test that app_id filtering matches memory, eviction and both conflict sides."""
        feeder_events = self.db.get_recent_events(app_id="app_feed")

        self.assertEqual(len(feeder_events), 3)
        self.assertTrue(all(event["app_id"] == "app_feed" for event in feeder_events))
        self.assertEqual(
            sorted(event["event_type"] for event in feeder_events),
            sorted([MEMORY_EVENT, EVICTION_EVENT, CONFLICT_EVENT]),
        )

        waiter_events = self.db.get_recent_events(app_id="app_wait")
        self.assertEqual([event["event_type"] for event in waiter_events], [CONFLICT_EVENT])

    def test_get_recent_events_filters_by_resource_id(self):
        """Test that resource_id filtering narrows to conflicts on that resource."""
        events = self.db.get_recent_events(resource_id="GPS")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], CONFLICT_EVENT)
        self.assertEqual(events[0]["resource_id"], "GPS")

    def test_get_recent_events_filters_by_type(self):
        """Test that event_types restricts the timeline to the selected tables."""
        events = self.db.get_recent_events(event_types=[MEMORY_EVENT])

        self.assertEqual(len(events), 2)
        self.assertTrue(all(event["event_type"] == MEMORY_EVENT for event in events))

    def test_get_recent_events_combines_filters(self):
        """Test that app_id, resource_id and event_types filters compose."""
        events = self.db.get_recent_events(
            app_id="app_feed", resource_id="GPS", event_types=[CONFLICT_EVENT]
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], CONFLICT_EVENT)

    def test_get_recent_events_respects_limit(self):
        """Test that limit caps the number of returned events."""
        self.assertEqual(len(self.db.get_recent_events(limit=2)), 2)

    def test_get_recent_events_rejects_unknown_type(self):
        """Test that an unrecognized event type raises ValueError."""
        with self.assertRaises(ValueError):
            self.db.get_recent_events(event_types=["NOT_A_TYPE"])


class TestSchemaIndexes(RepositoryTestCase):
    """Test suite verifying the indexes declared in schema.sql are created."""

    def test_indexes_cover_app_id_timestamp_and_resource_id(self):
        """Test that the expected query-supporting indexes exist."""
        indexes = set(self.db.get_index_names())

        for expected in (
            "idx_resourcelocks_app",
            "idx_resourcelocks_resource",
            "idx_memoryevents_app",
            "idx_memoryevents_timestamp",
            "idx_memoryevents_action",
            "idx_evictionlog_app",
            "idx_evictionlog_timestamp",
            "idx_evictionlog_algorithm",
            "idx_conflictlog_resource",
            "idx_conflictlog_timestamp",
            "idx_conflictlog_waiting",
            "idx_conflictlog_blocking",
            "idx_conflictlog_strategy",
            "idx_apps_status",
            "idx_resources_held_by",
        ):
            self.assertIn(expected, indexes)

    def test_schema_initialization_is_idempotent(self):
        """Test that re-applying schema.sql neither errors nor drops data."""
        self.db.create_app(App(app_id="app_idem", name="Idempotent"))
        self.db.init_db()
        self.db.init_db()

        self.assertIsNotNone(self.db.get_app("app_idem"))
        self.assertEqual(len(self.db.get_index_names()), len(set(self.db.get_index_names())))


class TestTransactionBehaviour(RepositoryTestCase):
    """Test suite covering atomic multi-repository writes."""

    def test_transaction_commits_all_operations(self):
        """Test that repository writes inside a transaction are persisted together."""
        with self.db.transaction():
            self.db.create_app(App(app_id="app_tx", name="Transactional"))
            self.db.insert_memory_event("app_tx", "ALLOCATE", 0, 10, "GREEN")

        self.assertIsNotNone(self.db.get_app("app_tx"))
        self.assertEqual(len(self.db.memory_events.get_events_by_app("app_tx")), 1)

    def test_transaction_rolls_back_all_operations(self):
        """Test that a failure inside a transaction discards every write."""
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.db.create_app(App(app_id="app_tx_fail", name="Doomed"))
                self.db.insert_memory_event("app_tx_fail", "ALLOCATE", 0, 10, "GREEN")
                raise RuntimeError("force rollback")

        self.assertIsNone(self.db.get_app("app_tx_fail"))
        self.assertEqual(self.db.memory_events.get_events_by_app("app_tx_fail"), [])


if __name__ == "__main__":
    unittest.main()