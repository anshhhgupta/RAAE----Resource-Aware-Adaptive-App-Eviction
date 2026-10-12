# RAAE Database Reference

SQLite persistence layer for the RAAE simulator. Everything SQL lives in the
`database/` package; no other package issues a statement.

```python
from database.db import DatabaseManager

db = DatabaseManager("database/raae.db")   # or ":memory:"
db.apps.save(app)                         # one repository per table
db.analytics.summary()                    # read-only aggregations
db.close()
```

`DatabaseManager.__init__` applies `schema.sql` (fully idempotent) and sets
`PRAGMA foreign_keys = ON`, so referential integrity is enforced from the first
statement onward.

---

## 1. Tables

| Table | Purpose | Primary key |
|---|---|---|
| `Apps` | Registered applications and their memory/state | `app_id` |
| `Resources` | Hardware resources, their capacity and live lock state | `resource_id` |
| `ResourceLocks` | **Authoritative ledger** of lock ownership | `lock_id` |
| `LockRequests` | Every lock *attempt* and the outcome it received | `request_id` |
| `MemoryEvents` | Per-app allocation / deallocation / eviction footprint history | `event_id` |
| `EvictionLog` | Every eviction decision and its outcome | `event_id` |
| `ConflictLog` | Resource contention, Wound-Wait resolutions and freezes | `conflict_id` |
| `SystemEvents` | Machine-scoped telemetry (memory pressure samples) | `event_id` |

`LockRequests` is deliberately separate from `ResourceLocks`: ownership intervals
and request attempts are different facts, so a blocked request leaves a
`LockRequests` row (`QUEUED`) while never holding a lock.

`SystemEvents` carries no `app_id` because memory pressure is a property of the
machine, not of any one application.

### Schema level

Third normal form, with two documented exceptions:

- `Resources.waiting_queue` — a FIFO queue stored as CSV. Strictly a 1NF
  violation, kept because the queue is read and written as one ordered unit by
  `SemaphoreLock` and is never queried relationally.
- `Apps.held_resources` — a set of resource ids stored as CSV, maintained by
  Python. Same reasoning: read as one value, never joined.

Both are called out rather than silently accepted. `Resources.held_by_app_id` is
the normalized alternative to the latter and is maintained by trigger.

---

## 2. Relationships

```text
Apps (1) ──< (N) ResourceLocks >── (1) Resources
 │  ▲                                 ▲
 │  └──── Resources.held_by_app_id ───┘   ON DELETE SET NULL
 │
 ├──< (N) MemoryEvents
 ├──< (N) EvictionLog
 ├──< (N) LockRequests
 └──< (N) ConflictLog   (twice: waiting_app_id and blocking_app_id)

ConflictLog ──> Resources
SystemEvents   (no foreign key — machine-scoped)
```

Every relationship declares an explicit `ON DELETE` action, so dependent history
is never orphaned:

| Foreign key | On delete |
|---|---|
| `ResourceLocks.app_id` → `Apps` | `CASCADE` |
| `ResourceLocks.resource_id` → `Resources` | `CASCADE` |
| `MemoryEvents.app_id` → `Apps` | `CASCADE` |
| `EvictionLog.app_id` → `Apps` | `CASCADE` |
| `LockRequests.app_id` → `Apps` | `CASCADE` |
| `LockRequests.resource_id` → `Resources` | `CASCADE` |
| `ConflictLog.waiting_app_id` → `Apps` | `CASCADE` |
| `ConflictLog.blocking_app_id` → `Apps` | `CASCADE` |
| `ConflictLog.resource_id` → `Resources` | `CASCADE` |
| `Resources.held_by_app_id` → `Apps` | `SET NULL` |

`Resources.held_by_app_id` uses `SET NULL` rather than `CASCADE`: deleting an app
must not delete the resource itself.

Cascade deletes do fire the `ResourceLocks` triggers, so removing an app also
re-derives the state of every resource it was holding.

---

## 3. Important indexes

32 indexes. Every foreign-key column is indexed, so no relationship traversal is
a table scan.

**Lock integrity**

