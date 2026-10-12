"""Tests for the Phase-II analytics queries.

The dataset is deliberately tiny and fully timestamped, so every expected
number can be worked out by hand:

Apps
    ``app_maps`` (300 MB), ``app_fitness`` (250 MB), ``app_music`` (200 MB).

Resources
    ``GPS`` and ``Audio``.

Timeline
    100  app_maps  is granted GPS
    110  app_fitness is queued behind it for GPS
    150  app_maps  releases GPS, so app_fitness is granted it  -> 40 s wait
    160  app_maps  is evicted by RAAE_CLOCK with a safe release
    165  app_maps / app_fitness conflict on GPS and Wound-Wait wounds the holder
    170  app_fitness is protected, so its eviction is blocked (CONFLICT)
    200  app_music  is granted Audio
    250  app_music  is evicted by BASELINE_CLOCK without any lock check, which
         strands app_fitness waiting for GPS -> one freeze incident

Expected headline numbers: 2 completed evictions, 1 blocked decision, 1 freeze,
2 conflicts, 3 acquisitions, 2 releases, 1 queued request, mean latency 40 s.
"""

import unittest

import backend.models.app  # noqa: F401 - establishes the package import order
from backend.models.app import App
from backend.models.resource import Resource
from database.analytics import FREEZE_PREDICATE, POLICY_BASELINE, POLICY_RAAE
from database.db import DatabaseManager


class AnalyticsSampleData(unittest.TestCase):
    """Builds the fixed sample dataset described in this module's docstring."""

    def setUp(self):
        self.db = DatabaseManager(db_path=":memory:")
        self.analytics = self.db.analytics

        for app_id, name, footprint in (
            ("app_maps", "Maps", 300),
            ("app_fitness", "Fitness", 250),
            ("app_music", "Music", 200),
        ):
            self.db.create_app(
                App(app_id=app_id, name=name, memory_footprint=footprint)
            )

        self.db.save_resource(Resource("GPS", "GPS Sensor", capacity=1))
        self.db.save_resource(Resource("Audio", "Audio Output", capacity=1))

        self._seed_locks()
        self._seed_lock_requests()
        self._seed_evictions()
        self._seed_conflicts()
        self._seed_memory_events()

    def tearDown(self):
        self.db.close()

    def _seed_locks(self):
        """Records the ownership intervals the ledger holds for the run."""
        maps_lock = self.db.acquire_resource_lock("app_maps", "GPS", acquired_at=100.0)
        self.db.release_resource_lock(maps_lock, released_at=150.0)
        self.db.acquire_resource_lock("app_fitness", "GPS", acquired_at=150.0)
        music_lock = self.db.acquire_resource_lock("app_music", "Audio", acquired_at=200.0)
        self.db.release_resource_lock(music_lock, released_at=250.0)

    def _seed_lock_requests(self):
        """Records every lock attempt and the outcome it received."""
        for app_id, resource_id, outcome, at in (
            ("app_maps", "GPS", "GRANTED", 100.0),
            ("app_fitness", "GPS", "QUEUED", 110.0),
            ("app_fitness", "GPS", "GRANTED", 150.0),
            ("app_maps", "GPS", "RELEASED", 150.0),
            ("app_music", "Audio", "GRANTED", 200.0),
            ("app_music", "Audio", "RELEASED", 250.0),
        ):
            self.db.insert_lock_request(app_id, resource_id, outcome, requested_at=at)

    def _seed_evictions(self):
        """Records one RAAE eviction, one blocked RAAE decision, one baseline eviction."""
        self.db.insert_eviction_log(
            "app_maps", "RAAE_CLOCK", "RAAE released GPS then evicted.", True, True, "EVICTED", 160.0
        )
        self.db.insert_eviction_log(
            "app_fitness",
            "RAAE_CLOCK",
            "Holds a resource a waiter needs.",
            True,
            False,
            "CONFLICT",
            170.0,
        )
        self.db.insert_eviction_log(
            "app_music",
            "BASELINE_CLOCK",
            "Blind eviction without resource-aware check.",
            False,
            False,
            "EVICTED",
            250.0,
        )

    def _seed_conflicts(self):
        """Records the resolved Wound-Wait conflict and the resulting freeze."""
        self.db.insert_conflict_log(
            "app_fitness",
            "app_maps",
            "GPS",
            "WOUND_WAIT",
            resolved_by="WOUND",
            details="app_fitness outranks the holder; holder wounded.",
            timestamp=165.0,
        )
        self.db.insert_conflict_log(
            "app_fitness",
            "app_music",
            "GPS",
            "NONE",
            resolved_by=None,
            details="FREEZE: app_music was blindly killed; app_fitness is stranded.",
            timestamp=250.0,
        )

    def _seed_memory_events(self):
        """Records the allocation and the two evictions for the timeline series."""
        self.db.insert_memory_event("app_maps", "ALLOCATE", 100, 300, "ORANGE", 130.0)
        self.db.insert_memory_event("app_maps", "EVICT", 300, 0, "ORANGE", 160.0)
        self.db.insert_memory_event("app_music", "EVICT", 200, 0, "RED", 250.0)


