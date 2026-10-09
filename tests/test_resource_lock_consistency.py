"""Tests for ResourceLocks consistency: ledger persistence and trigger maintenance.

The contract these tests pin down:

* ``ResourceLocks`` records every acquisition and release, so the ledger is a
  faithful history of lock ownership.
* Invalid foreign keys and self-contradictory lock states are refused by the
  database.
* The lock-dependent columns of ``Resources`` are re-derived by SQLite triggers
  whenever the ledger changes, so the two tables cannot drift apart.
* SQLite never decides whether a lock may be acquired. It records the decision
  Python has already made and keeps the derived view consistent.
"""

import sqlite3
import unittest

from backend.models.app import App
from backend.models.resource import Resource, ResourceStatus
from backend.resource_manager.resource_manager import ResourceManager
from database.db import DatabaseManager


class ResourceLockTestCase(unittest.TestCase):
    """Base test case providing a fresh in-memory database with seeded resources."""

    def setUp(self):
        self.db = DatabaseManager(db_path=":memory:")
        self.db.create_app(App(app_id="app_a", name="Alpha"))
        self.db.create_app(App(app_id="app_b", name="Beta"))
        self.db.create_app(App(app_id="app_c", name="Gamma"))

    def tearDown(self):
        self.db.close()

    def resource_row(self, resource_id):
        """Returns the raw Resources row so tests can inspect persisted columns."""
        return self.db.get_connection().execute(
            "SELECT * FROM Resources WHERE resource_id = ?", (resource_id,)
        ).fetchone()

    def trigger_names(self):
        """Returns every trigger name defined in the database."""
        rows = self.db.get_connection().execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
        ).fetchall()
        return [row["name"] for row in rows]


class TestLockInsertion(ResourceLockTestCase):
    """Lock insertion is recorded faithfully in the ledger."""

    def test_acquire_resource_lock_returns_new_lock_id(self):
        """Test that acquiring a lock returns a usable identifier."""
        lock_id = self.db.acquire_resource_lock("app_a", "GPS")

        self.assertGreater(lock_id, 0)
        self.assertIsNotNone(self.db.get_active_lock("app_a", "GPS"))

    def test_acquire_resource_lock_persists_defaults(self):
        """Test that a new lock defaults to one HELD unit with no release time."""
        lock_id = self.db.acquire_resource_lock("app_a", "GPS")

        lock = self.db.resource_locks.get_active_lock("app_a", "GPS")
        self.assertEqual(lock["lock_id"], lock_id)
        self.assertEqual(lock["app_id"], "app_a")
        self.assertEqual(lock["resource_id"], "GPS")
        self.assertEqual(lock["units"], 1)
        self.assertEqual(lock["status"], "HELD")
        self.assertIsNone(lock["released_at"])
        self.assertGreater(lock["acquired_at"], 0)

    def test_acquire_resource_lock_persists_explicit_values(self):
        """Test that explicit units and timestamp are stored as supplied."""
        self.db.resource_locks.acquire_resource_lock(
            "app_a", "GPS", units=2, acquired_at=111.5
        )

        lock = self.db.get_active_lock("app_a", "GPS")
        self.assertEqual(lock["units"], 2)
        self.assertEqual(lock["acquired_at"], 111.5)

    def test_acquire_resource_lock_joins_app_and_resource_names(self):
        """Test that active locks expose the related app and resource names."""
        self.db.acquire_resource_lock("app_a", "GPS")

        active = self.db.resource_locks.get_active_locks()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["app_name"], "Alpha")
        self.assertEqual(active[0]["resource_name"], "GPS")

    def test_get_active_lock_returns_none_when_app_holds_nothing(self):
        """Test that a missing live lock reports None rather than an error."""
        self.assertIsNone(self.db.get_active_lock("app_a", "GPS"))

    def test_locks_across_apps_and_resources_are_recorded_independently(self):
        """Test that each app/resource pair gets its own ledger row."""
        self.db.acquire_resource_lock("app_a", "GPS")
        self.db.acquire_resource_lock("app_a", "Camera")
        self.db.acquire_resource_lock("app_b", "GPS")

        active = {(row["app_id"], row["resource_id"]) for row in self.db.resource_locks.get_active_locks()}
        self.assertEqual(
            active,
            {("app_a", "GPS"), ("app_a", "Camera"), ("app_b", "GPS")},
        )