- `ux_resourcelocks_held_app_resource` — `UNIQUE (app_id, resource_id) WHERE status = 'HELD'`.
  Partial and unique: one app cannot hold two live locks on the same resource,
  while released rows are excluded so full acquisition history is retained.

**Analytics access paths**

- `idx_evictionlog_result_app` — `(result, app_id)`. `result` leads the key so
  both a `COUNT` and a `GROUP BY app_id` are served from a single index; every
  dashboard headline is a filter on `result`.
- `idx_conflictlog_freeze` — partial index over `ConflictLog(conflict_id) WHERE
  details LIKE 'FREEZE%'`, holding only stranded-waiter rows.
  The predicate is written as a **literal, not a bind parameter**: SQLite matches
  a partial index only when the query repeats the index predicate literally, and
  a bound `?` is opaque at prepare time. `database/analytics.py` keeps it in
  `FREEZE_PREDICATE` for that reason.
- `idx_lockrequests_outcome` — `(outcome)`, serves the granted / released /
  queued counters as a covering index.

**Join and ordering paths**

`idx_resourcelocks_resource`, `idx_resourcelocks_status`,
`idx_resourcelocks_acquired`, `idx_conflictlog_waiting`,
`idx_conflictlog_blocking`, `idx_conflictlog_resource_ts`,
`idx_evictionlog_app_ts`, `idx_memoryevents_app_ts`, and a `timestamp` index on
each log table for the time-bucketed series.

Every one is asserted by `EXPLAIN QUERY PLAN` in `tests/test_analytics.py` and
`tests/test_resource_lock_consistency.py`.

---

## 4. The transaction used during eviction

`DatabaseManager.transaction()` (`database/transaction.py`) is the only atomic
unit of work. The outermost scope issues `BEGIN`; nested scopes use `SAVEPOINT`,
because SQLite has no nested `BEGIN`.

An eviction is one transaction. `DatabaseEvictionPersistence.persist_eviction_event`
issues, atomically:

1. `save_app` for every registered app (state after the decision),
2. `save_resource` for every resource involved,
3. `resource_locks.release_all_for_app(candidate)` when the eviction succeeded,
4. `memory_events.log_event(..., action = 'EVICT' | 'EVICT_ATTEMPT')`,
5. `eviction_log.log_eviction(...)` returning the `event_id`,
6. `conflict_log.log_conflict(...)` for each protection record.

**Rollback.** Any failure raises out of the `with` block, the scope rolls back
and the exception propagates, so the database never records a partially applied
eviction. Nested scopes roll back only their own writes before re-raising, so an
inner failure can be caught by the outer block without losing the outer work.

Three SQLite behaviours would silently break this, and each is blocked in
`TransactionConnection`:

| Blocked | Why |
|---|---|
| `with conn:` committing | Would commit the enclosing transaction early |
| `executescript()` | Issues an implicit `COMMIT` before running |
| `commit()` / `rollback()` | Reached through a proxy, would end the outer scope |

`init_db()` refuses to run inside an open transaction for the same reason.

---

## 5. Triggers

Four triggers, all in `schema.sql`. They maintain state; none of them makes a
policy decision.

### Authority

`ResourceLocks` is the authoritative ledger of who holds what. Three `Resources`
columns duplicate it for fast single-row reads and are **derived**:

```text
available_units = max(capacity - SUM(units) over HELD locks, 0)
held_by_app_id  = app_id of the earliest HELD lock, else NULL
status          = WAITING if waiting_queue is non-empty
                  else LOCKED if any HELD lock exists
                  else FREE
```

### The triggers

| Trigger | Fires on | Purpose |
|---|---|---|
| `trg_resourcelocks_refresh_after_insert` | `ResourceLocks` INSERT | Re-derive the affected resource |
| `trg_resourcelocks_refresh_after_update` | `ResourceLocks` UPDATE | Re-derive the new resource **and** the old one if the lock moved |
| `trg_resourcelocks_refresh_after_delete` | `ResourceLocks` DELETE | Re-derive the affected resource |
| `trg_resources_repair_derived` | `Resources` UPDATE | Repair the derived columns when they disagree with the ledger |

