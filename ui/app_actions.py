"""Application operations for the Applications page.

Every control on the Applications page goes through this module rather than
touching backend objects from the view, so the UI holds no simulation state of
its own and each operation is validated, executed against the real backend, and
reported with the outcome the backend produced.

Backend APIs actually used, and the ones deliberately not invented:

==========================================  ==============================================
Operation                                   Backend call
==========================================  ==============================================
Create                                      ``SimulationState.add_app(App)``
Change priority                             *(no setter exists — see ``change_priority``)*
Change state                                ``App.change_state(AppState)``
Simulate access                             ``MemoryManager.touch_app(app_id)``
Set the reference bit                       ``MemoryManager.update_reference_bit(app_id, bit)``
Request a resource                          ``ResourceManager.request_resource(app, resource_id)``
Release a resource                          ``ResourceManager.release_resource(app, resource_id)``
Read an application                         ``MemoryManager.apps`` / ``ResourceManager``
==========================================  ==============================================

Two behaviours are refused rather than faked, because the backend offers no way
to do them safely:

* An application that is queued in a resource's waiting queue cannot be moved to
  another state. The semaphore only dequeues when the holder releases, so the
  state would disagree with the queue.
* An evicted application cannot acquire a resource; ``SemaphoreLock.acquire``
  refuses it, and the UI says so rather than pretending the request was queued.

Changing an app's priority has no backend method. :func:`change_priority` is an
explicit adapter for that gap: it mirrors what ``MemoryManager`` itself does in
``update_reference_bit`` — mutate the model, then persist through the manager's
own database hook — and it rolls the change back if the write fails, so a
database error can never leave the in-memory state out of step with the log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.models.app import App, AppState

#: Documented priority band (``MODULE_DOCUMENTATION.md``: "1 to 5").
PRIORITY_MIN = 1
PRIORITY_MAX = 5

#: The only states a user may create an application in. ``WAITING`` is set by the
#: semaphore and ``EVICTED`` by the eviction engine, so neither is offered.
INITIAL_STATES: Tuple[str, ...] = ("FOREGROUND", "BACKGROUND")

#: The states a user may move an existing application between.
SWITCHABLE_STATES: Tuple[str, ...] = ("FOREGROUND", "BACKGROUND")

#: The hardware resources the page offers, exactly as specified.
CANONICAL_RESOURCES: Tuple[str, ...] = ("Camera", "Microphone", "GPS", "Audio")

MAX_NAME_LENGTH = 60

#: Rendered by the detail view when a resource row cannot be read.
UNAVAILABLE_TEXT = "Unavailable"


class ValidationError(ValueError):
    """Raised when an operation is refused before the backend is called."""


@dataclass(frozen=True)
class ActionResult:
    """The outcome of one operation.

    Attributes:
        ok: True only when the backend actually performed the change.
        message: A sentence describing what happened, suitable for display.
        app: The application the backend holds afterwards, when one is involved.
        detail: Extra structured context for the page (e.g. promoted waiters).
    """

    ok: bool
    message: str
    app: Optional[App] = None
    detail: Dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────
# Input validation (pure — no Streamlit, no backend)
# ──────────────────────────────────────────────────────────────

def clean_name(raw: Any) -> str:
    """Returns a validated application name.

    Raises:
        ValidationError: If the name is blank or longer than
            :data:`MAX_NAME_LENGTH`.
    """
    name = str(raw or "").strip()
    if not name:
        raise ValidationError("Application name is required.")
    if len(name) > MAX_NAME_LENGTH:
        raise ValidationError(
            f"Application name must be {MAX_NAME_LENGTH} characters or fewer."
        )
    return name


def _clean_whole_number(raw: Any, label: str) -> int:
    """Converts *raw* to a whole number, refusing anything else.

    Raises:
        ValidationError: If the value is not an integer, or is a float with a
            fractional part.
    """
    if isinstance(raw, bool) or raw is None or raw == "":
        raise ValidationError(f"{label} must be a whole number.")
    if isinstance(raw, float) and not raw.is_integer():
        raise ValidationError(f"{label} must be a whole number, not {raw}.")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{label} must be a whole number.") from None


def clean_memory_footprint(raw: Any) -> int:
    """Returns a validated memory footprint in MB.

    A fractional value is refused rather than truncated, so the registered
    footprint is always exactly the number of megabytes that was asked for.

    Raises:
        ValidationError: If the value is not a whole number or is negative.
    """
    value = _clean_whole_number(raw, "Memory footprint")
    if value < 0:
        raise ValidationError("Memory footprint cannot be negative.")
    return value


def clean_priority(raw: Any) -> int:
    """Returns a validated priority inside the documented 1-5 band.

    Raises:
        ValidationError: If the value is not an integer in
            ``[PRIORITY_MIN, PRIORITY_MAX]``.
    """
    if isinstance(raw, bool) or raw is None or raw == "":
        raise ValidationError(f"Priority must be a whole number from {PRIORITY_MIN} to {PRIORITY_MAX}.")
    value = _clean_whole_number(raw, "Priority")
    if not PRIORITY_MIN <= value <= PRIORITY_MAX:
        raise ValidationError(
            f"Priority must be between {PRIORITY_MIN} and {PRIORITY_MAX}; got {value}."
        )
    return value


def clean_initial_state(raw: Any) -> str:
    """Returns a validated initial state (``FOREGROUND`` or ``BACKGROUND``).

    Raises:
        ValidationError: If the state is not one a user may create an app in.
    """
    state = str(raw or "").strip().upper()
    if state not in INITIAL_STATES:
        raise ValidationError(
            f"Initial state must be {' or '.join(INITIAL_STATES)}."
        )
    return state


def clean_target_state(raw: Any) -> str:
    """Returns a validated state transition target.

    Raises:
        ValidationError: If the target is not a switchable user-settable state.
    """
    state = str(raw or "").strip().upper()
    if state not in SWITCHABLE_STATES:
        raise ValidationError(
            "State can only be changed to "
            f"{' or '.join(SWITCHABLE_STATES)}."
        )
    return state


def clean_reference_bit(raw: Any) -> int:
    """Returns a validated reference bit (0 or 1)."""
    if isinstance(raw, bool):
        return int(raw)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError("Reference bit must be 0 or 1.") from None
    if value not in (0, 1):
        raise ValidationError("Reference bit must be 0 or 1.")
    return value


def clean_resource_id(raw: Any, registered: Sequence[str]) -> str:
    """Returns a validated resource id from the canonical set.

    Args:
        raw: The requested resource id.
        registered: Resource ids the ResourceManager actually holds.

    Raises:
        ValidationError: If the id is outside :data:`CANONICAL_RESOURCES` or is
            not currently registered with the backend.
    """
    resource_id = str(raw or "").strip()
    if resource_id not in CANONICAL_RESOURCES:
        raise ValidationError(
            f"Choose one of: {', '.join(CANONICAL_RESOURCES)}."
        )
    if resource_id not in registered:
        raise ValidationError(
            f"Resource '{resource_id}' is not registered with the ResourceManager."
        )
    return resource_id


def build_app_id(name: str, taken_ids: Sequence[str]) -> str:
    """Derives a unique ``app_*`` identifier from an application name.

    The first application of a given name gets the plain slug; a further one with
    the same name is suffixed, so two applications never share an id.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    slug = slug or "app"
    base = f"app_{slug}"
    if base not in taken_ids:
        return base
    suffix = 2
    while f"{base}_{suffix}" in taken_ids:
        suffix += 1
    return f"{base}_{suffix}"


