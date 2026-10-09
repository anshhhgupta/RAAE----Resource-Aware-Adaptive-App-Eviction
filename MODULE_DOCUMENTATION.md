# RAAE — Resource-Aware Adaptive App Eviction: Module Documentation

This document provides a concise architectural overview of the core components implemented in the RAAE system: **App Model**, **Memory Manager**, **Clock Eviction Algorithm**, **Resource Manager**, **Semaphore Locking**, and **Workload Generator**.

---

## 1. App Model (`backend/models/app.py`)

The `App` class represents a simulated application running within the system.

### Attributes
- `app_id` (`str`): Unique identifier for the application (e.g., `"app_1"`).
- `name` (`str`): Human-readable name of the application (e.g., `"Camera"`, `"Maps"`).
- `priority` (`int`): Application priority level (1 to 5, where higher values indicate higher importance).
- `state` (`AppState`): Current execution state:
  - `FOREGROUND`: Active user-facing application (protected from normal eviction).
  - `BACKGROUND`: Inactive background application (eligible for eviction).
  - `WAITING`: Blocked and queued waiting for a locked resource.
  - `EVICTED`: Memory reclaimed and app terminated.
- `memory_footprint` (`int`): System memory consumed by the application in MB.
- `reference_bit` (`int`): Binary flag (`0` or `1`) used by the Clock eviction algorithm to track recent access activity.
- `last_access_time` (`float`): UNIX timestamp of the last activity or touch operation.
- `held_resources` (`Set[str]`): Set of resource IDs currently held by the application (e.g., `{"Camera", "GPS"}`).
- `status` (`AppStatus`): Operational status (`ACTIVE` or `EVICTED`).

### Key Methods
- `touch()`: Updates `last_access_time` to the current time and sets `reference_bit = 1`.
- `evict()`: Resets `memory_footprint = 0`, sets `reference_bit = 0`, and transitions `state` and `status` to `EVICTED`.
- `acquire_resource(resource_id)` / `release_resource(resource_id)`: Maintains the `held_resources` set.

---

## 2. Memory Manager (`backend/memory_manager/memory_manager.py`)

The `MemoryManager` controls system RAM allocation, tracks memory usage, detects pressure thresholds, and identifies eviction candidates.

### Key Capabilities
- **Configurable RAM**: Configured via `total_memory` (in MB, default `1024 MB`). Can be dynamically updated via `set_total_memory()`.
- **Memory Tracking**:
  - `get_used_memory()`: Sum of footprints for all active (`is_active == True`) applications.
  - `get_free_memory()`: Remaining free memory calculated as `max(0, total_memory - get_used_memory())`.
  - `get_usage_percentage()`: Returns current usage as a percentage (`0.0% - 100.0%`).
- **Memory Pressure Detection**:
  - `GREEN`: `< 60%` RAM usage (normal operations).
  - `YELLOW`: `60% - 75%` RAM usage (moderate pressure).
  - `ORANGE`: `75% - 90%` RAM usage (high pressure).
  - `RED`: `>= 90%` RAM usage (critical pressure).
  - `is_under_pressure()`: Returns `True` if pressure is `ORANGE` or `RED`.

---

## 3. Clock / Second-Chance Eviction Algorithm

The baseline eviction algorithm implements a circular Clock / Second-Chance page-replacement strategy adapted for application management.

### Algorithm Flow
1. **Circular Pointer (`clock_hand`)**: Maintains an integer index scanning across eligible background applications (`state != FOREGROUND` and `status != EVICTED`).
2. **Inspection Step**:
   - If an app's `reference_bit == 1`:
     - Set `reference_bit = 0` (**Second Chance Granted**).
     - Advance `clock_hand` to the next application.
   - If an app's `reference_bit == 0`:
     - **Select Candidate**: Choose this application as the eviction candidate.
     - Advance `clock_hand` to the next application.
     - Return the candidate `App` object to the caller.
3. **Protection & Non-Premature Eviction Rules**:
   - `FOREGROUND` applications and `EVICTED` applications are strictly skipped.
   - Candidate selection returns the candidate `App` object **without evicting it immediately**, allowing downstream decision engines (e.g., RAAE Engine) to inspect held resources before deciding whether to evict.

---

## 4. Resource Manager (`backend/resource_manager/resource_manager.py`)

The `ResourceManager` controls access to system hardware resources, tracks active holders, and maintains waiting queues.

### Hardware Resources
The system models four core hardware resources:
- `Camera`: Camera hardware sensor.
- `Microphone`: Audio capture input device.
- `GPS`: Location positioning sensor.
- `Audio`: Audio playback output stream.

### Interface Methods
- `request_resource(app_id, resource)`: Requests access to a hardware resource.
- `release_resource(app_id, resource)`: Releases a held hardware resource.
- `get_holder(resource)`: Returns the `app_id` of the current holder (or `None`).
- `get_waiting_queue(resource)`: Returns a list of `app_id`s currently queued in FIFO order.
- `get_all_active_locks()`: Returns a dictionary of active locks (`resource_id -> holder_app_id`).