class TestEmptyDatabase(unittest.TestCase):
    """A dashboard opened before any run must render zeros, not errors."""

    def setUp(self):
        self.db = DatabaseManager(db_path=":memory:")
        self.analytics = self.db.analytics

    def tearDown(self):
        self.db.close()

    def test_every_counter_reports_zero(self):
        """Test that empty log tables aggregate to 0 rather than None."""
        self.assertEqual(self.analytics.total_eviction_count(), 0)
        self.assertEqual(self.analytics.total_freeze_incidents(), 0)
        self.assertEqual(self.analytics.total_conflicts(), 0)
        self.assertEqual(self.analytics.successful_resource_acquisitions(), 0)
        self.assertEqual(self.analytics.resource_releases(), 0)
        self.assertEqual(self.analytics.queued_resource_requests(), 0)

    def test_breakdowns_return_empty_lists(self):
        """Test that every breakdown is an empty list when nothing was recorded."""
        self.assertEqual(self.analytics.evictions_by_app(), [])
        self.assertEqual(self.analytics.evictions_by_resource(), [])
        self.assertEqual(self.analytics.conflicts_by_resource(), [])
        self.assertEqual(self.analytics.events_over_time(), [])
        self.assertEqual(self.analytics.conflict_resolution_samples(), [])

    def test_latency_is_none_rather_than_zero(self):
        """Test that 'no contention data' is distinguishable from 'instant resolution'."""
        self.assertIsNone(self.analytics.average_conflict_resolution_latency())

    def test_policy_comparison_is_empty_without_algorithm_data(self):
        """Test that absent policy information yields no rows to plot."""
        self.assertEqual(self.analytics.policy_comparison(), [])
        self.assertFalse(self.analytics.policy_delta()["available"])