# ──────────────────────────────────────────────────────────────
# Backend helpers
# ──────────────────────────────────────────────────────────────

def _memory_manager(sim_state: Any) -> Optional[Any]:
    """Returns the live MemoryManager, or None when no state is attached."""
    return getattr(sim_state, "memory_manager", None)


def _resource_manager(sim_state: Any) -> Optional[Any]:
    """Returns the live ResourceManager, or None when no state is attached."""
    return getattr(sim_state, "resource_manager", None)


def _registered_resource_ids(sim_state: Any) -> List[str]:
    """Returns the canonical resource ids the backend currently knows about."""
    manager = _resource_manager(sim_state)
    if manager is None:
        return []
    try:
        present = {r.resource_id for r in manager.get_all_resources()}
    except Exception:
        return []
    return [r for r in CANONICAL_RESOURCES if r in present]


def available_resources(sim_state: Any) -> List[str]:
    """Returns the canonical resources offered by the page, in fixed order."""
    return _registered_resource_ids(sim_state)


def get_application(sim_state: Any, app_id: str) -> Optional[App]:
    """Returns the backend's application object for *app_id*, or None."""
    manager = _memory_manager(sim_state)
    if manager is None:
        return None
    try:
        return manager.apps.get(str(app_id))
    except Exception:
        return None


