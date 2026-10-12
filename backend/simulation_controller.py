"""Simulation Controller — minimal orchestration layer for the RAAE UI.

Wires together the existing modules without modifying any of them:
  - MemoryManager      → RAM tracking, Clock algorithm, app registry
  - ResourceManager    → semaphore locking, waiting queues
  - WorkloadGenerator  → synthetic workload events
  - RAAEEngine         → safe-eviction orchestration with Wound-Wait
  - DatabaseManager    → persistence of apps, resources, conflict/eviction logs

The Streamlit UI calls this controller exclusively so no simulation logic
lives inside the UI layer itself.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.memory_manager.memory_manager import MemoryManager, MemoryPressureLevel
from backend.models.app import App, AppState
from backend.models.resource import Resource
from backend.raae_engine.conflict_manager import ResourceConflictManager
from backend.raae_engine.raae_engine import RAAEEngine
from backend.raae_engine.models import RAAEEngineResult, EvictionDecisionType
from backend.resource_manager.resource_manager import ResourceManager
from backend.workload_generator.workload_generator import WorkloadGenerator
from database.db import DatabaseManager


# ---------------------------------------------------------------------------
# Lightweight event log entry
# ---------------------------------------------------------------------------

@dataclass
class SimEvent:
    """A single timestamped simulation log entry shown in the UI."""
    kind: str          # "eviction" | "conflict" | "resource" | "info" | "error"
    message: str
    timestamp: float = field(default_factory=time.time)
    details: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# SimulationController
# ---------------------------------------------------------------------------

class SimulationController:
    """Orchestrates all RAAE subsystems for the Streamlit UI.

    Lifecycle
    ---------
    ``start()``  → marks simulation as running; wires RAAE engine
    ``stop()``   → pauses simulation (state is preserved)
    ``reset()``  → tears down and re-initialises everything

    UI-facing actions
    -----------------
    ``add_app()``              → create & register a new App
    ``trigger_memory_pressure()`` → drive RAM toward a target %
    ``request_resource()``     → acquire a semaphore lock for an app
    ``release_resource()``     → release a semaphore lock for an app
    ``run_clock_tick()``       → one Clock + RAAE + conflict resolution pass

    State queries (called by the UI to render panels)
    --------------------------------------------------
    ``get_ram_snapshot()``     → used/free/total/pressure
    ``get_active_apps()``      → list of App objects
    ``get_resource_locks()``   → per-resource holder map
    ``get_waiting_queues()``   → per-resource waiting list
    ``get_recent_events()``    → last N SimEvent entries
    ``is_running``             → bool property
    """

    DEFAULT_TOTAL_MEMORY = 1024   # MB
    DEFAULT_RESOURCES = ["GPS", "MIC", "AUDIO", "DB_LOCK", "FILE_LOCK", "BLUETOOTH"]
    MAX_EVENTS = 100              # ring-buffer size for the event log

    def __init__(self) -> None:
        self._running: bool = False
        self._events: List[SimEvent] = []
        self._init_subsystems()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _init_subsystems(self) -> None:
        """(Re-)initialise all subsystems from scratch."""
        self.db = DatabaseManager(":memory:")
        self.mem_mgr = MemoryManager(total_memory=self.DEFAULT_TOTAL_MEMORY)
        self.res_mgr = ResourceManager()
        self.res_mgr.create_default_resources()
        self.gen = WorkloadGenerator()
        self._rebuild_raae_engine()
        self._log(SimEvent("info", "Simulation initialised. Press Start to begin."))

    def _rebuild_raae_engine(self) -> None:
        """Reconstructs the RAAE engine whenever registered_apps changes."""
        rcm = ResourceConflictManager(
            resource_manager=self.res_mgr,
            registered_apps=self.mem_mgr.apps,
            db=self.db,
        )
        self.raae_engine = RAAEEngine(conflict_manager=rcm)

    def start(self) -> None:
        """Marks the simulation as running."""
        self._running = True
        self._rebuild_raae_engine()
        self._log(SimEvent("info", "▶ Simulation started."))

    def stop(self) -> None:
        """Pauses the simulation (state is preserved)."""
        self._running = False
        self._log(SimEvent("info", "⏹ Simulation stopped."))

    def reset(self) -> None:
        """Tears down and re-initialises everything."""
        self._running = False
        self._events.clear()
        self._init_subsystems()
        self._log(SimEvent("info", "↺ Simulation reset."))

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # UI Actions
    # ------------------------------------------------------------------

    def add_app(
        self,
        name: str,
        priority: int = 2,
        memory_footprint: int = 100,
        state: AppState = AppState.BACKGROUND,
    ) -> App:
        """Creates and registers a new application."""
        app_id = f"app_{uuid.uuid4().hex[:6]}"
        app = App(
            app_id=app_id,
            name=name.strip() or "Unnamed App",
            priority=int(priority),
            state=state,
            memory_footprint=int(memory_footprint),
            reference_bit=1,
        )
        self.mem_mgr.register_app(app)
        self.db.save_app(app)
        # Rebuild engine so new app is in registered_apps
        self._rebuild_raae_engine()
        self._log(SimEvent(
            "info",
            f"➕ App '{app.name}' (ID={app_id}) added — prio={priority}, mem={memory_footprint}MB.",
        ))
        return app

    def trigger_memory_pressure(self, target_pct: float = 85.0) -> int:
        """Drives RAM usage toward target_pct via WorkloadGenerator."""
        allocated = self.gen.trigger_memory_pressure(self.mem_mgr, target_percentage=target_pct)
        level = self.mem_mgr.get_pressure_level().value
        pct = self.mem_mgr.get_usage_percentage()
        self._log(SimEvent(
            "info",
            f"🔴 Memory pressure triggered → +{allocated}MB allocated. "
            f"Usage: {pct:.1f}% ({level})",
        ))
        return allocated

    def request_resource(self, app_id: str, resource_id: str) -> bool:
        """Attempts to acquire a resource lock for an app."""
        app = self.mem_mgr.apps.get(app_id)
        resource = self.res_mgr.get_resource(resource_id)
        if app is None:
            self._log(SimEvent("error", f"Request resource: app '{app_id}' not found."))
            return False
        if resource is None:
            self._log(SimEvent("error", f"Request resource: resource '{resource_id}' not registered."))
            return False

        # Ensure resource is in DB for FK compliance before RAAE may log conflicts
        self.db.save_resource(resource)
        self.db.save_app(app)

        success = self.res_mgr.acquire_resource(app, resource_id)
        status = "acquired ✅" if success else "queued / waiting ⏳"
        self._log(SimEvent(
            "resource",
            f"🔒 App '{app.name}' requested {resource_id}: {status}",
        ))
        return success

    def release_resource(self, app_id: str, resource_id: str) -> bool:
        """Releases a resource lock held by an app."""
        app = self.mem_mgr.apps.get(app_id)
        if app is None:
            self._log(SimEvent("error", f"Release resource: app '{app_id}' not found."))
            return False

        success = self.res_mgr.release_resource(
            app, resource_id, registered_apps=self.mem_mgr.apps
        )
        status = "released ✅" if success else "not held ⚠️"
        self._log(SimEvent(
            "resource",
            f"🔓 App '{app.name}' releasing {resource_id}: {status}",
        ))
        return success

    def run_clock_tick(self) -> Optional[RAAEEngineResult]:
        """One Clock + RAAE + conflict resolution pass.

        Returns the RAAEEngineResult (or None if no candidate found).
        Automatically persists results to DB.
        """
        if not self._running:
            self._log(SimEvent("error", "Simulation not running. Press Start first."))
            return None

        result, logs = self.mem_mgr.evaluate_clock_candidate_with_raae(self.raae_engine)
        if result is None:
            self._log(SimEvent("info", "⏱ Clock tick: no eviction candidate found."))
            return None

        candidate = result.candidate
        decision = result.decision_type.value
        reason = result.eviction_decision.reason

        kind = "eviction" if result.can_evict else "conflict"
        icon = "✅" if result.can_evict else "⚠️"
        self._log(SimEvent(
            kind,
            f"{icon} Clock tick → candidate='{candidate.name}' decision={decision}: {reason}",
            details={
                "candidate_id": candidate.app_id,
                "decision": decision,
                "reason": reason,
                "held_resources": [r.resource_id for r in result.held_resources],
                "conflict_status": result.conflict_decision.status.value,
            },
        ))

        # Persist eviction decision to DB
        if result.can_evict:
            self.mem_mgr.evict_app(candidate.app_id)
            self.db.save_app(candidate)
            try:
                self.db.eviction_log.log_eviction(
                    app_id=candidate.app_id,
                    algorithm="CLOCK+RAAE",
                    reason=reason,
                    lock_checked=bool(result.held_resources),
                    safe_release=True,
                    result=decision,
                )
            except Exception:
                pass  # FK error (app not yet in DB) — swallow quietly

        return result

    def run_random_event(self) -> Optional[str]:
        """Triggers a random workload event (allocate, release, state change, etc.)."""
        if not self.mem_mgr.get_active_apps():
            return None
        event = self.gen.generate_random_event(self.mem_mgr, self.res_mgr)
        if event:
            self._log(SimEvent("info", f"🎲 Random event: {event.event_type} on {event.app_id}"))
            return event.event_type
        return None

    # ------------------------------------------------------------------
    # State queries for the UI
    # ------------------------------------------------------------------

    def get_ram_snapshot(self) -> Dict[str, Any]:
        """Returns current RAM statistics."""
        return {
            "total_mb": self.mem_mgr.total_memory,
            "used_mb": self.mem_mgr.get_used_memory(),
            "free_mb": self.mem_mgr.get_free_memory(),
            "usage_pct": self.mem_mgr.get_usage_percentage(),
            "pressure": self.mem_mgr.get_pressure_level().value,
            "clock_hand": self.mem_mgr.clock_hand,
        }

    def get_active_apps(self) -> List[App]:
        """Returns all registered apps (active + evicted)."""
        return list(self.mem_mgr.apps.values())

    def get_resource_locks(self) -> List[Dict[str, Any]]:
        """Returns per-resource holder info for the Resource Locks panel."""
        rows = []
        for res in self.res_mgr.get_all_resources():
            rows.append({
                "resource_id": res.resource_id,
                "name": res.name,
                "capacity": res.capacity,
                "available": res.available_units,
                "status": res.status.value,
                "holders": list(res.holders.keys()),
                "holder_units": dict(res.holders),
            })
        return rows

    def get_waiting_queues(self) -> List[Dict[str, Any]]:
        """Returns per-resource waiting queue info."""
        rows = []
        for res in self.res_mgr.get_all_resources():
            if res.waiting_queue:
                rows.append({
                    "resource_id": res.resource_id,
                    "waiting": list(res.waiting_queue),
                    "queue_depth": len(res.waiting_queue),
                })
        return rows

    def get_recent_events(self, n: int = 20) -> List[SimEvent]:
        """Returns the last n simulation events (newest first)."""
        return list(reversed(self._events[-self.MAX_EVENTS:]))[:n]

    def get_available_resource_ids(self) -> List[str]:
        """Returns IDs of all registered resources."""
        return [r.resource_id for r in self.res_mgr.get_all_resources()]

    def get_app_ids(self) -> List[str]:
        """Returns all registered app IDs."""
        return list(self.mem_mgr.apps.keys())

    def get_conflict_log(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns recent conflict log entries from the DB."""
        try:
            return self.db.conflict_log.get_all_conflicts(limit=limit)
        except Exception:
            return []

    def get_eviction_log(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns recent eviction log entries from the DB."""
        try:
            return self.db.eviction_log.get_all_evictions(limit=limit)
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _log(self, event: SimEvent) -> None:
        self._events.append(event)
        # Keep ring-buffer bounded
        if len(self._events) > self.MAX_EVENTS:
            self._events.pop(0)