---

## 5. Semaphore Behavior (`backend/resource_manager/semaphore.py`)

Resource locking is implemented using **Binary Semaphores** (Mutexes) via the `SemaphoreLock` class.

### Locking Rules
1. **Single Active Holder**: Each hardware resource has a capacity of 1 unit. Only one app can hold a resource at any given time.
2. **FIFO Waiting Queue**: When an app requests a locked resource:
   - The app is placed into the resource's FIFO `waiting_queue`.
   - The app's state is updated to `AppState.WAITING`.
   - `request_resource()` returns `False`.
3. **Duplicate Prevention**: An app cannot acquire the same resource twice. Attempting duplicate acquisition returns `False`.
4. **Automatic Unblocking**: When a holder releases a resource:
   - The lock is released and automatically granted to the next app in the FIFO `waiting_queue`.
   - The unblocked app receives the lock, its state transitions back to active (`AppState.BACKGROUND`), and its `touch()` method is called.
5. **Ledger Synchronization**: When a `DatabaseManager` is attached, every granted acquisition appends a `HELD` row to `ResourceLocks`, every release retires that row, and queue hand-off moves it to the next app. The rules above are decided here in Python; the ledger only records the outcome.

---

## 6. Workload Generation (`backend/workload_generator/workload_generator.py`)

The `WorkloadGenerator` generates reproducible synthetic workload events for simulation testing.

### Design Principles
- **Reproducible Determinism**: Uses an explicit `random.Random(seed)` instance. Passing the same `seed` guarantees identical apps, priorities, footprints, and event sequences.
- **No Eviction Logic**: Produces workload event streams (`WorkloadEvent`) for the simulation controller to process without containing RAAE decision logic.

### Supported Workload Scenarios
1. **`"normal"`**: Balanced mix of memory allocations, deallocations, state changes, resource requests/releases, and reference bit touch accesses.
2. **`"high_memory_pressure"`**: Heavy memory allocations and explicit pressure events driving RAM usage into `ORANGE`/`RED` pressure levels.
3. **`"resource_contention"`**: High volume of resource acquisition requests across multiple apps targeting shared hardware resources (`Camera`, `Microphone`, `GPS`, `Audio`) to build wait queues.

### Primary Interface
`generate_workload(seed, ticks, scenario, num_apps, ram_mb)` returns a `WorkloadTrace` containing setup apps and a sequence of tick events for execution.

---

## 7. Database Layer (`database/`)

The `database` package is the only place SQL is written. `DatabaseManager` (`db.py`) owns the connection, `PRAGMA foreign_keys = ON`, schema application, and transaction boundaries; `repositories.py` holds every statement; `schema.sql` defines the tables.

### Entity Relationships

```text
                 (1)                (*)                 (1)
   Apps ──────────────────── ResourceLocks ──────────────────── Resources
     │                                                     ▲          │
     │ (1)                                                  │ (1)      │ (1)
     │                                                     └──────────┘
     │ (*)
     ├── MemoryEvents        (every allocation / deallocation / pressure sample)
     ├── EvictionLog         (every eviction decision and its outcome)
     ├── ConflictLog         (two references per row: waiting_app_id, blocking_app_id)
     │                       (plus a third reference to Resources.resource_id)
     └── LockRequests        (every lock attempt, including blocked ones)

   Resources.held_by_app_id  ──► Apps.app_id   (ON DELETE SET NULL)
   ResourceLocks.app_id      ──► Apps.app_id         (ON DELETE CASCADE)
   ResourceLocks.resource_id ──► Resources.resource_id (ON DELETE CASCADE)
   LockRequests.app_id       ──► Apps.app_id         (ON DELETE CASCADE)
   LockRequests.resource_id  ──► Resources.resource_id (ON DELETE CASCADE)

   SystemEvents has no foreign key: memory pressure is a property of the whole
   machine, so a sample is not stored against any single app.
```

The schema is in third normal form: every fact lives in exactly one table. `ConflictLog` deliberately references `Apps` twice rather than storing app attributes inline, and `LockRequests` is separate from `ResourceLocks` because ownership intervals and request attempts are different facts — a blocked request leaves a `LockRequests` row but never owns a lock.

### Persisted Events

| Event                   | Stored in                                  |
| ----------------------- | ------------------------------------------ |
| App creation            | `Apps`                                     |
| Memory allocation       | `MemoryEvents` (`action = ALLOCATE`)       |
| Memory deallocation     | `MemoryEvents` (`action = DEALLOCATE`)     |
| Memory pressure         | `SystemEvents`                             |
| Resource acquisition    | `ResourceLocks` + `LockRequests` (`GRANTED`)  |
| Resource request blocked| `LockRequests` (`QUEUED`)                  |
| Resource release        | `ResourceLocks` (`RELEASED`) + `LockRequests` (`RELEASED`) |
| Eviction attempt        | `EvictionLog` (`result = EVICTED` / `CONFLICT` / `WAIT`) |
| Successful eviction     | `EvictionLog` (`result = EVICTED`) + `MemoryEvents` (`EVICT`) |
| Blocked / waiting eviction | `EvictionLog` (`result = CONFLICT` or `WAIT`) + `MemoryEvents` (`EVICT_ATTEMPT`) |
| Wound-Wait conflict     | `ConflictLog` (`resolution_strategy = WOUND_WAIT`) |
| Freeze incident         | `ConflictLog` (`resolution_strategy = NONE`, details prefixed `FREEZE`) |

