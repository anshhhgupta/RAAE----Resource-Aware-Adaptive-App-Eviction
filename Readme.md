# RAAE — Resource-Aware Adaptive App Eviction

> A resource-aware operating system simulation that prevents unsafe application eviction by coordinating memory management with shared resource locks.

## Phase-II Quick Start

RAAE is a Python/Streamlit simulation that combines Clock (second-chance)
candidate selection with resource checks and Wound-Wait conflict decisions.
The dashboard displays live app and resource state, RAM metrics, simulation
events, SQLite eviction/conflict logs, and an isolated Clock-vs-RAAE evaluation.
It does not implement operating-system process control or claim real device
access or real application freezes.

### Architecture

```text
Streamlit dashboard (app.py)
          │
          ▼
SimulationController ───── SQLite repositories
    │          │
    │          └── ResourceManager / semaphore queues
    ├── MemoryManager / Clock candidate selection
    ├── RAAEEngine
    │       └── ResourceConflictManager ── Wound-Wait ConflictManager
    └── WorkloadGenerator

Policy evaluator: independent cloned snapshots for Clock vs RAAE charts
```

### Module responsibilities

| Module | Responsibility |
| --- | --- |
| `app.py` | Streamlit controls, app/resource tables, RAM metrics, event log, and comparison charts |
| `backend/simulation_controller.py` | Connects simulation managers, database operations, UI actions, and event queries |
| `backend/models/app.py`, `backend/models/resource.py` | App, resource, state, and status data models |
| `backend/memory_manager/memory_manager.py` | RAM accounting, pressure levels, Clock hand, candidate selection, and eviction state |
| `backend/resource_manager/resource_manager.py`, `backend/resource_manager/semaphore.py` | Resource registration, acquisition/release, holders, and FIFO wait queues |
| `backend/raae_engine/raae_engine.py`, `backend/raae_engine/conflict_manager.py` | Candidate resource checks and translation of conflict outcomes into eviction decisions |
| `backend/conflict_manager/conflict_manager.py` | Wound-Wait decisions based on priority, then timestamp |
| `backend/workload_generator/workload_generator.py` | Sample app templates, random events, and memory-pressure allocation |
| `backend/policy_evaluation.py` | Fair fixed-snapshot Clock-vs-RAAE trial on independent state clones |
| `backend/phase2_resources.py` | Idempotently registers the Camera, Microphone, GPS, and Audio IDs used by the dashboard demo |
| `database/schema.sql`, `database/db.py`, `database/repositories.py` | SQLite schema, database lifecycle, and persistence queries |
| `tests/` | Model, manager, database, controller, engine, and policy-evaluation tests |

### Setup and run

Use Python 3.10 or later. From the repository root, create and activate a
virtual environment and install the dashboard dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start the dashboard with this command from the activated environment:

```powershell
python -m streamlit run app.py
```

Open the local URL printed by Streamlit. `demo.py` is a separate command-line
component demonstration; the full Phase-II workflow runs in the dashboard.

### Phase-II demonstration steps

1. In **Add Application**, create a low-priority background app (for example,
   priority 1 and 250 MB) and a higher-priority app (for example, priority 10
   and 200 MB). Priority, initial state, and memory are configurable at creation.
2. In **Resource Operations**, choose the first app and request **Camera**.
   Choose the second app and request **Camera** too; it should appear as a
   waiter. The same controls expose **Microphone**, **GPS**, and **Audio**.
3. Check the application/resource tables and RAM cards. Trigger memory pressure
   from the sidebar to allocate memory toward the selected percentage.
4. Press **Start**, then **Clock Tick**. The event log identifies the selected
   Clock candidate and RAAE decision. With a higher-priority waiter, Wound-Wait
   can wound the lower-priority holder, safely release the resource, wake the
   waiter, and allow the candidate eviction.
5. Confirm the resource holder/queue state and inspect the dashboard event log.
   Expand the SQLite **Conflict Log** and **Eviction Log** to verify recorded
   outcomes. Release any remaining resource using **Release Resource**.
6. Run **Clock vs RAAE Evaluation** and inspect its metrics and three charts.
   Both policies start from independent copies of the same current snapshot;
   this is a fixed-snapshot eviction-attempt comparison, not a replay of
   recorded operations.

The controller currently uses SQLite `:memory:` storage for each Streamlit
session. Database log entries are available during that session but do not
survive a process restart. The dashboard event table is a bounded in-memory
simulation event log.

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
* Configure priority, initial foreground/background state, and initial memory
* View application state, memory usage, reference bit, and held resources

Example application:

```text
App Name: Maps
Priority: 3
Memory: 250 MB
State: Background
Resource Held: GPS
```

---

## 🧠 Memory Management

The system simulates limited system memory.

Features:

