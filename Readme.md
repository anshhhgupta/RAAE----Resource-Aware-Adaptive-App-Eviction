# RAAE — Resource-Aware Adaptive App Eviction

> A resource-aware operating system simulation that prevents unsafe application eviction by coordinating memory management with shared resource locks.

---

## 📌 Overview

**RAAE (Resource-Aware Adaptive App Eviction)** is a simulation-based project designed around concepts from:

* Operating Systems
* Database Management Systems
* Design and Analysis of Algorithms
* Resource Allocation
* Deadlock Prevention

Traditional memory eviction algorithms select processes or applications based mainly on memory usage and recent activity. However, an application selected for eviction may still be holding an important shared resource.

RAAE introduces a **resource-aware eviction mechanism** that checks active resource locks before evicting an application.

---

## 🎯 Problem Statement

Consider the following scenario:

```text
Background App A
        │
        ▼
Holds GPS / Database Lock
        │
        ▼
Foreground App B requests the resource
        │
        ▼
Memory pressure occurs
        │
        ▼
Traditional algorithm selects App A
        │
        ▼
Unsafe eviction
        │
        ▼
Resource conflict / blocking occurs
```

RAAE solves this problem by making the **Memory Manager** communicate with the **Resource Manager** before eviction.

---

## 💡 Proposed Solution

The RAAE workflow is:

```text
Memory Pressure
       │
       ▼
Clock Algorithm
Selects Candidate
       │
       ▼
Does the App Hold a Resource?
       │
   ┌───┴────┐
   │        │
  NO       YES
   │        │
   ▼        ▼
 Evict   Check Waiting Apps
            │
            ▼
      Apply Conflict Resolution
            │
            ▼
      Safely Release Resource
            │
            ▼
           Evict
```

---

# 🚀 Features

## 📱 Application Management

Users can:

* Create simulated applications
* Start and stop applications
* Move applications between foreground and background
* Assign application priorities
* Allocate memory
* Monitor application states
* View active resources

Example application:

```text
App Name: Maps
Priority: High
Memory Used: 250 MB
State: Background
Resource Held: GPS
```

---

## 🧠 Memory Management

The system simulates limited system memory.

Features:

* Memory allocation
* Memory deallocation
* Memory pressure detection
* Used memory calculation
* Free memory calculation
* Eviction triggering
* Memory pressure levels

```text
GREEN   → Low memory usage
YELLOW  → Moderate memory usage
ORANGE  → High memory usage
RED     → Critical memory pressure
```

---

## ⏱️ Clock / Second-Chance Algorithm

The **Clock Algorithm** is used as the baseline memory eviction algorithm.

Each application has a reference bit:

```text
App A → 1
App B → 1
App C → 0
App D → 1
```

The Clock pointer searches for an application with:

```text
Reference Bit = 0
```

That application becomes an eviction candidate.

---

## 🔒 Resource Management

RAAE can simulate multiple shared resources:

* 🎤 Microphone
* 📍 GPS
* 🔊 Audio Output
* 📁 File Locks
* 🗄️ Database Locks
* 🔵 Bluetooth Devices

Each resource can have states such as:

```text
FREE
LOCKED
WAITING
```

Example:

```text
Resource: GPS

Held By:
Maps App

Waiting Queue:
1. Fitness App
2. Delivery App
```

---

## ⚔️ Resource Conflict Resolution

When multiple applications request the same resource, RAAE analyzes:

* Application priority
* Current resource holder
* Waiting applications
* Request order

The system can use the **Wound-Wait algorithm** to resolve conflicts.

```text
App A holds resource
        │
        ▼
App B requests resource
        │
        ▼
Compare Priority
        │
        ▼
Apply Wound-Wait
        │
        ▼
Resolve Conflict
```

---

# ⭐ RAAE Algorithm

The core contribution of the project is the integration of memory eviction and resource management.

### Traditional Approach

```text
Memory Pressure
      ↓
Select App
      ↓
Evict App
```

### RAAE Approach

```text
Memory Pressure
      ↓
Clock Selects App
      ↓
Check Resource Locks
      ↓
Check Waiting Applications
      ↓
Resolve Resource Conflict
      ↓
Release Resource Safely
      ↓
Evict Application
```

---

# 🖥️ User Interface

The application can include the following pages.

## 🏠 Dashboard

Displays:

