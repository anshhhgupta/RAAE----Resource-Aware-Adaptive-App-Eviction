"""Tests for isolated Clock vs RAAE workload evaluations."""

import math
import unittest

from backend.models.app import App, AppState
from backend.models.resource import Resource
from backend.raae_engine.models import ConflictStatus
from backend.policy_evaluation import (
    UnsupportedWorkloadError,
    _is_resource_blocked_decision,
    _is_simulated_wait_freeze_proxy,
    _snapshot_fingerprint,
    compare_eviction_policies,
)


class TestPolicyEvaluation(unittest.TestCase):
    def _contended_workload(self):
        holder = App(
            app_id="holder",
            name="Resource Holder",
            priority=10,
            state=AppState.BACKGROUND,
            memory_footprint=300,
            reference_bit=1,
            last_access_time=100.0,
            held_resources={"GPS"},
        )
        waiter = App(
            app_id="waiter",
            name="Waiting App",
            priority=1,
            state=AppState.WAITING,
            memory_footprint=200,
            reference_bit=0,
            last_access_time=200.0,
        )
        gps = Resource(
            resource_id="GPS",
            name="GPS",
            capacity=1,
            available_units=0,
            holders={holder.app_id: 1},
            waiting_queue=[waiter.app_id],
        )
        return [holder, waiter], [gps]

    def test_both_policies_start_from_same_snapshot_without_mutating_it(self):
        apps, resources = self._contended_workload()
        original_app_states = [
            (app.state, app.memory_footprint, app.reference_bit, set(app.held_resources))
            for app in apps
        ]
        original_resource_state = (
            resources[0].available_units,
            dict(resources[0].holders),
            list(resources[0].waiting_queue),
        )

        comparison = compare_eviction_policies(
            apps=apps,
            resources=resources,
            total_memory=1024,
            clock_hand=0,
            max_attempts=1,
        )

        self.assertEqual(comparison.plain_clock.attempts, 1)
        self.assertEqual(comparison.raae.attempts, 1)
        self.assertEqual(comparison.plain_clock.passive_contention_observations, 1)
        self.assertEqual(comparison.raae.passive_contention_observations, 1)
        self.assertEqual(comparison.plain_clock.logged_conflict_outcomes, 0)
        self.assertGreater(comparison.raae.logged_conflict_outcomes, 0)
        self.assertEqual(comparison.plain_clock.eviction_count, 1)
        self.assertEqual(comparison.raae.eviction_count, 0)
        self.assertEqual(comparison.raae.simulated_wait_freeze_proxy_count, 1)
        self.assertEqual(comparison.raae.resource_blocked_evictions, 1)
        self.assertIsNone(comparison.plain_clock.conflict_case_orchestration_latency_ms)
        self.assertIsNotNone(comparison.raae.conflict_case_orchestration_latency_ms)
        self.assertGreaterEqual(
            comparison.raae.conflict_case_orchestration_latency_ms, 0
        )
        self.assertEqual(
            [
                (app.state, app.memory_footprint, app.reference_bit, set(app.held_resources))
                for app in apps
            ],
            original_app_states,
        )
        self.assertEqual(
            (
                resources[0].available_units,
                dict(resources[0].holders),
                list(resources[0].waiting_queue),
            ),
            original_resource_state,
        )
        self.assertEqual(
            comparison.workload_fingerprint,
            _snapshot_fingerprint(apps, resources, 1024, 0),
        )

    def test_metrics_are_derived_from_run_and_missing_latency_is_not_zero(self):
        apps = [
            App(
                app_id="one",
                name="First",
                memory_footprint=125,
                reference_bit=0,
            ),
            App(
                app_id="two",
                name="Second",
                memory_footprint=250,
                reference_bit=0,
            ),
        ]
        comparison = compare_eviction_policies(
            apps=apps,
            resources=[],
            total_memory=1000,
            clock_hand=0,
            max_attempts=1,
        )

        self.assertEqual(comparison.initial_used_memory_mb, 375)
        self.assertEqual(comparison.plain_clock.eviction_count, 1)
        self.assertEqual(comparison.raae.eviction_count, 1)
        self.assertEqual(comparison.plain_clock.logged_conflict_outcomes, 0)
        self.assertEqual(comparison.raae.logged_conflict_outcomes, 0)
        self.assertEqual(comparison.plain_clock.passive_contention_observations, 0)
        self.assertEqual(comparison.raae.passive_contention_observations, 0)
        self.assertIsNone(comparison.plain_clock.conflict_case_orchestration_latency_ms)
        self.assertIsNone(comparison.raae.conflict_case_orchestration_latency_ms)

    def test_resource_blocked_counts_require_resource_identified_blocking_status(self):
        blocking_statuses = {
            ConflictStatus.WAIT,
            ConflictStatus.RESOLVE_REQUIRED,
            ConflictStatus.BLOCKED,
            ConflictStatus.NOT_SAFE,
        }
        for status in blocking_statuses:
            with self.subTest(status=status):
                self.assertTrue(
                    _is_resource_blocked_decision(status, ("GPS",), can_evict=False)
                )
                self.assertFalse(
                    _is_resource_blocked_decision(status, (), can_evict=False)
                )
                self.assertFalse(
                    _is_resource_blocked_decision(status, ("GPS",), can_evict=True)
                )
        self.assertFalse(
            _is_resource_blocked_decision(
                ConflictStatus.NO_CONFLICT, ("GPS",), can_evict=False
            )
        )

    def test_wait_count_is_only_a_simulated_wait_freeze_proxy(self):
        self.assertTrue(_is_simulated_wait_freeze_proxy(ConflictStatus.WAIT))
        for status in ConflictStatus:
            if status != ConflictStatus.WAIT:
                with self.subTest(status=status):
                    self.assertFalse(_is_simulated_wait_freeze_proxy(status))

    def test_unsupported_snapshots_are_reported_instead_of_returning_zero_metrics(self):
        with self.assertRaisesRegex(UnsupportedWorkloadError, "at least one active app"):
            compare_eviction_policies([], [], 1024, 0)

        apps = [App(app_id="known", name="Known")]
        resources = [
            Resource(
                resource_id="GPS",
                name="GPS",
                holders={"missing": 1},
            )
        ]
        with self.assertRaisesRegex(UnsupportedWorkloadError, "unknown app"):
            compare_eviction_policies(apps, resources, 1024, 0)

    def test_snapshot_fingerprint_and_policy_runs_are_separate(self):
        apps, resources = self._contended_workload()
        comparison = compare_eviction_policies(apps, resources, 1024, 0, max_attempts=2)

        self.assertEqual(len(comparison.workload_fingerprint), 64)
        self.assertNotEqual(
            comparison.plain_clock.eviction_count,
            comparison.raae.eviction_count,
        )
        self.assertTrue(
            math.isfinite(comparison.raae.conflict_case_orchestration_latency_ms)
        )


if __name__ == "__main__":
    unittest.main()