class TestHeadlineCounters(AnalyticsSampleData):
    """The scalar counters match the hand-computed totals."""

    def test_total_eviction_count_counts_completed_evictions(self):
        """Test that only EVICTED rows count as evictions."""
        self.assertEqual(self.analytics.total_eviction_count(), 2)

    def test_total_eviction_count_can_include_blocked_decisions(self):
        """Test that result=None widens the count to every recorded decision."""
        self.assertEqual(self.analytics.total_eviction_count(result=None), 3)

    def test_evictions_break_down_by_result(self):
        """Test that the outcome split distinguishes evictions from blocks."""
        self.assertEqual(
            self.analytics.total_evictions_by_result(),
            {"EVICTED": 2, "CONFLICT": 1},
        )

    def test_total_freeze_incidents_counts_only_freeze_rows(self):
        """Test that the FREEZE marker separates a stranded waiter from a resolved conflict."""
        self.assertEqual(self.analytics.total_freeze_incidents(), 1)

    def test_total_conflicts_counts_every_incident(self):
        """Test that conflicts include both the resolved and the frozen case."""
        self.assertEqual(self.analytics.total_conflicts(), 2)

    def test_total_conflicts_can_be_restricted_to_freezes(self):
        """Test that freeze_only reuses the freeze count rather than re-deriving it."""
        self.assertEqual(self.analytics.total_conflicts(freeze_only=True), 1)

    def test_successful_acquisitions_and_releases(self):
        """Test that granted and released requests are counted separately."""
        self.assertEqual(self.analytics.successful_resource_acquisitions(), 3)
        self.assertEqual(self.analytics.resource_releases(), 2)

    def test_queued_requests_are_counted(self):
        """Test that a blocked attempt is visible even though it held no lock."""
        self.assertEqual(self.analytics.queued_resource_requests(), 1)

    def test_summary_bundles_every_headline_number(self):
        """Test that one call fills a whole metric row."""
        self.assertEqual(
            self.analytics.summary(),
            {
                "total_evictions": 2,
                "blocked_evictions": 1,
                "total_freezes": 1,
                "total_conflicts": 2,
                "successful_acquisitions": 3,
                "resource_releases": 2,
                "queued_requests": 1,
                "average_conflict_latency": 40.0,
            },
        )

    def test_summary_values_are_plain_python_types(self):
        """Test that the bundle needs no conversion before Streamlit renders it."""
        for key, value in self.analytics.summary().items():
            with self.subTest(key=key):
                self.assertIsInstance(value, (int, float))


class TestEvictionsByApp(AnalyticsSampleData):
    """The per-app breakdown ranks apps and separates attempts from outcomes."""

    def test_rows_are_ranked_by_evictions_then_attempts(self):
        """Test that the busiest app leads, with the blocked app ordered second."""
        self.assertEqual(
            [(row["app_id"], row["evictions"]) for row in self.analytics.evictions_by_app()],
            [("app_maps", 1), ("app_music", 1)],
        )

    def test_app_names_are_joined_for_labels(self):
        """Test that a bar chart can label bars with names, not just ids."""
        names = {row["app_id"]: row["app_name"] for row in self.analytics.evictions_by_app()}
        self.assertEqual(names["app_maps"], "Maps")

    def test_blocked_decisions_are_visible_when_counting_every_attempt(self):
        """Test that a protected app is reported rather than silently dropped."""
        completed = {row["app_id"] for row in self.analytics.evictions_by_app()}
        self.assertNotIn("app_fitness", completed)

        rows = self.analytics.evictions_by_app(result=None)
        by_app = {row["app_id"]: row for row in rows}

        self.assertEqual(by_app["app_fitness"]["evictions"], 0)
        self.assertEqual(by_app["app_fitness"]["attempts"], 1)
        self.assertEqual(by_app["app_fitness"]["blocked"], 1)
        self.assertEqual(by_app["app_maps"]["attempts"], 1)

    def test_memory_freed_comes_from_the_recorded_eviction(self):
        """Test that freed memory is the EVICT delta, not the app's current footprint.

        Both evicted apps have a zeroed footprint in Apps by now, so a figure read
        off that column would be 0 for both and the dashboard would under-report.
        """
        rows = {row["app_id"]: row for row in self.analytics.evictions_by_app()}

        self.assertEqual(rows["app_maps"]["memory_freed_mb"], 300)
        self.assertEqual(rows["app_music"]["memory_freed_mb"], 200)
        self.assertEqual(self.db.get_app("app_maps").memory_footprint, 300)

    def test_an_app_with_no_eviction_reports_no_memory_freed(self):
        """Test that a protected app contributes 0 rather than NULL."""
        rows = {row["app_id"]: row for row in self.analytics.evictions_by_app(result=None)}

        self.assertEqual(rows["app_fitness"]["memory_freed_mb"], 0)

    def test_limit_caps_the_result(self):
        """Test that a top-N widget can bound the query."""
        self.assertEqual(len(self.analytics.evictions_by_app(limit=1)), 1)


