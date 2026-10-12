"""Tests for the Streamlit-facing simulation state queries."""

import unittest

from backend.simulation_controller import SimulationController


class TestSimulationControllerDashboardQueries(unittest.TestCase):
    def setUp(self):
        self.controller = SimulationController()

    def tearDown(self):
        self.controller.db.close()

    def test_system_metrics_use_current_state_and_exact_database_counts(self):
        evicted_app = self.controller.add_app("Evicted", memory_footprint=200)
        active_app = self.controller.add_app("Active", memory_footprint=125)
        self.controller.mem_mgr.evict_app(evicted_app.app_id)

        self.controller.db.eviction_log.log_eviction(
            app_id=evicted_app.app_id,
            algorithm="CLOCK+RAAE",
            reason="test",
            lock_checked=False,
            safe_release=True,
            result="EVICTED",
        )
        self.controller.db.conflict_log.log_conflict(
            waiting_app_id=active_app.app_id,
            blocking_app_id=evicted_app.app_id,
            resource_id="GPS",
        )

        event_count_before = len(self.controller.get_recent_events())
        metrics = self.controller.get_system_metrics()
        repeated_metrics = self.controller.get_system_metrics()

        self.assertEqual(metrics["total_mb"], self.controller.DEFAULT_TOTAL_MEMORY)
        self.assertEqual(metrics["used_mb"], 125)
        self.assertEqual(metrics["free_mb"], self.controller.DEFAULT_TOTAL_MEMORY - 125)
        self.assertEqual(metrics["total_apps"], 2)
        self.assertEqual(metrics["active_apps"], 1)
        self.assertEqual(metrics["eviction_count"], 1)
        self.assertEqual(metrics["conflict_count"], 1)
        self.assertEqual(repeated_metrics, metrics)
        self.assertEqual(len(self.controller.get_recent_events()), event_count_before)

    def test_event_log_includes_structured_resource_details_without_mutating_state(self):
        app = self.controller.add_app("Maps", memory_footprint=150)
        self.assertTrue(self.controller.request_resource(app.app_id, "GPS"))

        event_count_before = len(self.controller.get_recent_events())
        event = self.controller.get_event_log(n=1)[0]

        self.assertEqual(event["event_type"], "resource")
        self.assertEqual(event["app"], f"Maps ({app.app_id})")
        self.assertEqual(event["resource"], "GPS Location Sensor (GPS)")
        self.assertEqual(event["decision"], "ACQUIRED")
        self.assertEqual(len(self.controller.get_recent_events()), event_count_before)
        self.assertEqual(self.controller.mem_mgr.get_used_memory(), 150)


if __name__ == "__main__":
    unittest.main()
