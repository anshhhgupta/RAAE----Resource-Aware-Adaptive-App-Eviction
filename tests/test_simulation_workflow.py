"""Unit tests for integrated simulation workflow between MemoryManager and ResourceManager."""

import unittest
from backend.models.app import App, AppState
from backend.memory_manager.memory_manager import MemoryManager, MemoryPressureLevel
from backend.resource_manager.resource_manager import ResourceManager
from backend.simulation.simulation_state import SimulationState, EvictionCandidateInfo


class TestSimulationWorkflow(unittest.TestCase):
    """Test suite for unified simulation state and deterministic eviction candidate selection."""

    def setUp(self):
        self.sim = SimulationState(
            memory_manager=MemoryManager(total_memory=1000),
            resource_manager=ResourceManager()
        )

    def test_deterministic_simulation_workflow(self):
        """Deterministic test scenario:
        - App A -> Camera (ref_bit = 1)
        - App B -> Microphone (ref_bit = 1)
        - App C -> no resources (ref_bit = 0)
        - Trigger memory pressure
        - Demonstrate candidate selection without immediate eviction
        - Expose held resources of candidate and active locks
        """
        # 1. Create simulated apps
        app_a = App(app_id="AppA", name="Navigation App", state=AppState.BACKGROUND, memory_footprint=200, reference_bit=1)
        app_b = App(app_id="AppB", name="Voice Recorder", state=AppState.BACKGROUND, memory_footprint=200, reference_bit=1)
        app_c = App(app_id="AppC", name="Calculator", state=AppState.BACKGROUND, memory_footprint=200, reference_bit=0)

        self.sim.add_app(app_a)
        self.sim.add_app(app_b)
        self.sim.add_app(app_c)

        # 2. Allocate memory & check initial used RAM
        self.assertEqual(self.sim.memory_manager.get_used_memory(), 600)
        self.assertEqual(self.sim.memory_manager.get_free_memory(), 400)

        # 3. Allow apps to access resources
        # App A -> Camera
        success_a = self.sim.request_resource("AppA", "Camera")
        self.assertTrue(success_a)
        self.assertEqual(self.sim.resource_manager.get_holder("Camera"), "AppA")

        # App B -> Microphone
        success_b = self.sim.request_resource("AppB", "Microphone")
        self.assertTrue(success_b)
        self.assertEqual(self.sim.resource_manager.get_holder("Microphone"), "AppB")

        # App C -> no resources
        self.assertEqual(app_c.held_resources, set())

        # 4. Generate memory pressure
        extra_alloc = self.sim.trigger_memory_pressure(target_percentage=85.0)
        self.assertGreater(extra_alloc, 0)
        self.assertTrue(self.sim.memory_manager.is_under_pressure())
        self.assertIn(
            self.sim.memory_manager.get_pressure_level(),
            (MemoryPressureLevel.ORANGE, MemoryPressureLevel.RED)
        )

        # 5. Update reference bits (App A=1, App B=1, App C=0)
        self.sim.update_reference_bit("AppA", 1)
        self.sim.update_reference_bit("AppB", 1)
        self.sim.update_reference_bit("AppC", 0)

        self.assertEqual(app_a.reference_bit, 1)
        self.assertEqual(app_b.reference_bit, 1)
        self.assertEqual(app_c.reference_bit, 0)

        # 6. Memory Manager selects a Clock candidate
        candidate = self.sim.select_eviction_candidate()

        # 7. Candidate is returned to caller (NOT evicted directly!)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.app_id, "AppC")
        self.assertTrue(candidate.is_active)
        self.assertFalse(candidate.is_evicted)
        self.assertNotEqual(candidate.state, AppState.EVICTED)

        # 8. Resource Manager exposes the resources held by that candidate
        candidate_resources = self.sim.get_candidate_resources(candidate)
        self.assertEqual(candidate_resources, [])

        # Expose active resources held across all apps
        active_locks = self.sim.resource_manager.get_all_active_locks()
        self.assertEqual(active_locks.get("Camera"), "AppA")
        self.assertEqual(active_locks.get("Microphone"), "AppB")

    def test_candidate_info_packaging_without_eviction(self):
        """Test packaging candidate App and held resources into EvictionCandidateInfo."""
        app_a = App(app_id="AppA", name="Camera App", state=AppState.BACKGROUND, memory_footprint=300, reference_bit=0)
        self.sim.add_app(app_a)
        self.sim.request_resource("AppA", "Camera")

        info = self.sim.get_candidate_info()
        self.assertIsNotNone(info)
        self.assertEqual(info.app.app_id, "AppA")
        self.assertEqual(info.held_resources, ["Camera"])
        # Ensure candidate was not evicted directly
        self.assertTrue(info.app.is_active)
        self.assertFalse(info.app.is_evicted)


if __name__ == "__main__":
    unittest.main()
