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