The first three only see ledger changes, which left a gap: a writer updating
`Resources` directly could set the derived columns to anything, and a `capacity`
change invalidated them without touching `ResourceLocks` at all. Either path
broke `available_units = capacity - SUM(held units)`.

`trg_resources_repair_derived` closes it by comparing the incoming row against
the same three expressions and rewriting only when they disagree. Two consequences
are intentional:

- It is a **no-op** for the other three triggers and for any writer that already
  persisted a correct row.
- It **terminates**. The repair makes the row agree, so the `WHEN` clause is
  false on the next evaluation and nothing recurses.

It is deliberately **not** an `AFTER INSERT` trigger: `create_resource()` is
legitimately used to insert a resource that is already held (`available_units = 0`
plus a `held_by_app_id`) with no `ResourceLocks` row behind it, so re-deriving on
insert would discard a state the repository API promises to persist.

### Division of responsibility

Triggers contain no policy. They never ask whether an acquisition should be
granted or queued — that is Python's `SemaphoreLock` / `ResourceManager`
decision, and the database records it. `available_units` is floored at zero
rather than rejecting the write, so an over-subscribed ledger is still recorded.

---

## 6. How the OS modules interact with SQLite

They do not talk to SQLite directly. Every call is a repository method, and
every write is wrapped in a fault-tolerant adapter.

```text
MemoryManager    ─┐
ResourceManager  ─┤
RAAEEngine       ─┼─→ PersistenceBridge ─→ DatabaseManager ─→ repositories ─→ SQLite
SemaphoreLock    ─┤        (fault containment)
ConflictManager  ─┘
```

### PersistenceBridge

`backend/persistence_bridge.py` sits between the OS modules and the repositories.
It exists to supply two hooks the modules already called that matched no
repository — `SemaphoreLock.log_lock_request` and
`MemoryManager.get_pressure_level.log_memory_pressure` — without rewriting either
module, and to enforce one rule:

> **A database failure may cost history, but must never corrupt in-memory
> simulation state.**

The managers mutate their objects *before* persisting, so an exception escaping a
write would abort the simulation halfway through a change it had already made.
Every write therefore goes through a guard that records the failure and returns
`None` — whether made directly (`save_app`), through a forwarded repository
(`db.apps.*`, proxied by `SafeRepository`), or inside a transaction
(`TolerantTransaction`). Failures are collected in `bridge.failures` for
diagnostics.

An exception raised by the simulation's own logic is deliberately **not**
swallowed: that is a bug, not a persistence problem.

### Who writes what

| OS module | Repository calls |
|---|---|
| `MemoryManager` | `save_app` (register, touch, evict), `log_memory_action` (ALLOCATE / DEALLOCATE), `log_memory_pressure` → `SystemEvents` |
| `SemaphoreLock` | `log_lock_request` → `LockRequests` + the `ResourceLocks` ledger (GRANTED / RELEASED; QUEUED writes no lock row) |
| `ResourceManager` | `save_app`, `save_resource` |
| `ConflictManager` | `conflict_log.log_conflict` (Wound-Wait resolution) |
| `PersistenceBridge.log_freeze` | `conflict_log.log_conflict` with `strategy='NONE'` and a `FREEZE` marker |
| `RAAEEngine` via `DatabaseEvictionPersistence` | the six-step eviction transaction above |

### Design constraints this layer holds to

- **No SQL outside `database/`.** Verified: no `INSERT`/`UPDATE`/`SELECT`/
  `CREATE` and no `.execute(` anywhere in `backend/`.
- **Every statement is parameterized.** No caller-supplied value is ever
  interpolated into SQL; the only f-string fragments are module constants and
  internally built clause lists.
- **No duplicated write paths.** The ledger has a single owner
  (`PersistenceBridge.log_lock_request`), and repositories expose documented
  aliases rather than re-implementing statements.
- **Reusable, not global.** `PersistenceBridge.wrap()` is idempotent and leaves a
  plain `DatabaseManager` untouched, so any module can accept either.
