"""Resource monitoring helpers for the Resource page.

Everything here is a read or a delegation to the existing backend:

* Ownership, status and waiting queues come from ``ResourceManager`` and its
  ``Resource`` models — the page never inspects them any other way.
* Acquisition and release go through :mod:`ui.app_actions`, which is the only
  place the page acquires or releases a lock, and which calls
  ``ResourceManager.request_resource`` / ``release_resource``. The semaphore's own
  checks therefore always run; nothing here can hand a lock out.
* Conflict adjudication calls ``backend.conflict_manager.ConflictManager``,
  which is the project's real Wound-Wait implementation. It returns the decision
  and its reason, delegates any release to the ResourceManager, and logs the
  incident to ConflictLog itself.

No conflict, decision or outcome is invented here: when the backend reports no
contention, the page is told there is none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.conflict_manager.conflict_manager import ConflictManager, ConflictResult
from ui.app_actions import CANONICAL_RESOURCES, release_resource, request_resource

__all__ = [
    "CANONICAL_RESOURCES",
    "Adjudication",
    "ConflictView",
    "ResourceRow",
    "apply_wound_wait",
    "available_resources",
    "evaluate_conflict",
    "integrity_problems",
    "live_conflicts",
    "recent_conflict_events",
    "release_resource",
    "request_resource",
    "resource_rows",
]


@dataclass(frozen=True)
class ResourceRow:
    """One resource as the ResourceManager currently holds it."""

    resource_id: str
    name: str
    status: str
    capacity: int
    available_units: int
    holder_id: Optional[str]
    holder_name: Optional[str]
    waiting: Tuple[Tuple[str, str], ...]
    problems: Tuple[str, ...] = ()

    @property
    def waiting_count(self) -> Optional[int]:
        """Number of waiting applications, or None when it cannot be read."""
        if self.waiting is None:
            return None
        return len(self.waiting)


@dataclass(frozen=True)
class ConflictView:
    """A live conflict: one holder and the application queued behind it."""

    resource_id: str
    holder_id: str
    holder_name: str
    holder_priority: Optional[int]
    holder_last_access: Optional[float]
    requester_id: str
    requester_name: str
    requester_priority: Optional[int]
    requester_last_access: Optional[float]
    queue_length: int


@dataclass(frozen=True)
class Adjudication:
    """The result of asking the backend to resolve one observed conflict."""

    resource_id: str
    decision: str
    reason: str
    strategy: str
    resolved_by: Optional[str]
    requester_id: Optional[str]
    holder_id: Optional[str]
    requester_priority: Optional[int]
    holder_priority: Optional[int]
    requester_timestamp: Optional[float]
    holder_timestamp: Optional[float]
    released: bool
    persisted: bool
    note: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_wound(self) -> bool:
        """True when the backend decided to wound the holder."""
        return self.decision.upper().replace(" ", "_") in ("WOUND", "WOUND_HOLDER")


# ──────────────────────────────────────────────────────────────
# Reads
# ──────────────────────────────────────────────────────────────

def _registry(sim_state: Any) -> Dict[str, Any]:
    """Returns app_id → App for name lookups, empty when unavailable."""
    manager = getattr(sim_state, "memory_manager", None)
    if manager is None:
        return {}
    try:
        return dict(manager.apps)
    except Exception:
        return {}


def _name_of(apps: Dict[str, Any], app_id: Optional[str]) -> Optional[str]:
    """Returns an application's display name, or its id when it is unknown."""
    if not app_id:
        return None
    app = apps.get(str(app_id))
    return str(getattr(app, "name", app_id)) if app is not None else str(app_id)


def available_resources(sim_state: Any) -> List[str]:
    """Returns the canonical resources the backend has registered."""
    manager = getattr(sim_state, "resource_manager", None)
    if manager is None:
        return []
    try:
        present = {r.resource_id for r in manager.get_all_resources()}
    except Exception:
        return []
    return [r for r in CANONICAL_RESOURCES if r in present]


def integrity_problems(resource: Any) -> List[str]:
    """Checks the single-holder invariant the semaphore is supposed to enforce.

    Returns one message per broken expectation, so an empty list means the
    resource matches the model the backend documents: at most ``capacity``
    holders, units that add up, and a holder that is not also queued.
    """
    problems: List[str] = []
    holders = dict(getattr(resource, "holders", {}) or {})
    capacity = int(getattr(resource, "capacity", 1) or 1)
    available = int(getattr(resource, "available_units", 0) or 0)
    queue = list(getattr(resource, "waiting_queue", []) or [])

    if len(holders) > capacity:
        problems.append(
            f"{len(holders)} holders for {capacity} unit(s): {', '.join(sorted(holders))}"
        )
    if available + sum(holders.values()) != capacity:
        problems.append(
            f"unit accounting mismatch: {available} available + "
            f"{sum(holders.values())} held ≠ {capacity}"
        )
    for holder in holders:
        if holder in queue:
            problems.append(f"holder '{holder}' is also queued for its own resource")
    return problems


