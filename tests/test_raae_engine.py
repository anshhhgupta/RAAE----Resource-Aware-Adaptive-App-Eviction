"""Unit tests for RAAE Engine orchestration."""

import unittest

from backend.memory_manager.memory_manager import MemoryManager
from backend.models.app import App
from backend.models.resource import Resource
from backend.raae_engine import (
    ConflictDecision,
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

    def get_held_resources(self, candidate):
        return list(self.held_resources)

    def evaluate_eviction_conflict(self, candidate, held_resources):
        self.calls += 1
        return self.decision


class TestRAAEEngine(unittest.TestCase):
    def setUp(self):
        self.resource_manager = ResourceManager()
        self.resource_manager.create_default_resources()
        self.conflict_manager = ResourceConflictManager(self.resource_manager)
        self.engine = RAAEEngine(self.conflict_manager)

    def test_candidate_without_resources_is_safe_to_evict(self):
        app = App(app_id="app_1", name="Notes", reference_bit=0)
        engine = RAAEEngine(NullConflictManager())

        result = engine.evaluate_eviction_candidate(app)

        self.assertEqual(result.decision_type, EvictionDecisionType.SAFE_TO_EVICT)
        self.assertTrue(result.can_evict)
        self.assertEqual(result.held_resources, ())

    def test_candidate_holding_resource_without_waiters_is_safe_to_evict(self):
        app = App(app_id="app_1", name="Maps", reference_bit=0)
        self.resource_manager.acquire_resource(app, "GPS")

        result = self.engine.evaluate_eviction_candidate(app)

        self.assertEqual(result.decision_type, EvictionDecisionType.SAFE_TO_EVICT)
        self.assertTrue(result.can_evict)
        self.assertEqual([resource.resource_id for resource in result.held_resources], ["GPS"])

    def test_candidate_holding_resource_with_waiters_requires_resolution(self):
        holder = App(app_id="holder", name="Maps", reference_bit=0)
        waiter = App(app_id="waiter", name="Fitness")

        self.resource_manager.acquire_resource(holder, "GPS")
        acquired = self.resource_manager.acquire_resource(waiter, "GPS")

        result = self.engine.evaluate_eviction_candidate(holder)

        self.assertFalse(acquired)
        self.assertEqual(result.decision_type, EvictionDecisionType.RESOLVE_REQUIRED)
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
        self.assertEqual(result.decision_type, EvictionDecisionType.RESOLVE_REQUIRED)
        self.assertEqual(logs[-1]["action"], "selected_for_eviction")

    def _create_camera_simulation(self, app_a_priority, app_b_priority, app_a_time, app_b_time):
        db = DatabaseManager(":memory:")
        self.addCleanup(db.close)

        memory_manager = MemoryManager(total_memory=1000)
        resource_manager = ResourceManager()
        camera = Resource("Camera", "Camera", capacity=1)
        resource_manager.register_resource(camera)

        app_a = App(app_id="app_a", name="App A", priority=app_a_priority)
        app_b = App(app_id="app_b", name="App B", priority=app_b_priority)
        memory_manager.register_app(app_a)
        memory_manager.register_app(app_b)

        self.assertTrue(resource_manager.acquire_resource(app_a, "Camera"))
        self.assertFalse(resource_manager.acquire_resource(app_b, "Camera"))

        app_a.last_access_time = app_a_time
        app_b.last_access_time = app_b_time
        db.save_app(app_a)
        db.save_app(app_b)
        db.save_resource(camera)

        conflict_manager = ResourceConflictManager(
            resource_manager,
            registered_apps=memory_manager.apps,
            db=db,
        )
        engine = RAAEEngine(conflict_manager)
        return db, memory_manager, resource_manager, app_a, app_b, engine

    def test_older_waiting_candidate_wounds_younger_camera_holder(self):
        db, _, resource_manager, app_a, app_b, engine = self._create_camera_simulation(
            app_a_priority=2,
            app_b_priority=2,
            app_a_time=200.0,
            app_b_time=100.0,
        )

        result = engine.evaluate_eviction_candidate(app_b)

        self.assertEqual(result.decision_type, EvictionDecisionType.SAFE_TO_EVICT)
        self.assertTrue(result.can_evict)
        self.assertNotIn("Camera", app_a.held_resources)
        self.assertNotIn("Camera", app_b.held_resources)
        self.assertNotIn("app_b", resource_manager.get_resource("Camera").waiting_queue)
        conflicts = db.conflict_log.get_conflicts_by_resource("Camera")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["waiting_app_id"], "app_b")
        self.assertEqual(conflicts[0]["blocking_app_id"], "app_a")
        self.assertEqual(conflicts[0]["resolved_by"], "WOUND")

    def test_younger_waiting_candidate_waits_for_older_camera_holder(self):
        _, _, resource_manager, app_a, app_b, engine = self._create_camera_simulation(
            app_a_priority=2,
            app_b_priority=2,
            app_a_time=100.0,
            app_b_time=200.0,
        )

        result = engine.evaluate_eviction_candidate(app_b)

        self.assertEqual(result.decision_type, EvictionDecisionType.WAIT)
        self.assertFalse(result.can_evict)
        self.assertIn("Camera", app_a.held_resources)
        self.assertIn("app_b", resource_manager.get_resource("Camera").waiting_queue)

    def test_equal_priority_and_timestamp_waits_deterministically(self):
        _, _, resource_manager, app_a, app_b, engine = self._create_camera_simulation(
            app_a_priority=3,
            app_b_priority=3,
            app_a_time=150.0,
            app_b_time=150.0,
        )

        first = engine.evaluate_eviction_candidate(app_b)
        second = engine.evaluate_eviction_candidate(app_b)

        self.assertEqual(first.decision_type, EvictionDecisionType.WAIT)
        self.assertEqual(second.decision_type, EvictionDecisionType.WAIT)
        self.assertEqual(first.conflict_decision.reason, second.conflict_decision.reason)
        self.assertIn("Camera", app_a.held_resources)
        self.assertIn("app_b", resource_manager.get_resource("Camera").waiting_queue)

    def test_wound_releases_all_holder_units_before_safe_result(self):
        db = DatabaseManager(":memory:")
        self.addCleanup(db.close)
        memory_manager = MemoryManager(total_memory=1000)
        resource_manager = ResourceManager()
        camera = Resource("Camera", "Camera", capacity=3)
        resource_manager.register_resource(camera)

        app_a = App(app_id="app_a", name="App A", priority=1)
        app_b = App(app_id="app_b", name="App B", priority=5)
        memory_manager.register_app(app_a)
        memory_manager.register_app(app_b)
        self.assertTrue(resource_manager.acquire_resource(app_a, "Camera", units=2))
        self.assertFalse(resource_manager.acquire_resource(app_b, "Camera", units=2))
        app_a.last_access_time = 200.0
        app_b.last_access_time = 100.0
        db.save_app(app_a)
        db.save_app(app_b)
        db.save_resource(camera)

        engine = RAAEEngine(ResourceConflictManager(
            resource_manager,
            registered_apps=memory_manager.apps,
            db=db,
        ))

        result = engine.evaluate_eviction_candidate(app_b)

        self.assertEqual(result.decision_type, EvictionDecisionType.SAFE_TO_EVICT)
        self.assertNotIn("app_a", camera.holders)
        self.assertNotIn("app_b", camera.holders)
        self.assertNotIn("app_b", camera.waiting_queue)
        self.assertEqual(camera.available_units, camera.capacity)
        self.assertEqual(db.conflict_log.get_conflicts_by_resource("Camera")[0]["resolved_by"], "WOUND")

    def test_conflict_resolution_runs_ten_clock_ticks(self):
        _, memory_manager, _, _, _, engine = self._create_camera_simulation(
            app_a_priority=1,
            app_b_priority=5,
            app_a_time=200.0,
            app_b_time=100.0,
        )

        results = []
        for _ in range(10):
            result, logs = memory_manager.evaluate_clock_candidate_with_raae(engine)
            self.assertIsNotNone(result)
            self.assertTrue(logs)
            results.append(result)

        self.assertEqual(len(results), 10)
        self.assertTrue(all(result.candidate.is_active for result in results))


if __name__ == "__main__":
    unittest.main()
