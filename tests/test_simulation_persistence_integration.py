"""End-to-end tests integrating the DBMS layer with the simulation modules.

Covers the nine events the simulation must persist:

1. app creation
2. memory pressure
3. resource acquisition
4. resource release
5. eviction attempt
6. successful eviction
7. blocked / waiting eviction
8. Wound-Wait conflict
9. freeze incident

It also proves the invariant that matters most for an OS simulator: a database
failure may cost history, but must never corrupt in-memory simulation state.
"""

import unittest

from backend.conflict_manager import ConflictManager as WoundWaitConflictManager
from backend.memory_manager.memory_manager import MemoryManager
from backend.models.app import App
from backend.persistence_bridge import PersistenceBridge
from backend.raae_engine import (
    DatabaseEvictionPersistence,
    EvictionDecisionType,
    RAAEEngine,
    WoundWaitConflictAdapter,
)
from backend.resource_manager.resource_manager import ResourceManager
from backend.simulation_engine import (
    BaselineClockPolicy,
    SimulationConfig,
    SimulationEngine,
)
from database.db import DatabaseManager


class SimulationHarness(unittest.TestCase):
    """Builds a four-app simulation wired to a real database.

    One PersistenceBridge is created here and handed to every collaborator, so
    diagnostics collected in a failure test all land in the same place.
    """

    def setUp(self):
        self.db = DatabaseManager(db_path=":memory:")
        self.bridge = PersistenceBridge(self.db)

        self.memory = MemoryManager(total_memory=1000, db=self.bridge)
        self.resources = ResourceManager(db=self.bridge)
        self.resources.create_default_resources()

        # Four apps. Fitness outranks Maps, so when they contend for GPS the
        # waiter wounds the holder and the eviction is blocked.
        self.maps = self._add("app_maps", "Maps", priority=2, memory_footprint=300, ref=0)
        self.fitness = self._add("app_fitness", "Fitness", priority=5, memory_footprint=250, ref=0)
        self.music = self._add("app_music", "Music", priority=1, memory_footprint=200, ref=0)
        self.chat = self._add("app_chat", "Chat", priority=1, memory_footprint=100, ref=1)

    def tearDown(self):
        self.db.close()

    def _add(self, app_id, name, priority, memory_footprint, ref):
        app = App(
            app_id=app_id,
            name=name,
            priority=priority,
            memory_footprint=memory_footprint,
            reference_bit=ref,
        )
        self.memory.add_app(app)
        self.resources.register_app(app)
        return app

    def build_raae_engine(self):
        """Builds an engine whose conflict manager is the real Wound-Wait one."""
        conflict_manager = WoundWaitConflictAdapter(
            WoundWaitConflictManager(db=self.bridge),
            self.resources,
        )
        return RAAEEngine(
            conflict_manager,
            memory_manager=self.memory,
            resource_manager=self.resources,
            persistence=DatabaseEvictionPersistence(self.bridge),
        )

    def run_full_scenario(self):
        """Drives the scenario used by most assertions in this module.

        Returns:
            Tuple: the blocked, safe-released and uncontested eviction results.
        """
        # 3. Resource acquisition: GPS granted, second request blocked and queued.
        self.assertTrue(self.resources.acquire_resource(self.maps, "GPS"))
        self.assertFalse(self.resources.acquire_resource(self.fitness, "GPS"))
        # An uncontested acquisition, used for the safe-release case below.
        self.assertTrue(self.resources.acquire_resource(self.music, "Audio"))

        # 2. Memory pressure: allocate until the system reaches RED.
        self.assertTrue(self.memory.allocate_memory("app_chat", 50))
        self.assertTrue(self.memory.allocate_memory("app_maps", 100))

        engine = self.build_raae_engine()

        # 7 + 8. Maps holds a contested resource, so it is blocked from eviction
        # and the Wound-Wait evaluation is recorded.
        blocked = engine.execute_safe_eviction(self.maps)

        # 4. Releasing GPS hands it to the queued app and records the release.
        self.assertTrue(self.resources.release_resource(self.maps, "GPS"))

        # 5 + 6. Music holds an uncontested resource: safely released, then evicted.
        evicted = engine.execute_safe_eviction(self.music)

        # 6. Chat holds nothing: evicted outright.
        uncontested = engine.execute_safe_eviction(self.chat)

        return blocked, evicted, uncontested


