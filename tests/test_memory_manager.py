"""Unit tests for MemoryManager in RAAE."""

import unittest
from backend.models.app import App, AppState
from backend.memory_manager.memory_manager import MemoryManager, MemoryPressureLevel


class TestMemoryManager(unittest.TestCase):
    """Test suite for MemoryManager functionality."""

    def setUp(self):
        self.mem_mgr = MemoryManager(total_memory=1000)

    def test_memory_registration_and_usage(self):
        """Test registering apps and calculating memory usage and free space."""
        app1 = App(app_id="1", name="App1", memory_footprint=200)
        app2 = App(app_id="2", name="App2", memory_footprint=300)

        self.mem_mgr.register_app(app1)
        self.mem_mgr.register_app(app2)

        self.assertEqual(self.mem_mgr.get_used_memory(), 500)
        self.assertEqual(self.mem_mgr.get_free_memory(), 500)
        self.assertEqual(self.mem_mgr.get_usage_percentage(), 50.0)

    def test_memory_pressure_levels(self):
        """Test memory pressure level threshold calculation."""
        app = App(app_id="1", name="App1", memory_footprint=100)
        self.mem_mgr.register_app(app)

        # 100 / 1000 = 10% -> GREEN
        self.assertEqual(self.mem_mgr.get_pressure_level(), MemoryPressureLevel.GREEN)
        self.assertFalse(self.mem_mgr.is_under_pressure())

        # 650 / 1000 = 65% -> YELLOW
        app.memory_footprint = 650
        self.assertEqual(self.mem_mgr.get_pressure_level(), MemoryPressureLevel.YELLOW)

        # 800 / 1000 = 80% -> ORANGE
        app.memory_footprint = 800
        self.assertEqual(self.mem_mgr.get_pressure_level(), MemoryPressureLevel.ORANGE)
        self.assertTrue(self.mem_mgr.is_under_pressure())

        # 950 / 1000 = 95% -> RED
        app.memory_footprint = 950
        self.assertEqual(self.mem_mgr.get_pressure_level(), MemoryPressureLevel.RED)
        self.assertTrue(self.mem_mgr.is_under_pressure())

    def test_memory_allocation_and_deallocation(self):
        """Test allocating and deallocating memory for apps."""
        app = App(app_id="1", name="App1", memory_footprint=100)
        self.mem_mgr.register_app(app)

        # Allocate 200 MB
        success = self.mem_mgr.allocate_memory("1", 200)
        self.assertTrue(success)
        self.assertEqual(app.memory_footprint, 300)

        # Attempt to over-allocate beyond total capacity
        success = self.mem_mgr.allocate_memory("1", 1000)
        self.assertFalse(success)
        self.assertEqual(app.memory_footprint, 300)

        # Partial deallocation
        freed = self.mem_mgr.deallocate_memory("1", 100)
        self.assertEqual(freed, 100)
        self.assertEqual(app.memory_footprint, 200)

        # Full deallocation
        freed = self.mem_mgr.deallocate_memory("1")
        self.assertEqual(freed, 200)
        self.assertEqual(app.memory_footprint, 0)

    def test_clock_algorithm_step(self):
        """Test baseline Clock algorithm candidate selection."""
        appA = App(app_id="A", name="App A", reference_bit=1)
        appB = App(app_id="B", name="App B", reference_bit=0)
        appC = App(app_id="C", name="App C", reference_bit=1)

        self.mem_mgr.register_app(appA)
        self.mem_mgr.register_app(appB)
        self.mem_mgr.register_app(appC)

        # Clock hand starts at 0 (appA has ref_bit=1 -> reset to 0, advance to appB which has ref_bit=0)
        candidate, logs = self.mem_mgr.clock_step()
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.app_id, "B")
        self.assertEqual(appA.reference_bit, 0)

    def test_evict_app(self):
        """Test evicting an application through MemoryManager."""
        app = App(app_id="1", name="App1", memory_footprint=300)
        self.mem_mgr.register_app(app)

        self.assertTrue(self.mem_mgr.evict_app("1"))
        self.assertTrue(app.is_evicted)
        self.assertEqual(app.memory_footprint, 0)
        self.assertEqual(self.mem_mgr.get_used_memory(), 0)


if __name__ == "__main__":
    unittest.main()