* Total RAM
* Used RAM
* Free RAM
* Memory pressure
* Active applications
* Background applications
* Active locks
* Waiting applications
* Freeze incidents

---

## 📱 App Manager

Users can:

* Create an app
* Start an app
* Stop an app
* Change app priority
* Allocate memory
* Move app to background
* View app details

---

## 🧠 Memory Manager

Users can:

* Allocate memory
* Release memory
* Trigger memory pressure
* View memory usage
* View Clock pointer
* View reference bits
* Run the Clock algorithm

---

## 🔒 Resource Manager

Users can:

* Request resources
* Release resources
* View resource ownership
* View waiting queues
* Simulate resource conflicts

---

## ⚙️ Simulation Control

Users can:

```text
▶ Start Simulation
⏸ Pause
⏭ Next Step
🔄 Reset
⚡ Generate Memory Pressure
```

The simulation can run step-by-step.

---

## 🤖 RAAE Decision Engine

Displays the complete eviction decision:

```text
Eviction Candidate
        ↓
Resource Lock Check
        ↓
Waiting Queue Analysis
        ↓
Conflict Resolution
        ↓
Safe Resource Release
        ↓
Final Eviction
```

---

## 📊 Analytics

The system can display:

* Total evictions
* Freeze incidents
* Resource conflicts
* Average conflict resolution time
* Memory usage history
* Most conflicted resources
* Most frequently evicted applications

---

# 🧠 Algorithms Used

| Algorithm               | Purpose                        |
| ----------------------- | ------------------------------ |
| Clock / Second-Chance   | Select eviction candidate      |
| Wound-Wait              | Resolve resource conflicts     |
| DFS Cycle Detection     | Detect deadlocks               |
| Wait-For Graph          | Represent process dependencies |
| Adaptive Eviction Score | Improve eviction decisions     |
| Historical Analysis     | Analyze previous system events |

---

# 🗄️ Database Design

The project uses **SQLite** for storing simulation events and historical data. All SQL lives in the `database` package; see `database/schema.sql` for the authoritative definition.

## Entity Relationships

```text
              (1)             (*)             (1)
 Apps ───────────────── ResourceLocks ───────────────── Resources
  │                                                ▲          │
  │ (1)                                             │ (1)      │ (1)
  │                                                └──────────┘
  │ (*)
  ├── MemoryEvents   allocation / deallocation / pressure samples
  ├── EvictionLog    eviction decisions and outcomes
  ├── ConflictLog    two refs per row: waiting_app_id, blocking_app_id
  │                  plus a third reference to Resources.resource_id
  └── LockRequests   every lock attempt, including blocked ones

 Resources.held_by_app_id  ──► Apps.app_id          (ON DELETE SET NULL)
 ResourceLocks.app_id      ──► Apps.app_id          (ON DELETE CASCADE)
 ResourceLocks.resource_id ──► Resources.resource_id (ON DELETE CASCADE)
 LockRequests.app_id       ──► Apps.app_id          (ON DELETE CASCADE)
 LockRequests.resource_id  ──► Resources.resource_id (ON DELETE CASCADE)

 SystemEvents   no foreign key — memory pressure is machine-scoped
```

The schema is normalized to third normal form — every fact is stored in exactly one table. `ConflictLog` points at `Apps` twice rather than duplicating app data, and `LockRequests` stays separate from `ResourceLocks` because ownership intervals and request attempts are different facts: a blocked request leaves a `LockRequests` row but never owns a lock.

## Persisted Events

| Event                     | Stored in                                             |
| ------------------------- | ----------------------------------------------------- |
| App creation              | `Apps`                                                |
| Memory allocation         | `MemoryEvents` (`ALLOCATE`)                           |
| Memory pressure           | `SystemEvents`                                        |
| Resource acquisition      | `ResourceLocks` + `LockRequests` (`GRANTED`)          |
| Resource request blocked  | `LockRequests` (`QUEUED`)                             |
| Resource release          | `ResourceLocks` (`RELEASED`) + `LockRequests` (`RELEASED`) |
| Eviction attempt          | `EvictionLog` (`EVICTED` / `CONFLICT` / `WAIT`)       |
| Successful eviction       | `EvictionLog` (`EVICTED`) + `MemoryEvents` (`EVICT`)  |
| Blocked / waiting eviction| `EvictionLog` (`CONFLICT` / `WAIT`) + `MemoryEvents` (`EVICT_ATTEMPT`) |
| Wound-Wait conflict       | `ConflictLog` (`WOUND_WAIT`)                          |
| Freeze incident           | `ConflictLog` (`NONE`, details prefixed `FREEZE`)     |

