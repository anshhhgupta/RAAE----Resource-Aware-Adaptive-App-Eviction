"""Unit tests for transaction support and atomic eviction in RAAE."""

import sqlite3
import unittest

from backend.models.app import App, AppState
from backend.raae_engine.persistence import ConflictRecord, DatabaseEvictionPersistence
from backend.models.resource import Resource
from database.db import DatabaseManager
from database.transaction import TransactionError


class TransactionTestCase(unittest.TestCase):
    """Base test case providing a fresh in-memory database per test."""

    def setUp(self):
        self.db = DatabaseManager(db_path=":memory:")
        self.db.create_app(App(app_id="app_victim", name="Victim"))
        self.db.create_app(App(app_id="app_waiter", name="Waiter"))

    def tearDown(self):
        self.db.close()


class TestTransactionCommit(TransactionTestCase):
    """Test suite verifying that a successful transaction commits all writes."""

    def test_successful_transaction_commits_every_operation(self):
        """Test that all writes inside a clean transaction are persisted together."""
        lock_id = self.db.acquire_resource_lock("app_victim", "GPS")
        with self.db.transaction():
            self.db.release_resource_lock(lock_id)
            self.db.update_app(App(app_id="app_victim", name="Victim", state=AppState.EVICTED))
            self.db.insert_eviction_log("app_victim", "RAAE_CLOCK", "PRESSURE", True, True, "EVICTED")
            self.db.insert_memory_event("app_victim", "EVICT", 300, 0, "ORANGE")
            self.db.insert_conflict_log("app_waiter", "app_victim", "GPS")

        self.assertEqual(len(self.db.resource_locks.get_active_locks()), 0)
        self.assertEqual(self.db.get_app("app_victim").state, AppState.EVICTED)
        self.assertEqual(len(self.db.eviction_log.get_evictions_by_app("app_victim")), 1)
        self.assertEqual(len(self.db.memory_events.get_events_by_app("app_victim")), 1)
        self.assertEqual(len(self.db.conflict_log.get_conflicts_by_resource("GPS")), 1)

    def test_connection_usable_after_commit(self):
        """Test that normal writes resume once the transaction has closed."""
        with self.db.transaction():
            self.db.insert_memory_event("app_victim", "EVICT", 10, 0, "GREEN")

        self.db.insert_memory_event("app_waiter", "ALLOCATE", 0, 5, "GREEN")
        self.assertEqual(len(self.db.memory_events.get_all_events()), 2)


class TestTransactionRollback(TransactionTestCase):
    """Test suite verifying rollback when a transaction fails part way through.

    These are the critical cases: a failure in the middle of an eviction must
    undo every write made before it, so the database never records a
    partially applied eviction.
    """

    def test_failure_in_middle_rolls_back_all_prior_writes(self):
        """Test that a mid-transaction exception discards locks, state and all logs."""
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                # 1. Release resource locks held by the evicted app
                self.db.acquire_resource_lock("app_victim", "GPS")
                # 2. Update the app's active state
                self.db.update_app(App(app_id="app_victim", name="Victim", state=AppState.EVICTED))
                # 3. Insert the eviction log
                self.db.insert_eviction_log("app_victim", "RAAE_CLOCK", "PRESSURE", True, True, "EVICTED")
                # 4. Insert conflict info -- then fail before the group can commit
                self.db.insert_conflict_log("app_waiter", "app_victim", "GPS")
                raise RuntimeError("simulated failure after logging")

        # Nothing may survive: no eviction, no lock release, no state change, no logs.
        self.assertIsNone(self.db.get_app("app_victim").state == AppState.EVICTED or None)
        self.assertEqual(self.db.get_app("app_victim").state, AppState.BACKGROUND)
        self.assertEqual(len(self.db.resource_locks.get_locks_by_app("app_victim")), 0)
        self.assertEqual(len(self.db.eviction_log.get_evictions_by_app("app_victim")), 0)
        self.assertEqual(len(self.db.memory_events.get_all_events()), 0)
        self.assertEqual(len(self.db.conflict_log.get_conflicts_by_resource("GPS")), 0)

    def test_foreign_key_failure_rolls_back_transaction(self):
        """Test that a real IntegrityError mid-transaction rolls back every write."""
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction():
                self.db.update_app(App(app_id="app_victim", name="Victim", state=AppState.EVICTED))
                self.db.insert_eviction_log("app_victim", "RAAE_CLOCK", "PRESSURE", True, True, "EVICTED")
                # Violates the Apps foreign key and aborts the whole transaction.
                self.db.insert_eviction_log("app_missing", "RAAE_CLOCK", "PRESSURE", True, True, "EVICTED")

        self.assertEqual(self.db.get_app("app_victim").state, AppState.BACKGROUND)
        self.assertEqual(len(self.db.eviction_log.get_evictions_by_app("app_victim")), 0)

    def test_rollback_leaves_connection_usable(self):
        """Test that the connection still works normally after a rollback."""
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.db.insert_memory_event("app_victim", "EVICT", 10, 0, "GREEN")
                raise RuntimeError("boom")

        self.db.insert_memory_event("app_waiter", "ALLOCATE", 0, 5, "GREEN")
        self.assertEqual(len(self.db.memory_events.get_all_events()), 1)
        self.assertFalse(self.db.in_transaction)

    def test_transaction_can_be_retried_after_rollback(self):
        """Test that a failed transaction leaves no partial state blocking a retry."""
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                with self.db.transaction():
                    self.db.insert_eviction_log("app_victim", "RAAE_CLOCK", "PRESSURE", True, True, "EVICTED")
                    raise RuntimeError("fail again")

        with self.db.transaction():
            self.db.insert_eviction_log("app_victim", "RAAE_CLOCK", "PRESSURE", True, True, "EVICTED")

        self.assertEqual(len(self.db.eviction_log.get_evictions_by_app("app_victim")), 1)