class TestEvictionsByResource(AnalyticsSampleData):
    """Evictions are attributed through the ResourceLocks ledger."""

    def test_each_evicted_app_is_attributed_to_the_resource_it_held(self):
        """Test that GPS and Audio each carry their one eviction."""
        self.assertEqual(
            {row["resource_id"]: row["evictions"] for row in self.analytics.evictions_by_resource()},
            {"GPS": 1, "Audio": 1},
        )

    def test_resource_names_are_included_for_labels(self):
        """Test that the breakdown carries a human-readable name."""
        names = {row["resource_id"]: row["resource_name"] for row in self.analytics.evictions_by_resource()}
        self.assertEqual(names["GPS"], "GPS Sensor")

    def test_holders_that_were_not_evicted_are_not_counted(self):
        """Test that app_fitness holding GPS adds no eviction to GPS."""
        for row in self.analytics.evictions_by_resource():
            self.assertEqual(row["apps_evicted"], 1)

    def test_results_are_ranked_by_eviction_count(self):
        """Test that rows are ordered, with resource_id breaking a tie deterministically."""
        rows = self.analytics.evictions_by_resource()

        self.assertEqual(
            [row["resource_id"] for row in rows],
            sorted((row["resource_id"] for row in rows)),
        )


class TestConflictsByResource(AnalyticsSampleData):
    """Conflicts and freezes are broken down per contended resource."""

    def test_counts_split_resolved_from_frozen(self):
        """Test that the breakdown distinguishes the two outcomes."""
        row = self.analytics.conflicts_by_resource()[0]

        self.assertEqual(row["resource_id"], "GPS")
        self.assertEqual(row["conflicts"], 2)
        self.assertEqual(row["freezes"], 1)


class TestEventsOverTime(AnalyticsSampleData):
    """The bucketed timeline carries one row per interval with all series filled."""

    def test_buckets_cover_the_run_in_order(self):
        """Test that buckets are oldest-first and aligned to the requested width."""
        rows = self.analytics.events_over_time(bucket_seconds=60)

        self.assertEqual([row["bucket_start"] for row in rows], [60, 120, 180, 240])
        self.assertEqual(rows[0]["bucket_end"], 120)

    def test_every_series_is_present_on_every_bucket(self):
        """Test that a chart can plot all five series without filling gaps itself."""
        rows = self.analytics.events_over_time(bucket_seconds=60)
        for row in rows:
            for series in (
                "evictions",
                "blocked_evictions",
                "conflicts",
                "freezes",
                "grants",
                "releases",
                "memory_events",
            ):
                with self.subTest(bucket=row["bucket_start"], series=series):
                    self.assertIn(series, row)
                    self.assertIsInstance(row[series], int)

    def test_events_land_in_their_own_bucket(self):
        """Test that the grant at 100 and the queue at 110 share the first bucket."""
        rows = self.analytics.events_over_time(bucket_seconds=60)
        by_bucket = {row["bucket_start"]: row for row in rows}

        self.assertEqual(by_bucket[60]["grants"], 1)
        self.assertEqual(by_bucket[120]["grants"], 1)
        self.assertEqual(by_bucket[120]["releases"], 1)
        self.assertEqual(by_bucket[120]["blocked_evictions"], 1)
        self.assertEqual(by_bucket[120]["conflicts"], 1)
        self.assertEqual(by_bucket[120]["memory_events"], 2)
        self.assertEqual(by_bucket[180]["grants"], 1)
        self.assertEqual(by_bucket[240]["evictions"], 1)
        self.assertEqual(by_bucket[240]["freezes"], 1)
        self.assertEqual(by_bucket[240]["memory_events"], 1)

    def test_start_and_end_bound_the_window(self):
        """Test that a date-range filter in the UI restricts the series."""
        rows = self.analytics.events_over_time(bucket_seconds=60, start=200.0, end=260.0)

        self.assertEqual([row["bucket_start"] for row in rows], [180, 240])
        self.assertEqual(rows[0]["grants"], 1)
        self.assertEqual(rows[1]["evictions"], 1)

    def test_default_bucket_is_sixty_seconds(self):
        """Test that omitting the width uses the documented default."""
        self.assertEqual(
            self.analytics.events_over_time(),
            self.analytics.events_over_time(bucket_seconds=60),
        )

    def test_non_positive_bucket_is_rejected(self):
        """Test that a zero-width bucket is reported instead of dividing by zero."""
        for bad in (0, -60):
            with self.subTest(bucket_seconds=bad):
                with self.assertRaises(ValueError):
                    self.analytics.events_over_time(bucket_seconds=bad)


