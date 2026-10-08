"""Unit tests for ConflictManager in RAAE."""

import unittest
from backend.models.app import App, AppState
from backend.models.resource import Resource, ResourceStatus
from backend.resource_manager.resource_manager import ResourceManager
from backend.memory_manager.memory_manager import MemoryManager
from backend.conflict_manager import (
    ConflictManager,
    ConflictDecision,
    ConflictResult,
    WOUND_HOLDER,
    WOUND,
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

    def test_older_requester_vs_younger_holder(self):
        """Test Case 1: Older requester conflicts with younger holder -> WOUND younger holder."""
        # 1a. Older by timestamp (earlier timestamp)
        result_ts = self.cm.resolve_conflict(
            requester="app_older",
            requester_timestamp=100.0,
            holder="app_younger",
            holder_timestamp=200.0,
            resource="GPS"
        )
        self.assertEqual(result_ts.decision, ConflictDecision.WOUND_HOLDER)
        self.assertEqual(result_ts.decision, WOUND)
        self.assertTrue(result_ts.is_wound)
        self.assertFalse(result_ts.is_wait)
        self.assertEqual(result_ts.requester, "app_older")
        self.assertEqual(result_ts.holder, "app_younger")
        self.assertEqual(result_ts.resource, "GPS")
        self.assertIn("older than", result_ts.reason)
        self.assertIsInstance(result_ts.resolution_timestamp, float)

        # 1b. Older/higher by priority
        result_p = self.cm.resolve_conflict(
            requester="app_high_prio",
            requester_priority=5,
            holder="app_low_prio",
            holder_priority=2,
            resource="MIC"
        )
        self.assertEqual(result_p.decision, ConflictDecision.WOUND_HOLDER)
        self.assertTrue(result_p.is_wound)
        self.assertEqual(result_p.requester, "app_high_prio")
        self.assertEqual(result_p.holder, "app_low_prio")
        self.assertEqual(result_p.resource, "MIC")
        self.assertIn("Holder wounded", result_p.reason)

    def test_younger_requester_vs_older_holder(self):
        """Test Case 2: Younger requester conflicts with older holder -> WAIT."""
        # 2a. Younger by timestamp (later timestamp)
        result_ts = self.cm.resolve_conflict(
            requester="app_younger",
            requester_timestamp=350.0,
            holder="app_older",
            holder_timestamp=120.0,
            resource="GPS"
        )
        self.assertEqual(result_ts.decision, ConflictDecision.WAIT)
        self.assertEqual(result_ts.decision, WAIT)
        self.assertTrue(result_ts.is_wait)
        self.assertFalse(result_ts.is_wound)
        self.assertEqual(result_ts.requester, "app_younger")
        self.assertEqual(result_ts.holder, "app_older")
        self.assertEqual(result_ts.resource, "GPS")
        self.assertIn("is younger than", result_ts.reason)
        self.assertIn("must wait", result_ts.reason)
        self.assertIsInstance(result_ts.resolution_timestamp, float)

        # 2b. Younger/lower by priority
        result_p = self.cm.resolve_conflict(
            requester="app_low_prio",
            requester_priority=1,
            holder="app_high_prio",
            holder_priority=4,
            resource="MIC"
        )
        self.assertEqual(result_p.decision, ConflictDecision.WAIT)
        self.assertTrue(result_p.is_wait)
        self.assertEqual(result_p.requester, "app_low_prio")
        self.assertEqual(result_p.holder, "app_high_prio")
        self.assertEqual(result_p.resource, "MIC")
        self.assertIn("must wait", result_p.reason)

    def test_equal_priority_and_timestamp(self):
        """Test Case 3: Equal priority and equal timestamp -> WAIT deterministically."""
        # Both priority and timestamp identical
        result = self.cm.resolve_conflict(
            requester="app_req",
            requester_priority=3,
            requester_timestamp=500.0,
            holder="app_hold",
            holder_priority=3,
            holder_timestamp=500.0,
            resource="DB_LOCK"
        )
        self.assertEqual(result.decision, ConflictDecision.WAIT)
        self.assertTrue(result.is_wait)
        self.assertFalse(result.is_wound)
        self.assertEqual(result.requester, "app_req")
        self.assertEqual(result.holder, "app_hold")
        self.assertEqual(result.resource, "DB_LOCK")
        self.assertIn("equal precedence", result.reason)
        self.assertIn("must wait", result.reason)

        # Determinism check: swapping order or repeating yields identical outcome
        result_rev = self.cm.resolve_conflict(
            requester="app_hold",
            requester_priority=3,
            requester_timestamp=500.0,
            holder="app_req",
            holder_priority=3,
            holder_timestamp=500.0,
            resource="DB_LOCK"
        )
        self.assertEqual(result_rev.decision, ConflictDecision.WAIT)
        self.assertTrue(result_rev.is_wait)

    def test_no_conflict_scenarios(self):
        """Test Case 4: No conflict situations produce NO_CONFLICT."""
        # 4a. Resource is None
        r1 = self.cm.resolve_conflict(requester="app_1", holder="app_2", resource=None)
        self.assertEqual(r1.decision, ConflictDecision.NO_CONFLICT)
        self.assertTrue(r1.is_no_conflict)
        self.assertIn("No requested resource", r1.reason)

        # 4b. Resource is free (no current holder)
        r2 = self.cm.resolve_conflict(requester="app_1", holder=None, resource="GPS")
        self.assertEqual(r2.decision, ConflictDecision.NO_CONFLICT)
        self.assertTrue(r2.is_no_conflict)
        self.assertIn("resource is free", r2.reason)

        # 4c. Requester already holds the resource
        r3 = self.cm.resolve_conflict(requester="app_1", holder="app_1", resource="GPS")
        self.assertEqual(r3.decision, ConflictDecision.NO_CONFLICT)
        self.assertTrue(r3.is_no_conflict)
        self.assertIn("already holds", r3.reason)

        # 4d. Holder App does not hold the requested resource
        holder_app = App(app_id="app_h", name="Holder", held_resources={"MIC"})
        requester_app = App(app_id="app_r", name="Requester", priority=3)
        r4 = self.cm.resolve_conflict(requester=requester_app, holder=holder_app, resource="GPS")
        self.assertEqual(r4.decision, ConflictDecision.NO_CONFLICT)
        self.assertTrue(r4.is_no_conflict)
        self.assertIn("does not hold", r4.reason)

        # 4e. Resource object has available capacity
        res_obj = Resource(resource_id="FILE_LOCK", name="File", capacity=2, available_units=1)
        r5 = self.cm.resolve_conflict(requester="app_1", holder="app_2", resource=res_obj)
        self.assertEqual(r5.decision, ConflictDecision.NO_CONFLICT)
        self.assertTrue(r5.is_no_conflict)

    def test_structured_results_contents(self):
        """Verify structured ConflictResult contains all required fields:
        decision, requester, holder, resource, reason, resolution timestamp.
        """
        fixed_ts = 1700000000.0
        result = self.cm.resolve_conflict(
            requester="app_older",
            requester_priority=4,
            holder="app_younger",
            holder_priority=1,
            resource="GPS",
            resolution_timestamp=fixed_ts
        )

        # Attribute access
        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)
        self.assertEqual(result.requester, "app_older")
        self.assertEqual(result.holder, "app_younger")
        self.assertEqual(result.resource, "GPS")
        self.assertIn("Holder wounded", result.reason)
        self.assertEqual(result.resolution_timestamp, fixed_ts)

        # Dictionary-style key access
        self.assertEqual(result["decision"], "WOUND_HOLDER")
        self.assertEqual(result["requester"], "app_older")
        self.assertEqual(result["holder"], "app_younger")
        self.assertEqual(result["resource"], "GPS")
        self.assertEqual(result["reason"], result.reason)
        self.assertEqual(result["resolution_timestamp"], fixed_ts)
        self.assertEqual(result["resolution timestamp"], fixed_ts)

        # Serialization to dictionary
        d = result.to_dict()
        self.assertIn("decision", d)
        self.assertIn("requester", d)
        self.assertIn("holder", d)
        self.assertIn("resource", d)
        self.assertIn("reason", d)
        self.assertIn("resolution_timestamp", d)
        self.assertEqual(d["decision"], "WOUND_HOLDER")
        self.assertEqual(d["requester"], "app_older")
        self.assertEqual(d["holder"], "app_younger")
        self.assertEqual(d["resource"], "GPS")
        self.assertEqual(d["resolution_timestamp"], fixed_ts)

    def test_resource_manager_release_interface(self):
        """Conflict Manager may request resource release through the Resource Manager interface."""
        rm = ResourceManager()
        rm.create_default_resources()

        holder_app = App(app_id="holder_1", name="YoungApp", priority=1, last_access_time=200.0)
        requester_app = App(app_id="req_1", name="OldApp", priority=4, last_access_time=100.0)

        # Holder acquires GPS
        acquired = rm.acquire_resource(holder_app, "GPS")
        self.assertTrue(acquired)
        self.assertIn("GPS", holder_app.held_resources)
        gps = rm.get_resource("GPS")
        self.assertIn("holder_1", gps.holders)

        # Conflict Manager resolves conflict and requests release through ResourceManager interface
        cm = ConflictManager(resource_manager=rm)
        result = cm.resolve_conflict(
            requester=requester_app,
            holder=holder_app,
            resource="GPS",
            request_release=True
        )

        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)
        self.assertTrue(result.resource_released)

        # Verify GPS was released through ResourceManager interface
        self.assertNotIn("GPS", holder_app.held_resources)
        self.assertNotIn("holder_1", gps.holders)
        self.assertEqual(gps.available_units, 1)

    def test_conflict_manager_never_manipulates_memory_logic(self):
        """Conflict Manager must never directly manipulate unrelated memory logic."""
        mem_mgr = MemoryManager(total_memory=1024)
        app1 = App(app_id="app_1", name="App1", memory_footprint=200, reference_bit=1, held_resources={"GPS"})
        app2 = App(app_id="app_2", name="App2", memory_footprint=300, reference_bit=1)
        mem_mgr.register_app(app1)
        mem_mgr.register_app(app2)

        initial_used_memory = mem_mgr.get_used_memory()
        initial_clock_hand = mem_mgr.clock_hand
        initial_pressure = mem_mgr.get_pressure_level()

        cm = ConflictManager()
        # Resolve conflict between app1 and app2
        result = cm.resolve_conflict(
            requester=app2,
            requester_priority=5,
            holder=app1,
            holder_priority=1,
            resource="GPS"
        )
        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)

        # Verify memory manager state is completely unchanged
        self.assertEqual(mem_mgr.get_used_memory(), initial_used_memory)
        self.assertEqual(mem_mgr.clock_hand, initial_clock_hand)
        self.assertEqual(mem_mgr.get_pressure_level(), initial_pressure)
        self.assertEqual(app1.memory_footprint, 200)
        self.assertEqual(app2.memory_footprint, 300)
        self.assertFalse(app1.is_evicted)
        self.assertFalse(app2.is_evicted)

    def test_wound_wait_determinism(self):
        """Wound-Wait evaluation must be strictly deterministic across repeated runs."""
        for _ in range(50):
            res_wound = self.cm.resolve_conflict("app_a", 5, "app_b", 1, "GPS")
            self.assertEqual(res_wound.decision, ConflictDecision.WOUND_HOLDER)

            res_wait = self.cm.resolve_conflict("app_a", 1, "app_b", 5, "GPS")
            self.assertEqual(res_wait.decision, ConflictDecision.WAIT)

            res_eq = self.cm.resolve_conflict(
                requester="app_a",
                requester_priority=2,
                requester_timestamp=100.0,
                holder="app_b",
                holder_priority=2,
                holder_timestamp=100.0,
                resource="GPS"
            )
            self.assertEqual(res_eq.decision, ConflictDecision.WAIT)


if __name__ == "__main__":
    unittest.main()

