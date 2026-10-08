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
