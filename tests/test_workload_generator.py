"""Unit tests for WorkloadGenerator in RAAE."""

import unittest
from backend.models.app import App
from backend.memory_manager.memory_manager import MemoryManager, MemoryPressureLevel
from backend.resource_manager.resource_manager import ResourceManager
from backend.workload_generator.workload_generator import WorkloadGenerator, WorkloadEvent, WorkloadTrace


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

    def test_generate_workload_reproducibility(self):
        """Test that passing the same seed produces identical workload events and initial apps."""
        trace1 = self.generator.generate_workload(seed=123, ticks=15, scenario="normal", num_apps=4, ram_mb=1024)
        trace2 = self.generator.generate_workload(seed=123, ticks=15, scenario="normal", num_apps=4, ram_mb=1024)

        self.assertEqual(len(trace1.events), 15)
        self.assertEqual(len(trace2.events), 15)
        self.assertEqual(trace1.scenario, trace2.scenario)
        self.assertEqual(trace1.total_memory, trace2.total_memory)

        # Compare initial apps deterministically
        for app1, app2 in zip(trace1.initial_apps, trace2.initial_apps):
            self.assertEqual(app1.app_id, app2.app_id)
            self.assertEqual(app1.name, app2.name)
            self.assertEqual(app1.priority, app2.priority)
            self.assertEqual(app1.memory_footprint, app2.memory_footprint)
            self.assertEqual(app1.state, app2.state)
            self.assertEqual(app1.reference_bit, app2.reference_bit)

        # Compare events deterministically
        for e1, e2 in zip(trace1.events, trace2.events):
            self.assertEqual(e1.event_type, e2.event_type)
            self.assertEqual(e1.app_id, e2.app_id)
            self.assertEqual(e1.tick, e2.tick)
            self.assertEqual(e1.payload, e2.payload)


    def test_scenario_normal_workload(self):
        """Test generating normal workload scenario."""
        trace = self.generator.generate_workload(seed=42, ticks=20, scenario="normal", num_apps=5)
        self.assertEqual(trace.scenario, "normal")
        self.assertEqual(len(trace.initial_apps), 5)
        self.assertEqual(len(trace.events), 20)

        event_types = {e.event_type for e in trace.events}
        self.assertTrue(event_types.intersection({
            "ACCESS_APP", "ALLOCATE_MEMORY", "ACQUIRE_RESOURCE", "RELEASE_RESOURCE"
        }))

    def test_scenario_high_memory_pressure(self):
        """Test generating high memory pressure scenario."""
        trace = self.generator.generate_workload(seed=99, ticks=25, scenario="high_memory_pressure", num_apps=6)
        self.assertEqual(trace.scenario, "high_memory_pressure")
        self.assertEqual(len(trace.events), 25)

        event_types = [e.event_type for e in trace.events]
        self.assertIn("ALLOCATE_MEMORY", event_types)
        self.assertIn("MEMORY_PRESSURE", event_types)

    def test_scenario_resource_contention(self):
        """Test generating resource contention scenario."""
        trace = self.generator.generate_workload(seed=77, ticks=30, scenario="resource_contention", num_apps=5)
        self.assertEqual(trace.scenario, "resource_contention")
        self.assertEqual(len(trace.events), 30)

        acquire_events = [e for e in trace.events if e.event_type == "ACQUIRE_RESOURCE"]
        self.assertGreater(len(acquire_events), 5)

    def test_generate_random_events(self):
        """Test generating random workload events for active apps."""
        apps = self.generator.create_sample_apps(count=3)
        for app in apps:
            self.mem_mgr.register_app(app)

        event = self.generator.generate_random_event(self.mem_mgr, self.res_mgr)
        self.assertIsNotNone(event)
        self.assertIsInstance(event, WorkloadEvent)

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