class TestLockRelease(ResourceLockTestCase):
    """Releasing a lock retires the ledger row and keeps it as history."""

    def test_release_resource_lock_transitions_status_and_timestamp(self):
        """Test that releasing by lock_id records the release time."""
        lock_id = self.db.acquire_resource_lock("app_a", "GPS")

        self.assertTrue(self.db.release_resource_lock(lock_id, released_at=222.5))

        history = self.db.resource_locks.get_locks_by_resource("GPS")
        self.assertEqual(history[0]["lock_id"], lock_id)
        self.assertEqual(history[0]["status"], "RELEASED")
        self.assertEqual(history[0]["released_at"], 222.5)
        self.assertEqual(self.db.resource_locks.get_active_locks(), [])

    def test_release_resource_lock_defaults_timestamp_to_now(self):
        """Test that omitting the release time stamps it automatically."""
        lock_id = self.db.acquire_resource_lock("app_a", "GPS")

        self.assertTrue(self.db.release_resource_lock(lock_id))

        history = self.db.resource_locks.get_locks_by_resource("GPS")
        self.assertIsNotNone(history[0]["released_at"])
        self.assertGreaterEqual(history[0]["released_at"], history[0]["acquired_at"])

    def test_release_resource_lock_is_not_repeatable(self):
        """Test that a second release reports that nothing changed."""
        lock_id = self.db.acquire_resource_lock("app_a", "GPS")

        self.assertTrue(self.db.release_resource_lock(lock_id))
        self.assertFalse(self.db.release_resource_lock(lock_id))

    def test_release_resource_lock_keeps_first_release_time(self):
        """Test that re-releasing never overwrites the recorded release time."""
        lock_id = self.db.acquire_resource_lock("app_a", "GPS")
        self.db.release_resource_lock(lock_id, released_at=100.0)

        self.db.release_resource_lock(lock_id, released_at=200.0)

        self.assertEqual(self.db.resource_locks.get_locks_by_resource("GPS")[0]["released_at"], 100.0)

    def test_release_lock_for_retires_by_app_and_resource(self):
        """Test that a holder can be identified without knowing the lock_id."""
        self.db.acquire_resource_lock("app_a", "GPS")

        self.assertTrue(self.db.release_lock_for("app_a", "GPS"))
        self.assertIsNone(self.db.get_active_lock("app_a", "GPS"))

    def test_release_lock_for_reports_nothing_to_release(self):
        """Test that releasing a lock the app does not hold changes no rows."""
        self.db.acquire_resource_lock("app_a", "GPS")

        self.assertFalse(self.db.release_lock_for("app_b", "GPS"))
        self.assertFalse(self.db.release_lock_for("app_a", "Camera"))

    def test_release_all_for_app_returns_transitioned_count(self):
        """Test that bulk release reports how many locks it retired."""
        self.db.acquire_resource_lock("app_a", "GPS")
        self.db.acquire_resource_lock("app_a", "Camera")
        self.db.acquire_resource_lock("app_b", "Audio")

        self.assertEqual(self.db.resource_locks.release_all_for_app("app_a"), 2)

        remaining = {row["app_id"] for row in self.db.resource_locks.get_active_locks()}
        self.assertEqual(remaining, {"app_b"})
        self.assertEqual(
            {row["status"] for row in self.db.resource_locks.get_locks_by_app("app_a")},
            {"RELEASED"},
        )

    def test_release_all_for_app_leaves_released_history_untouched(self):
        """Test that bulk release does not re-stamp locks already released."""
        self.db.acquire_resource_lock("app_a", "GPS")
        self.db.release_resource_lock(self.db.get_active_lock("app_a", "GPS")["lock_id"], released_at=50.0)

        self.assertEqual(self.db.resource_locks.release_all_for_app("app_a"), 0)
        self.assertEqual(self.db.resource_locks.get_locks_by_app("app_a")[0]["released_at"], 50.0)


