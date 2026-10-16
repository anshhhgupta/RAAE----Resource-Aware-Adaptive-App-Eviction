"""Step-wise simulation run for the Simulation page.

The page needs the *existing* backend to advance one tick at a time under the
user's control, so this module owns a single
:class:`~backend.simulation_engine.controller.SimulationController` for the length
of a run and drives it.

Why ``engine.tick()`` is called directly
-----------------------------------------
``SimulationController.run(ticks)`` is a ``for`` loop over
``SimulationEngine.tick()`` with no other logic, and it returns a summary that
throws away the per-tick evidence the decision timeline is built from —
``clock_inspections``, ``is_under_pressure`` and the before/after pressure levels.
Calling the engine's own ``tick()`` once per button press is therefore the same
backend code path with the detail preserved, and no eviction logic lives in this
module. ``tests/test_simulation_session.py`` pins that equivalence by comparing
this stepping against ``SimulationController.run()`` for the same workload.

Run lifecycle
-------------
Ticks execute synchronously when a control is pressed — there is no background
thread and no sleeping — so the status reflects what the session can actually do
next rather than pretending work is happening in the background:

``IDLE``    no run exists yet; ``Start`` builds one.
``RUNNING`` the run is live and the next tick executes on demand.
``PAUSED``  the run exists but ticking is withheld until ``Resume``.
``STOPPED`` the controller has been closed and no further tick is possible.

Resetting drops the in-memory run only. Every run writes to its own SQLite file,
which is kept and listed in the page's run history, so resetting can never
destroy the records a previous run produced.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.simulation_engine.controller import (
    SimulationController,
    SimulationResult,
    Workload,
)
from backend.simulation.simulation_state import SimulationState

STATUS_IDLE = "IDLE"
STATUS_RUNNING = "RUNNING"
STATUS_PAUSED = "PAUSED"
STATUS_STOPPED = "STOPPED"

POLICY_RAAE = "RAAE"
POLICY_BASELINE = "BASELINE_CLOCK"

# Timeline step states.
CONFIRMED = "confirmed"
NOT_EXECUTED = "not_executed"
NOT_APPLICABLE = "not_applicable"
UNAVAILABLE = "unavailable"


class SimulationError(RuntimeError):
    """Raised when a transition is refused because it makes no sense."""


# ──────────────────────────────────────────────────────────────
# Records
# ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TimelineStep:
    """One step of the eviction workflow and the evidence for its state.

    Attributes:
        number: 1-based position in the documented workflow.
        title: The step as the user sees it.
        status: One of ``confirmed``, ``not_executed``, ``not_applicable`` or
            ``unavailable``.
        detail: What the backend reported for this step.
        evidence: Where the claim comes from, when it can be named.
    """

    number: int
    title: str
    status: str
    detail: str
    evidence: str = ""


@dataclass
class TickRecord:
    """One executed tick and everything the backend reported for it."""

    tick_number: int
    status: str
    started_at: float
    finished_at: float
    tick_result: Any
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    persisted_rows: Dict[str, int] = field(default_factory=dict)
    read_errors: List[str] = field(default_factory=list)

    @property
    def eviction_results(self) -> List[Any]:
        """Returns the eviction results the engine produced this tick."""
        return list(getattr(self.tick_result, "eviction_results", []) or [])

    @property
    def clock_inspections(self) -> List[Dict[str, Any]]:
        """Returns the Clock / Second-Chance inspections performed this tick."""
        return list(getattr(self.tick_result, "clock_inspections", []) or [])


# ──────────────────────────────────────────────────────────────
# Session
# ──────────────────────────────────────────────────────────────

class SimulationSession:
    """A single interactive simulation run backed by the existing controller."""

    def __init__(
        self,
        policy: str,
        workload: Workload,
        db_path: Optional[str] = None,
    ) -> None:
        """Builds the controller and initialises the workload.

        Args:
            policy: ``RAAE`` or ``BASELINE_CLOCK``.
            workload: The workload specification to run.
            db_path: SQLite file for this run. When given it overrides the
                workload's own ``db_path``, so each run keeps its own log.
        """
        if policy not in (POLICY_RAAE, POLICY_BASELINE):
            raise SimulationError(f"Unknown policy '{policy}'.")

        self.policy = policy
        self.workload = workload
        if db_path:
            workload.db_path = db_path
        self.db_path = workload.db_path

        self.controller = SimulationController(policy=policy, workload=workload)
        self.engine = self.controller.engine
        self.status = STATUS_RUNNING
        self.started_at = time.time()
        self.records: List[TickRecord] = []

        # A view over the controller's own managers, used for the one helper the
        # controller does not expose: SimulationState.trigger_memory_pressure.
        # It holds the same MemoryManager and ResourceManager objects, so no
        # simulation state is duplicated, and it is built without a database so
        # the controller's persistence wiring is left exactly as it is.
        self._state_view = SimulationState(
            memory_manager=self.controller.mem_mgr,
            resource_manager=self.controller.res_mgr,
        )

    # ── State ────────────────────────────────────────────────

    @property
    def current_tick(self) -> int:
        """Returns the number of ticks the engine has executed."""
        return int(getattr(self.engine, "current_tick", 0))

    @property
    def metrics(self) -> Any:
        """Returns the engine's cumulative metrics object."""
        return self.engine.metrics

    @property
    def db(self) -> Any:
        """Returns the run's DatabaseManager."""
        return self.controller.db

    @property
    def is_running(self) -> bool:
        return self.status == STATUS_RUNNING

    @property
    def can_advance(self) -> bool:
        """Ticks may only run while the run is live and unpaused."""
        return self.status == STATUS_RUNNING

    @property
    def can_pause(self) -> bool:
        return self.status == STATUS_RUNNING

    @property
    def can_resume(self) -> bool:
        return self.status == STATUS_PAUSED

    @property
    def can_stop(self) -> bool:
        return self.status in (STATUS_RUNNING, STATUS_PAUSED)

    def memory_snapshot(self) -> Dict[str, Optional[Any]]:
        """Reads the current memory picture from the live MemoryManager."""
        manager = self.controller.mem_mgr
        try:
            total = int(manager.total_memory)
            used = int(manager.get_used_memory())
            free = int(manager.get_free_memory())
            pct: Optional[float] = round(float(manager.get_usage_percentage()), 2)
            pressure: Optional[str] = manager.get_pressure_level().value
            active = len(manager.get_active_apps())
            registered = len(manager.apps)
        except Exception:
            return {
                "total_mb": None,
                "used_mb": None,
                "available_mb": None,
                "utilization_pct": None,
                "pressure": None,
                "active_apps": None,
                "registered_apps": None,
            }
        return {
            "total_mb": total,
            "used_mb": used,
            "available_mb": free,
            "utilization_pct": pct,
            "pressure": pressure,
            "active_apps": active,
            "registered_apps": registered,
        }

    def app_rows(self) -> List[Dict[str, Any]]:
        """Returns the registry as plain rows for the page's table."""
        rows: List[Dict[str, Any]] = []
        for app in self.controller.mem_mgr.apps.values():
            rows.append(
                {
                    "app_id": app.app_id,
                    "name": app.name,
                    "priority": app.priority,
                    "state": app.state.value,
                    "memory": app.memory_footprint,
                    "reference_bit": app.reference_bit,
                    "held_resources": sorted(app.held_resources),
                    "status": app.status.value,
                    "is_evicted": app.is_evicted,
                }
            )
        return sorted(rows, key=lambda r: r["app_id"])

    # ── Transitions ──────────────────────────────────────────

    def pause(self) -> str:
        """Withholds further ticking until :meth:`resume`."""
        if not self.can_pause:
            raise SimulationError(f"Cannot pause a run that is {self.status}.")
        self.status = STATUS_PAUSED
        return f"Run paused at tick {self.current_tick}. No further ticks will execute until Resume."

    def resume(self) -> str:
        """Allows ticking to continue."""
        if not self.can_resume:
            raise SimulationError(f"Cannot resume a run that is {self.status}.")
        self.status = STATUS_RUNNING
        return f"Run resumed at tick {self.current_tick}."

    def stop(self) -> str:
        """Closes the controller. The run's database file is kept."""
        if not self.can_stop:
            raise SimulationError(f"Cannot stop a run that is {self.status}.")
        self.status = STATUS_STOPPED
        self.controller.close()
        return (
            f"Run stopped after {self.current_tick} tick(s). "
            f"The log stays at {self.db_path}."
        )

    # ── Execution ────────────────────────────────────────────

    def advance(self, count: int = 1) -> List[TickRecord]:
        """Executes *count* ticks through the engine.

        Raises:
            SimulationError: If the run is paused or stopped, or the count is
                not a positive integer. Refusing here is what prevents an
                invalid transition rather than silently doing nothing.
        """
        if not self.can_advance:
            raise SimulationError(
                f"Cannot advance ticks while the run is {self.status}."
            )
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise SimulationError(f"Tick count must be a positive integer, got {count!r}.")

        produced: List[TickRecord] = []
        for _ in range(count):
            record = self._tick_once()
            produced.append(record)
            self.records.append(record)
            if self._completed():
                break
        return produced

    def _completed(self) -> bool:
        """True once the workload's configured tick budget is spent."""
        budget = getattr(self.workload, "ticks", None)
        if not isinstance(budget, int) or budget <= 0:
            return False
        return self.current_tick >= budget

    def _tick_once(self) -> TickRecord:
        """Runs one engine tick and collects the evidence it produced."""
        started = time.time()
        tick_result = self.engine.tick()
        finished = time.time()

        record = TickRecord(
            tick_number=tick_result.tick_number,
            status=tick_result.status,
            started_at=started,
            finished_at=finished,
            tick_result=tick_result,
        )
        self._collect_database_evidence(record)
        return record

    def _collect_database_evidence(self, record: TickRecord) -> None:
        """Reads back the rows this tick wrote, so claims can be cross-checked.

        Every read is optional: a database failure is recorded on the record
        rather than raised, and the timeline then reports the step as
        unavailable instead of asserting something it could not verify.
        """
        db = getattr(self.controller, "db", None)
        if db is None:
            record.read_errors.append("No database is attached to this run.")
            return

        for result in record.eviction_results:
            app_id = getattr(result.candidate, "app_id", None)
            if not app_id:
                continue

            try:
                rows = db.conflict_log.get_conflicts_by_app(app_id) or []
            except Exception as error:  # noqa: BLE001 - optional evidence
                record.read_errors.append(
                    f"ConflictLog could not be read for {app_id}: {error}"
                )
                rows = []

            record.conflicts.extend(
                row
                for row in rows
                if record.started_at <= float(row.get("timestamp") or 0) <= record.finished_at
            )

            event_id = getattr(result, "persisted_event_id", None)
            if event_id is None:
                continue
            try:
                logged = db.eviction_log.get_evictions_by_app(app_id) or []
            except Exception as error:  # noqa: BLE001 - optional evidence
                record.read_errors.append(
                    f"EvictionLog could not be read for {app_id}: {error}"
                )
                continue
            for row in logged:
                if row.get("event_id") == event_id:
                    record.persisted_rows[app_id] = int(event_id)
                    break

    def trigger_pressure(self, target_percentage: float = 85.0) -> Dict[str, Any]:
        """Allocates memory until the live MemoryManager reaches the target.

        Delegates to ``SimulationState.trigger_memory_pressure`` over the
        controller's own managers.

        Returns:
            Dict[str, Any]: What was allocated, and what it did — including the
            case where the system was already above the target and nothing was
            allocated.
        """
        if self.status != STATUS_RUNNING:
            raise SimulationError(
                f"Cannot trigger memory pressure while the run is {self.status}."
            )
        before = self.controller.mem_mgr.get_used_memory()
        allocated = self._state_view.trigger_memory_pressure(target_percentage)
        after = self.controller.mem_mgr.get_used_memory()
        return {
            "allocated_mb": int(allocated),
            "used_before_mb": int(before),
            "used_after_mb": int(after),
            "target_percentage": float(target_percentage),
            "utilization_pct": round(float(self.controller.mem_mgr.get_usage_percentage()), 2),
            "message": (
                f"Allocated {allocated} MB across active apps; usage is now "
                f"{self.controller.mem_mgr.get_usage_percentage():.1f}% "
                f"({after} MB of {self.controller.mem_mgr.total_memory} MB)."
                if allocated
                else (
                    f"No allocation made: the system is already at or above "
                    f"{target_percentage:.0f}% ({self.controller.mem_mgr.get_usage_percentage():.1f}%)."
                )
            ),
        }

    # ── Reporting ────────────────────────────────────────────

    def last_record(self) -> Optional[TickRecord]:
        """Returns the most recent executed tick, or None."""
        return self.records[-1] if self.records else None

    def last_decision(self) -> Optional[Dict[str, Any]]:
        """Returns the most recent eviction decision the backend produced."""
        for record in reversed(self.records):
            for result in record.eviction_results:
                candidate = result.candidate
                return {
                    "tick": record.tick_number,
                    "app_id": candidate.app_id,
                    "app_name": candidate.name,
                    "policy": result.policy_type.value,
                    "evicted": bool(result.evicted),
                    "deferred": not bool(result.evicted),
                    "memory_freed": int(getattr(result, "memory_freed", 0) or 0),
                    "safe_release": bool(getattr(result, "safe_release_performed", False)),
                    "released_resources": list(getattr(result, "released_resource_ids", ()) or ()),
                    "conflict_detected": bool(getattr(result, "conflict_detected", False)),
                    "waiting_apps": list(getattr(result, "waiting_app_ids", ()) or ()),
                    "unsafe": bool(getattr(result, "is_unsafe", False)),
                    "reason": getattr(result, "reason", "") or "",
                    "persisted_event_id": getattr(result, "persisted_event_id", None),
                }
        return None

    def result_snapshot(self) -> Dict[str, Any]:
        """Returns the run so far in ``SimulationResult`` shape.

        The dashboard header reads this to report status, tick and policy, so it
        is built from the engine's own cumulative metrics rather than recounted
        here.
        """
        metrics = self.engine.metrics
        manager = self.controller.mem_mgr
        result = SimulationResult(
            policy=self.policy,
            eviction_count=int(metrics.successful_evictions),
            blocked_waiting_evictions=int(metrics.conflicts_prevented),
            resource_conflicts=int(metrics.conflicts_detected),
            freeze_incidents=int(metrics.freeze_incidents),
            conflict_resolution_time=0.0,
            resource_releases=int(metrics.safe_releases_performed),
            total_ticks=int(metrics.total_ticks),
            memory_freed=int(metrics.total_memory_freed),
            peak_memory_used=int(metrics.peak_memory_used),
            final_memory_used=int(manager.get_used_memory()),
            final_usage_percentage=round(float(manager.get_usage_percentage()), 2),
            active_apps=[a.name for a in manager.get_active_apps()],
            evicted_apps=[a.name for a in manager.apps.values() if a.is_evicted],
            ticks_detail=[
                {
                    "tick": record.tick_number,
                    "status": record.status,
                    "memory_before": record.tick_result.memory_before_mb,
                    "memory_after": record.tick_result.memory_after_mb,
                    "evictions": [
                        {
                            "app_id": r.candidate.app_id,
                            "name": r.candidate.name,
                            "evicted": bool(r.evicted),
                            "is_unsafe": bool(getattr(r, "is_unsafe", False)),
                            "safe_release": bool(getattr(r, "safe_release_performed", False)),
                            "memory_freed": int(getattr(r, "memory_freed", 0) or 0),
                            "reason": getattr(r, "reason", "") or "",
                        }
                        for r in record.eviction_results
                    ],
                }
                for record in self.records
            ],
        )
        return result.to_dict()


