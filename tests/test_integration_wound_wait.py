"""Integration test: Conflict Manager ↔ RAAE Engine ↔ Resource Manager.

Scenario
--------
App A (Camera) holds the Camera resource.
App B becomes an eviction candidate (Clock selection).

The workflow under test:
  Clock selects candidate (App B)
  → RAAE checks candidate resources
  → conflict detected
  → Conflict Manager applies Wound-Wait
  → holder is wounded OR candidate waits (verified by priority/timestamp)
  → resources are safely released when appropriate
  → RAAE continues or delays eviction

Persistence
-----------
All conflict events are persisted through the DB repository layer
(ConflictLogRepository via DatabaseManager).

FK compliance
-------------
The ConflictLog table has foreign key constraints on waiting_app_id → Apps,
blocking_app_id → Apps, and resource_id → Resources.  Every test that triggers
DB-backed conflict logging must therefore:
  • save the participating apps via db.save_app()
  • save the participating resource via db.save_resource()
before any conflict event is persisted.

Simulation
----------
10 ticks are run without crashes to validate stability.
"""

import time
import unittest

from backend.models.app import App, AppState
from backend.models.resource import Resource
from backend.resource_manager.resource_manager import ResourceManager
from backend.raae_engine.conflict_manager import ResourceConflictManager
from backend.raae_engine.raae_engine import RAAEEngine
from backend.raae_engine.models import (
    ConflictStatus,
    EvictionDecisionType,
)
from backend.conflict_manager.conflict_manager import ConflictDecision
from database.db import DatabaseManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CAMERA_ID = "CAMERA"


def _make_resource_manager() -> ResourceManager:
    """Create a ResourceManager pre-populated with a CAMERA resource."""
    rm = ResourceManager()
    camera = Resource(resource_id=CAMERA_ID, name="Camera Sensor", capacity=1)
    rm.register_resource(camera)
    return rm


def _make_db() -> DatabaseManager:
    """Create an in-memory DatabaseManager (schema initialised automatically)."""
    return DatabaseManager(":memory:")


def _save_resource_to_db(db: DatabaseManager, rm: ResourceManager, resource_id: str) -> None:
    """Persist a resource from the ResourceManager into the DB (satisfies FK constraint)."""
    res = rm.get_resource(resource_id)
    if res is not None:
        db.save_resource(res)


def _build_engine(rm: ResourceManager, registered_apps: dict, db: DatabaseManager):
    """Wire ResourceConflictManager → RAAEEngine with DB persistence."""
    rcm = ResourceConflictManager(
        resource_manager=rm,
        registered_apps=registered_apps,
        db=db,
    )
    return RAAEEngine(conflict_manager=rcm)


# ---------------------------------------------------------------------------
# Main integration test suite
# ---------------------------------------------------------------------------