class TestNestedTransactions(TransactionTestCase):
    """Test suite verifying savepoint-based nesting semantics."""

    def test_inner_failure_rolls_back_only_inner_writes(self):
        """Test that a caught inner failure discards inner work but keeps outer work."""
        with self.db.transaction():
            self.db.insert_memory_event("app_victim", "OUTER", 0, 1, "GREEN")

            with self.assertRaises(RuntimeError):
                with self.db.transaction():
                    self.db.insert_memory_event("app_victim", "INNER", 0, 2, "GREEN")
                    raise RuntimeError("inner failure")

            self.db.insert_memory_event("app_victim", "AFTER", 0, 3, "GREEN")

        actions = [row["action"] for row in self.db.memory_events.get_events_by_app("app_victim")]
        self.assertEqual(sorted(actions), ["AFTER", "OUTER"])
        self.assertNotIn("INNER", actions)

    def test_inner_success_commits_with_outer(self):
        """Test that a successful nested transaction commits with the outer block."""
        with self.db.transaction():
            self.db.insert_memory_event("app_victim", "OUTER", 0, 1, "GREEN")
            with self.db.transaction():
                self.db.insert_memory_event("app_victim", "INNER", 0, 2, "GREEN")

        self.assertEqual(len(self.db.memory_events.get_events_by_app("app_victim")), 2)

    def test_outer_failure_rolls_back_nested_committed_writes(self):
        """Test that a failure after a successful nested block undoes both."""
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                with self.db.transaction():
                    self.db.insert_memory_event("app_victim", "INNER", 0, 2, "GREEN")
                raise RuntimeError("outer failure after inner committed")

        self.assertEqual(len(self.db.memory_events.get_all_events()), 0)

    def test_three_level_nesting_rolls_back_completely_when_uncaught(self):
        """Test that an uncaught deep failure unwinds every enclosing level."""
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.db.insert_memory_event("app_victim", "L1", 0, 1, "GREEN")
                with self.db.transaction():
                    self.db.insert_memory_event("app_victim", "L2", 0, 2, "GREEN")
                    with self.db.transaction():
                        self.db.insert_memory_event("app_victim", "L3", 0, 3, "GREEN")
                        raise RuntimeError("innermost failure")

        self.assertEqual(len(self.db.memory_events.get_all_events()), 0)

    def test_three_level_nesting_keeps_outer_writes_when_caught(self):
        """Test that a caught deep failure discards only the inner two levels."""
        with self.db.transaction():
            self.db.insert_memory_event("app_victim", "L1", 0, 1, "GREEN")
            try:
                with self.db.transaction():
                    self.db.insert_memory_event("app_victim", "L2", 0, 2, "GREEN")
                    with self.db.transaction():
                        self.db.insert_memory_event("app_victim", "L3", 0, 3, "GREEN")
                        raise RuntimeError("innermost failure")
            except RuntimeError:
                pass

        actions = [row["action"] for row in self.db.memory_events.get_events_by_app("app_victim")]
        self.assertEqual(actions, ["L1"])