# ──────────────────────────────────────────────────────────────
# Decision timeline
# ──────────────────────────────────────────────────────────────

def build_timeline(
    session: SimulationSession,
    record: Optional[TickRecord] = None,
) -> List[TimelineStep]:
    """Builds the eight-step eviction timeline for one tick.

    Every step is reported from what the tick produced or what the database
    holds afterwards. A step that did not run — because the tick was not under
    pressure, because the policy skips the check, or because no conflict
    existed — is labelled as such rather than being ticked off.
    """
    record = record or session.last_record()
    if record is None:
        return [
            TimelineStep(
                number=index,
                title=title,
                status=NOT_EXECUTED,
                detail="No tick has been executed yet, so no step has run.",
            )
            for index, title in enumerate(_STEP_TITLES, start=1)
        ]

    tick = record.tick_result
    results = record.eviction_results
    threshold = getattr(session.engine.config, "memory_pressure_threshold", None)

    steps: List[TimelineStep] = []

    # 1 — memory pressure
    if getattr(tick, "is_under_pressure", False):
        detail = (
            f"Usage reached {tick.usage_percentage_before:.1f}% at the start of the tick"
            + (f" against a {threshold:.0f}% threshold" if threshold else "")
            + f"; pressure moved {tick.pressure_level_before} → {tick.pressure_level_after}."
        )
        steps.append(TimelineStep(1, "Memory pressure detected", CONFIRMED, detail,
                                  f"tick status {record.status}"))
    else:
        steps.append(
            TimelineStep(
                1,
                "Memory pressure detected",
                NOT_EXECUTED,
                f"Not under pressure this tick (usage {tick.usage_percentage_before:.1f}%), "
                "so the engine returned NO_PRESSURE and skipped the eviction workflow.",
                f"tick status {record.status}",
            )
        )

    # 2 — Clock / Second-Chance
    inspections = record.clock_inspections
    if not inspections:
        steps.append(
            TimelineStep(
                2,
                "Clock / Second-Chance selects a candidate",
                NOT_EXECUTED,
                "The Clock made no inspection this tick."
                if getattr(tick, "is_under_pressure", False)
                else "No inspection ran because the tick was not under pressure.",
            )
        )
    else:
        second_chances = [i for i in inspections if i.get("action") == "cleared_ref_bit"]
        selected = [i for i in inspections if i.get("action") == "selected_for_eviction"]
        names = ", ".join(
            f"{i.get('app_name')} ({i.get('app_id')})" for i in selected
        ) or "no candidate"
        steps.append(
            TimelineStep(
                2,
                "Clock / Second-Chance selects a candidate",
                CONFIRMED,
                f"{len(inspections)} inspection(s): {len(second_chances)} given a second "
                f"chance (reference bit cleared), selected → {names}.",
                f"clock hand position {inspections[-1].get('clock_hand')}",
            )
        )

    # 3 — RAAE resource check
    if session.policy == POLICY_BASELINE:
        steps.append(
            TimelineStep(
                3,
                "RAAE checks held resources",
                NOT_APPLICABLE,
                "BASELINE_CLOCK performs no resource-aware check — this is the "
                "behaviour RAAE exists to replace.",
            )
        )
    elif not results:
        steps.append(
            TimelineStep(3, "RAAE checks held resources", NOT_EXECUTED,
                         "No candidate reached the policy evaluation this tick.")
        )
    else:
        held = sorted({r for res in results for r in getattr(res, "released_resource_ids", ()) or ()})
        reason = "; ".join((getattr(r, "reason", "") or "").strip() for r in results)
        steps.append(
            TimelineStep(
                3,
                "RAAE checks held resources",
                CONFIRMED,
                (f"Held resources released safely: {', '.join(held)}. " if held else "")
                + (reason or "The policy evaluated the candidate's locks."),
                f"policy {results[0].policy_type.value}",
            )
        )

    # 4 — Conflict Manager
    if record.conflicts:
        strategies = sorted({str(c.get("resolution_strategy")) for c in record.conflicts})
        resources = sorted({str(c.get("resource_id")) for c in record.conflicts})
        steps.append(
            TimelineStep(
                4,
                "Conflict Manager evaluates the conflict",
                CONFIRMED,
                f"{len(record.conflicts)} conflict row(s) recorded for "
                f"{', '.join(resources)} using {', '.join(strategies)}.",
                "ConflictLog",
            )
        )
    elif any(getattr(r, "conflict_detected", False) for r in results):
        steps.append(
            TimelineStep(
                4,
                "Conflict Manager evaluates the conflict",
                CONFIRMED,
                "The policy reported a conflict during evaluation, but no "
                "ConflictLog row was written for this tick.",
            )
        )
    else:
        steps.append(
            TimelineStep(
                4,
                "Conflict Manager evaluates the conflict",
                NOT_EXECUTED,
                "No candidate held a resource another application was waiting for.",
            )
        )

    # 5 — Wound-Wait
    wounded = [
        c for c in record.conflicts
        if str(c.get("resolution_strategy")) == "WOUND_WAIT"
        or "wound" in str(c.get("details") or "").lower()
    ]
    if wounded:
        steps.append(
            TimelineStep(
                5,
                "Wound-Wait determines the response",
                CONFIRMED,
                f"{len(wounded)} Wound-Wait decision(s) recorded: "
                + "; ".join(str(c.get("details") or c.get("resolution_strategy")) for c in wounded),
                "ConflictLog",
            )
        )
    elif record.conflicts:
        strategies = sorted({str(c.get("resolution_strategy")) for c in record.conflicts})
        steps.append(
            TimelineStep(
                5,
                "Wound-Wait determines the response",
                NOT_EXECUTED,
                f"A conflict was recorded as {', '.join(strategies)}, but no "
                "Wound-Wait precedence decision was written for this tick.",
                "ConflictLog",
            )
        )
    else:
        steps.append(
            TimelineStep(
                5,
                "Wound-Wait determines the response",
                NOT_APPLICABLE,
                "There was no conflict to resolve.",
            )
        )

    # 6 — release or waiting
    released = sorted({r for res in results for r in getattr(res, "released_resource_ids", ()) or ()})
    waiting = sorted({w for res in results for w in getattr(res, "waiting_app_ids", ()) or ()})
    if released:
        steps.append(
            TimelineStep(6, "Resource release or waiting", CONFIRMED,
                         f"Released {', '.join(released)}.",
                         "SemaphoreLock.release")
        )
    elif waiting:
        steps.append(
            TimelineStep(6, "Resource release or waiting", CONFIRMED,
                         f"{', '.join(waiting)} left queued for the contended resource.",
                         "resource waiting queue")
        )
    else:
        steps.append(
            TimelineStep(6, "Resource release or waiting", NOT_EXECUTED,
                         "No lock was released and no application was queued this tick.")
        )

    # 7 — eviction outcome
    if not results:
        steps.append(
            TimelineStep(7, "Eviction succeeds or is deferred", NOT_EXECUTED,
                         "No eviction was attempted this tick.")
        )
    else:
        parts = []
        for result in results:
            name = f"{result.candidate.name} ({result.candidate.app_id})"
            if result.evicted:
                parts.append(f"{name} evicted, {getattr(result, 'memory_freed', 0)} MB freed")
            else:
                parts.append(f"{name} deferred — {getattr(result, 'reason', '') or 'protected'}")
        steps.append(
            TimelineStep(7, "Eviction succeeds or is deferred", CONFIRMED,
                         "; ".join(parts),
                         f"{sum(1 for r in results if r.evicted)} succeeded / "
                         f"{sum(1 for r in results if not r.evicted)} deferred")
        )

    # 8 — persistence
    ids = sorted({r.persisted_event_id for r in results if getattr(r, "persisted_event_id", None)})
    if ids:
        verified = sorted(set(ids) & set(record.persisted_rows.values()))
        steps.append(
            TimelineStep(
                8,
                "Event is persisted",
                CONFIRMED,
                f"EvictionLog row(s) {', '.join(f'#{i}' for i in ids)} written.",
                "verified in EvictionLog" if verified else "reported by the engine",
            )
        )
    elif results:
        steps.append(
            TimelineStep(8, "Event is persisted", NOT_EXECUTED,
                         "The engine reported no persisted event id for this tick.")
        )
    else:
        steps.append(
            TimelineStep(8, "Event is persisted", NOT_EXECUTED,
                         "Nothing to persist: no eviction was attempted this tick.")
        )

    return steps


_STEP_TITLES: Tuple[str, ...] = (
    "Memory pressure detected",
    "Clock / Second-Chance selects a candidate",
    "RAAE checks held resources",
    "Conflict Manager evaluates the conflict",
    "Wound-Wait determines the response",
    "Resource release or waiting",
    "Eviction succeeds or is deferred",
    "Event is persisted",
)