class TestLockForeignKeyIntegrity(ResourceLockTestCase):
    """Foreign keys and column checks reject impossible ledger rows."""

    def test_unknown_app_id_is_rejected(self):
        """Test that a lock for an unregistered app violates the Apps foreign key."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.acquire_resource_lock("app_ghost", "GPS")

    def test_unknown_resource_id_is_rejected(self):
        """Test that a lock on an unregistered resource violates the Resources foreign key."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.acquire_resource_lock("app_a", "TELEPORTER")

    def test_rejected_insert_leaves_no_ledger_or_resource_change(self):
        """Test that a refused insert rolls back cleanly and leaves no trace."""
        self.db.acquire_resource_lock("app_a", "GPS")

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.acquire_resource_lock("app_ghost", "GPS")

        self.assertEqual(len(self.db.resource_locks.get_locks_by_app("app_ghost")), 0)
        self.assertEqual(len(self.db.resource_locks.get_locks_by_resource("GPS")), 1)

    def test_deleting_an_app_cascades_to_its_locks(self):
        """Test that removing an app removes the locks that referenced it."""
        self.db.acquire_resource_lock("app_a", "GPS")
        self.db.acquire_resource_lock("app_b", "GPS")

        self.assertTrue(self.db.delete_app("app_a"))

        self.assertEqual(self.db.resource_locks.get_locks_by_app("app_a"), [])
        self.assertEqual(
            [row["app_id"] for row in self.db.resource_locks.get_active_locks()],
            ["app_b"],
        )

    def test_deleting_a_resource_cascades_to_its_locks(self):
        """Test that removing a resource removes the locks that referenced it."""
        self.db.acquire_resource_lock("app_a", "GPS")

        self.assertTrue(self.db.resources.delete_resource("GPS"))

        self.assertEqual(self.db.resource_locks.get_locks_by_resource("GPS"), [])

    def test_zero_or_negative_units_are_rejected(self):
        """Test that a lock must cover at least one unit."""
        for units in (0, -1):
            with self.subTest(units=units):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.db.acquire_resource_lock("app_a", "GPS", units=units)

    def test_unknown_status_is_rejected(self):
        """Test that lock status is constrained to the documented values."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.resource_locks.acquire_resource_lock("app_a", "GPS", status="PENDING")

    def test_released_lock_without_a_release_time_is_rejected(self):
        """Test that a RELEASED row must say when it was released."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.get_connection().execute(
                "INSERT INTO ResourceLocks "
                "(app_id, resource_id, units, status, acquired_at, released_at) "
                "VALUES (?, ?, ?, 'RELEASED', ?, NULL)",
                ("app_a", "GPS", 1, 1.0),
            )


class TestDuplicateLockPrevention(ResourceLockTestCase):
    """Mutually exclusive lock rows are refused; compatible ones are accepted."""

    def test_second_concurrent_lock_for_same_app_and_resource_is_rejected(self):
        """Test that one app cannot hold two live locks on the same resource."""
        self.db.acquire_resource_lock("app_a", "GPS")

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.acquire_resource_lock("app_a", "GPS")

        self.assertEqual(len(self.db.resource_locks.get_locks_by_resource("GPS")), 1)

    def test_duplicate_lock_does_not_disturb_the_existing_holder(self):
        """Test that a refused duplicate leaves the original lock untouched."""
        self.db.acquire_resource_lock("app_a", "GPS", acquired_at=10.0)

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.acquire_resource_lock("app_a", "GPS", acquired_at=20.0)

        lock = self.db.get_active_lock("app_a", "GPS")
        self.assertEqual(lock["acquired_at"], 10.0)
        self.assertEqual(lock["status"], "HELD")

    def test_reacquiring_after_release_is_allowed_and_kept_as_history(self):
        """Test that released rows are excluded from duplicate detection."""
        first = self.db.acquire_resource_lock("app_a", "GPS")
        self.db.release_resource_lock(first, released_at=50.0)

        second = self.db.acquire_resource_lock("app_a", "GPS")

        self.assertNotEqual(second, first)
        history = self.db.resource_locks.get_locks_by_resource("GPS")
        self.assertEqual(len(history), 2)
        self.assertEqual(
            sorted(row["status"] for row in history),
            ["HELD", "RELEASED"],
        )

    def test_same_app_may_hold_locks_on_different_resources(self):
        """Test that duplicate prevention is scoped to one app/resource pair."""
        self.db.acquire_resource_lock("app_a", "GPS")
        self.db.acquire_resource_lock("app_a", "Camera")

        self.assertEqual(len(self.db.resource_locks.get_active_locks()), 2)

    def test_unique_index_enforces_the_duplicate_rule_directly(self):
        """Test that the partial unique index, not just the API, blocks duplicates."""
        self.db.acquire_resource_lock("app_a", "GPS")

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.get_connection().execute(
                "INSERT INTO ResourceLocks (app_id, resource_id, units, status, acquired_at) "
                "VALUES (?, ?, ?, 'HELD', ?)",
                ("app_a", "GPS", 1, 99.0),
            )

    def test_unique_index_name_is_registered(self):
        """Test that the duplicate-prevention index is part of the schema."""
        self.assertIn("ux_resourcelocks_held_app_resource", self.db.get_index_names())