class TestEndToEndEventPersistence(SimulationHarness):
    """The full nine-event scenario is persisted through the repository layer."""

    def setUp(self):
        super().setUp()
        self.blocked, self.evicted, self.uncontested = self.run_full_scenario()
        self.assertEqual(self.bridge.failures, [], "no write may fail on a healthy database")

    def test_at_least_three_apps_were_simulated(self):
        """Test that the scenario exercises the required three-app minimum."""
        self.assertGreaterEqual(len(self.memory.apps), 3)
        self.assertEqual(len(self.db.get_all_apps()), 4)

    def test_event_1_app_creation_is_persisted(self):
        """Test that every registered app reaches the Apps table."""
        stored = {app.app_id: app for app in self.db.get_all_apps()}

        self.assertEqual(stored["app_maps"].name, "Maps")
        self.assertEqual(stored["app_fitness"].name, "Fitness")
        self.assertEqual(stored["app_music"].name, "Music")
        self.assertEqual(stored["app_chat"].name, "Chat")

    def test_event_2_memory_pressure_is_persisted(self):
        """Test that memory pressure samples reach SystemEvents."""
        samples = self.db.get_all_pressure_samples()

        self.assertTrue(samples, "expected at least one memory pressure sample")
        self.assertTrue(all(s["event_type"] == "MEMORY_PRESSURE" for s in samples))
        self.assertTrue(
            any(s["pressure_level"] in ("ORANGE", "RED") for s in samples),
            f"expected a critical sample, got {[s['pressure_level'] for s in samples]}",
        )
        self.assertTrue(all(s["total_memory"] == 1000 for s in samples))

    def test_event_3_resource_acquisition_is_persisted(self):
        """Test that granted acquisitions reach both the ledger and the request log."""
        granted = self.db.lock_requests.get_requests_by_outcome("GRANTED")
        pairs = {(row["app_id"], row["resource_id"]) for row in granted}

        self.assertIn(("app_maps", "GPS"), pairs)
        self.assertIn(("app_music", "Audio"), pairs)
        # The queued app was handed GPS when Maps released it.
        self.assertIn(("app_fitness", "GPS"), pairs)

    def test_acquisition_ledger_matches_live_ownership(self):
        """Test that held ledger rows match the resources actually held."""
        held = {
            (row["app_id"], row["resource_id"])
            for row in self.db.resource_locks.get_active_locks()
        }

        self.assertEqual(held, {("app_fitness", "GPS")})
        self.assertEqual(self.resources.get_holder("GPS"), "app_fitness")

    def test_blocked_acquisition_is_persisted_without_a_lock(self):
        """Test that a queued request is recorded but holds no lock."""
        queued = self.db.lock_requests.get_requests_by_outcome("QUEUED")

        self.assertEqual(
            {(row["app_id"], row["resource_id"]) for row in queued},
            {("app_fitness", "GPS")},
        )

    def test_event_4_resource_release_is_persisted(self):
        """Test that a release retires the ledger row and logs the request."""
        released = self.db.lock_requests.get_requests_by_outcome("RELEASED")
        pairs = {(row["app_id"], row["resource_id"]) for row in released}

        self.assertIn(("app_maps", "GPS"), pairs)
        self.assertIn(("app_music", "Audio"), pairs)

        self.assertEqual(self.db.get_active_lock("app_maps", "GPS"), None)
        history = self.db.resource_locks.get_locks_by_app("app_maps")
        self.assertEqual(history[0]["status"], "RELEASED")
        self.assertIsNotNone(history[0]["released_at"])

    def test_event_5_eviction_attempt_is_persisted(self):
        """Test that every eviction decision reaches the EvictionLog."""
        logged = {row["app_id"] for row in self.db.eviction_log.get_all_evictions()}

        self.assertEqual(logged, {"app_maps", "app_music", "app_chat"})
        for row in self.db.eviction_log.get_all_evictions():
            self.assertEqual(row["algorithm"], "RAAE_CLOCK")
            self.assertTrue(row["reason"])

    def test_event_6_successful_eviction_is_persisted(self):
        """Test that a completed eviction is recorded with its safe-release flag."""
        music_logs = self.db.eviction_log.get_evictions_by_app("app_music")
        chat_logs = self.db.eviction_log.get_evictions_by_app("app_chat")

        self.assertEqual(music_logs[0]["result"], "EVICTED")
        self.assertEqual(music_logs[0]["safe_release"], 1)
        self.assertEqual(chat_logs[0]["result"], "EVICTED")
        self.assertEqual(chat_logs[0]["safe_release"], 1)

        # Only Music actually held something, so only it reports a release.
        self.assertEqual(self.evicted.released_resource_ids, ("Audio",))
        self.assertEqual(self.uncontested.released_resource_ids, ())

        # MemoryEvents carries the before/after footprint for each attempt.
        music_memory = self.db.memory_events.get_events_by_app("app_music")[0]
        self.assertEqual(music_memory["action"], "EVICT")
        self.assertEqual(music_memory["memory_before"], 200)
        self.assertEqual(music_memory["memory_after"], 0)

        # The evicted apps really are gone from the in-memory simulation too.
        self.assertTrue(self.music.is_evicted)
        self.assertTrue(self.chat.is_evicted)
        # Maps (400) and Fitness (250) remain; Music (200) and Chat (150) are gone.
        self.assertEqual(self.memory.get_used_memory(), 650)

    def test_event_7_blocked_eviction_is_persisted(self):
        """Test that a protected candidate records the outcome and keeps its resource."""
        self.assertEqual(self.blocked.decision_type, EvictionDecisionType.CONFLICT)
        self.assertEqual(self.blocked.eviction_event.result, "CONFLICT")
        self.assertFalse(self.maps.is_evicted)

        logs = self.db.eviction_log.get_evictions_by_app("app_maps")
        self.assertEqual(logs[0]["result"], "CONFLICT")
        self.assertEqual(logs[0]["safe_release"], 0)
        self.assertEqual(logs[0]["lock_checked"], 1)

        attempts = self.db.memory_events.get_events_by_app("app_maps")
        self.assertEqual(attempts[0]["action"], "EVICT_ATTEMPT")
        self.assertEqual(attempts[0]["memory_before"], 400)
        self.assertEqual(attempts[0]["memory_after"], 400)

    def test_event_8_wound_wait_conflict_is_persisted(self):
        """Test that the Wound-Wait evaluation reaches the ConflictLog."""
        conflicts = self.db.conflict_log.get_all_conflicts()

        self.assertTrue(conflicts, "expected a Wound-Wait conflict record")
        record = conflicts[0]
        self.assertEqual(record["resolution_strategy"], "WOUND_WAIT")
        self.assertEqual(record["resolved_by"], "WOUND")
        self.assertEqual(record["waiting_app_id"], "app_fitness")
        self.assertEqual(record["blocking_app_id"], "app_maps")
        self.assertEqual(record["resource_id"], "GPS")
        # The stored detail is the reason Wound-Wait itself produced.
        self.assertIn("wounded", record["details"].lower())

    def test_wounded_candidate_reports_the_wound_in_its_reason(self):
        """Test that the adapter's reason names the outcome it mapped."""
        self.assertIn("wounded", self.blocked.conflict_decision.reason.lower())
        self.assertEqual(
            self.blocked.conflict_decision.waiting_app_ids, ("app_fitness",)
        )

    def test_conflict_log_is_queryable_by_both_participants(self):
        """Test that a conflict can be found from either side of the contention."""
        self.assertTrue(self.db.conflict_log.get_conflicts_by_app("app_maps"))
        self.assertTrue(self.db.conflict_log.get_conflicts_by_app("app_fitness"))
        self.assertTrue(self.db.conflict_log.get_conflicts_by_resource("GPS"))

    def test_memory_actions_are_persisted(self):
        """Test that allocations recorded by the Memory Manager reach MemoryEvents."""
        actions = {
            (row["app_id"], row["action"]) for row in self.db.memory_events.get_all_events()
        }

        self.assertIn(("app_chat", "ALLOCATE"), actions)
        self.assertIn(("app_maps", "ALLOCATE"), actions)
        self.assertIn(("app_music", "EVICT"), actions)

    def test_memory_events_record_the_pressure_level_at_the_time(self):
        """Test that a memory event carries the pressure level it happened under."""
        rows = self.db.memory_events.get_events_by_app("app_chat")
        allocation = next(r for r in rows if r["action"] == "ALLOCATE")

        self.assertIn(allocation["pressure_level"], ("ORANGE", "RED"))
        self.assertEqual(allocation["memory_before"], 100)
        self.assertEqual(allocation["memory_after"], 150)

    def test_resource_state_stays_consistent_with_the_ledger(self):
        """Test that no lock survives an eviction that should have released it."""
        self.assertIsNone(self.db.get_active_lock("app_music", "Audio"))
        audio = self.db.get_resource("Audio")
        self.assertIsNone(audio.primary_holder_id)
        self.assertEqual(audio.status.value, "FREE")

        gps = self.db.get_resource("GPS")
        self.assertEqual(gps.primary_holder_id, "app_fitness")
        self.assertEqual(gps.available_units, 0)
        self.assertEqual(gps.status.value, "LOCKED")