## Apps

```text
Apps
---------------------------
app_id              (PK)
name
priority
state
memory_footprint
reference_bit
last_access_time
held_resources
status
```

---

## Resources

```text
Resources
---------------------------
resource_id          (PK)
name
capacity
available_units     -- derived from ResourceLocks
status              -- derived from ResourceLocks
held_by_app_id      (FK -> Apps.app_id) -- derived from ResourceLocks
waiting_queue
```

---

## ResourceLocks

The authoritative ledger of lock ownership. `HELD` rows describe current ownership; `RELEASED` rows are kept as history.

```text
ResourceLocks
---------------------------
lock_id              (PK, autoincrement)
app_id               (FK -> Apps.app_id)
resource_id          (FK -> Resources.resource_id)
units
status               HELD | RELEASED
acquired_at
released_at
```

**Consistency rules**

| Rule                                                    | Mechanism                                                |
| ------------------------------------------------------- | -------------------------------------------------------- |
| Locks reference existing apps and resources             | Foreign keys                                            |
| A lock covers at least one unit                          | `CHECK (units > 0)`                                     |
| Status is `HELD` or `RELEASED`                           | `CHECK`                                                  |
| A `RELEASED` row records when it was released            | `CHECK`                                                  |
| One live lock per `(app_id, resource_id)`                | Partial unique index `ux_resourcelocks_held_app_resource` |
| Deleting an app or resource clears dependent rows        | `ON DELETE CASCADE`                                      |

`available_units`, `held_by_app_id` and `status` on `Resources` duplicate the ledger for fast reads, so SQLite triggers re-derive them on every lock insert, update and delete:

```sql
available_units = max(capacity - SUM(units) over HELD locks, 0)
held_by_app_id  = app_id of the earliest HELD lock, NULL when none
status          = WAITING if waiting_queue is non-empty, else LOCKED / FREE
```

**Decision boundary:** Python decides *whether* a lock can be acquired (`SemaphoreLock` / `ResourceManager` / the RAAE engine). SQLite only records that decision and keeps the derived resource state consistent — no trigger grants, queues or refuses an acquisition.

---

## MemoryEvents

```text
MemoryEvents
---------------------------
event_id
app_id
action
memory_before
memory_after
pressure_level
timestamp
```

---

## EvictionLog

```text
EvictionLog
---------------------------
event_id
app_id
algorithm
reason
lock_checked
safe_release
result
timestamp
```

---

## ConflictLog

```text
ConflictLog
---------------------------
conflict_id
waiting_app_id          (FK -> Apps.app_id)
blocking_app_id         (FK -> Apps.app_id)
resource_id             (FK -> Resources.resource_id)
resolution_strategy
timestamp
resolved_by
details
```

---

## LockRequests

Every lock attempt and the answer it received. A blocked request has a row here even though no lock is ever held.

```text
LockRequests
---------------------------
request_id
app_id               (FK -> Apps.app_id)
resource_id          (FK -> Resources.resource_id)
outcome              GRANTED | QUEUED | RELEASED
requested_at
```

---

## SystemEvents

Machine-scoped telemetry. Memory pressure is a property of the whole system, so it is deliberately not stored against an `app_id`.

```text
SystemEvents
---------------------------
event_id
event_type                (MEMORY_PRESSURE)
pressure_level            GREEN | YELLOW | ORANGE | RED
used_memory
total_memory
timestamp
```

---

## Analytics (`db.analytics`)

`database/analytics.py` holds the read-only aggregations behind the Phase-II dashboards. Every function returns a plain Python structure — an `int`, a `float`/`None`, or a `list` of `dict` with stable keys — so results drop straight into Streamlit widgets or Matplotlib series with no adapter. No `sqlite3.Row`, cursor or model object is ever returned. Nothing here writes.

