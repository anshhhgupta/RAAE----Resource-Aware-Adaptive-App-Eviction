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


if __name__ == "__main__":
    unittest.main()
