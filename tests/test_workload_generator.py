"""Unit tests for WorkloadGenerator in RAAE."""

import unittest
from backend.models.app import App
from backend.memory_manager.memory_manager import MemoryManager, MemoryPressureLevel
from backend.resource_manager.resource_manager import ResourceManager
from backend.workload_generator.workload_generator import WorkloadGenerator, WorkloadEvent


class TestWorkloadGenerator(unittest.TestCase):
    """Test suite for WorkloadGenerator."""

    def setUp(self):
        self.generator = WorkloadGenerator(seed=42)
        self.mem_mgr = MemoryManager(total_memory=1000)
        self.res_mgr = ResourceManager()
        self.res_mgr.create_default_resources()

    def test_create_sample_apps(self):
        """Test creating sample simulated applications."""
        apps = self.generator.create_sample_apps(count=4)
        self.assertEqual(len(apps), 4)

        for app in apps:
            self.assertIsInstance(app, App)
            self.assertTrue(app.name)
            self.assertGreater(app.memory_footprint, 0)
            self.assertTrue(app.is_active)

    def test_generate_random_events(self):
        """Test generating workload events for active apps."""
        apps = self.generator.create_sample_apps(count=3)
        for app in apps:
            self.mem_mgr.register_app(app)

        event = self.generator.generate_random_event(self.mem_mgr, self.res_mgr)
        self.assertIsNotNone(event)
        self.assertIsInstance(event, WorkloadEvent)
        self.assertIn(event.event_type, [
            "STATE_CHANGE", "ALLOCATE_MEMORY", "DEALLOCATE_MEMORY",
            "ACQUIRE_RESOURCE", "RELEASE_RESOURCE"
        ])

    def test_trigger_memory_pressure(self):
        """Test driving system memory pressure up to target level."""
        apps = self.generator.create_sample_apps(count=3)
        for app in apps:
            self.mem_mgr.register_app(app)

        self.assertEqual(self.mem_mgr.get_pressure_level(), MemoryPressureLevel.GREEN)

        allocated = self.generator.trigger_memory_pressure(self.mem_mgr, target_percentage=80.0)
        self.assertGreater(allocated, 0)
        self.assertIn(self.mem_mgr.get_pressure_level(), [MemoryPressureLevel.ORANGE, MemoryPressureLevel.RED])


if __name__ == "__main__":
    unittest.main()