| Call | Returns |
|---|---|
| `total_eviction_count(result="EVICTED")` | `int`. Pass `result=None` to count every decision, including the `WAIT`/`CONFLICT` blocks. |
| `total_evictions_by_result()` | `dict` of `result` → count, for an outcome-split bar chart. |
| `total_freeze_incidents()` | `int` stranded waiters. |
| `total_conflicts(freeze_only=False)` | `int` conflict incidents. |
| `successful_resource_acquisitions()` | `int` granted lock requests. |
| `resource_releases()` | `int` released lock requests. |
| `queued_resource_requests()` | `int` blocked attempts, which hold no lock but still contended. |
| `evictions_by_app(result="EVICTED", limit=None)` | `list[dict]`: `app_id`, `app_name`, `evictions`, `attempts`, `blocked`, `memory_freed_mb`. |
| `evictions_by_resource(limit=None)` | `list[dict]`: `resource_id`, `resource_name`, `evictions`, `apps_evicted`. |
| `conflicts_by_resource(limit=None)` | `list[dict]`: `resource_id`, `resource_name`, `conflicts`, `freezes`. |
| `events_over_time(bucket_seconds=60, start=None, end=None)` | `list[dict]` timeline: `bucket_start`, `bucket_end`, plus `evictions`, `blocked_evictions`, `conflicts`, `freezes`, `grants`, `releases`, `memory_events`. |
| `average_conflict_resolution_latency()` | `float` seconds, or `None` when no waiter was ever served. |
| `conflict_resolution_samples(limit=None)` | `list[dict]` of the individual `queued_at` / `granted_at` / `latency_seconds` waits behind that average. |
| `policy_comparison()` | `list[dict]` per algorithm: `algorithm`, `policy`, `evictions`, `attempts`, `blocked_evictions`, `safe_releases`, `evictions_without_safe_release`, `freeze_incidents`. |
| `policy_delta()` | `dict` baseline-minus-RAAE difference, with `available: False` when either policy is missing. |
| `summary()` | `dict` of every headline counter — one call for a metric row. |

### Definitions worth knowing

- **Freeze** — a conflict whose `details` opens with the `FREEZE` marker written by both freeze paths. The marker, not `resolution_strategy = 'NONE'`, identifies it, so an unrelated unresolved conflict is not miscounted as a stranded waiter.
- **Latency** — `ConflictLog` stores one timestamp per incident, so it cannot express a duration on its own. The measurable wait is in `LockRequests`: the app was queued at its `QUEUED` request and served by the first `GRANTED` request after it for the same app and resource. Each queued request is charged its own wait. `None` is returned when nothing was ever served — that absence is itself the finding, and it is kept distinct from `0.0`.
- **Evictions by resource** — `EvictionLog` records which app was considered, not which resource it held. `ResourceLocks` is the only normalized link between an app and the resources it held, so an eviction is attributed to every resource in that app's lock *history*. The figure is "evictions of apps that had held this resource", not "evictions caused by this resource" — the eviction that retires those locks clears the evidence in the same moment.
- **`memory_freed_mb`** — the `memory_before - memory_after` delta of the app's `EVICT` MemoryEvents. It cannot be read off `Apps`, because eviction zeroes the persisted footprint in the same step that records the eviction.
- **Policy names** — the active policy is stored as `EvictionLog.algorithm`: baseline writes `BASELINE_CLOCK`, the RAAE engine writes `RAAE_CLOCK`. Both are mapped to their policy name (`policy_comparison` and `policy_delta` only) so callers need not know the engine suffixes its own algorithm string.

### Index use

Every query filters or groups on an indexed column. Two indexes exist specifically for these aggregations: `idx_evictionlog_result_app` puts `result` first so both a `COUNT` and a `GROUP BY app_id` are served from one index, and `idx_conflictlog_freeze` is a partial index holding only freeze rows. The freeze predicate is written as a literal rather than a bind parameter because SQLite can only match a partial index when the query repeats the index predicate literally — binding it would silently downgrade the count to a full table scan. `tests/test_analytics.py` asserts the query plans for both, including a test that pins down why the parameter form fails.

---

## Persistence Integration

`backend/persistence_bridge.py` connects the simulation modules to the repositories. It exists to resolve two hooks the managers already called that matched no repository — `SemaphoreLock`'s `log_lock_request` and `MemoryManager.get_pressure_level`'s `log_memory_pressure` — without rewriting either module.