* Live used/free RAM and pressure-level metrics
* Configurable memory-pressure trigger
* Clock/Second-Chance candidate selection on each manual tick
* Eviction after the RAAE safety decision

```text
GREEN   → below 60%
YELLOW  → 60% to below 75%
ORANGE  → 75% to below 90%
RED     → 90% or above
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
* 📷 Camera
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
* Last-access timestamp as a tie-breaker

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
* Simulated wait/freeze proxy (RAAE `WAIT` decisions; not a real freeze)

The Streamlit dashboard also provides a **Clock vs RAAE Evaluation** section. It
runs both policies on independent copies of the current app/resource snapshot,
using the same eviction-attempt limit and preserving the live simulation state.
This is a fixed-snapshot eviction-attempt comparison, not a replay of recorded
resource requests or other workload operations. The evaluation reports
successful evictions, Wound-Wait outcomes written to its isolated conflict log,
and passive holder/waiter contention observations separately. The latter are
not conflict-manager decisions. Its simulated wait/freeze proxy counts RAAE
`WAIT` decisions; it does not establish an actual or operating-system freeze.
Resource-blocked evictions count only non-eviction decisions in
`WAIT`, `RESOLVE_REQUIRED`, `BLOCKED`, or `NOT_SAFE` that identify at least one
resource. Latency is average RAAE engine orchestration time for conflict cases,
not isolated conflict-resolver execution time; it is unavailable when no
conflict case is measured. Plain Clock has no conflict resolver. Install the
dashboard dependencies with `pip install -r requirements.txt`.

---

## ⚙️ Simulation Control

```text
▶ Start Simulation
⏹ Stop
↺ Reset
⏱ Clock Tick
🎲 Random Event
```

The simulation advances explicitly through **Clock Tick**; the dashboard also
provides **Refresh view** without advancing simulation state.

---

## 🤖 RAAE Decision Engine

The event log displays the selected Clock candidate, RAAE status/reason, and
related resource identifiers. RAAE checks held/waiting resources, delegates
contention to Wound-Wait, and proceeds with eviction only after the safe
decision. A successful decision may release the candidate's resource and wake
the next queued app before evicting the candidate.

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

## 📊 Dashboard data

The dashboard shows live RAM/app/resource state, a bounded simulation event
log, and SQLite-backed conflict and eviction log expanders. It does not provide
historical memory charts or advanced risk scoring.

---

# 🧠 Algorithms Used

| Algorithm               | Purpose                        |
| ----------------------- | ------------------------------ |
| Clock / Second-Chance   | Select eviction candidate      |
| Wound-Wait              | Choose whether to wound a holder or wait, based on priority and timestamp |

---

# 🗄️ Database Design

`database/schema.sql` defines the SQLite tables `Apps`, `Resources`,
`ResourceLocks`, `MemoryEvents`, `EvictionLog`, and `ConflictLog`. The current
controller writes app/resource snapshots plus eviction and Wound-Wait conflict
records. The Streamlit controller currently opens a per-session `:memory:`
database; records are visible in the dashboard while that session is alive and
are not durable across process restarts.

---

# 🏗️ System Architecture

```text
Streamlit UI
    │
    ▼
SimulationController ─── SQLite repositories
    ├── MemoryManager / Clock
    ├── ResourceManager / SemaphoreLock
    ├── RAAEEngine ─── ResourceConflictManager ─── WoundWait ConflictManager
    └── WorkloadGenerator

Policy evaluator ─── cloned snapshot states ─── Clock and RAAE metrics/charts
```

---

# 📂 Project Structure

```text
RAAE/
├── app.py
├── backend/
│   ├── conflict_manager/
│   ├── memory_manager/
│   ├── models/
│   ├── raae_engine/
│   ├── resource_manager/
│   ├── workload_generator/
│   ├── policy_evaluation.py
│   ├── phase2_resources.py
│   └── simulation_controller.py
├── database/
│   ├── schema.sql
│   ├── db.py
│   └── repositories.py
├── tests/
├── demo.py
├── Readme.md
└── requirements.txt
```

---

# 🛠️ Tech Stack

| Component            | Technology            |
| -------------------- | --------------------- |
| Backend / Simulation | Python                |
| Database             | SQLite                |
| Frontend             | Streamlit             |
| Visualization        | Matplotlib            |
| Memory Algorithm     | Clock / Second-Chance |
| Resource Conflict    | Wound-Wait            |

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
| Snapshot comparison   | ❌                     | ✅    |

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

* Compare simulated wait/freeze proxy decisions (not real freezes)
* Prevent unsafe eviction
* Safely manage shared resources
* Resolve application conflicts
* Compare baseline algorithms with RAAE
* Record in-session eviction/conflict outcomes in SQLite
* Display current-state metrics and fixed-snapshot comparison charts

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