def queued_resources(sim_state: Any, app_id: str) -> List[str]:
    """Returns the resources whose waiting queue contains *app_id*."""
    manager = _resource_manager(sim_state)
    if manager is None:
        return []
    queued: List[str] = []
    try:
        for resource in manager.get_all_resources():
            if str(app_id) in (resource.waiting_queue or []):
                queued.append(resource.resource_id)
    except Exception:
        return queued
    return sorted(queued)


def _persist_app(memory_manager: Any, app: App) -> bool:
    """Persists an app through the manager's own database hook.

    Mirrors what ``MemoryManager.update_reference_bit`` does internally. Returns
    True when the change is safely stored, including when persistence is
    disabled entirely, and False only when a write was attempted and failed.
    """
    database = getattr(memory_manager, "db", None)
    if database is None or not hasattr(database, "save_app"):
        return True
    return bool(database.save_app(app))


def _no_state() -> ActionResult:
    """The failure returned when no simulation state is attached."""
    return ActionResult(
        False,
        "No simulation state is attached. Use Quick Start in the sidebar first.",
    )


def _snapshot(app: App) -> Tuple[str, AppState, Any, int, float]:
    """Captures the fields an adapter may need to restore."""
    return (app.name, app.state, app.status, app.reference_bit, app.last_access_time)


def _restore(app: App, snapshot: Tuple[str, AppState, Any, int, float]) -> None:
    """Restores a previously captured snapshot after a failed write."""
    app.name, app.state, app.status, app.reference_bit, app.last_access_time = snapshot


# ──────────────────────────────────────────────────────────────
# Operations
# ──────────────────────────────────────────────────────────────

def create_application(
    sim_state: Any,
    name: Any,
    memory_footprint: Any,
    priority: Any,
    initial_state: Any,
) -> ActionResult:
    """Creates and registers an application.

    Validation happens first, then the size check against free memory, and only
    then is the app handed to ``SimulationState.add_app`` so both managers
    register the same object.

    Returns:
        ActionResult: Carrying the backend's application on success.
    """
    try:
        clean = clean_name(name)
        memory_mb = clean_memory_footprint(memory_footprint)
        priority_value = clean_priority(priority)
        state_value = clean_initial_state(initial_state)
    except ValidationError as error:
        return ActionResult(False, str(error))

    memory_manager = _memory_manager(sim_state)
    if memory_manager is None:
        return _no_state()

    try:
        app_id = build_app_id(clean, list(memory_manager.apps))
        free_memory = int(memory_manager.get_free_memory())
    except Exception as error:  # noqa: BLE001 - surfaced to the user
        return ActionResult(False, f"Could not read the memory manager: {error}")

    if app_id in memory_manager.apps:
        return ActionResult(
            False, f"An application with id '{app_id}' already exists."
        )

    if memory_mb > free_memory:
        return ActionResult(
            False,
            f"{memory_mb} MB was requested but only {free_memory} MB is free. "
            "Reduce the footprint or evict an application first.",
            detail={"requested_mb": memory_mb, "free_mb": free_memory},
        )

    app = App(
        app_id=app_id,
        name=clean,
        priority=priority_value,
        state=AppState(state_value),
        memory_footprint=memory_mb,
    )
    try:
        sim_state.add_app(app)
    except Exception as error:  # noqa: BLE001 - surfaced to the user
        return ActionResult(False, f"The backend refused to register the app: {error}")

    return ActionResult(
        True,
        f"Created '{app.name}' as {app.app_id} — {app.memory_footprint} MB, "
        f"priority {app.priority}, {app.state.value}, reference bit {app.reference_bit}.",
        app=app,
        detail={"app_id": app_id, "free_mb": free_memory},
    )


def change_priority(sim_state: Any, app_id: Any, priority: Any) -> ActionResult:
    """Changes an application's priority.

    Adapter for a missing backend setter: the model field is updated and the
    change is persisted through the MemoryManager's own database hook, which is
    the same pattern ``MemoryManager.update_reference_bit`` uses. A failed write
    rolls the in-memory value back so the two never drift apart.
    """
    memory_manager = _memory_manager(sim_state)
    if memory_manager is None:
        return _no_state()

    app = get_application(sim_state, app_id)
    if app is None:
        return ActionResult(False, f"No application with id '{app_id}' is registered.")

    try:
        value = clean_priority(priority)
    except ValidationError as error:
        return ActionResult(False, str(error))

    if app.priority == value:
        return ActionResult(True, f"{app.name} is already at priority {value}.", app=app)

    previous = app.priority
    app.priority = value
    if not _persist_app(memory_manager, app):
        app.priority = previous
        return ActionResult(
            False,
            "The priority change could not be saved, so it was rolled back.",
            app=app,
        )

    return ActionResult(
        True, f"{app.name} priority {previous} → {value}.", app=app
    )