def resource_rows(sim_state: Any) -> List[ResourceRow]:
    """Returns the four monitored resources as the ResourceManager holds them."""
    manager = getattr(sim_state, "resource_manager", None)
    apps = _registry(sim_state)
    if manager is None:
        return []

    rows: List[ResourceRow] = []
    for resource_id in available_resources(sim_state):
        try:
            resource = manager.get_resource(resource_id)
        except Exception:
            resource = None
        if resource is None:
            continue

        try:
            queue = list(resource.waiting_queue or [])
        except Exception:
            queue = []

        try:
            status = resource.status.value
        except Exception:
            status = "UNAVAILABLE"

        holder_id = None
        try:
            holder_id = resource.primary_holder_id
        except Exception:
            holder_id = None

        rows.append(
            ResourceRow(
                resource_id=resource.resource_id,
                name=str(getattr(resource, "name", resource.resource_id)),
                status=str(status),
                capacity=int(getattr(resource, "capacity", 1) or 1),
                available_units=int(getattr(resource, "available_units", 0) or 0),
                holder_id=holder_id,
                holder_name=_name_of(apps, holder_id),
                waiting=tuple((str(w), _name_of(apps, w) or str(w)) for w in queue),
                problems=tuple(integrity_problems(resource)),
            )
        )
    return rows


def live_conflicts(sim_state: Any) -> List[ConflictView]:
    """Returns the conflicts the semaphore has actually produced.

    A conflict exists only where a resource is held *and* another application is
    queued behind it. The first queued application is the requester, because the
    waiting queue is FIFO.
    """
    manager = getattr(sim_state, "resource_manager", None)
    apps = _registry(sim_state)
    if manager is None:
        return []

    conflicts: List[ConflictView] = []
    for row in resource_rows(sim_state):
        if not row.holder_id or not row.waiting:
            continue
        requester_id = row.waiting[0][0]
        if requester_id == row.holder_id:
            continue

        requester = apps.get(requester_id)
        holder = apps.get(row.holder_id)
        conflicts.append(
            ConflictView(
                resource_id=row.resource_id,
                holder_id=row.holder_id,
                holder_name=row.holder_name or row.holder_id,
                holder_priority=getattr(holder, "priority", None),
                holder_last_access=getattr(holder, "last_access_time", None),
                requester_id=requester_id,
                requester_name=_name_of(apps, requester_id) or requester_id,
                requester_priority=getattr(requester, "priority", None),
                requester_last_access=getattr(requester, "last_access_time", None),
                queue_length=len(row.waiting),
            )
        )
    return conflicts


def recent_conflict_events(db: Optional[Any], limit: int = 20) -> List[Dict[str, Any]]:
    """Returns the most recent ConflictLog rows, newest first."""
    if db is None:
        return []
    try:
        return list(db.conflict_log.get_all_conflicts(limit) or [])
    except Exception:
        return []


# ──────────────────────────────────────────────────────────────
# Wound-Wait adjudication
# ──────────────────────────────────────────────────────────────

def _resolve(
    sim_state: Any,
    db: Optional[Any],
    resource_id: str,
    request_release: bool,
    requester_id: Optional[str] = None,
) -> Optional[Adjudication]:
    """Runs the backend's Wound-Wait manager over one observed or proposed conflict."""
    manager = getattr(sim_state, "resource_manager", None)
    apps = _registry(sim_state)
    if manager is None:
        return None

    try:
        resource = manager.get_resource(resource_id)
    except Exception:
        resource = None
    if resource is None:
        return None

    conflicts = [c for c in live_conflicts(sim_state) if c.resource_id == resource_id]
    if requester_id:
        req_id = requester_id
        hold_id = resource.primary_holder_id
    elif conflicts:
        conflict = conflicts[0]
        req_id = conflict.requester_id
        hold_id = conflict.holder_id
    else:
        req_id = None
        hold_id = resource.primary_holder_id

    requester = apps.get(req_id) if req_id else None
    holder = apps.get(hold_id) if hold_id else None

    # The backend's own manager, wired to the live ResourceManager. With
    # request_release it delegates the release through the semaphore rather than
    # touching locks itself.
    wound_wait = ConflictManager(db=db, resource_manager=manager)
    before = len(recent_conflict_events(db, limit=1000))

    result: ConflictResult = wound_wait.resolve_conflict(
        requester=requester if requester is not None else req_id,
        holder=holder if holder is not None else hold_id,
        resource=resource,
        request_release=request_release,
        registered_apps=apps,
    )

    after = len(recent_conflict_events(db, limit=1000))
    decision = getattr(getattr(result, "decision", None), "value", None) or str(
        getattr(result, "decision", "UNKNOWN")
    )

    note = ""
    if decision.upper() == "NO_CONFLICT":
        note = "The backend reported no contention, so no conflict row was written."

    return Adjudication(
        resource_id=resource_id,
        decision=decision,
        reason=str(getattr(result, "reason", "") or ""),
        strategy=str(getattr(result, "strategy", "") or ""),
        resolved_by="WOUND" if result.is_wound else ("WAIT" if result.is_wait else None),
        requester_id=req_id,
        holder_id=hold_id,
        requester_priority=getattr(result, "candidate_priority", None),
        holder_priority=getattr(result, "holder_priority", None),
        requester_timestamp=getattr(result, "candidate_timestamp", None),
        holder_timestamp=getattr(result, "holder_timestamp", None),
        released=bool(getattr(result, "resource_released", False)),
        persisted=after > before,
        note=note,
    )


