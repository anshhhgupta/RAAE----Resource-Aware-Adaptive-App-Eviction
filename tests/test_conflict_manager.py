"""Unit tests for ConflictManager in RAAE."""

import unittest
from backend.models.app import App, AppState
from backend.models.resource import Resource, ResourceStatus
from backend.conflict_manager import (
    ConflictManager,
    ConflictDecision,
    ConflictResult,
    WOUND_HOLDER,
    WAIT,
    NO_CONFLICT
)
from database.db import DatabaseManager


class TestConflictManager(unittest.TestCase):
    """Test suite for ConflictManager and Wound-Wait conflict resolution."""

    def setUp(self):
        self.cm = ConflictManager()

    def test_wound_holder_higher_priority(self):
        """Candidate with higher priority should wound lower-priority holder."""
        result = self.cm.resolve_conflict(
            candidate="app_cand",
            candidate_priority=3,
            holder="app_holder",
            holder_priority=1,
            requested_resource="GPS"
        )
        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)
        self.assertTrue(result.is_wound)
        self.assertFalse(result.is_wait)
        self.assertFalse(result.is_no_conflict)
        self.assertEqual(result, "WOUND_HOLDER")
        self.assertEqual(result, ConflictDecision.WOUND_HOLDER)
        self.assertIn("Holder wounded", result.reason)

    def test_wait_lower_priority(self):
        """Candidate with lower priority must wait for higher-priority holder."""
        result = self.cm.resolve_conflict(
            candidate="app_cand",
            candidate_priority=1,
            holder="app_holder",
            holder_priority=3,
            requested_resource="GPS"
        )
        self.assertEqual(result.decision, ConflictDecision.WAIT)
        self.assertTrue(result.is_wait)
        self.assertFalse(result.is_wound)
        self.assertEqual(result, "WAIT")
        self.assertIn("must wait", result.reason)

    def test_equal_priority_wound_older_candidate(self):
        """Equal priority: older candidate (earlier timestamp) wounds younger holder."""
        result = self.cm.resolve_conflict(
            candidate="app_cand",
            candidate_priority=2,
            candidate_timestamp=100.0,
            holder="app_holder",
            holder_priority=2,
            holder_timestamp=200.0,
            requested_resource="GPS"
        )
        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)
        self.assertTrue(result.is_wound)
        self.assertIn("is older than", result.reason)

    def test_equal_priority_wait_younger_candidate(self):
        """Equal priority: younger candidate (later timestamp) waits for older holder."""
        result = self.cm.resolve_conflict(
            candidate="app_cand",
            candidate_priority=2,
            candidate_timestamp=300.0,
            holder="app_holder",
            holder_priority=2,
            holder_timestamp=150.0,
            requested_resource="GPS"
        )
        self.assertEqual(result.decision, ConflictDecision.WAIT)
        self.assertTrue(result.is_wait)
        self.assertIn("is younger than", result.reason)

    def test_pure_timestamp_wound_older_candidate(self):
        """Pure timestamp comparison: older candidate wounds younger holder."""
        result = self.cm.resolve_conflict(
            candidate="app_1",
            candidate_timestamp=1000.0,
            holder="app_2",
            holder_timestamp=2000.0,
            requested_resource="DB_LOCK",
            mode="timestamp"
        )
        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)
        self.assertTrue(result.is_wound)

    def test_pure_timestamp_wait_younger_candidate(self):
        """Pure timestamp comparison: younger candidate waits for older holder."""
        result = self.cm.resolve_conflict(
            candidate="app_1",
            candidate_timestamp=5000.0,
            holder="app_2",
            holder_timestamp=2000.0,
            requested_resource="DB_LOCK",
            mode="timestamp"
        )
        self.assertEqual(result.decision, ConflictDecision.WAIT)
        self.assertTrue(result.is_wait)

    def test_no_conflict_when_resource_is_none(self):
        """No requested resource means NO_CONFLICT."""
        result = self.cm.resolve_conflict(
            candidate="app_1",
            candidate_priority=3,
            holder="app_2",
            holder_priority=1,
            requested_resource=None
        )
        self.assertEqual(result.decision, ConflictDecision.NO_CONFLICT)
        self.assertTrue(result.is_no_conflict)
        self.assertEqual(result, "NO_CONFLICT")

    def test_no_conflict_when_holder_is_none(self):
        """No current holder means resource is free -> NO_CONFLICT."""
        result = self.cm.resolve_conflict(
            candidate="app_1",
            candidate_priority=3,
            holder=None,
            requested_resource="GPS"
        )
        self.assertEqual(result.decision, ConflictDecision.NO_CONFLICT)
        self.assertTrue(result.is_no_conflict)
        self.assertIn("resource is free", result.reason)

    def test_no_conflict_when_candidate_is_none(self):
        """No candidate specified -> NO_CONFLICT."""
        result = self.cm.resolve_conflict(
            candidate=None,
            holder="app_2",
            requested_resource="GPS"
        )
        self.assertEqual(result.decision, ConflictDecision.NO_CONFLICT)
        self.assertTrue(result.is_no_conflict)

    def test_no_conflict_when_candidate_is_holder(self):
        """When candidate already holds the resource, there is no conflict."""
        result = self.cm.resolve_conflict(
            candidate="app_1",
            holder="app_1",
            requested_resource="GPS"
        )
        self.assertEqual(result.decision, ConflictDecision.NO_CONFLICT)
        self.assertTrue(result.is_no_conflict)

    def test_no_conflict_when_holder_does_not_hold_resource(self):
        """When holder App does not hold the requested resource -> NO_CONFLICT."""
        cand = App(app_id="app_1", name="Maps", priority=3)
        holder = App(app_id="app_2", name="Camera", priority=2, held_resources={"MIC"})

        result = self.cm.resolve_conflict(
            candidate=cand,
            holder=holder,
            requested_resource="GPS"
        )
        self.assertEqual(result.decision, ConflictDecision.NO_CONFLICT)
        self.assertTrue(result.is_no_conflict)
        self.assertIn("does not hold resource", result.reason)

    def test_no_conflict_when_resource_is_free(self):
        """When Resource object has no holders -> NO_CONFLICT."""
        cand = App(app_id="app_1", name="Maps", priority=3)
        holder = App(app_id="app_2", name="Camera", priority=1)
        res = Resource(resource_id="GPS", name="GPS", capacity=1)

        result = self.cm.resolve_conflict(
            candidate=cand,
            holder=holder,
            requested_resource=res
        )
        self.assertEqual(result.decision, ConflictDecision.NO_CONFLICT)
        self.assertTrue(result.is_no_conflict)

    def test_no_conflict_when_candidate_is_evicted(self):
        """When candidate App is already evicted -> NO_CONFLICT."""
        cand = App(app_id="app_1", name="Maps", priority=3, state=AppState.EVICTED)
        holder = App(app_id="app_2", name="Camera", priority=1, held_resources={"GPS"})

        result = self.cm.resolve_conflict(
            candidate=cand,
            holder=holder,
            requested_resource="GPS"
        )
        self.assertEqual(result.decision, ConflictDecision.NO_CONFLICT)
        self.assertTrue(result.is_no_conflict)

    def test_resolution_with_app_objects(self):
        """Extracts priorities and timestamps automatically from App objects."""
        cand = App(app_id="app_cand", name="Foreground Nav", priority=4)
        holder = App(app_id="app_holder", name="Background Sync", priority=1, held_resources={"GPS"})

        result = self.cm.resolve_conflict(
            candidate=cand,
            holder=holder,
            requested_resource="GPS"
        )
        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)
        self.assertEqual(result.candidate_id, "app_cand")
        self.assertEqual(result.holder_id, "app_holder")
        self.assertEqual(result.candidate_priority, 4)
        self.assertEqual(result.holder_priority, 1)

    def test_structured_decision_properties_and_dict(self):
        """Verifies structured ConflictResult fields, serialization, and dictionary access."""
        result = self.cm.resolve_conflict(
            candidate="app_c",
            candidate_priority=3,
            candidate_timestamp=100.5,
            holder="app_h",
            holder_priority=1,
            holder_timestamp=200.5,
            requested_resource="AUDIO"
        )
        self.assertIsInstance(result, ConflictResult)
        self.assertEqual(result.decision, WOUND_HOLDER)
        self.assertEqual(result.candidate_id, "app_c")
        self.assertEqual(result.holder_id, "app_h")
        self.assertEqual(result.resource_id, "AUDIO")

        d = result.to_dict()
        self.assertEqual(d["decision"], "WOUND_HOLDER")
        self.assertEqual(d["candidate_id"], "app_c")
        self.assertEqual(d["holder_id"], "app_h")
        self.assertEqual(d["resource_id"], "AUDIO")
        self.assertEqual(d["candidate_priority"], 3)
        self.assertEqual(d["holder_priority"], 1)

        # Dictionary-style access
        self.assertEqual(result["decision"], "WOUND_HOLDER")
        self.assertEqual(result["resource_id"], "AUDIO")

    def test_positional_arguments_5_args(self):
        """Supports 5 positional arguments according to requirement specification."""
        # Signature: candidate, cand_metric, holder, holder_metric, requested_resource
        result = self.cm.resolve_conflict("app_1", 3, "app_2", 1, "GPS")
        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)

        result_wait = self.cm.resolve_conflict("app_1", 1, "app_2", 3, "GPS")
        self.assertEqual(result_wait.decision, ConflictDecision.WAIT)

    def test_positional_arguments_3_args(self):
        """Supports 3 positional arguments: (candidate, holder, requested_resource)."""
        cand = App(app_id="app_1", name="HighApp", priority=5)
        holder = App(app_id="app_2", name="LowApp", priority=1, held_resources={"GPS"})

        result = self.cm.resolve_conflict(cand, holder, "GPS")
        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)

    def test_callable_manager_and_aliases(self):
        """ConflictManager instance can be called directly and aliases work identically."""
        r1 = self.cm("app_1", 3, "app_2", 1, "GPS")
        r2 = self.cm.evaluate_conflict("app_1", 3, "app_2", 1, "GPS")
        r3 = self.cm.check_conflict("app_1", 3, "app_2", 1, "GPS")

        self.assertEqual(r1.decision, ConflictDecision.WOUND_HOLDER)
        self.assertEqual(r2.decision, ConflictDecision.WOUND_HOLDER)
        self.assertEqual(r3.decision, ConflictDecision.WOUND_HOLDER)

    def test_database_logging_integration(self):
        """When DatabaseManager is provided, conflicts are persisted in ConflictLog table."""
        db = DatabaseManager(":memory:")
        app1 = App(app_id="app_1", name="Maps", priority=3)
        app2 = App(app_id="app_2", name="Music", priority=1, held_resources={"GPS"})
        db.save_app(app1)
        db.save_app(app2)

        cm_with_db = ConflictManager(db=db)
        result = cm_with_db.resolve_conflict(
            candidate=app1,
            holder=app2,
            requested_resource="GPS"
        )
        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)

        # Check DB repository
        conflicts = db.conflict_log.get_conflicts_by_resource("GPS")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["waiting_app_id"], "app_1")
        self.assertEqual(conflicts[0]["blocking_app_id"], "app_2")
        self.assertEqual(conflicts[0]["resource_id"], "GPS")
        self.assertEqual(conflicts[0]["resolved_by"], "WOUND")


if __name__ == "__main__":
    unittest.main()
