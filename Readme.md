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