class TestWoundWaitOutcomes(SimulationHarness):
    """Both Wound-Wait branches protect the contended resource and get logged."""

    def _contend_for_gps(self):
        self.assertTrue(self.resources.acquire_resource(self.maps, "GPS"))
        self.assertFalse(self.resources.acquire_resource(self.fitness, "GPS"))

    def test_outranking_waiter_wounds_the_candidate(self):
        """Test that a higher-priority waiter blocks the holder's eviction."""
        self._contend_for_gps()
        result = self.build_raae_engine().execute_safe_eviction(self.maps)

        self.assertEqual(result.decision_type, EvictionDecisionType.CONFLICT)
        self.assertFalse(self.maps.is_evicted)
        self.assertEqual(
            self.db.eviction_log.get_evictions_by_app("app_maps")[0]["result"], "CONFLICT"
        )
        self.assertEqual(self.db.conflict_log.get_all_conflicts()[0]["resolved_by"], "WOUND")

    def test_outranking_holder_is_protected_with_a_wait_outcome(self):
        """Test that the holder winning still cannot strand the waiter.

        Wound-Wait keeps the resource with the holder, but evicting it would
        freeze the waiter, so the candidate is reported as WAIT and protected.
        """
        self.maps.priority = 9
        self._contend_for_gps()
        result = self.build_raae_engine().execute_safe_eviction(self.maps)

        self.assertEqual(result.decision_type, EvictionDecisionType.WAIT)
        self.assertFalse(self.maps.is_evicted)
        self.assertEqual(
            self.db.eviction_log.get_evictions_by_app("app_maps")[0]["result"], "WAIT"
        )
        self.assertEqual(self.db.conflict_log.get_all_conflicts()[0]["resolved_by"], "WAIT")

    def test_uncontested_holder_is_released_then_evicted(self):
        """Test that a holder nobody is waiting for is released and evicted."""
        self._contend_for_gps()
        self.assertTrue(self.resources.release_resource(self.maps, "GPS"))
        self.assertTrue(self.resources.acquire_resource(self.music, "Audio"))

        result = self.build_raae_engine().execute_safe_eviction(self.music)

        self.assertEqual(result.decision_type, EvictionDecisionType.RELEASE_THEN_EVICT)
        self.assertTrue(self.music.is_evicted)
        self.assertEqual(self.db.eviction_log.get_evictions_by_app("app_music")[0]["result"], "EVICTED")
        self.assertEqual(self.db.conflict_log.get_all_conflicts(), [])