def change_state(sim_state: Any, app_id: Any, target_state: Any) -> ActionResult:
    """Moves an application between foreground and background.

    Refuses to touch an application that is queued for a resource: the semaphore
    only leaves the waiting queue when the holder releases, so the app's state
    would disagree with the queue and the backend has no dequeue API to fix it.
    """
    memory_manager = _memory_manager(sim_state)
    if memory_manager is None:
        return _no_state()

    app = get_application(sim_state, app_id)
    if app is None:
        return ActionResult(False, f"No application with id '{app_id}' is registered.")

    try:
        target = clean_target_state(target_state)
    except ValidationError as error:
        return ActionResult(False, str(error))

    if app.is_evicted:
        return ActionResult(
            False,
            f"{app.name} is evicted, so its state cannot be changed. "
            "Eviction is performed by the simulation engine.",
            app=app,
        )

    if app.state.value == target:
        return ActionResult(True, f"{app.name} is already {target}.", app=app)

    queued = queued_resources(sim_state, app.app_id)
    if queued:
        return ActionResult(
            False,
            f"{app.name} is queued for {', '.join(queued)}. The backend only "
            "dequeues on release, so its state cannot change until it is granted.",
            app=app,
            detail={"queued_resources": queued},
        )

    snapshot = _snapshot(app)
    previous = app.state.value
    app.change_state(AppState(target))
    if not _persist_app(memory_manager, app):
        _restore(app, snapshot)
        return ActionResult(
            False,
            "The state change could not be saved, so it was rolled back.",
            app=app,
        )

    return ActionResult(
        True,
        f"{app.name} moved {previous} → {app.state.value} "
        f"(reference bit {app.reference_bit}).",
        app=app,
    )


def simulate_access(sim_state: Any, app_id: Any) -> ActionResult:
    """Records an access, setting the reference bit to 1 via ``touch_app``."""
    memory_manager = _memory_manager(sim_state)
    if memory_manager is None:
        return _no_state()

    app = get_application(sim_state, app_id)
    if app is None:
        return ActionResult(False, f"No application with id '{app_id}' is registered.")

    try:
        updated = bool(memory_manager.touch_app(app.app_id))
    except Exception as error:  # noqa: BLE001 - surfaced to the user
        return ActionResult(False, f"The backend refused the access: {error}")

    if not updated:
        return ActionResult(False, f"{app.name} could not be touched.")

    return ActionResult(
        True,
        f"{app.name} was accessed — reference bit set to {app.reference_bit}, "
        f"last access recorded.",
        app=app,
    )


def set_reference_bit(sim_state: Any, app_id: Any, bit: Any) -> ActionResult:
    """Sets the reference bit explicitly via ``update_reference_bit``."""
    memory_manager = _memory_manager(sim_state)
    if memory_manager is None:
        return _no_state()

    app = get_application(sim_state, app_id)
    if app is None:
        return ActionResult(False, f"No application with id '{app_id}' is registered.")

    try:
        value = clean_reference_bit(bit)
    except ValidationError as error:
        return ActionResult(False, str(error))

    previous = app.reference_bit
    try:
        updated = bool(memory_manager.update_reference_bit(app.app_id, value))
    except Exception as error:  # noqa: BLE001 - surfaced to the user
        return ActionResult(False, f"The backend refused the update: {error}")

    if not updated:
        return ActionResult(False, f"{app.name} could not be updated.")

    return ActionResult(
        True,
        f"{app.name} reference bit {previous} → {value}.",
        app=app,
    )