def evaluate_conflict(
    sim_state: Any,
    db: Optional[Any],
    resource_id: str,
    requester_id: Optional[str] = None,
) -> Optional[Adjudication]:
    """Asks the backend what Wound-Wait would decide, without changing anything."""
    return _resolve(sim_state, db, resource_id, request_release=False, requester_id=requester_id)


def apply_wound_wait(sim_state: Any, db: Optional[Any], resource_id: str) -> Optional[Adjudication]:
    """Applies the backend's WOUND_HOLDER decision, releasing through the semaphore."""
    return _resolve(sim_state, db, resource_id, request_release=True)


# ──────────────────────────────────────────────────────────────
# Outcome reporting
# ──────────────────────────────────────────────────────────────

def outcome_for(sim_state: Any, adjudication: Adjudication) -> Dict[str, Any]:
    """Re-reads the resource and reports what actually happened afterwards.

    This is deliberately a fresh read rather than a prediction: the page states
    the outcome the backend's current state shows.
    """
    rows = {row.resource_id: row for row in resource_rows(sim_state)}
    row = rows.get(adjudication.resource_id)

    if row is None:
        return {
            "state": "Unavailable",
            "detail": "The resource is no longer registered with the ResourceManager.",
        }

    if adjudication.decision.upper() == "NO_CONFLICT":
        return {
            "state": "No Contention",
            "detail": (
                f"{adjudication.resource_id} is free or already held by the requester; "
                "no conflict occurred."
            ),
        }

    if adjudication.released and row.holder_id != adjudication.holder_id:
        promoted = (
            row.holder_id == adjudication.requester_id
            or adjudication.requester_id in [w[0] for w in row.waiting]
        )
        return {
            "state": "Released",
            "detail": (
                f"{adjudication.holder_id} no longer holds {row.resource_id}. "
                + (
                    f"{adjudication.requester_id} now holds it."
                    if promoted and row.holder_id == adjudication.requester_id
                    else "The waiting queue was re-processed by the semaphore."
                )
            ),
        }

    if not row.waiting:
        return {
            "state": "Resolved",
            "detail": f"Nothing is queued for {row.resource_id} any more.",
        }

    return {
        "state": "Waiting" if adjudication.decision.upper() == "WAIT" else "Unchanged",
        "detail": (
            f"{row.holder_id or 'no one'} holds {row.resource_id}; "
            f"{len(row.waiting)} application(s) queued."
            if adjudication.decision.upper() != "WAIT"
            else f"{adjudication.requester_id} remains queued behind {adjudication.holder_id}."
        ),
    }


def event_rows(events: Sequence[Dict[str, Any]], apps: Dict[str, str]) -> List[List[str]]:
    """Formats ConflictLog rows for the recent-events table."""
    rows: List[List[str]] = []
    for event in events:
        waiting_id = str(event.get("waiting_app_id") or "—")
        holder_id = str(event.get("blocking_app_id") or "—")
        rows.append(
            [
                _clock(event.get("timestamp")),
                waiting_id + (f" · {apps[waiting_id]}" if waiting_id in apps else ""),
                holder_id + (f" · {apps[holder_id]}" if holder_id in apps else ""),
                str(event.get("resource_id") or "—"),
                str(event.get("resolution_strategy") or "—"),
                str(event.get("resolved_by") or "—"),
                str(event.get("details") or "—"),
            ]
        )
    return rows


def app_name_map(sim_state: Any) -> Dict[str, str]:
    """Returns app_id → name for event formatting."""
    return {app_id: str(getattr(app, "name", app_id)) for app_id, app in _registry(sim_state).items()}


def _clock(timestamp: Any) -> str:
    """Formats a UNIX timestamp as HH:MM:SS."""
    import datetime

    if timestamp is None:
        return "—"
    try:
        return datetime.datetime.fromtimestamp(float(timestamp)).strftime("%H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(timestamp)