class TestTransactionGuards(TransactionTestCase):
    """Test suite verifying that callers cannot break transaction boundaries."""

    def test_commit_is_blocked_inside_transaction(self):
        """Test that a stray commit() cannot escape the transaction context."""
        with self.assertRaises(TransactionError):
            with self.db.transaction():
                self.db.get_connection().commit()

    def test_rollback_is_blocked_inside_transaction(self):
        """Test that a stray rollback() cannot escape the transaction context."""
        with self.assertRaises(TransactionError):
            with self.db.transaction():
                self.db.get_connection().rollback()

    def test_executescript_is_blocked_inside_transaction(self):
        """Test that executescript is refused because SQLite commits implicitly."""
        with self.assertRaises(TransactionError):
            with self.db.transaction():
                self.db.get_connection().executescript("SELECT 1;")

    def test_repository_helpers_do_not_commit_early(self):
        """Test that using a connection as a context manager cannot commit mid-transaction."""
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                with self.db.get_connection() as conn:
                    conn.execute(
                        "INSERT INTO MemoryEvents "
                        "(app_id, action, memory_before, memory_after, pressure_level, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        ("app_victim", "EVICT", 10, 0, "GREEN", 1.0),
                    )
                raise RuntimeError("must roll back despite the with-block exiting cleanly")

        self.assertEqual(len(self.db.memory_events.get_all_events()), 0)

    def test_init_db_is_blocked_inside_transaction(self):
        """Test that init_db cannot silently commit an in-flight transaction."""
        with self.assertRaises(TransactionError):
            with self.db.transaction():
                self.db.init_db()

    def test_in_transaction_flag_tracks_state(self):
        """Test that the in_transaction flag reflects the current nesting state."""
        self.assertFalse(self.db.in_transaction)
        with self.db.transaction():
            self.assertTrue(self.db.in_transaction)
            with self.db.transaction():
                self.assertTrue(self.db.in_transaction)
            self.assertTrue(self.db.in_transaction)
        self.assertFalse(self.db.in_transaction)


class TestAtomicEvictionPersistence(TransactionTestCase):
    """Test suite verifying the eviction persistence adapter is all-or-nothing."""

    def _event(self):
        return self._build_event()

    def _build_event(self):
        from backend.raae_engine.models import EvictionDecisionType, EvictionEvent

        return EvictionEvent(
            app_id="app_victim",
            algorithm="RAAE_CLOCK",
            reason="Memory pressure",
            decision_type=EvictionDecisionType.RELEASE_THEN_EVICT,
            memory_before=300,
            memory_after=0,
            released_resource_ids=("GPS",),
            lock_checked=True,
            safe_release=True,
            result="EVICTED",
        )

    def test_full_eviction_is_atomic_on_success(self):
        """Test that a complete eviction persists locks, state and every log."""
        self.db.acquire_resource_lock("app_victim", "GPS")
        candidate = App(app_id="app_victim", name="Victim", memory_footprint=300)
        candidate.evict()
        gps = self.db.get_resource("GPS")

        adapter = DatabaseEvictionPersistence(
            self.db,
            conflict_records=[
                ConflictRecord(
                    waiting_app_id="app_waiter",
                    blocking_app_id="app_victim",
                    resource_id="GPS",
                    resolution_strategy="RAAE_PROTECT",
                    resolved_by="RAAE_ENGINE",
                    details="protected",
                )
            ],
        )
        event_id = adapter.persist_eviction_event(
            event=self._build_event(),
            candidate=candidate,
            resources=[gps],
            registered_apps={"app_victim": candidate, "app_waiter": App(app_id="app_waiter", name="Waiter")},
        )

        self.assertGreater(event_id, 0)
        self.assertEqual(self.db.get_app("app_victim").state, AppState.EVICTED)
        self.assertEqual(len(self.db.eviction_log.get_evictions_by_app("app_victim")), 1)
        self.assertEqual(len(self.db.memory_events.get_events_by_app("app_victim")), 1)
        self.assertEqual(len(self.db.conflict_log.get_conflicts_by_resource("GPS")), 1)
        self.assertEqual(self.db.resource_locks.get_locks_by_app("app_victim")[0]["status"], "RELEASED")

    def test_eviction_rolls_back_completely_when_conflict_log_fails(self):
        """Test that a failing conflict-log insert discards the whole eviction.

        This is the core atomicity guarantee: the eviction log must not survive
        without its accompanying conflict record.
        """
        self.db.acquire_resource_lock("app_victim", "GPS")
        candidate = App(app_id="app_victim", name="Victim", memory_footprint=300)
        candidate.evict()
        gps = self.db.get_resource("GPS")

        # ConflictLog has a foreign key to Apps; this record references an
        # unknown app so the insert fails while the eviction is already written.
        adapter = DatabaseEvictionPersistence(
            self.db,
            conflict_records=[
                ConflictRecord(
                    waiting_app_id="app_does_not_exist",
                    blocking_app_id="app_victim",
                    resource_id="GPS",
                    resolution_strategy="RAAE_PROTECT",
                )
            ],
        )

        with self.assertRaises(sqlite3.IntegrityError):
            adapter.persist_eviction_event(
                event=self._build_event(),
                candidate=candidate,
                resources=[gps],
                registered_apps={"app_victim": candidate},
            )

        # The eviction, its memory event and the lock release must all be gone.
        self.assertEqual(len(self.db.eviction_log.get_evictions_by_app("app_victim")), 0)
        self.assertEqual(len(self.db.memory_events.get_events_by_app("app_victim")), 0)
        self.assertEqual(self.db.get_app("app_victim").state, AppState.BACKGROUND)
        self.assertEqual(self.db.resource_locks.get_locks_by_app("app_victim")[0]["status"], "HELD")
        self.assertEqual(len(self.db.conflict_log.get_conflicts_by_resource("GPS")), 0)


if __name__ == "__main__":
    unittest.main()