class TestResourceLockTriggers(ResourceLockTestCase):
    """SQLite triggers keep the lock-dependent Resources columns in step."""

    def test_triggers_are_declared_by_the_schema(self):
        """Test that all three ledger triggers exist after initialization."""
        names = self.trigger_names()

        for expected in (
            "trg_resourcelocks_refresh_after_insert",
            "trg_resourcelocks_refresh_after_update",
            "trg_resourcelocks_refresh_after_delete",
        ):
            self.assertIn(expected, names)

    def test_schema_reapplication_does_not_duplicate_triggers(self):
        """Test that re-running the schema keeps exactly one trigger per event."""
        self.db.init_db()
        self.db.init_db()

        names = self.trigger_names()
        self.assertEqual(len(names), len(set(names)))

    def test_insert_lock_marks_the_resource_locked(self):
        """Test that inserting a HELD lock consumes a unit and names the holder."""
        self.db.acquire_resource_lock("app_a", "GPS")

        row = self.resource_row("GPS")
        self.assertEqual(row["available_units"], 0)
        self.assertEqual(row["held_by_app_id"], "app_a")
        self.assertEqual(row["status"], "LOCKED")

    def test_release_lock_restores_the_resource_to_free(self):
        """Test that retiring the only lock returns the resource to its full state."""
        lock_id = self.db.acquire_resource_lock("app_a", "GPS")
        self.db.release_resource_lock(lock_id)

        row = self.resource_row("GPS")
        self.assertEqual(row["available_units"], 1)
        self.assertIsNone(row["held_by_app_id"])
        self.assertEqual(row["status"], "FREE")

    def test_releasing_one_of_two_locks_leaves_the_resource_locked(self):
        """Test that the derived columns follow the remaining held locks."""
        self.db.acquire_resource_lock("app_a", "GPS")
        second = self.db.acquire_resource_lock("app_b", "GPS")

        self.db.release_resource_lock(second)

        row = self.resource_row("GPS")
        self.assertEqual(row["available_units"], 0)
        self.assertEqual(row["held_by_app_id"], "app_a")
        self.assertEqual(row["status"], "LOCKED")

    def test_delete_lock_row_refreshes_the_resource(self):
        """Test that deleting a ledger row, not just updating it, re-derives state."""
        lock_id = self.db.acquire_resource_lock("app_a", "GPS")

        self.db.get_connection().execute(
            "DELETE FROM ResourceLocks WHERE lock_id = ?", (lock_id,)
        )

        row = self.resource_row("GPS")
        self.assertEqual(row["available_units"], 1)
        self.assertIsNone(row["held_by_app_id"])
        self.assertEqual(row["status"], "FREE")

    def test_cascading_app_delete_refreshes_the_resource(self):
        """Test that locks removed by ON DELETE CASCADE also refresh Resources."""
        self.db.acquire_resource_lock("app_a", "GPS")

        self.db.delete_app("app_a")

        row = self.resource_row("GPS")
        self.assertEqual(row["available_units"], 1)
        self.assertIsNone(row["held_by_app_id"])
        self.assertEqual(row["status"], "FREE")

    def test_bulk_release_refreshes_the_resource(self):
        """Test that release_all_for_app re-derives the resource after each row."""
        self.db.acquire_resource_lock("app_a", "GPS")
        self.db.acquire_resource_lock("app_a", "Camera")

        self.db.resource_locks.release_all_for_app("app_a")

        for resource_id in ("GPS", "Camera"):
            with self.subTest(resource_id=resource_id):
                row = self.resource_row(resource_id)
                self.assertEqual(row["available_units"], 1)
                self.assertIsNone(row["held_by_app_id"])
                self.assertEqual(row["status"], "FREE")

    def test_waiting_queue_takes_precedence_over_lock_state(self):
        """Test that a non-empty waiting queue is reported as WAITING."""
        self.db.acquire_resource_lock("app_a", "GPS")
        self.db.save_resource(Resource("GPS", "GPS Sensor", waiting_queue=["app_b"]))
        self.db.acquire_resource_lock("app_b", "GPS")

        row = self.resource_row("GPS")
        self.assertEqual(row["status"], "WAITING")
        self.assertEqual(row["available_units"], 0)

    def test_multi_unit_resources_track_summed_held_units(self):
        """Test that a counting semaphore consumes one unit per held lock."""
        self.db.save_resource(Resource("SHARED", "Shared Pool", capacity=3))

        self.db.acquire_resource_lock("app_a", "SHARED", units=2)

        row = self.resource_row("SHARED")
        self.assertEqual(row["available_units"], 1)
        self.assertEqual(row["held_by_app_id"], "app_a")

    def test_earliest_held_lock_is_reported_as_the_holder(self):
        """Test that the holder is the earliest acquired live lock."""
        self.db.save_resource(Resource("SHARED", "Shared Pool", capacity=3))

        self.db.acquire_resource_lock("app_a", "SHARED", acquired_at=100.0)
        self.db.acquire_resource_lock("app_b", "SHARED", acquired_at=200.0)
        self.assertEqual(self.resource_row("SHARED")["held_by_app_id"], "app_a")

        first = self.db.get_active_lock("app_a", "SHARED")
        self.db.release_resource_lock(first["lock_id"])
        self.assertEqual(self.resource_row("SHARED")["held_by_app_id"], "app_b")

    def test_available_units_never_goes_negative(self):
        """Test that an over-subscribed ledger cannot drive the column negative.

        The trigger reports what the ledger contains; it does not judge whether
        the acquisition should have been granted in the first place.
        """
        self.db.acquire_resource_lock("app_a", "GPS")
        self.db.acquire_resource_lock("app_b", "GPS")

        row = self.resource_row("GPS")
        self.assertEqual(row["available_units"], 0)
        self.assertEqual(row["status"], "LOCKED")

    def test_trigger_records_rather_than_adjudicates_contended_acquisitions(self):
        """Test that SQLite never refuses an acquisition on contention grounds.

        Whether an app may take a resource is decided by the Python resource
        manager. Two apps holding the same resource is a state the database must
        be able to record, because in a counting semaphore it can be legitimate.
        """
        self.db.acquire_resource_lock("app_a", "GPS")
        self.db.acquire_resource_lock("app_b", "GPS")

        self.assertEqual(len(self.db.resource_locks.get_active_locks()), 2)
        self.assertEqual(self.resource_row("GPS")["held_by_app_id"], "app_a")

    def test_trigger_repairs_a_stale_resource_row(self):
        """Test that the ledger reasserts itself over a stale denormalized value."""
        self.db.acquire_resource_lock("app_a", "GPS")
        self.db.save_resource(Resource("GPS", "GPS Sensor"))

        # Re-recording the same acquisition re-derives the row from the ledger.
        self.db.release_lock_for("app_a", "GPS")
        self.db.acquire_resource_lock("app_a", "GPS")

        row = self.resource_row("GPS")
        self.assertEqual(row["available_units"], 0)
        self.assertEqual(row["held_by_app_id"], "app_a")
        self.assertEqual(row["status"], "LOCKED")

    def test_moving_a_lock_to_another_resource_refreshes_both_resources(self):
        """Test that reassigning a lock row updates the source and target resource."""
        first = self.db.acquire_resource_lock("app_a", "GPS")
        self.db.save_resource(Resource("Audio", "Audio Output"))

        self.db.get_connection().execute(
            "UPDATE ResourceLocks SET resource_id = 'Audio' WHERE lock_id = ?", (first,)
        )

        self.assertEqual(self.resource_row("GPS")["available_units"], 1)
        self.assertEqual(self.resource_row("GPS")["held_by_app_id"], None)
        self.assertEqual(self.resource_row("Audio")["available_units"], 0)
        self.assertEqual(self.resource_row("Audio")["held_by_app_id"], "app_a")