### Lock Consistency

`ResourceLocks` is the **authoritative ledger** of who holds what. `HELD` rows describe current ownership; `RELEASED` rows are retained history.

Three `Resources` columns duplicate that ledger for fast single-row reads, so triggers re-derive them on every ledger change:

| Column            | Derivation                                                    |
| ----------------- | ------------------------------------------------------------- |
| `available_units` | `max(capacity - SUM(units) over HELD locks, 0)`               |
| `held_by_app_id`  | `app_id` of the earliest `HELD` lock, `NULL` when none        |
| `status`          | `WAITING` if `waiting_queue` is non-empty, else `LOCKED`/`FREE` |

- `trg_resourcelocks_refresh_after_insert`
- `trg_resourcelocks_refresh_after_update`
- `trg_resourcelocks_refresh_after_delete`

Integrity rules enforced by the database:

| Rule                                                      | Mechanism                                                     |
| --------------------------------------------------------- | ------------------------------------------------------------- |
| Locks reference existing apps and resources               | `FOREIGN KEY` constraints                                      |
| A lock covers at least one unit                            | `CHECK (units > 0)`                                           |
| `status` is one of `HELD` / `RELEASED`                     | `CHECK`                                                        |
| A `RELEASED` row carries a `released_at`                   | `CHECK`                                                        |
| One live lock per `(app_id, resource_id)`                  | Partial unique index `ux_resourcelocks_held_app_resource`      |
| Removing an app or resource clears its dependent rows      | `ON DELETE CASCADE`                                            |

### Responsibility Split

- **Python decides.** `SemaphoreLock`, `ResourceManager` and the RAAE engine determine whether a lock may be granted, queued, or refused. No SQL expression encodes that policy.
- **SQLite persists and enforces consistency.** The triggers only reflect a decision that has already been made; they never adjudicate contention, and they clamp `available_units` at zero instead of rejecting a recorded acquisition.

---

## 8. Persistence Integration (`backend/persistence_bridge.py`)

`PersistenceBridge` connects the simulation modules to the repositories. It exists because two hooks the managers already called matched no repository, and because a database failure must never be allowed to corrupt in-memory state.

### Interface Mismatches It Resolves

| Hook already called by              | Problem                                         | Resolution                                     |
| ----------------------------------- | ----------------------------------------------- | ---------------------------------------------- |
| `SemaphoreLock` → `db.log_lock_request(...)`  | No matching repository, so nothing was written | Writes `LockRequests` plus the `ResourceLocks` ledger row |
| `MemoryManager.get_pressure_level` → `db.log_memory_pressure(...)` | No matching repository | Writes `SystemEvents`, the correct home for a machine-scoped sample |

`WoundWaitConflictAdapter` (`backend/raae_engine/conflict_manager.py`) covers a second mismatch: the Wound-Wait `ConflictManager` exposes `resolve_conflict(...)`, while the RAAE Engine asks for `get_held_resources(...)` and `evaluate_eviction_conflict(...)`. The adapter supplies the RAAE interface and delegates every contested decision to Wound-Wait, so neither module had to change.

### Failure Containment

The managers mutate their objects **before** persisting, so an exception escaping a write would abort the simulation halfway through a change it has already made. The bridge therefore routes every write through a guard that records the failure in `bridge.failures` and returns `None`/`False`:

- Writes made directly on the bridge — `save_app`, `save_resource`, `log_memory_action`, `log_lock_request`, `log_eviction`, `log_conflict`, `log_freeze`, `log_memory_pressure`.
- Writes made through a forwarded repository — `bridge.resource_locks.release_all_for_app(...)` returns a `SafeRepository` proxy that guards each call.
- Transactional groups — `bridge.transaction()` returns a `TolerantTransaction`, which swallows a failure to open or commit.

An exception raised by the *caller's own body* is still propagated: that is a simulation bug, not a persistence problem, and hiding it would be worse than losing a row.

### Wiring

`PersistenceBridge.wrap(db)` is idempotent and returns a plain `DatabaseManager` untouched, so the managers may be constructed with or without a bridge:

```python
db = DatabaseManager(":memory:")
bridge = PersistenceBridge(db)

memory = MemoryManager(total_memory=1000, db=bridge)
resources = ResourceManager(db=bridge)      # ResourceManager and SemaphoreLock wrap db themselves
resources.create_default_resources()

conflict_manager = WoundWaitConflictAdapter(
    WoundWaitConflictManager(db=bridge), resources
)
engine = RAAEEngine(
    conflict_manager,
    memory_manager=memory,
    resource_manager=resources,
    persistence=DatabaseEvictionPersistence(bridge),
)
```