class TestFreezeIncidentPersistence(SimulationHarness):
    """A blind eviction strands a waiter, and that freeze is persisted."""

    def test_freeze_incident_is_persisted(self):
        """Test that an unsafe eviction of a lock holder records a freeze."""
        self.assertTrue(self.resources.acquire_resource(self.maps, "GPS"))
        self.assertFalse(self.resources.acquire_resource(self.fitness, "GPS"))

        # Acquiring a resource touches the app, so re-age Maps into the candidate
        # slot the way an idle background app would be.
        self.memory.update_reference_bit("app_maps", 0)
        self.assertTrue(self.memory.allocate_memory("app_maps", 150))

        engine = SimulationEngine(
            memory_manager=self.memory,
            resource_manager=self.resources,
            db_manager=self.bridge,
            policy=BaselineClockPolicy(),
            config=SimulationConfig(memory_pressure_threshold=75.0),
        )
        result = engine.tick()

        self.assertTrue(result.is_under_pressure)
        self.assertEqual(engine.metrics.freeze_incidents, 1)
        self.assertTrue(self.maps.is_evicted)

        conflicts = self.db.conflict_log.get_all_conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertIn("FREEZE", conflicts[0]["details"])
        self.assertEqual(conflicts[0]["resolution_strategy"], "NONE")
        self.assertEqual(conflicts[0]["waiting_app_id"], "app_fitness")
        self.assertEqual(conflicts[0]["blocking_app_id"], "app_maps")
        self.assertEqual(conflicts[0]["resource_id"], "GPS")

    def test_log_freeze_helper_writes_the_same_shape(self):
        """Test that the reusable freeze primitive matches the policy's record."""
        self.bridge.log_freeze(
            waiting_app_id="app_fitness",
            blocking_app_id="app_maps",
            resource_id="GPS",
            details="waiting app stranded by a blind eviction",
        )

        record = self.db.conflict_log.get_all_conflicts()[0]
        self.assertIn("FREEZE", record["details"])
        self.assertIn("stranded", record["details"])
        self.assertIsNone(record["resolved_by"])


