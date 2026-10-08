"""Unit tests for MemoryManager in RAAE."""

import time
import unittest
from backend.models.app import App, AppState
from backend.memory_manager.memory_manager import MemoryManager, MemoryPressureLevel


class TestMemoryManager(unittest.TestCase):
    """Test suite for MemoryManager functionality."""

    def setUp(self):
        self.mem_mgr = MemoryManager(total_memory=1000)

    def test_configurable_ram(self):
        """Test configuring total system RAM dynamically."""
        self.assertEqual(self.mem_mgr.total_memory, 1000)
        self.mem_mgr.set_total_memory(2048)
        self.assertEqual(self.mem_mgr.total_memory, 2048)

    def test_add_and_remove_apps(self):
        """Test adding and removing simulated apps."""
        app1 = App(app_id="1", name="App1", memory_footprint=200)
        self.mem_mgr.add_app(app1)
        self.assertIn("1", self.mem_mgr.apps)

        removed = self.mem_mgr.remove_app("1")
        self.assertEqual(removed, app1)
        self.assertNotIn("1", self.mem_mgr.apps)

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

    def test_update_reference_bit_and_timestamps(self):
        """Test updating app reference bits and access timestamps."""
        app = App(app_id="1", name="App1", reference_bit=0, last_access_time=100.0)
        self.mem_mgr.add_app(app)

        self.assertTrue(self.mem_mgr.update_reference_bit("1", 1))
        self.assertEqual(app.reference_bit, 1)

        t_custom = 200.0
        self.assertTrue(self.mem_mgr.update_last_access_time("1", t_custom))
        self.assertEqual(app.last_access_time, 200.0)

        self.assertTrue(self.mem_mgr.touch_app("1"))
        self.assertEqual(app.reference_bit, 1)
        self.assertGreater(app.last_access_time, 200.0)

    def test_eligible_background_apps(self):
        """Test identifying active background apps eligible for eviction."""
        app1 = App(app_id="1", name="App1", state=AppState.FOREGROUND)
        app2 = App(app_id="2", name="App2", state=AppState.BACKGROUND)
        app3 = App(app_id="3", name="App3", state=AppState.WAITING)
        app4 = App(app_id="4", name="App4", state=AppState.EVICTED)

        for app in (app1, app2, app3, app4):
            self.mem_mgr.add_app(app)

        eligible = self.mem_mgr.get_eligible_background_apps()
        eligible_ids = [a.app_id for a in eligible]

        self.assertNotIn("1", eligible_ids)  # FOREGROUND
        self.assertIn("2", eligible_ids)     # BACKGROUND
        self.assertIn("3", eligible_ids)     # WAITING
        self.assertNotIn("4", eligible_ids)  # EVICTED

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

    def test_select_eviction_candidate_second_chance(self):
        """Test select_eviction_candidate with circular pointer and second-chance ref bit reset."""
        appA = App(app_id="A", name="App A", reference_bit=1, state=AppState.BACKGROUND)
        appB = App(app_id="B", name="App B", reference_bit=1, state=AppState.BACKGROUND)
        appC = App(app_id="C", name="App C", reference_bit=0, state=AppState.BACKGROUND)

        self.mem_mgr.add_app(appA)
        self.mem_mgr.add_app(appB)
        self.mem_mgr.add_app(appC)

        # First call: appA(1 -> 0), appB(1 -> 0), appC(0 -> candidate!)
        candidate = self.mem_mgr.select_eviction_candidate()
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.app_id, "C")
        self.assertEqual(appA.reference_bit, 0)
        self.assertEqual(appB.reference_bit, 0)
        self.assertTrue(candidate.is_active)  # Candidate is returned, NOT evicted immediately

        # Second call: starting at clock_hand=0 -> appA(0 -> candidate!)
        candidate2 = self.mem_mgr.select_eviction_candidate()
        self.assertIsNotNone(candidate2)
        self.assertEqual(candidate2.app_id, "A")

    def test_clock_skips_foreground_and_evicted_apps(self):
        """Test that Clock algorithm ignores foreground and evicted apps."""
        app_fg = App(app_id="FG", name="Foreground App", reference_bit=0, state=AppState.FOREGROUND)
        app_ev = App(app_id="EV", name="Evicted App", reference_bit=0, state=AppState.EVICTED)
        app_bg = App(app_id="BG", name="Background App", reference_bit=0, state=AppState.BACKGROUND)

        self.mem_mgr.add_app(app_fg)
        self.mem_mgr.add_app(app_ev)
        self.mem_mgr.add_app(app_bg)

        candidate = self.mem_mgr.select_eviction_candidate()
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.app_id, "BG")

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