It also enforces one rule for an OS simulator: **a database failure may cost history, but must never corrupt in-memory simulation state.** The managers mutate their objects before persisting, so an exception escaping a write would abort the simulation halfway through a change it had already made. The bridge routes every write — direct, through a forwarded repository, or inside a transaction — through a guard that records the failure and returns `None`. An error raised by the simulation's own logic still propagates.

```python
db = DatabaseManager(":memory:")
bridge = PersistenceBridge(db)

memory = MemoryManager(total_memory=1000, db=bridge)
resources = ResourceManager(db=bridge)
resources.create_default_resources()

engine = RAAEEngine(
    WoundWaitConflictAdapter(WoundWaitConflictManager(db=bridge), resources),
    memory_manager=memory,
    resource_manager=resources,
    persistence=DatabaseEvictionPersistence(bridge),
)
```

---

# 🏗️ System Architecture

```text
                    ┌───────────────┐
                    │   Frontend UI │
                    │   Dashboard   │
                    └───────┬───────┘
                            │
                            ▼
                  ┌───────────────────┐
                  │ Simulation Engine │
                  └─────────┬─────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
   Memory Manager    Resource Manager    App Manager
          │                 │                 │
          ▼                 ▼                 ▼
   Clock Algorithm     Lock Manager      App States
          │                 │
          └──────────┬──────┘
                     ▼
              ┌──────────────┐
              │ RAAE Engine  │
              └──────┬───────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
    Wound-Wait           Deadlock Detection
          │                     │
          └──────────┬──────────┘
                     ▼
                  SQLite
                     │
                     ▼
            Analytics & History
```

---

# 📂 Project Structure

```text
RAAE/
│
├── frontend/
│   ├── components/
│   ├── pages/
│   └── dashboard/
│
├── backend/
│   ├── simulation_engine/
│   ├── memory_manager/
│   ├── resource_manager/
│   ├── raee_engine/
│   └── algorithms/
│
├── database/
│   ├── schema.sql
│   └── raee.db
│
├── logs/
├── tests/
│
├── README.md
└── requirements.txt
```

---

# 🛠️ Tech Stack

| Component            | Technology            |
| -------------------- | --------------------- |
| Backend / Simulation | Python                |
| Database             | SQLite                |
| Frontend             | React or Streamlit    |
| Visualization        | Matplotlib / Chart.js |
| Memory Algorithm     | Clock / Second-Chance |
| Resource Conflict    | Wound-Wait            |
| Deadlock Detection   | DFS + Wait-For Graph  |

---

# 📊 Algorithm Comparison

The project can compare traditional eviction algorithms with RAAE.

| Metric                | Traditional Algorithm | RAAE |
| --------------------- | --------------------- | ---- |
| Memory Eviction       | ✅                     | ✅    |
| Resource Awareness    | ❌                     | ✅    |
| Lock Check            | ❌                     | ✅    |
| Conflict Resolution   | ❌                     | ✅    |
| Safe Resource Release | ❌                     | ✅    |
| Historical Analysis   | Optional              | ✅    |

---

# 🔬 Advanced Features

Future or advanced features include:

* Wait-For Graph visualization
* Deadlock detection
* Adaptive eviction scoring
* Freeze risk prediction
* Historical conflict analysis
* Event replay system
* Multiple algorithm comparison
* Priority-based scheduling
* Resource conflict analytics

---

# 🎯 Project Novelty

RAAE does **not** claim to invent the Clock or Wound-Wait algorithms.

The main contribution is the **resource-aware integration layer**.

```text
Traditional System

Memory Manager
      ↓
Eviction


RAAE System

Memory Manager
      ↓
Resource Check
      ↓
Conflict Analysis
      ↓
Safe Resource Release
      ↓
Eviction
```

This integration allows memory-management decisions to consider active resource dependencies.

---

# 📈 Expected Outcome

RAAE aims to:

* Reduce simulated application freezes
* Prevent unsafe eviction
* Safely manage shared resources
* Resolve application conflicts
* Compare baseline algorithms with RAAE
* Store simulation history in SQLite
* Provide useful analytics through the UI

---

# 🖥️ Interactive Streamlit Dashboard

RAAE includes a full interactive Streamlit dashboard featuring custom dark mode styling, real-time memory monitoring, algorithm comparison, and deep analytics.

### Launching the Dashboard

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the automated test suite
pytest tests/test_end_to_end_scenario.py -v

