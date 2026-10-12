"""Integration coverage for the Phase-II dashboard demonstration flow."""

import sqlite3
import unittest
from unittest.mock import patch

from backend.models.app import AppState
from backend.phase2_resources import register_demo_resources
from backend.simulation_controller import SimulationController


class TestPhaseTwoIntegration(unittest.TestCase):
    def setUp(self):
        self.controller = SimulationController()
        register_demo_resources(self.controller.res_mgr)

    def tearDown(self):
        self.controller.db.close()

    def test_camera_microphone_gps_and_audio_can_be_requested_and_released(self):
        app = self.controller.add_app(
            "Resource Demo",
            priority=3,
            memory_footprint=200,
            state=AppState.BACKGROUND,
        )
        required_resources = {"Camera", "Microphone", "GPS", "Audio"}
        self.assertTrue(required_resources.issubset(
            set(self.controller.get_available_resource_ids())
        ))

        for resource_id in sorted(required_resources):
            with self.subTest(resource_id=resource_id):
                self.assertTrue(
                    self.controller.request_resource(app.app_id, resource_id)
                )
                resource = self.controller.res_mgr.get_resource(resource_id)
                self.assertEqual(resource.holders.get(app.app_id), 1)
                self.assertTrue(
                    self.controller.release_resource(app.app_id, resource_id)
                )
                self.assertNotIn(app.app_id, resource.holders)

    def test_wound_wait_releases_resource_and_persists_safe_eviction(self):
        holder = self.controller.add_app(
            "Low Priority Holder",
            priority=1,
            memory_footprint=250,
            state=AppState.BACKGROUND,
        )
        waiter = self.controller.add_app(
            "High Priority Waiter",
            priority=10,
            memory_footprint=200,
            state=AppState.FOREGROUND,
        )

        self.assertTrue(self.controller.request_resource(holder.app_id, "Camera"))
        self.assertFalse(self.controller.request_resource(waiter.app_id, "Camera"))
        camera = self.controller.res_mgr.get_resource("Camera")
        self.assertEqual(camera.holders, {holder.app_id: 1})
        self.assertEqual(camera.waiting_queue, [waiter.app_id])

        self.assertGreater(self.controller.trigger_memory_pressure(85.0), 0)
        self.assertGreaterEqual(self.controller.get_system_metrics()["used_mb"], 850)
        self.controller.start()
        result = self.controller.run_clock_tick()

        self.assertIsNotNone(result)
        self.assertEqual(result.candidate.app_id, holder.app_id)
        self.assertTrue(result.can_evict)
        self.assertTrue(holder.is_evicted)
        self.assertNotIn(holder.app_id, camera.holders)
        self.assertEqual(camera.holders, {waiter.app_id: 1})
        self.assertEqual(camera.waiting_queue, [])

        conflict_rows = self.controller.db.conflict_log.get_conflicts_by_resource(
            "Camera"
        )
        self.assertTrue(any(row["resolved_by"] == "WOUND" for row in conflict_rows))
        eviction_rows = self.controller.db.eviction_log.get_evictions_by_app(
            holder.app_id
        )
        self.assertEqual(len(eviction_rows), 1)
        self.assertEqual(eviction_rows[0]["result"], "SAFE_TO_EVICT")
        self.assertEqual(
            self.controller.db.get_app(holder.app_id).state,
            AppState.EVICTED,
        )

        dashboard_event = next(
            event
            for event in self.controller.get_event_log()
            if event["app"] == f"Low Priority Holder ({holder.app_id})"
            and event["decision"] == "SAFE_TO_EVICT"
        )
        self.assertIn("Clock tick", dashboard_event["message"])
        self.assertIn("Camera", dashboard_event["resource"])

        self.assertTrue(self.controller.release_resource(waiter.app_id, "Camera"))
        self.assertEqual(camera.holders, {})
        self.assertEqual(camera.available_units, 1)

    def test_eviction_log_persistence_failure_is_logged(self):
        app = self.controller.add_app(
            "Persistence Failure",
            priority=1,
            memory_footprint=250,
            state=AppState.BACKGROUND,
        )
        self.controller.add_app(
            "Memory Pressure",
            priority=10,
            memory_footprint=700,
            state=AppState.FOREGROUND,
        )
        self.controller.start()

        with patch.object(
            self.controller.db.eviction_log,
            "_get_connection",
            side_effect=sqlite3.OperationalError("database is unavailable"),
        ):
            with self.assertLogs("database.repositories", level="ERROR") as logs:
                self.controller.run_clock_tick()

        self.assertTrue(app.is_evicted)
        self.assertTrue(
            any(
                "Unable to persist eviction log" in message
                and "database is unavailable" in message
                for message in logs.output
            )
        )
        self.assertEqual(
            self.controller.db.eviction_log.count_evictions(),
            0,
        )


if __name__ == "__main__":
    unittest.main()
