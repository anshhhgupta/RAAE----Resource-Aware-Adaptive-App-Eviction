"""Integration tests for SQLite persistence layer, 20+ tick simulations, and state consistency."""

import unittest
from backend.models.app import App, AppState
from backend.memory_manager.memory_manager import MemoryManager
from backend.resource_manager.resource_manager import ResourceManager
from backend.simulation.simulation_state import SimulationState
from backend.simulation.controller import SimulationController
from backend.workload_generator.workload_generator import WorkloadGenerator
from database.db import DatabaseManager


class TestPersistenceIntegration(unittest.TestCase):
    """Test suite for verifying persistence hooks and in-memory vs SQLite state consistency."""

    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.sim_state = SimulationState(db=self.db)
        self.controller = SimulationController(sim_state=self.sim_state, db=self.db)
        self.workload_gen = WorkloadGenerator(seed=100)

    def tearDown(self):
        self.db.close()

    def test_persistence_hooks_individual(self):
        """Test clean persistence hooks for all required events."""
        # 1. App Created
        app = App(app_id="app_test1", name="Test App", state=AppState.BACKGROUND, memory_footprint=100)
        self.sim_state.add_app(app)
        db_app = self.db.get_app("app_test1")
        self.assertIsNotNone(db_app)
        self.assertEqual(db_app.name, "Test App")

        # 2. Memory Allocated
        self.sim_state.allocate_memory("app_test1", 50)
        db_app = self.db.get_app("app_test1")
        self.assertEqual(db_app.memory_footprint, 150)

        # 3. App Access / Reference Update
        self.sim_state.update_reference_bit("app_test1", 0)
        db_app = self.db.get_app("app_test1")
        self.assertEqual(db_app.reference_bit, 0)

        # 4. Resource Acquired
        success_acq = self.sim_state.request_resource("app_test1", "Camera")
        self.assertTrue(success_acq)
        db_res = self.db.get_resource("Camera")
        self.assertEqual(db_res.primary_holder_id, "app_test1")

        # 5. Resource Request Queued
        app2 = App(app_id="app_test2", name="Requester App", state=AppState.BACKGROUND)
        self.sim_state.add_app(app2)
        success_queue = self.sim_state.request_resource("app_test2", "Camera")
        self.assertFalse(success_queue)
        db_res = self.db.get_resource("Camera")
        self.assertIn("app_test2", db_res.waiting_queue)

        # 6. Resource Released
        released = self.sim_state.release_resource("app_test1", "Camera")
        self.assertTrue(released)
        db_res = self.db.get_resource("Camera")
        self.assertEqual(db_res.primary_holder_id, "app_test2")

        # 7. Memory Pressure Detected
        self.sim_state.trigger_memory_pressure(target_percentage=90.0)
        logs = self.db.get_all_memory_logs()
        self.assertGreater(len(logs), 0)

        # Verify in-memory state matches DB state
        self.assertTrue(self.controller.verify_state_consistency())

    def test_e2e_simulation_normal_workload_25_ticks(self):
        """Run end-to-end simulation for 25 ticks (normal workload) and verify state consistency."""
        trace = self.workload_gen.generate_workload(seed=42, ticks=25, scenario="normal", num_apps=5)
        summary = self.controller.run_workload(trace)

        self.assertEqual(summary["processed_events"], 25)
        self.assertEqual(summary["total_ticks"], 25)

        # Verify 100% consistency between memory state and SQLite database state
        self.assertTrue(self.controller.verify_state_consistency())

    def test_e2e_simulation_high_memory_pressure_25_ticks(self):
        """Run end-to-end simulation for 25 ticks (high memory pressure) and verify state consistency."""
        trace = self.workload_gen.generate_workload(seed=88, ticks=25, scenario="high_memory_pressure", num_apps=5)
        summary = self.controller.run_workload(trace)

        self.assertEqual(summary["processed_events"], 25)
        self.assertTrue(self.controller.verify_state_consistency())

    def test_e2e_simulation_resource_contention_30_ticks(self):
        """Run end-to-end simulation for 30 ticks (resource contention) and verify state consistency."""
        trace = self.workload_gen.generate_workload(seed=99, ticks=30, scenario="resource_contention", num_apps=6)
        summary = self.controller.run_workload(trace)

        self.assertEqual(summary["processed_events"], 30)
        self.assertTrue(self.controller.verify_state_consistency())


if __name__ == "__main__":
    unittest.main()
