"""Unit tests for RAAE Engine orchestration."""

import unittest

from backend.memory_manager.memory_manager import MemoryManager
from backend.models.app import App, AppState
from backend.models.resource import Resource
from backend.raae_engine import (
    ConflictDecision,
    DatabaseEvictionPersistence,
    EvictionDecisionType,
    RAAEEngine,
    ResourceConflictManager,
)
from backend.resource_manager.resource_manager import ResourceManager
from database.db import DatabaseManager


class NullConflictManager:
    def get_held_resources(self, candidate):
        return []

    def evaluate_eviction_conflict(self, candidate, held_resources):
        raise AssertionError("Conflict decision should not be requested without held resources.")


class FixedConflictManager:
    def __init__(self, decision, held_resources=None):
        self.decision = decision
        self.held_resources = held_resources or []
        self.calls = 0
        self.last_candidate = None
        self.last_held_resources = None

    def get_held_resources(self, candidate):
        return list(self.held_resources)

    def evaluate_eviction_conflict(self, candidate, held_resources):
        self.calls += 1
        self.last_candidate = candidate
        self.last_held_resources = tuple(held_resources)
        return self.decision


class TestRAAEEngine(unittest.TestCase):
    def setUp(self):
        self.memory_manager = MemoryManager(total_memory=1000)
        self.resource_manager = ResourceManager()
        self.resource_manager.create_default_resources()
        self.conflict_manager = ResourceConflictManager(self.resource_manager)
        self.engine = RAAEEngine(
            self.conflict_manager,
            memory_manager=self.memory_manager,
            resource_manager=self.resource_manager
        )

    def test_candidate_without_resources_is_safe_to_evict(self):
        app = App(app_id="app_1", name="Notes", reference_bit=0)
        engine = RAAEEngine(NullConflictManager())

        result = engine.evaluate_eviction_candidate(app)

        self.assertEqual(result.decision_type, EvictionDecisionType.ALLOW_EVICTION)
        self.assertTrue(result.can_evict)
        self.assertEqual(result.held_resources, ())

    def test_candidate_holding_resource_without_waiters_requires_release_then_evict(self):
        app = App(app_id="app_1", name="Maps", reference_bit=0)
        self.resource_manager.acquire_resource(app, "GPS")

        result = self.engine.evaluate_eviction_candidate(app)

        self.assertEqual(result.decision_type, EvictionDecisionType.RELEASE_THEN_EVICT)
        self.assertFalse(result.can_evict)
        self.assertTrue(result.eviction_decision.requires_resource_release)
        self.assertEqual([resource.resource_id for resource in result.held_resources], ["GPS"])

    def test_held_resource_candidate_is_sent_to_conflict_manager(self):
        app = App(app_id="app_1", name="Maps", reference_bit=0)
        gps = Resource("GPS", "GPS Location Sensor")
        manager = FixedConflictManager(
            ConflictDecision.no_conflict("No waiting app is blocked."),
            held_resources=[gps]
        )
        engine = RAAEEngine(manager)

        result = engine.evaluate_eviction_candidate(app)

        self.assertEqual(result.decision_type, EvictionDecisionType.RELEASE_THEN_EVICT)
        self.assertEqual(manager.calls, 1)
        self.assertIs(manager.last_candidate, app)
        self.assertEqual(manager.last_held_resources, (gps,))

    def test_candidate_holding_resource_with_waiters_requires_resolution(self):
        holder = App(app_id="holder", name="Maps", reference_bit=0)
        waiter = App(app_id="waiter", name="Fitness")

        self.resource_manager.acquire_resource(holder, "GPS")
        acquired = self.resource_manager.acquire_resource(waiter, "GPS")

        result = self.engine.evaluate_eviction_candidate(holder)

        self.assertFalse(acquired)
        self.assertEqual(result.decision_type, EvictionDecisionType.CONFLICT)
        self.assertFalse(result.can_evict)
        self.assertEqual(result.conflict_decision.resource_ids, ("GPS",))
        self.assertEqual(result.conflict_decision.waiting_app_ids, ("waiter",))

    def test_conflict_status_maps_to_wait_decision(self):
        app = App(app_id="app_1", name="Maps", reference_bit=0)
        manager = FixedConflictManager(
            ConflictDecision.wait("Retry after active release."),
            held_resources=[Resource("GPS", "GPS Location Sensor")]
        )
        engine = RAAEEngine(manager)

        result = engine.evaluate_eviction_candidate(app)

        self.assertEqual(result.decision_type, EvictionDecisionType.WAIT)
        self.assertFalse(result.can_evict)
        self.assertEqual(manager.calls, 1)

    def test_memory_manager_passes_clock_candidate_to_raae_engine(self):
        memory_manager = MemoryManager(total_memory=1000)
        holder = App(app_id="holder", name="Maps", reference_bit=0)
        waiter = App(app_id="waiter", name="Fitness")

        memory_manager.register_app(holder)
        memory_manager.register_app(waiter)
        self.resource_manager.acquire_resource(holder, "GPS")
        self.resource_manager.acquire_resource(waiter, "GPS")

        result, logs = memory_manager.evaluate_clock_candidate_with_raae(self.engine)

        self.assertIsNotNone(result)
        self.assertEqual(result.candidate.app_id, "holder")
        self.assertEqual(result.decision_type, EvictionDecisionType.CONFLICT)
        self.assertEqual(logs[-1]["action"], "selected_for_eviction")

    def test_safe_eviction_candidate_with_no_resources(self):
        app = App(app_id="app_no_res", name="Notes", memory_footprint=200, reference_bit=0)
        self.memory_manager.register_app(app)

        result = self.engine.execute_safe_eviction(app)

        self.assertEqual(result.decision_type, EvictionDecisionType.ALLOW_EVICTION)
        self.assertTrue(result.eviction_performed)
        self.assertTrue(app.is_evicted)
        self.assertEqual(app.memory_footprint, 0)
        self.assertEqual(result.memory_released, 200)
        self.assertEqual(result.released_resource_ids, ())
        self.assertEqual(result.eviction_event.result, "EVICTED")

    def test_safe_eviction_candidate_holding_one_resource(self):
        app = App(app_id="app_one_res", name="Maps", memory_footprint=250, reference_bit=0)
        self.memory_manager.register_app(app)
        self.resource_manager.acquire_resource(app, "GPS")

        result = self.engine.execute_safe_eviction(app)

        self.assertEqual(result.decision_type, EvictionDecisionType.RELEASE_THEN_EVICT)
        self.assertTrue(result.eviction_performed)
        self.assertTrue(app.is_evicted)
        self.assertEqual(app.held_resources, set())
        self.assertEqual(result.released_resource_ids, ("GPS",))
        self.assertEqual(result.memory_released, 250)

    def test_safe_eviction_candidate_holding_multiple_resources(self):
        app = App(app_id="app_multi_res", name="Camera", memory_footprint=350, reference_bit=0)
        self.memory_manager.register_app(app)
        self.resource_manager.acquire_resource(app, "GPS")
        self.resource_manager.acquire_resource(app, "MIC")

        result = self.engine.execute_safe_eviction(app)

        self.assertEqual(result.decision_type, EvictionDecisionType.RELEASE_THEN_EVICT)
        self.assertTrue(result.eviction_performed)
        self.assertTrue(app.is_evicted)
        self.assertEqual(app.held_resources, set())
        self.assertEqual(set(result.released_resource_ids), {"GPS", "MIC"})
        self.assertEqual(result.memory_released, 350)

    def test_safe_eviction_waiting_candidate_is_not_evicted(self):
        app = App(app_id="app_waiting", name="Maps", memory_footprint=150, reference_bit=0)
        app.acquire_resource("GPS")
        self.memory_manager.register_app(app)
        manager = FixedConflictManager(
            ConflictDecision.wait("Conflict Manager requested wait."),
            held_resources=[Resource("GPS", "GPS Location Sensor")]
        )
        engine = RAAEEngine(
            manager,
            memory_manager=self.memory_manager,
            resource_manager=self.resource_manager
        )

        result = engine.execute_safe_eviction(app)

        self.assertEqual(result.decision_type, EvictionDecisionType.WAIT)
        self.assertFalse(result.eviction_performed)
        self.assertFalse(app.is_evicted)
        self.assertEqual(app.memory_footprint, 150)
        self.assertEqual(app.held_resources, {"GPS"})
        self.assertEqual(result.eviction_event.result, "WAIT")

    def test_waiting_candidate_persistence_does_not_release_locks(self):
        db = DatabaseManager(db_path=":memory:")
        try:
            app = App(app_id="app_waiting_persist", name="Maps", memory_footprint=150, reference_bit=0)
            app.acquire_resource("GPS")
            self.memory_manager.register_app(app)
            db.save_app(app)
            for resource in self.resource_manager.get_all_resources():
                db.save_resource(resource)
            db.resource_locks.acquire_lock(app.app_id, "GPS", units=1)

            manager = FixedConflictManager(
                ConflictDecision.wait("Conflict Manager requested wait."),
                held_resources=[Resource("GPS", "GPS Location Sensor")]
            )
            engine = RAAEEngine(
                manager,
                memory_manager=self.memory_manager,
                resource_manager=self.resource_manager,
                persistence=DatabaseEvictionPersistence(db)
            )

            result = engine.execute_safe_eviction(app)
            active_locks = db.resource_locks.get_active_locks()
            eviction_logs = db.eviction_log.get_evictions_by_app(app.app_id)

            self.assertEqual(result.decision_type, EvictionDecisionType.WAIT)
            self.assertFalse(result.eviction_performed)
            self.assertTrue(any(lock["app_id"] == app.app_id for lock in active_locks))
            self.assertEqual(eviction_logs[0]["result"], "WAIT")
        finally:
            db.close()

    def test_successfully_evicted_candidate_is_persisted_atomically(self):
        db = DatabaseManager(db_path=":memory:")
        try:
            app = App(app_id="app_persist", name="Maps", memory_footprint=300, reference_bit=0)
            self.memory_manager.register_app(app)
            db.save_app(app)
            for resource in self.resource_manager.get_all_resources():
                db.save_resource(resource)

            self.resource_manager.acquire_resource(app, "GPS")
            db.save_app(app)
            db.resource_locks.acquire_lock(app.app_id, "GPS", units=1)

            engine = RAAEEngine(
                self.conflict_manager,
                memory_manager=self.memory_manager,
                resource_manager=self.resource_manager,
                persistence=DatabaseEvictionPersistence(db)
            )

            result = engine.execute_safe_eviction(app)

            persisted_app = db.get_app(app.app_id)
            eviction_logs = db.eviction_log.get_evictions_by_app(app.app_id)
            memory_events = db.memory_events.get_events_by_app(app.app_id)
            active_locks = db.resource_locks.get_active_locks()

            self.assertTrue(result.eviction_performed)
            self.assertGreater(result.persisted_event_id, 0)
            self.assertEqual(persisted_app.state, AppState.EVICTED)
            self.assertEqual(persisted_app.memory_footprint, 0)
            self.assertEqual(persisted_app.held_resources, set())
            self.assertEqual(eviction_logs[0]["result"], "EVICTED")
            self.assertEqual(eviction_logs[0]["safe_release"], 1)
            self.assertEqual(memory_events[0]["action"], "EVICT")
            self.assertFalse(any(lock["app_id"] == app.app_id for lock in active_locks))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