class TestConflictResolutionLatency(AnalyticsSampleData):
    """Latency is measured from the queued request to the grant that served it."""

    def test_average_latency_is_the_queue_to_grant_gap(self):
        """Test that 150 - 110 is reported as 40 seconds."""
        self.assertEqual(self.analytics.average_conflict_resolution_latency(), 40.0)

    def test_samples_expose_the_underlying_waits(self):
        """Test that the average can be explained by listing what it averaged."""
        samples = self.analytics.conflict_resolution_samples()

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["app_id"], "app_fitness")
        self.assertEqual(samples[0]["resource_id"], "GPS")
        self.assertEqual(samples[0]["queued_at"], 110.0)
        self.assertEqual(samples[0]["granted_at"], 150.0)
        self.assertEqual(samples[0]["latency_seconds"], 40.0)

    def test_a_waiter_that_is_never_served_contributes_nothing(self):
        """Test that a stranded waiter is excluded rather than counted as instant."""
        self.db.insert_lock_request("app_maps", "Audio", "QUEUED", requested_at=90.0)

        samples = self.analytics.conflict_resolution_samples()
        self.assertEqual([row["app_id"] for row in samples], ["app_fitness"])

    def test_a_grant_before_the_queue_does_not_end_the_wait(self):
        """Test that only a grant at or after the queue can close it."""
        self.db.insert_lock_request("app_maps", "GPS", "GRANTED", requested_at=95.0)

        self.assertEqual(self.analytics.average_conflict_resolution_latency(), 40.0)


class TestPolicyComparison(AnalyticsSampleData):
    """Clock and RAAE statistics are compared from the recorded algorithm."""

    def test_one_row_per_algorithm_present(self):
        """Test that each policy run becomes a plottable row."""
        self.assertEqual(
            {row["policy"] for row in self.analytics.policy_comparison()},
            {POLICY_BASELINE, POLICY_RAAE},
        )

    def test_raae_row_reports_the_safe_release(self):
        """Test that RAAE's safe release and blocked decision are both counted."""
        raae = next(row for row in self.analytics.policy_comparison() if row["policy"] == POLICY_RAAE)

        self.assertEqual(raae["evictions"], 1)
        self.assertEqual(raae["attempts"], 2)
        self.assertEqual(raae["blocked_evictions"], 1)
        self.assertEqual(raae["safe_releases"], 1)
        self.assertEqual(raae["evictions_without_safe_release"], 0)

    def test_baseline_row_reports_the_unsafe_eviction_and_freeze(self):
        """Test that a blind eviction is attributed its freeze incident."""
        baseline = next(
            row for row in self.analytics.policy_comparison() if row["policy"] == POLICY_BASELINE
        )

        self.assertEqual(baseline["evictions"], 1)
        self.assertEqual(baseline["evictions_without_safe_release"], 1)
        self.assertEqual(baseline["freeze_incidents"], 1)

    def test_delta_reports_baseline_minus_raae(self):
        """Test that the headline comparison is a plain difference."""
        delta = self.analytics.policy_delta()

        self.assertTrue(delta["available"])
        self.assertEqual(delta["metrics"]["freeze_incidents"]["baseline"], 1)
        self.assertEqual(delta["metrics"]["freeze_incidents"]["raae"], 0)
        self.assertEqual(delta["metrics"]["freeze_incidents"]["difference"], 1)
        self.assertEqual(delta["metrics"]["safe_releases"]["difference"], -1)

    def test_delta_is_unavailable_when_only_one_policy_ran(self):
        """Test that a half-filled comparison is reported, not rendered as zero."""
        self.db.get_connection().execute(
            "DELETE FROM EvictionLog WHERE algorithm = ?", ("RAAE_CLOCK",)
        )
        self.db.get_connection().commit()

        delta = self.analytics.policy_delta()
        self.assertFalse(delta["available"])
        self.assertEqual(delta["metrics"], {})