class TestWoundWaitIntegration(unittest.TestCase):
    """Integration: Conflict Manager + RAAE Engine + Resource Manager."""

    def setUp(self):
        self.db = _make_db()
        self.rm = _make_resource_manager()

        # App A – holds Camera, HIGH priority
        # Older timestamp (lower value) → wins on timestamp tie-break too.
        self.app_a = App(
            app_id="app_a",
            name="CameraApp",
            priority=5,
            state=AppState.FOREGROUND,
            memory_footprint=200,
            last_access_time=1_000_000.0,
        )

        # App B – eviction candidate, LOWER priority
        self.app_b = App(
            app_id="app_b",
            name="BackgroundSync",
            priority=2,
            state=AppState.BACKGROUND,
            memory_footprint=100,
            last_access_time=1_000_100.0,
        )

        self.registered_apps = {
            "app_a": self.app_a,
            "app_b": self.app_b,
        }

        # App A acquires Camera
        acquired = self.rm.acquire_resource(self.app_a, CAMERA_ID)
        self.assertTrue(acquired, "App A must acquire CAMERA before tests run.")

        # Persist both apps and the CAMERA resource in DB (satisfies FK constraints)
        self.db.save_app(self.app_a)
        self.db.save_app(self.app_b)
        _save_resource_to_db(self.db, self.rm, CAMERA_ID)

    # ------------------------------------------------------------------
    # Scenario 1 – App B (lower priority) is the eviction candidate
    #              and waits in the Camera waiting queue.
    #              Expected: Wound-Wait → WAIT (App B < App A in priority).
    # ------------------------------------------------------------------

    def test_wound_wait_lower_priority_candidate_waits(self):
        """App B (prio=2) cannot wound App A (prio=5) – must WAIT."""
        # Put App B on the waiting queue for CAMERA
        self.rm.acquire_resource(self.app_b, CAMERA_ID)
        camera = self.rm.get_resource(CAMERA_ID)
        self.assertIn("app_b", camera.waiting_queue)

        engine = _build_engine(self.rm, self.registered_apps, self.db)
        result = engine.orchestrate_safe_eviction(self.app_b)

        # RAAE must decide NOT to evict – candidate should wait
        self.assertNotEqual(
            result.decision_type, EvictionDecisionType.SAFE_TO_EVICT,
            "Lower-priority candidate should NOT be safe to evict when holder outranks it."
        )
        self.assertIn(
            result.decision_type,
            (EvictionDecisionType.WAIT, EvictionDecisionType.NOT_SAFE,
             EvictionDecisionType.RESOLVE_REQUIRED),
        )

        # Conflict was logged to DB
        conflicts = self.db.conflict_log.get_conflicts_by_resource(CAMERA_ID)
        self.assertGreaterEqual(len(conflicts), 1,
                                "Conflict event must be persisted in ConflictLog.")

    # ------------------------------------------------------------------
    # Scenario 2 – App B has HIGHER priority than App A.
    #              Expected: Wound-Wait → WOUND_HOLDER (App A wounded).
    # ------------------------------------------------------------------

    def test_wound_wait_higher_priority_candidate_wounds_holder(self):
        """App B (prio=10) outranks App A (prio=5) – should wound the holder."""
        self.app_b.priority = 10  # Elevate App B above App A
        self.registered_apps["app_b"] = self.app_b

        # Update App B in DB with new priority
        self.db.save_app(self.app_b)

        # Put App B in Camera waiting queue
        self.rm.acquire_resource(self.app_b, CAMERA_ID)
        camera = self.rm.get_resource(CAMERA_ID)
        self.assertIn("app_b", camera.waiting_queue)

        engine = _build_engine(self.rm, self.registered_apps, self.db)
        result = engine.orchestrate_safe_eviction(self.app_b)

        # After wounding the holder, Camera must be released from App A.
        camera = self.rm.get_resource(CAMERA_ID)
        self.assertNotIn(
            "app_a", camera.holders,
            "After wounding, App A must no longer hold CAMERA."
        )

        # Conflict event persisted
        conflicts = self.db.conflict_log.get_conflicts_by_resource(CAMERA_ID)
        self.assertGreaterEqual(len(conflicts), 1)
        wound_events = [c for c in conflicts if c.get("resolved_by") == "WOUND"]
        self.assertGreaterEqual(len(wound_events), 1, "At least one WOUND event must be persisted.")

    # ------------------------------------------------------------------
    # Scenario 3 – Direct Wound-Wait resolution (raw ConflictManager)
    # ------------------------------------------------------------------

    def test_direct_wound_wait_priority_based(self):
        """Direct Wound-Wait: higher priority candidate wounds lower priority holder."""
        from backend.conflict_manager.conflict_manager import ConflictManager

        db = _make_db()

        high_b = App(app_id="app_b_high", name="HighPrioApp", priority=9,
                     last_access_time=1_000_200.0)
        low_a = App(app_id="app_a_low", name="LowPrioHolder", priority=2,
                    held_resources={"CAMERA"}, last_access_time=1_000_000.0)

        # Register apps and resource in DB before conflict logging
        db.save_app(high_b)
        db.save_app(low_a)
        camera_res = Resource(resource_id="CAMERA", name="Camera Sensor", capacity=1)
        db.save_resource(camera_res)

        cm = ConflictManager(db=db)
        result = cm.resolve_conflict(
            requester=high_b,
            holder=low_a,
            resource="CAMERA",
        )
        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)
        self.assertTrue(result.is_wound)

        conflicts = db.conflict_log.get_conflicts_by_resource("CAMERA")
        self.assertGreaterEqual(len(conflicts), 1)

    def test_direct_wound_wait_timestamp_based(self):
        """Direct Wound-Wait: older requester (earlier timestamp) wounds younger holder."""
        from backend.conflict_manager.conflict_manager import ConflictManager

        db = _make_db()

        # Register apps and resource
        older = App(app_id="app_older", name="OlderApp", priority=5,
                    last_access_time=1_000_000.0)
        younger = App(app_id="app_younger", name="YoungerApp", priority=5,
                      last_access_time=2_000_000.0)
        db.save_app(older)
        db.save_app(younger)
        camera_res = Resource(resource_id="CAMERA", name="Camera Sensor", capacity=1)
        db.save_resource(camera_res)

        cm = ConflictManager(db=db)
        result = cm.resolve_conflict(
            requester="app_older",
            requester_priority=5,
            requester_timestamp=1_000_000.0,
            holder="app_younger",
            holder_priority=5,
            holder_timestamp=2_000_000.0,
            resource="CAMERA",
        )
        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)
        self.assertTrue(result.is_wound)
        self.assertIn("older than", result.reason)

    def test_direct_wound_wait_lower_priority_waits(self):
        """Direct Wound-Wait: lower priority requester must wait for higher holder."""
        from backend.conflict_manager.conflict_manager import ConflictManager

        db = _make_db()

        low = App(app_id="app_low", name="LowApp", priority=1,
                  last_access_time=1_000_000.0)
        high = App(app_id="app_high", name="HighApp", priority=5,
                   last_access_time=1_000_000.0)
        db.save_app(low)
        db.save_app(high)
        camera_res = Resource(resource_id="CAMERA", name="Camera Sensor", capacity=1)
        db.save_resource(camera_res)

        cm = ConflictManager(db=db)
        result = cm.resolve_conflict(
            requester="app_low",
            requester_priority=1,
            holder="app_high",
            holder_priority=5,
            resource="CAMERA",
        )
        self.assertEqual(result.decision, ConflictDecision.WAIT)
        self.assertTrue(result.is_wait)
        self.assertIn("must wait", result.reason)

    # ------------------------------------------------------------------
    # Scenario 4 – Resource is safely released via ResourceManager interface
    # ------------------------------------------------------------------

    def test_resource_safely_released_through_resource_manager(self):
        """Conflict Manager requests release via ResourceManager – Camera freed."""
        from backend.conflict_manager.conflict_manager import ConflictManager

        cm = ConflictManager(resource_manager=self.rm, db=self.db)

        # High-priority requester wounds App A (holder of Camera)
        requester = App(app_id="req_high", name="HighPrioRequester", priority=10)
        self.db.save_app(requester)  # must be in DB for FK

        result = cm.resolve_conflict(
            requester=requester,
            holder=self.app_a,
            resource=CAMERA_ID,
            request_release=True,
            registered_apps=self.registered_apps,
        )

        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)
        self.assertTrue(result.resource_released, "Resource must be marked as released.")

        # Verify Camera state through ResourceManager
        camera = self.rm.get_resource(CAMERA_ID)
        self.assertNotIn("app_a", camera.holders)
        self.assertEqual(camera.available_units, 1)

    # ------------------------------------------------------------------
    # Scenario 5 – No conflict when candidate holds no resources
    # ------------------------------------------------------------------

    def test_no_conflict_when_resource_free(self):
        """RAAE safely evicts a candidate that holds no resources."""
        free_app = App(
            app_id="app_free",
            name="FreeApp",
            priority=3,
            state=AppState.BACKGROUND,
        )
        self.registered_apps["app_free"] = free_app
        self.db.save_app(free_app)

        engine = _build_engine(self.rm, self.registered_apps, self.db)
        result = engine.orchestrate_safe_eviction(free_app)

        self.assertEqual(result.decision_type, EvictionDecisionType.SAFE_TO_EVICT)
        self.assertTrue(result.can_evict)

    # ------------------------------------------------------------------
    # Scenario 6 – 10-tick simulation without crashes
    # ------------------------------------------------------------------

    def test_10_tick_simulation_no_crash(self):
        """Run 10 simulation ticks; RAAE must not crash on any tick."""
        db = _make_db()
        rm = _make_resource_manager()

        # Stable holder for the full simulation
        holder = App(
            app_id="sim_holder",
            name="CameraHolder",
            priority=5,
            state=AppState.FOREGROUND,
            memory_footprint=150,
            last_access_time=time.time() - 1000,
        )
        rm.acquire_resource(holder, CAMERA_ID)
        db.save_app(holder)
        _save_resource_to_db(db, rm, CAMERA_ID)

        tick_results = []

        for tick in range(10):
            # Each tick: a fresh candidate with varying priority
            candidate_priority = (tick % 7) + 1   # cycles 1..7
            candidate_ts = time.time() + tick * 50

            candidate = App(
                app_id=f"sim_cand_{tick}",
                name=f"CandidateApp_{tick}",
                priority=candidate_priority,
                state=AppState.BACKGROUND,
                memory_footprint=50,
                last_access_time=candidate_ts,
            )
            # Must persist candidate in DB before any conflict is logged
            db.save_app(candidate)

            reg_apps = {
                "sim_holder": holder,
                candidate.app_id: candidate,
            }

            # Put candidate in Camera waiting queue to trigger conflict path
            camera = rm.get_resource(CAMERA_ID)
            if candidate.app_id not in camera.waiting_queue:
                camera.waiting_queue.append(candidate.app_id)
            candidate.change_state(AppState.WAITING)

            rcm = ResourceConflictManager(
                resource_manager=rm,
                registered_apps=reg_apps,
                db=db,
            )
            engine = RAAEEngine(conflict_manager=rcm)

            try:
                result = engine.orchestrate_safe_eviction(candidate)
                tick_results.append({
                    "tick": tick,
                    "candidate": candidate.app_id,
                    "priority": candidate_priority,
                    "decision": result.decision_type.value,
                    "can_evict": result.can_evict,
                })
            except Exception as exc:  # pragma: no cover
                self.fail(f"Tick {tick} raised an unexpected exception: {exc}")
            finally:
                # Clean up waiting queue for next tick (so holder can participate next round)
                if candidate.app_id in camera.waiting_queue:
                    camera.waiting_queue.remove(candidate.app_id)
                # If holder was wounded and lost Camera, re-acquire for next tick
                if holder.app_id not in camera.holders and camera.available_units > 0:
                    rm.acquire_resource(holder, CAMERA_ID)

        # All 10 ticks must have produced a result
        self.assertEqual(len(tick_results), 10, "All 10 ticks must complete without crash.")

        print("\n[Simulation Results – 10 Ticks]")
        for r in tick_results:
            print(f"  Tick {r['tick']:02d} | cand={r['candidate']} prio={r['priority']:2d} "
                  f"→ {r['decision']:25s} can_evict={r['can_evict']}")

    # ------------------------------------------------------------------
    # Scenario 7 – DB persistence: conflict rows are stored correctly
    # ------------------------------------------------------------------

    def test_conflict_events_persisted_in_db(self):
        """All conflict events must be persisted through the DB repository layer."""
        from backend.conflict_manager.conflict_manager import ConflictManager

        db = _make_db()

        # Pre-register apps and resource in DB (FK compliance)
        holder = App(app_id="holder_steady", name="SteadyHolder", priority=3,
                     last_access_time=1_000_000.0)
        db.save_app(holder)
        camera_res = Resource(resource_id="CAMERA", name="Camera Sensor", capacity=1)
        db.save_resource(camera_res)

        cm = ConflictManager(db=db)

        # Log 5 conflict events with different requesters
        for i in range(5):
            req = App(app_id=f"req_{i}", name=f"Requester_{i}", priority=i + 1,
                      last_access_time=1_000_000.0 + i)
            db.save_app(req)
            cm.resolve_conflict(
                requester=f"req_{i}",
                requester_priority=i + 1,
                holder="holder_steady",
                holder_priority=3,
                resource="CAMERA",
            )

        all_conflicts = db.conflict_log.get_all_conflicts(limit=20)
        self.assertGreaterEqual(len(all_conflicts), 1, "At least one conflict must be persisted.")

        camera_conflicts = db.conflict_log.get_conflicts_by_resource("CAMERA")
        self.assertGreaterEqual(len(camera_conflicts), 1)

        # Verify required fields are present and correct
        for row in camera_conflicts:
            self.assertIn("waiting_app_id", row)
            self.assertIn("blocking_app_id", row)
            self.assertIn("resource_id", row)
            self.assertIn("resolution_strategy", row)
            self.assertEqual(row["resource_id"], "CAMERA")
            self.assertEqual(row["resolution_strategy"], "WOUND_WAIT")

    # ------------------------------------------------------------------
    # Scenario 8 – RAAE continues after wound clears the holder
    # ------------------------------------------------------------------

    def test_raae_continues_eviction_after_wound(self):
        """After wounding App A, the RAAE Engine should process App B cleanly."""
        # Elevate App B's priority above App A
        self.app_b.priority = 8
        self.registered_apps["app_b"] = self.app_b
        self.db.save_app(self.app_b)

        # Transfer Camera from App A to App B
        self.rm.release_resource(self.app_a, CAMERA_ID, registered_apps=self.registered_apps)
        acquired = self.rm.acquire_resource(self.app_b, CAMERA_ID)
        self.assertTrue(acquired)

        engine = _build_engine(self.rm, self.registered_apps, self.db)
        result = engine.orchestrate_safe_eviction(self.app_b)

        # With no conflicting waiters, RAAE must return a valid (non-crashing) decision
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.decision_type)

    # ------------------------------------------------------------------
    # Scenario 9 – Wound-Wait is deterministic across repeated calls
    # ------------------------------------------------------------------

    def test_wound_wait_is_deterministic(self):
        """Same inputs must always produce the same Wound-Wait outcome."""
        from backend.conflict_manager.conflict_manager import ConflictManager

        cm = ConflictManager()  # No DB; no FK concerns

        for _ in range(20):
            result_wound = cm.resolve_conflict(
                requester="app_high", requester_priority=9,
                holder="app_low", holder_priority=2,
                resource="CAMERA",
            )
            self.assertEqual(result_wound.decision, ConflictDecision.WOUND_HOLDER)

            result_wait = cm.resolve_conflict(
                requester="app_low", requester_priority=2,
                holder="app_high", holder_priority=9,
                resource="CAMERA",
            )
            self.assertEqual(result_wait.decision, ConflictDecision.WAIT)

    # ------------------------------------------------------------------
    # Scenario 10 – ConflictResult fields are fully populated
    # ------------------------------------------------------------------

    def test_conflict_result_all_fields_populated(self):
        """ConflictResult must expose all required structured fields."""
        from backend.conflict_manager.conflict_manager import ConflictManager, ConflictResult

        cm = ConflictManager()  # No DB
        fixed_ts = 1_700_000_000.0

        result = cm.resolve_conflict(
            requester="app_b",
            requester_priority=10,
            holder="app_a",
            holder_priority=5,
            resource=CAMERA_ID,
            resolution_timestamp=fixed_ts,
        )

        self.assertIsInstance(result, ConflictResult)
        self.assertEqual(result.decision, ConflictDecision.WOUND_HOLDER)
        self.assertEqual(result.requester, "app_b")
        self.assertEqual(result.holder, "app_a")
        self.assertEqual(result.resource, CAMERA_ID)
        self.assertEqual(result.resolution_timestamp, fixed_ts)
        self.assertIsNotNone(result.reason)
        self.assertEqual(result.strategy, "WOUND_WAIT")

        # Serialisation
        d = result.to_dict()
        self.assertEqual(d["decision"], "WOUND_HOLDER")
        self.assertEqual(d["resource_id"], CAMERA_ID)
        self.assertEqual(d["resolution_timestamp"], fixed_ts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