class TestDatabaseFailureIsolation(SimulationHarness):
    """A broken database costs history, never simulation correctness."""

    def test_memory_manager_keeps_working_after_the_database_fails(self):
        """Test that app registration and allocation survive a dead database."""
        self.db.close()

        self.memory.add_app(App(app_id="app_extra", name="Extra", memory_footprint=50))
        self.assertIn("app_extra", self.memory.apps)

        self.assertTrue(self.memory.allocate_memory("app_extra", 40))
        self.assertEqual(self.memory.apps["app_extra"].memory_footprint, 90)

        self.assertGreater(self.bridge.failure_count, 0)

    def test_resource_manager_keeps_working_after_the_database_fails(self):
        """Test that acquisition, queueing and hand-off survive a dead database."""
        self.db.close()

        self.assertTrue(self.resources.acquire_resource(self.maps, "GPS"))
        self.assertFalse(self.resources.acquire_resource(self.fitness, "GPS"))
        self.assertEqual(self.resources.get_resource("GPS").waiting_queue, ["app_fitness"])

        self.assertTrue(self.resources.release_resource(self.maps, "GPS"))
        gps = self.resources.get_resource("GPS")
        self.assertEqual(gps.primary_holder_id, "app_fitness")
        self.assertEqual(gps.available_units, 0)
        self.assertEqual(gps.waiting_queue, [])

    def test_full_scenario_completes_with_the_database_closed(self):
        """Test that the whole eviction workflow runs to completion and stays correct."""
        self.resources.acquire_resource(self.maps, "GPS")
        self.resources.acquire_resource(self.fitness, "GPS")
        self.resources.acquire_resource(self.music, "Audio")

        engine = self.build_raae_engine()
        self.db.close()

        blocked = engine.execute_safe_eviction(self.maps)
        evicted = engine.execute_safe_eviction(self.music)
        uncontested = engine.execute_safe_eviction(self.chat)

        self.assertFalse(self.maps.is_evicted)
        self.assertTrue(self.music.is_evicted)
        self.assertTrue(self.chat.is_evicted)
        self.assertEqual(blocked.eviction_event.result, "CONFLICT")
        self.assertEqual(evicted.eviction_event.result, "EVICTED")
        self.assertEqual(uncontested.eviction_event.result, "EVICTED")

        # Persistence reported failure rather than returning an event id.
        self.assertIsNone(blocked.persisted_event_id)
        self.assertIsNone(evicted.persisted_event_id)
        self.assertGreater(self.bridge.failure_count, 0)

    def test_baseline_tick_completes_with_the_database_closed(self):
        """Test that the policy engine's transactional write cannot abort the tick."""
        engine = SimulationEngine(
            memory_manager=self.memory,
            resource_manager=self.resources,
            db_manager=self.bridge,
            policy=BaselineClockPolicy(),
            config=SimulationConfig(memory_pressure_threshold=75.0),
        )
        self.db.close()

        result = engine.tick()

        self.assertGreaterEqual(result.tick_number, 1)
        self.assertEqual(len(result.eviction_results), 1)
        self.assertTrue(result.eviction_results[0].evicted)
        self.assertIsNone(result.eviction_results[0].persisted_event_id)

    def test_failures_are_recorded_for_diagnostics(self):
        """Test that dropped writes are attributed to their operation."""
        self.db.close()
        self.memory.add_app(App(app_id="app_late", name="Late"))

        operations = {failure["operation"] for failure in self.bridge.failures}
        self.assertIn("save_app", operations)
        for failure in self.bridge.failures:
            self.assertIn("error", failure)
            self.assertTrue(failure["error"])

    def test_in_memory_state_is_untouched_by_a_failed_write(self):
        """Test that a rejected write leaves the simulated object exactly as decided."""
        self.db.close()
        self.memory.add_app(App(app_id="app_x", name="X", memory_footprint=100))

        app = self.memory.apps["app_x"]
        self.assertFalse(self.memory.db.save_app(app))
        self.assertEqual(app.memory_footprint, 100)
        self.assertTrue(app.is_active)
        self.assertIn("app_x", self.memory.apps)

    def test_simulation_bugs_are_not_swallowed(self):
        """Test that a genuine error in the caller's body still propagates."""
        with self.assertRaises(RuntimeError):
            with self.bridge.transaction():
                raise RuntimeError("simulation logic error")

    def test_transaction_rolls_back_and_re_raises_a_body_error(self):
        """Test that a failed transaction leaves no half-written rows behind."""
        self.db.create_app(App(app_id="app_tx", name="Transactional"))

        with self.assertRaises(RuntimeError):
            with self.bridge.transaction():
                self.bridge.save_app(App(app_id="app_ghost", name="Ghost"))
                raise RuntimeError("force rollback")

        self.assertIsNone(self.db.get_app("app_ghost"))

    def test_transaction_commits_normally_on_a_healthy_database(self):
        """Test that the tolerant transaction still commits real work."""
        with self.bridge.transaction():
            self.bridge.save_app(App(app_id="app_tx_ok", name="Transactional"))

        self.assertIsNotNone(self.db.get_app("app_tx_ok"))
        self.assertEqual(self.bridge.failures, [])


