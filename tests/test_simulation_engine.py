"""Unit tests for the Simulation Engine and policy comparison (BASELINE_CLOCK vs RAAE)."""

import unittest

from backend.models.app import App, AppState
from backend.models.resource import Resource
from backend.memory_manager.memory_manager import MemoryManager
from backend.resource_manager.resource_manager import ResourceManager
from backend.simulation_engine import (
    BaselineClockPolicy,
    EvictionPolicyType,
    RAAEEvictionPolicy,
    SimulationConfig,
    SimulationEngine,
    run_deterministic_comparison,
    setup_deterministic_simulation,
)
from database.db import DatabaseManager


class TestSimulationEngine(unittest.TestCase):
    def setUp(self):
        self.total_memory = 1000
        self.mem_mgr = MemoryManager(total_memory=self.total_memory)
        self.res_mgr = ResourceManager()
        self.res_mgr.create_default_resources()
        self.db = DatabaseManager(":memory:")

    def tearDown(self):
        self.db.close()

    def test_tick_workflow_no_pressure(self):
        """Simulation tick when usage is low should detect no pressure and perform no evictions."""
        app = App(app_id="app_1", name="App 1", memory_footprint=100)
        self.mem_mgr.register_app(app)
        self.db.save_app(app)

        engine = SimulationEngine(
            memory_manager=self.mem_mgr,
            resource_manager=self.res_mgr,
            db_manager=self.db,
            policy=RAAEEvictionPolicy(),
            config=SimulationConfig(memory_pressure_threshold=75.0)
        )

        res = engine.tick()

        self.assertEqual(res.tick_number, 1)
        self.assertFalse(res.is_under_pressure)
        self.assertEqual(res.status, "NO_PRESSURE")
        self.assertEqual(len(res.eviction_results), 0)
        self.assertEqual(engine.metrics.successful_evictions, 0)
        self.assertEqual(engine.metrics.unsafe_evictions, 0)

    def test_baseline_clock_blindly_evicts_resource_holding_app(self):
        """Under BASELINE_CLOCK, an app holding a contested resource is killed blindly, causing a freeze."""
        holder = App(app_id="holder", name="Maps", memory_footprint=500, reference_bit=0)
        waiter = App(app_id="waiter", name="Fitness", memory_footprint=400, reference_bit=1)

        self.mem_mgr.register_app(holder)
        self.mem_mgr.register_app(waiter)
        self.db.save_app(holder)
        self.db.save_app(waiter)

        for r in self.res_mgr.get_all_resources():
            self.db.save_resource(r)

        # Holder acquires GPS
        self.res_mgr.acquire_resource(holder, "GPS")
        self.db.resource_locks.acquire_lock(holder.app_id, "GPS")

        # Waiter requests GPS and gets blocked
        self.res_mgr.acquire_resource(waiter, "GPS")

        engine = SimulationEngine(
            memory_manager=self.mem_mgr,
            resource_manager=self.res_mgr,
            db_manager=self.db,
            policy=BaselineClockPolicy(),
            config=SimulationConfig(memory_pressure_threshold=75.0)
        )

        # Used memory is 900 / 1000 = 90% (RED pressure)
        res = engine.tick()

        self.assertTrue(res.is_under_pressure)
        self.assertEqual(len(res.eviction_results), 1)

        eviction_result = res.eviction_results[0]
        self.assertEqual(eviction_result.candidate.app_id, "holder")
        self.assertTrue(eviction_result.evicted)
        self.assertTrue(eviction_result.is_unsafe)  # Killed while holding resource!
        self.assertFalse(eviction_result.safe_release_performed)
        self.assertTrue(holder.is_evicted)

        # Metrics verify unsafe eviction
        self.assertEqual(engine.metrics.unsafe_evictions, 1)
        self.assertEqual(engine.metrics.freeze_incidents, 1)
        self.assertEqual(engine.metrics.safe_releases_performed, 0)

        # Waiter was left blocked on GPS in resource manager
        gps = self.res_mgr.get_resource("GPS")
        self.assertIn("waiter", gps.waiting_queue)

        # Database logging check
        eviction_logs = self.db.eviction_log.get_evictions_by_app("holder")
        self.assertEqual(len(eviction_logs), 1)
        self.assertEqual(eviction_logs[0]["algorithm"], "BASELINE_CLOCK")
        self.assertEqual(eviction_logs[0]["lock_checked"], 0)
        self.assertEqual(eviction_logs[0]["safe_release"], 0)

        conflict_logs = self.db.conflict_log.get_all_conflicts()
        self.assertGreaterEqual(len(conflict_logs), 1)
        self.assertIn("FREEZE", conflict_logs[0]["details"])

    def test_raae_prevents_blind_eviction_of_resource_holding_app(self):
        """Under RAAE, an app holding a contested resource is NOT blindly killed.

        The engine protects it and advances Clock to evict a safe candidate.
        """
        holder = App(app_id="holder", name="Maps", memory_footprint=400, reference_bit=0)
        waiter = App(app_id="waiter", name="Fitness", memory_footprint=200, reference_bit=1)
        safe_app = App(app_id="safe_app", name="Chat", memory_footprint=300, reference_bit=0)

        self.mem_mgr.register_app(holder)
        self.mem_mgr.register_app(waiter)
        self.mem_mgr.register_app(safe_app)
        self.db.save_app(holder)
        self.db.save_app(waiter)
        self.db.save_app(safe_app)

        for r in self.res_mgr.get_all_resources():
            self.db.save_resource(r)

        # Holder acquires GPS
        self.res_mgr.acquire_resource(holder, "GPS")
        self.db.resource_locks.acquire_lock(holder.app_id, "GPS")

        # Waiter requests GPS and gets blocked
        self.res_mgr.acquire_resource(waiter, "GPS")

        # Aging: holder has been idle in background after acquiring lock
        holder.reference_bit = 0
        safe_app.reference_bit = 0

        engine = SimulationEngine(
            memory_manager=self.mem_mgr,
            resource_manager=self.res_mgr,
            db_manager=self.db,
            policy=RAAEEvictionPolicy(),
            config=SimulationConfig(memory_pressure_threshold=75.0)
        )

        # Initial memory is 900 / 1000 = 90% (RED pressure)
        res = engine.tick()

        # Holder MUST NOT be evicted!
        self.assertFalse(holder.is_evicted)
        self.assertEqual(holder.memory_footprint, 400)
        self.assertIn("GPS", holder.held_resources)

        # Safe app SHOULD be evicted to relieve memory pressure!
        self.assertTrue(safe_app.is_evicted)
        self.assertEqual(safe_app.memory_footprint, 0)

        # Telemetry metrics
        self.assertEqual(engine.metrics.unsafe_evictions, 0)
        self.assertEqual(engine.metrics.freeze_incidents, 0)
        self.assertEqual(engine.metrics.conflicts_prevented, 1)
        self.assertEqual(engine.metrics.safe_evictions, 1)
        self.assertEqual(engine.metrics.successful_evictions, 1)

        # Database checks: holder is logged as protected/conflict, safe_app as evicted
        holder_logs = self.db.eviction_log.get_evictions_by_app("holder")
        self.assertEqual(len(holder_logs), 1)
        self.assertEqual(holder_logs[0]["result"], "CONFLICT")
        self.assertEqual(holder_logs[0]["lock_checked"], 1)

        safe_logs = self.db.eviction_log.get_evictions_by_app("safe_app")
        self.assertEqual(len(safe_logs), 1)
        self.assertEqual(safe_logs[0]["result"], "EVICTED")

    def test_raae_safely_releases_uncontested_resource_before_eviction(self):
        """Under RAAE, an app holding an uncontested resource safely releases it before eviction."""
        app = App(app_id="music", name="Music Player", memory_footprint=800, reference_bit=0)
        self.mem_mgr.register_app(app)
        self.db.save_app(app)

        for r in self.res_mgr.get_all_resources():
            self.db.save_resource(r)

        self.res_mgr.acquire_resource(app, "AUDIO")
        self.db.resource_locks.acquire_lock(app.app_id, "AUDIO")

        engine = SimulationEngine(
            memory_manager=self.mem_mgr,
            resource_manager=self.res_mgr,
            db_manager=self.db,
            policy=RAAEEvictionPolicy(),
            config=SimulationConfig(memory_pressure_threshold=75.0)
        )

        res = engine.tick()

        self.assertEqual(len(res.eviction_results), 1)
        result = res.eviction_results[0]

        self.assertTrue(result.evicted)
        self.assertTrue(result.safe_release_performed)
        self.assertIn("AUDIO", result.released_resource_ids)
        self.assertFalse(result.is_unsafe)

        # Resource in resource_manager should now be free
        audio = self.res_mgr.get_resource("AUDIO")
        self.assertFalse(audio.is_locked)
        self.assertEqual(audio.available_units, 1)

        self.assertEqual(engine.metrics.safe_releases_performed, 1)
        self.assertEqual(engine.metrics.unsafe_evictions, 0)

    def test_deterministic_simulation_comparison(self):
        """Runs the deterministic 5-app, 2-resource comparison and validates that:

        1. At least 5 apps are created.
        2. At least 2 apps hold resources.
        3. BASELINE_CLOCK blindly kills an app holding a resource.
        4. RAAE protects the app holding a resource from blind eviction.
        5. Comparable metrics are produced.
        """
        results = run_deterministic_comparison(
            total_memory=1000,
            pressure_threshold=75.0,
            ticks_to_run=1
        )

        summary = results["summary"]
        baseline = results["baseline"]
        raae = results["raae"]

        # 1. Hypothesis verified
        self.assertTrue(summary["hypothesis_verified"])

        # 2. Maps app state comparison
        self.assertTrue(summary["app_holding_resource_blindly_killed_in_baseline"])
        self.assertTrue(summary["app_holding_resource_protected_in_raae"])
        self.assertTrue(baseline["maps_state"]["is_evicted"])
        self.assertFalse(raae["maps_state"]["is_evicted"])

        # 3. Unsafe evictions & freeze incidents
        self.assertEqual(baseline["metrics"]["unsafe_evictions"], 1)
        self.assertEqual(raae["metrics"]["unsafe_evictions"], 0)
        self.assertEqual(baseline["metrics"]["freeze_incidents"], 1)
        self.assertEqual(raae["metrics"]["freeze_incidents"], 0)

        # 4. Conflicts prevented & safe releases
        self.assertEqual(raae["metrics"]["conflicts_prevented"], 1)
        self.assertGreaterEqual(raae["metrics"]["safe_releases_performed"], 1)

        # 5. Comparable metrics returned
        for key in [
            "total_ticks", "memory_pressure_ticks", "total_eviction_attempts",
            "successful_evictions", "safe_evictions", "unsafe_evictions",
            "safe_releases_performed", "conflicts_detected", "conflicts_prevented",
            "freeze_incidents", "total_memory_freed", "peak_memory_used"
        ]:
            self.assertIn(key, baseline["metrics"])
            self.assertIn(key, raae["metrics"])


if __name__ == "__main__":
    unittest.main()