class TestSemaphoreLedgerSynchronization(ResourceLockTestCase):
    """ResourceManager acquisitions and releases reach the ledger."""

    def setUp(self):
        super().setUp()
        self.res_mgr = ResourceManager(db=self.db)
        self.res_mgr.create_default_resources()

    def test_granted_acquisition_is_recorded_in_the_ledger(self):
        """Test that a successful request produces a HELD lock row."""
        app = App(app_id="app_a", name="Alpha")
        self.res_mgr.register_app(app)

        self.assertTrue(self.res_mgr.acquire_resource(app, "GPS"))

        lock = self.db.get_active_lock("app_a", "GPS")
        self.assertIsNotNone(lock)
        self.assertEqual(lock["units"], 1)
        self.assertEqual(lock["status"], "HELD")

    def test_queued_request_is_not_recorded_as_a_lock(self):
        """Test that a blocked request leaves the ledger unchanged."""
        holder = App(app_id="app_a", name="Alpha")
        waiter = App(app_id="app_b", name="Beta")
        self.res_mgr.register_app(holder)
        self.res_mgr.register_app(waiter)
        self.res_mgr.acquire_resource(holder, "GPS")

        self.assertFalse(self.res_mgr.acquire_resource(waiter, "GPS"))

        self.assertEqual(
            [row["app_id"] for row in self.db.resource_locks.get_active_locks()],
            ["app_a"],
        )

    def test_release_retires_the_ledger_row_and_frees_the_resource(self):
        """Test that releasing through the manager keeps the ledger and Resources aligned."""
        app = App(app_id="app_a", name="Alpha")
        self.res_mgr.register_app(app)
        self.res_mgr.acquire_resource(app, "GPS")

        self.assertTrue(self.res_mgr.release_resource(app, "GPS"))

        self.assertIsNone(self.db.get_active_lock("app_a", "GPS"))
        resource = self.db.get_resource("GPS")
        self.assertEqual(resource.available_units, 1)
        self.assertIsNone(resource.primary_holder_id)
        self.assertEqual(resource.status, ResourceStatus.FREE)

    def test_repeat_acquisition_by_the_same_app_is_refused_by_python_not_sql(self):
        """Test that duplicate prevention starts in the resource manager."""
        app = App(app_id="app_a", name="Alpha")
        self.res_mgr.register_app(app)
        self.res_mgr.acquire_resource(app, "GPS")

        self.assertFalse(self.res_mgr.acquire_resource(app, "GPS"))

        self.assertEqual(len(self.db.resource_locks.get_locks_by_resource("GPS")), 1)

    def test_handoff_to_a_waiting_app_moves_the_single_ledger_row(self):
        """Test that queue hand-off retires one lock and records the next."""
        holder = App(app_id="app_a", name="Alpha")
        waiter = App(app_id="app_b", name="Beta")
        self.res_mgr.register_app(holder)
        self.res_mgr.register_app(waiter)

        self.res_mgr.acquire_resource(holder, "GPS")
        self.assertFalse(self.res_mgr.acquire_resource(waiter, "GPS"))

        self.res_mgr.release_resource(holder, "GPS")

        self.assertIsNone(self.db.get_active_lock("app_a", "GPS"))
        self.assertIsNotNone(self.db.get_active_lock("app_b", "GPS"))
        resource = self.db.get_resource("GPS")
        self.assertEqual(resource.primary_holder_id, "app_b")
        self.assertEqual(resource.waiting_queue, [])

    def test_release_all_for_app_clears_every_ledger_row(self):
        """Test that bulk release at the manager level matches the ledger."""
        app = App(app_id="app_a", name="Alpha")
        self.res_mgr.register_app(app)
        self.res_mgr.acquire_resource(app, "GPS")
        self.res_mgr.acquire_resource(app, "Camera")

        released = self.res_mgr.release_all_resources_for_app(app)

        self.assertEqual(sorted(released), ["Camera", "GPS"])
        self.assertEqual(self.db.resource_locks.get_active_locks(), [])
        self.assertEqual(self.db.get_resource("GPS").available_units, 1)

    def test_eviction_release_all_for_app_syncs_the_ledger(self):
        """Test that the eviction persistence path retires the app's locks."""
        app = App(app_id="app_a", name="Alpha")
        self.res_mgr.register_app(app)
        self.res_mgr.acquire_resource(app, "GPS")
        self.db.save_app(app)

        self.assertEqual(self.db.resource_locks.release_all_for_app("app_a"), 1)

        self.assertEqual(self.db.resource_locks.get_active_locks(), [])
        resource = self.db.get_resource("GPS")
        self.assertEqual(resource.primary_holder_id, None)
        self.assertEqual(resource.status, ResourceStatus.FREE)

    def test_lock_is_not_recorded_for_an_unpersisted_app(self):
        """Test that the ledger is skipped when the app has no Apps row to reference."""
        app = App(app_id="app_unregistered", name="Ghost")
        res_mgr = ResourceManager()
        res_mgr.create_default_resources()

        self.assertTrue(res_mgr.acquire_resource(app, "GPS"))

        self.assertEqual(self.db.resource_locks.get_active_locks(), [])


if __name__ == "__main__":
    unittest.main()