def request_resource(sim_state: Any, app_id: Any, resource_id: Any) -> ActionResult:
    """Requests a resource lock through the semaphore.

    ``ResourceManager.request_resource`` returns False when the resource is
    already held; that is a queue, not a failure, and the message says so.
    """
    memory_manager = _memory_manager(sim_state)
    resource_manager = _resource_manager(sim_state)
    if memory_manager is None or resource_manager is None:
        return _no_state()

    app = get_application(sim_state, app_id)
    if app is None:
        return ActionResult(False, f"No application with id '{app_id}' is registered.")

    try:
        resource = clean_resource_id(resource_id, _registered_resource_ids(sim_state))
    except ValidationError as error:
        return ActionResult(False, str(error))

    if app.is_evicted:
        return ActionResult(
            False,
            f"{app.name} is evicted, and the semaphore refuses locks for evicted apps.",
            app=app,
        )
    if resource in app.held_resources:
        return ActionResult(
            False, f"{app.name} already holds {resource}.", app=app
        )

    target = resource_manager.get_resource(resource)
    if target is None:
        return ActionResult(
            False, f"Resource '{resource}' is not registered with the backend.", app=app
        )
    if str(app.app_id) in (target.waiting_queue or []):
        return ActionResult(
            False, f"{app.name} is already queued for {resource}.", app=app
        )

    holder = target.primary_holder_id
    try:
        granted = bool(resource_manager.request_resource(app, resource))
    except Exception as error:  # noqa: BLE001 - surfaced to the user
        return ActionResult(False, f"The backend refused the request: {error}", app=app)

    app = get_application(sim_state, app_id) or app
    if granted:
        return ActionResult(
            True, f"{app.name} was granted {resource}.", app=app
        )

    blocked_by = holder if holder else "another application"
    return ActionResult(
        False,
        f"{resource} is held by {blocked_by}, so {app.name} was queued and is "
        f"now {app.state.value}. It is granted automatically on release.",
        app=app,
        detail={"resource_id": resource, "blocked_by": holder, "queued": True},
    )


def release_resource(sim_state: Any, app_id: Any, resource_id: Any) -> ActionResult:
    """Releases a resource lock through the semaphore.

    A release can promote the next waiter, so the message reports it.
    """
    memory_manager = _memory_manager(sim_state)
    resource_manager = _resource_manager(sim_state)
    if memory_manager is None or resource_manager is None:
        return _no_state()

    app = get_application(sim_state, app_id)
    if app is None:
        return ActionResult(False, f"No application with id '{app_id}' is registered.")

    try:
        resource = clean_resource_id(resource_id, _registered_resource_ids(sim_state))
    except ValidationError as error:
        return ActionResult(False, str(error))

    if resource not in app.held_resources:
        held = ", ".join(sorted(app.held_resources)) or "none"
        return ActionResult(
            False,
            f"{app.name} does not hold {resource} (holds: {held}).",
            app=app,
        )

    queues_before: Dict[str, List[str]] = {}
    try:
        for candidate in resource_manager.get_all_resources():
            queues_before[candidate.resource_id] = list(candidate.waiting_queue or [])
    except Exception:
        queues_before = {}

    try:
        released = bool(resource_manager.release_resource(app, resource))
    except Exception as error:  # noqa: BLE001 - surfaced to the user
        return ActionResult(False, f"The backend refused the release: {error}", app=app)

    if not released:
        return ActionResult(
            False, f"{app.name} could not release {resource}.", app=app
        )

    promoted: List[str] = []
    try:
        for candidate in resource_manager.get_all_resources():
            before = queues_before.get(candidate.resource_id, [])
            after = list(candidate.waiting_queue or [])
            for waiter in set(before) - set(after):
                if waiter != app.app_id:
                    promoted.append(f"{waiter} → {candidate.resource_id}")
    except Exception:
        promoted = []

    message = f"{app.name} released {resource}."
    if promoted:
        message += f" Promoted from the queue: {', '.join(sorted(promoted))}."

    return ActionResult(
        True, message, app=app, detail={"promoted": promoted}
    )


# ──────────────────────────────────────────────────────────────
# Inspection
# ──────────────────────────────────────────────────────────────

def describe_application(sim_state: Any, app: App) -> Dict[str, Any]:
    """Builds the detail view for one application.

    Everything here is read from the backend: the model for the app's own facts,
    the ResourceManager for lock and queue state, and the database for the
    eviction history.
    """
    queued = queued_resources(sim_state, app.app_id)
    held = sorted(app.held_resources)

    resource_rows: List[List[str]] = []
    manager = _resource_manager(sim_state)
    for resource_id in held + queued:
        resource = manager.get_resource(resource_id) if manager else None
        if resource is None:
            resource_rows.append([resource_id, UNAVAILABLE_TEXT, UNAVAILABLE_TEXT, UNAVAILABLE_TEXT])
            continue
        resource_rows.append(
            [
                resource_id,
                resource.status.value,
                resource.primary_holder_id or "—",
                ", ".join(resource.waiting_queue) if resource.waiting_queue else "—",
            ]
        )

    return {
        "app": app,
        "held_resources": held,
        "queued_resources": queued,
        "resource_rows": resource_rows,
        "last_access_time": app.last_access_time,
        "is_active": app.is_active,
        "is_evicted": app.is_evicted,
    }