# 3. Launch the Streamlit application
streamlit run app.py
```

### Dashboard Views
1. **🏠 Overview**: System-monitoring dashboard — header (simulation status, tick, selected policy), metric cards (total/used/available RAM, utilization, active apps, evictions, conflicts), memory monitor (RAM bar, pressure indicator, used-vs-available gauge, utilization chart over ticks), application summary, and a filterable event log with timestamp, type, app, resource, decision and explanation. Every figure is read live from the backend or SQLite; anything unreadable is shown as **Unavailable** with its origin listed in the "Data availability" panel.
2. **📱 Applications**: Create applications (name, footprint, priority 1–5, initial state) and drive the backend against them — change priority, move between foreground and background, simulate an access or set the reference bit, and request or release a lock on Camera, Microphone, GPS or Audio. Every action reports what the backend actually did, refuses inputs it cannot accept (blank names, negative footprints, out-of-band priorities, footprints larger than free memory), and disables the controls the backend cannot honour — such as changing the state of an app queued for a contended resource. Includes the full application table and a detail inspector with lock, queue and eviction-history data.
3. **⚙️ Simulation**: Runs the real eviction workflow under your control — Start, Pause, Resume, Stop, Reset, advance one tick or many, trigger memory pressure, and choose BASELINE_CLOCK or RAAE. Ticks execute on demand (no background threads), and the controls are enabled from the run's actual status so an invalid transition cannot be taken. Shows the current tick, status, total/used/available RAM, active applications, the selected policy and the most recent eviction decision, plus an eight-step **decision timeline** — pressure → Clock selection → RAAE resource check → Conflict Manager → Wound-Wait → release/waiting → eviction outcome → persistence — where each step is marked *Confirmed*, *Not executed*, *Not applicable* or *Unavailable* according to the events the run actually produced. Every run writes its own SQLite log under `database/runs/`, and Reset keeps those files and lists them in the run history.
4. **🔒 Resources**: Real-time semaphore monitor for the four hardware resources (`Camera`, `Microphone`, `GPS`, `Audio`). Displays resource status (name, holder, free/locked, waiting queue, queued count, single-holder integrity check), interactive controls to request/release locks via the ResourceManager, conflict monitor tracking requesting app, holder, priority/timestamps, Wound-Wait decisions (`WOUND_HOLDER`, `WAIT`, `NO_CONFLICT`), reason, and actual resolution outcomes, plus recent conflict events from SQLite `ConflictLog`.
5. **📈 Analytics & Insights**: Empirical comparison between `BASELINE_CLOCK` and `RAAE` under identical reproducible workloads (deterministic or seeded synthetic traces). Displays side-by-side metric tiles (evictions, blocked attempts, conflicts, freezes, latency, memory utilization, releases), an unbiased empirical comparison table, and five dark-themed Matplotlib charts: (1) Eviction counts comparison, (2) Resource conflicts, (3) Freeze incidents, (4) Memory utilization over time, and (5) Eviction events across simulation ticks.

---

## 🧪 Step-by-Step Demonstration Guide

Follow this end-to-end evaluation flow to observe RAAE's resource-aware adaptive eviction, deadlock avoidance, and live telemetry:

| Step | Action / Page | Expected Observation | Backend Verification |
|:---:|:---|:---|:---|
| **1** | **Launch App** (`streamlit run app.py`) | Streamlit opens at `http://localhost:8501`. Dark-themed layout loads with sidebar navigation, live SQLite connection, and status metrics. | `DatabaseManager` connects, default resources (`Camera`, `Microphone`, `GPS`, `Audio`) initialized. |
| **2** | **Applications Page** → Create Apps | Register 3 applications: <br>• **Maps** (300 MB, Priority 3, Background)<br>• **Fitness** (250 MB, Priority 2, Background)<br>• **Music** (300 MB, Priority 1, Background) | Validated via `ui/app_actions.py`, registered in `MemoryManager` & persisted in SQLite. |
| **3** | **Overview Page** → Verify RAM | Used RAM shows **850 MB / 1000 MB** (85.0% utilization). Pressure gauge displays **ORANGE** warning (`is_under_pressure() == True`). | `MemoryManager.get_pressure_level()` returns `ORANGE`. |
| **4** | **Resources Page** → Lock Allocation | Under *Lock Allocation Controls*:<br>• Grant `Camera` and `GPS` to `Maps`<br>• Grant `Audio` and `Microphone` to `Fitness` | `ResourceManager.request_resource()` acquires hardware locks and updates `holders`. |
| **5** | **Resources Page** → Resource Queue | Request `GPS` for `Music` (Priority 1). Notice `GPS` is already held by `Maps` (Priority 3). Request is denied and `Music` transitions to `WAITING`. | Single-holder semaphore integrity holds: `GPS` holder is `Maps`, waiting queue contains `(app_music, Music)`. |
| **6** | **Simulation Page** → Memory Pressure | Trigger simulation memory pressure (target threshold 75–85%). | Pressure detected (`usage >= 75%`). Simulation tick loop triggers candidate search. |
| **7** | **Simulation Page** → Clock Selection | Advance 1 tick under `RAAE`. Clock hand scans circular buffer and identifies background candidate with reference bit 0. | `MemoryManager.clock_step()` selects candidate (`Maps` or `Music`). |
| **8** | **Simulation Page** → RAAE Resource Check | Timeline Step 3 highlights *Confirmed*: RAAE identifies candidate's held hardware resources before any eviction decision. | `sim.get_candidate_resources()` exposes candidate locks. |
| **9** | **Resources Page** → Conflict Monitor | Live conflict detected on `GPS`: Requester `Music` (P1) vs Holder `Maps` (P3). | `ResourceConflictManager` identifies contested semaphore. |
| **10** | **Resources Page** → Wound-Wait Adjudication | Adjudication shows `WAIT` (Requester P1 < Holder P3; younger/lower priority requester waits, older/higher priority holder continues). No preemptive wound. | Wound-Wait algorithm in `ResourceConflictManager.resolve_conflict()` evaluates priorities correctly. |
| **11** | **Resources Page** → Safe Release | Select `Maps` and release `GPS`. Semaphore immediately awakens `Music`, transitions `Music` back to `BACKGROUND`, and promotes it as the new holder of `GPS`. | `ResourceManager.release_resource()` promotes head of queue; zero integrity problems. |
| **12** | **Simulation Page** → Valid Eviction | Complete eviction of `Maps`. Remaining held resource (`Camera`) is safely released first, preventing orphaned locks, then `Maps` memory footprint is freed. | `MemoryManager.evict_app()` sets memory footprint to 0 and state to `EVICTED`. |
| **13** | **Overview Page** → RAM Accounting | Used RAM drops from 850 MB to 550 MB. `Maps` is removed from active apps. `Camera` is completely free (0 holders). | Memory accounting matches: `1000 - 450 = 550 MB free`. |
| **14** | **Overview Page** → SQLite Persistence | Confirm eviction record is persisted in SQLite with `algorithm="RAAE"`, `lock_checked=1`, and `safe_release=1`. | SQLite query on `EvictionLog` verifies persisted record. |
| **15** | **Overview Page** → Event Timeline | The unified SQLite Event Feed displays memory, lock, and eviction events in reverse-chronological order. | `DatabaseManager.get_recent_events()` returns actual history. |
| **16** | **Analytics Page** → Policy Comparison | Select Deterministic Workload (1000 MB RAM, 75% threshold, 1 tick). Run comparison. | `BASELINE_CLOCK` blindly kills holder causing **1 Freeze Incident**; `RAAE` inspects locks and achieves **0 Freeze Incidents**. |
| **17** | **Analytics Page** → Telemetry Charts | Inspect 5 Matplotlib charts: Eviction Counts, Resource Conflicts, Freeze Incidents, Memory Utilization Over Time, and Eviction Timeline. | Visual confirmation of freeze prevention and safe lock handoff under identical initial states. |

---

# 👨‍💻 Future Scope

Possible future improvements:

* Machine learning-based eviction prediction
* Reinforcement learning
* Android system integration
* Linux process monitoring
* Real-time resource monitoring
* Cloud resource scheduling
* Distributed systems resource management

---

# 🏁 Conclusion

RAAE combines important concepts from:

```text
Operating Systems
        +
Memory Management
        +
Resource Allocation
        +
Deadlock Prevention
        +
Algorithms
        +
DBMS
        +
Analytics
```

The project provides an interactive simulation environment where users can observe how applications compete for limited memory and shared resources. It demonstrates how resource-aware eviction can improve upon traditional eviction strategies by checking dependencies before removing an application.