class TestPersistenceBridgeAdapter(unittest.TestCase):
    """Unit tests for the adapter itself."""

    def setUp(self):
        self.db = DatabaseManager(db_path=":memory:")
        self.db.create_app(App(app_id="app_a", name="Alpha"))
        self.db.create_app(App(app_id="app_b", name="Beta"))
        self.bridge = PersistenceBridge(self.db)

    def tearDown(self):
        self.db.close()

    def test_wrap_is_idempotent_and_leaves_none_alone(self):
        """Test that wrapping never double-wraps or invents a manager."""
        self.assertIsNone(PersistenceBridge.wrap(None))
        self.assertIs(self.bridge, PersistenceBridge.wrap(self.bridge))
        self.assertIsNot(self.db, PersistenceBridge.wrap(self.db))

    def test_bridge_forwards_unmanaged_attributes(self):
        """Test that the bridge is a usable stand-in for the DatabaseManager."""
        self.assertEqual(self.bridge.get_table_names(), self.db.get_table_names())
        self.assertIs(self.bridge.unwrapped, self.db)

    def test_bridge_guards_repository_attributes(self):
        """Test that a forwarded repository is replaced by a fault-tolerant proxy."""
        self.assertEqual(self.bridge.apps.__class__.__name__, "SafeRepository")
        self.assertEqual(self.bridge.resource_locks.__class__.__name__, "SafeRepository")

    def test_log_lock_request_grants_once(self):
        """Test that a repeated grant does not duplicate the ledger row."""
        self.bridge.log_lock_request("app_a", "GPS", "GRANTED")
        self.bridge.log_lock_request("app_a", "GPS", "GRANTED")

        self.assertEqual(len(self.db.resource_locks.get_locks_by_app("app_a")), 1)
        self.assertEqual(len(self.db.lock_requests.get_all_requests()), 2)

    def test_log_lock_request_release_without_a_lock_is_harmless(self):
        """Test that releasing an unheld resource records the event and nothing else."""
        self.bridge.log_lock_request("app_a", "GPS", "RELEASED")

        self.assertEqual(self.db.resource_locks.get_locks_by_app("app_a"), [])
        self.assertEqual(
            [row["outcome"] for row in self.db.lock_requests.get_all_requests()],
            ["RELEASED"],
        )

    def test_queued_request_creates_no_ledger_row(self):
        """Test that a blocked attempt is logged without taking ownership."""
        self.bridge.log_lock_request("app_a", "GPS", "GRANTED")
        self.bridge.log_lock_request("app_b", "GPS", "QUEUED")

        held = self.db.resource_locks.get_active_locks()
        self.assertEqual([row["app_id"] for row in held], ["app_a"])

    def test_unknown_outcome_is_rejected_by_the_repository(self):
        """Test that the outcome vocabulary is enforced by the schema."""
        import sqlite3

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.lock_requests.insert_lock_request("app_a", "GPS", "MAYBE")

    def test_bridge_contains_repository_failures(self):
        """Test that a write through a forwarded repository cannot raise."""
        self.db.close()

        self.assertIsNone(self.bridge.resource_locks.release_all_for_app("app_a"))
        self.assertEqual(
            [f["operation"] for f in self.bridge.failures],
            ["resource_locks.release_all_for_app"],
        )

    def test_bridge_without_a_database_is_inert(self):
        """Test that a detached bridge performs no work and raises nothing."""
        bridge = PersistenceBridge()

        self.assertFalse(bridge.save_app(App(app_id="x", name="X")))
        self.assertIsNone(bridge.log_memory_pressure("RED", 100, 100))
        self.assertIsNone(bridge.log_lock_request("x", "GPS", "GRANTED"))
        with bridge.transaction():
            pass
        self.assertEqual(bridge.failures, [])

    def test_bridge_rejects_unknown_attributes_without_a_database(self):
        """Test that a detached bridge reports missing attributes clearly."""
        bridge = PersistenceBridge()

        with self.assertRaises(AttributeError):
            _ = bridge.get_table_names

    def test_failure_operations_cover_both_hook_styles(self):
        """Test that a failed app write and lock write are both attributed."""
        self.db.close()

        self.bridge.save_app(App(app_id="app_a", name="Alpha"))
        self.bridge.log_lock_request("app_a", "GPS", "GRANTED")

        operations = [failure["operation"] for failure in self.bridge.failures]
        self.assertIn("save_app", operations)
        self.assertIn("log_lock_request", operations)


if __name__ == "__main__":
    unittest.main()