class TestAnalyticsUseIndexes(AnalyticsSampleData):
    """The analytics predicates are served by indexes rather than full scans."""

    def _plan(self, sql, params=()):
        with self.db.get_connection() as conn:
            return [row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]

    def test_analytics_indexes_are_registered(self):
        """Test that the indexes the queries rely on are part of the schema."""
        indexes = self.db.get_index_names()

        self.assertIn("idx_evictionlog_result_app", indexes)
        self.assertIn("idx_conflictlog_freeze", indexes)

    def test_eviction_outcome_count_uses_an_index(self):
        """Test that filtering EvictionLog by result is an index search."""
        plan = self._plan("SELECT COUNT(*) FROM EvictionLog WHERE result = ?", ("EVICTED",))

        self.assertTrue(any("USING INDEX" in step or "USING COVERING INDEX" in step for step in plan), plan)
        self.assertFalse(any(step.strip() == "SCAN EvictionLog" for step in plan), plan)

    def test_evictions_grouped_by_app_use_an_index(self):
        """Test that the GROUP BY app_id rides the same index instead of scanning."""
        plan = self._plan(
            "SELECT app_id, COUNT(*) FROM EvictionLog WHERE result = 'EVICTED' GROUP BY app_id"
        )

        self.assertFalse(any(step.strip() == "SCAN EvictionLog" for step in plan), plan)

    def test_freeze_count_uses_the_partial_index(self):
        """Test that the FREEZE marker is answered without scanning the log.

        The predicate is asserted literally rather than as a bind parameter,
        because inlining it is what lets SQLite match the partial index. Binding
        it would silently downgrade this count to a full table scan.
        """
        plan = self._plan(f"SELECT COUNT(*) FROM ConflictLog WHERE {FREEZE_PREDICATE}")

        self.assertTrue(any("idx_conflictlog_freeze" in step for step in plan), plan)
        self.assertFalse(any(step.strip() == "SCAN ConflictLog" for step in plan), plan)

    def test_binding_the_pattern_defeats_the_partial_index(self):
        """Test that the reason for inlining the marker is real, not superstition."""
        plan = self._plan("SELECT COUNT(*) FROM ConflictLog WHERE details LIKE ?", ("FREEZE%",))

        self.assertTrue(any(step.strip() == "SCAN ConflictLog" for step in plan), plan)

    def test_lock_request_outcome_count_uses_an_index(self):
        """Test that counting grants and releases is a covering index search."""
        plan = self._plan("SELECT COUNT(*) FROM LockRequests WHERE outcome = ?", ("GRANTED",))

        self.assertTrue(any("idx_lockrequests_outcome" in step for step in plan), plan)

    def test_conflicts_grouped_by_resource_use_an_index(self):
        """Test that the per-resource conflict breakdown is index served."""
        plan = self._plan(
            "SELECT resource_id, COUNT(*) FROM ConflictLog GROUP BY resource_id"
        )

        self.assertFalse(any(step.strip() == "SCAN ConflictLog" for step in plan), plan)


if __name__ == "__main__":
    unittest.main()
