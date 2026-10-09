-- SQLite Schema for RAAE (Resource-Aware Adaptive App Eviction) Simulation
-- Enables relational integrity with Foreign Key constraints
--
-- Relationships (normalized, 3NF):
--
--   Apps (1) --< (N) ResourceLocks >-- (1) Resources
--    |  ^                                   |
--    |  +-- Resources.held_by_app_id ------+   (ON DELETE SET NULL)
--    |
--   +--< (N) MemoryEvents
--   +--< (N) EvictionLog
--   +--< (N) ConflictLog  (twice: waiting_app_id and blocking_app_id,
--                          plus a third reference to Resources.resource_id)
--   +--< (N) LockRequests   (every lock attempt, including blocked ones)
--
--   SystemEvents has no foreign key: it holds machine-scoped telemetry such as
--   a memory pressure sample, which belongs to no single app.
--
--   Every relationship is enforced by a foreign key with an explicit
--   ON DELETE action, so dependent history is never orphaned.
--
-- Responsibility split:
--   * Python (SemaphoreLock / ResourceManager / RAAE engine) decides WHETHER a
--     lock may be acquired. That policy is never expressed in SQL.
--   * SQLite owns persistence and consistency: foreign keys, the ResourceLocks
--     ledger, and the triggers below that re-derive the lock-dependent columns
--     of Resources so the two tables can never drift apart.
--
-- Authority for lock state:
--   ResourceLocks is the authoritative ledger of who holds what. The following
--   Resources columns are derived from it and are re-computed by the triggers
--   at the bottom of this file whenever a lock row is inserted, updated or
--   deleted:
--       available_units <- capacity - SUM(units) over HELD locks (floored at 0)
--       held_by_app_id  <- app_id of the earliest HELD lock, NULL when none exist
--       status          <- WAITING if the waiting queue is non-empty,
--                          else LOCKED if any HELD lock exists, else FREE

PRAGMA foreign_keys = ON;

-- 1. Apps Table
CREATE TABLE IF NOT EXISTS Apps (
    app_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'BACKGROUND',
    memory_footprint INTEGER NOT NULL DEFAULT 0,
    reference_bit INTEGER NOT NULL DEFAULT 1,
    last_access_time REAL NOT NULL,
    held_resources TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ACTIVE'
);

-- 2. Resources Table
-- capacity, waiting_queue and name are the resource's own facts.
-- available_units, status and held_by_app_id describe its live lock state and are
-- re-derived from ResourceLocks by the triggers at the bottom of this file.
CREATE TABLE IF NOT EXISTS Resources (
    resource_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    capacity INTEGER NOT NULL DEFAULT 1,
    available_units INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'FREE',
    held_by_app_id TEXT,
    waiting_queue TEXT DEFAULT '',
    FOREIGN KEY (held_by_app_id) REFERENCES Apps(app_id) ON DELETE SET NULL
);

-- 3. ResourceLocks Table
-- Ledger of every lock an application has taken on a resource. A row is the
-- unit of consistency: HELD rows describe current ownership, RELEASED rows are
-- retained history. Resources columns are re-derived from the HELD rows by the
-- triggers further down.
CREATE TABLE IF NOT EXISTS ResourceLocks (
    lock_id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    units INTEGER NOT NULL DEFAULT 1 CHECK (units > 0),
    status TEXT NOT NULL DEFAULT 'HELD' CHECK (status IN ('HELD', 'RELEASED')),
    acquired_at REAL NOT NULL,
    released_at REAL,
    -- A lock may not claim to be released without saying when it was released.
    CHECK (status <> 'RELEASED' OR released_at IS NOT NULL),
    FOREIGN KEY (app_id) REFERENCES Apps(app_id) ON DELETE CASCADE,
    FOREIGN KEY (resource_id) REFERENCES Resources(resource_id) ON DELETE CASCADE
);

-- 4. MemoryEvents Table
CREATE TABLE IF NOT EXISTS MemoryEvents (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    action TEXT NOT NULL,
    memory_before INTEGER NOT NULL,
    memory_after INTEGER NOT NULL,
    pressure_level TEXT NOT NULL,
    timestamp REAL NOT NULL,
    FOREIGN KEY (app_id) REFERENCES Apps(app_id) ON DELETE CASCADE
);

-- 5. EvictionLog Table
CREATE TABLE IF NOT EXISTS EvictionLog (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    reason TEXT NOT NULL,
    lock_checked INTEGER NOT NULL DEFAULT 0,
    safe_release INTEGER NOT NULL DEFAULT 0,
    result TEXT NOT NULL,
    timestamp REAL NOT NULL,
    FOREIGN KEY (app_id) REFERENCES Apps(app_id) ON DELETE CASCADE
);

-- 6. ConflictLog Table
CREATE TABLE IF NOT EXISTS ConflictLog (
    conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
    waiting_app_id TEXT NOT NULL,
    blocking_app_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resolution_strategy TEXT NOT NULL DEFAULT 'WOUND_WAIT',
    timestamp REAL NOT NULL,
    resolved_by TEXT,
    details TEXT,
    FOREIGN KEY (waiting_app_id) REFERENCES Apps(app_id) ON DELETE CASCADE,
    FOREIGN KEY (blocking_app_id) REFERENCES Apps(app_id) ON DELETE CASCADE,
    FOREIGN KEY (resource_id) REFERENCES Resources(resource_id) ON DELETE CASCADE
);

-- 7. LockRequests Table
-- Append-only record of every lock attempt and the answer the resource manager
-- gave it. Distinct from ResourceLocks: this table describes the *request*, so a
-- blocked (QUEUED) attempt has a row even though no lock is ever held.
CREATE TABLE IF NOT EXISTS LockRequests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'GRANTED'
        CHECK (outcome IN ('GRANTED', 'QUEUED', 'RELEASED')),
    requested_at REAL NOT NULL,
    FOREIGN KEY (app_id) REFERENCES Apps(app_id) ON DELETE CASCADE,
    FOREIGN KEY (resource_id) REFERENCES Resources(resource_id) ON DELETE CASCADE
);

-- 8. SystemEvents Table
-- Machine-scoped telemetry. Memory pressure is a property of the whole system,
-- not of any one app, so it is deliberately not stored against an app_id.
CREATE TABLE IF NOT EXISTS SystemEvents (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL DEFAULT 'MEMORY_PRESSURE',
    pressure_level TEXT NOT NULL
        CHECK (pressure_level IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
    used_memory INTEGER NOT NULL,
    total_memory INTEGER NOT NULL,
    timestamp REAL NOT NULL
);

-- Indexes for performance and relational joins

-- Apps: lookup by lifecycle status and by execution state
CREATE INDEX IF NOT EXISTS idx_apps_status ON Apps(status);
CREATE INDEX IF NOT EXISTS idx_apps_state ON Apps(state);

-- Resources: lookup by holder and by lifecycle status
CREATE INDEX IF NOT EXISTS idx_resources_held_by ON Resources(held_by_app_id);
CREATE INDEX IF NOT EXISTS idx_resources_status ON Resources(status);

-- ResourceLocks: app_id, resource_id, lock status and acquisition time
CREATE INDEX IF NOT EXISTS idx_resourcelocks_app ON ResourceLocks(app_id);
CREATE INDEX IF NOT EXISTS idx_resourcelocks_resource ON ResourceLocks(resource_id);
CREATE INDEX IF NOT EXISTS idx_resourcelocks_status ON ResourceLocks(status);
CREATE INDEX IF NOT EXISTS idx_resourcelocks_acquired ON ResourceLocks(acquired_at);

-- ResourceLocks: one app may hold at most one live lock per resource.
-- Two concurrent HELD rows for the same (app_id, resource_id) would describe a
-- mutually exclusive state, so the database refuses to record it. RELEASED rows
-- are excluded, which keeps the full acquisition history per app and resource.
CREATE UNIQUE INDEX IF NOT EXISTS ux_resourcelocks_held_app_resource
    ON ResourceLocks(app_id, resource_id) WHERE status = 'HELD';

-- MemoryEvents: app_id, event type (action) and timestamp
CREATE INDEX IF NOT EXISTS idx_memoryevents_app ON MemoryEvents(app_id);
CREATE INDEX IF NOT EXISTS idx_memoryevents_action ON MemoryEvents(action);
CREATE INDEX IF NOT EXISTS idx_memoryevents_timestamp ON MemoryEvents(timestamp);
CREATE INDEX IF NOT EXISTS idx_memoryevents_app_ts ON MemoryEvents(app_id, timestamp DESC);

-- EvictionLog: app_id, event type (algorithm) and timestamp
CREATE INDEX IF NOT EXISTS idx_evictionlog_app ON EvictionLog(app_id);
CREATE INDEX IF NOT EXISTS idx_evictionlog_algorithm ON EvictionLog(algorithm);
CREATE INDEX IF NOT EXISTS idx_evictionlog_timestamp ON EvictionLog(timestamp);
CREATE INDEX IF NOT EXISTS idx_evictionlog_app_ts ON EvictionLog(app_id, timestamp DESC);

-- ConflictLog: resource_id, both participating apps and event type
CREATE INDEX IF NOT EXISTS idx_conflictlog_resource ON ConflictLog(resource_id);
CREATE INDEX IF NOT EXISTS idx_conflictlog_waiting ON ConflictLog(waiting_app_id);
CREATE INDEX IF NOT EXISTS idx_conflictlog_blocking ON ConflictLog(blocking_app_id);
CREATE INDEX IF NOT EXISTS idx_conflictlog_strategy ON ConflictLog(resolution_strategy);
CREATE INDEX IF NOT EXISTS idx_conflictlog_timestamp ON ConflictLog(timestamp);
CREATE INDEX IF NOT EXISTS idx_conflictlog_resource_ts ON ConflictLog(resource_id, timestamp DESC);

-- LockRequests: request history by app, by resource, by outcome and by time
CREATE INDEX IF NOT EXISTS idx_lockrequests_app ON LockRequests(app_id);
CREATE INDEX IF NOT EXISTS idx_lockrequests_resource ON LockRequests(resource_id);
CREATE INDEX IF NOT EXISTS idx_lockrequests_outcome ON LockRequests(outcome);
CREATE INDEX IF NOT EXISTS idx_lockrequests_timestamp ON LockRequests(requested_at);

-- SystemEvents: samples by type, pressure level and time
CREATE INDEX IF NOT EXISTS idx_systemevents_type ON SystemEvents(event_type);
CREATE INDEX IF NOT EXISTS idx_systemevents_level ON SystemEvents(pressure_level);
CREATE INDEX IF NOT EXISTS idx_systemevents_timestamp ON SystemEvents(timestamp);

-- Lock consistency triggers
-- ---------------------------------------------------------------------------
-- ResourceLocks is the authoritative ledger of lock ownership. The
-- lock-dependent columns of Resources duplicate that information for fast
-- single-row reads, so they must never drift. The triggers below recompute
-- them on every ledger change (INSERT / UPDATE / DELETE) using exactly the same
-- rules described in the header comment of this file.
--
-- These triggers deliberately contain no policy. They never ask whether an
-- acquisition should be granted or queued - that decision stays in Python's
-- SemaphoreLock. A trigger only reflects an already-made decision back into the
-- Resources row, and clamps available_units at zero so an over-subscribed
-- ledger can never drive the column negative.

CREATE TRIGGER IF NOT EXISTS trg_resourcelocks_refresh_after_insert
AFTER INSERT ON ResourceLocks
BEGIN
    UPDATE Resources
       SET available_units = MAX(capacity - COALESCE((
               SELECT SUM(l.units) FROM ResourceLocks AS l
                WHERE l.resource_id = NEW.resource_id AND l.status = 'HELD'), 0), 0),
           held_by_app_id = (
               SELECT l.app_id FROM ResourceLocks AS l
                WHERE l.resource_id = NEW.resource_id AND l.status = 'HELD'
                ORDER BY l.acquired_at ASC, l.lock_id ASC LIMIT 1),
           status = CASE
               WHEN TRIM(COALESCE(waiting_queue, '')) <> '' THEN 'WAITING'
               WHEN EXISTS (SELECT 1 FROM ResourceLocks AS l
                             WHERE l.resource_id = NEW.resource_id AND l.status = 'HELD') THEN 'LOCKED'
               ELSE 'FREE'
           END
     WHERE resource_id = NEW.resource_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_resourcelocks_refresh_after_update
AFTER UPDATE ON ResourceLocks
BEGIN
    UPDATE Resources
       SET available_units = MAX(capacity - COALESCE((
               SELECT SUM(l.units) FROM ResourceLocks AS l
                WHERE l.resource_id = NEW.resource_id AND l.status = 'HELD'), 0), 0),
           held_by_app_id = (
               SELECT l.app_id FROM ResourceLocks AS l
                WHERE l.resource_id = NEW.resource_id AND l.status = 'HELD'
                ORDER BY l.acquired_at ASC, l.lock_id ASC LIMIT 1),
           status = CASE
               WHEN TRIM(COALESCE(waiting_queue, '')) <> '' THEN 'WAITING'
               WHEN EXISTS (SELECT 1 FROM ResourceLocks AS l
                             WHERE l.resource_id = NEW.resource_id AND l.status = 'HELD') THEN 'LOCKED'
               ELSE 'FREE'
           END
     WHERE resource_id = NEW.resource_id;

    -- A lock row moved between resources: the resource it left is stale too.
    UPDATE Resources
       SET available_units = MAX(capacity - COALESCE((
               SELECT SUM(l.units) FROM ResourceLocks AS l
                WHERE l.resource_id = OLD.resource_id AND l.status = 'HELD'), 0), 0),
           held_by_app_id = (
               SELECT l.app_id FROM ResourceLocks AS l
                WHERE l.resource_id = OLD.resource_id AND l.status = 'HELD'
                ORDER BY l.acquired_at ASC, l.lock_id ASC LIMIT 1),
           status = CASE
               WHEN TRIM(COALESCE(waiting_queue, '')) <> '' THEN 'WAITING'
               WHEN EXISTS (SELECT 1 FROM ResourceLocks AS l
                             WHERE l.resource_id = OLD.resource_id AND l.status = 'HELD') THEN 'LOCKED'
               ELSE 'FREE'
           END
     WHERE OLD.resource_id <> NEW.resource_id
       AND resource_id = OLD.resource_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_resourcelocks_refresh_after_delete
AFTER DELETE ON ResourceLocks
BEGIN
    UPDATE Resources
       SET available_units = MAX(capacity - COALESCE((
               SELECT SUM(l.units) FROM ResourceLocks AS l
                WHERE l.resource_id = OLD.resource_id AND l.status = 'HELD'), 0), 0),
           held_by_app_id = (
               SELECT l.app_id FROM ResourceLocks AS l
                WHERE l.resource_id = OLD.resource_id AND l.status = 'HELD'
                ORDER BY l.acquired_at ASC, l.lock_id ASC LIMIT 1),
           status = CASE
               WHEN TRIM(COALESCE(waiting_queue, '')) <> '' THEN 'WAITING'
               WHEN EXISTS (SELECT 1 FROM ResourceLocks AS l
                             WHERE l.resource_id = OLD.resource_id AND l.status = 'HELD') THEN 'LOCKED'
               ELSE 'FREE'
           END
     WHERE resource_id = OLD.resource_id;
END;

-- Initial Seed Data for Resources (Idempotent: INSERT OR IGNORE)
INSERT OR IGNORE INTO Resources (resource_id, name, capacity, available_units, status, held_by_app_id, waiting_queue) VALUES
('Camera', 'Camera', 1, 1, 'FREE', NULL, ''),
('Microphone', 'Microphone', 1, 1, 'FREE', NULL, ''),
('GPS', 'GPS', 1, 1, 'FREE', NULL, ''),
('Audio', 'Audio', 1, 1, 'FREE', NULL, '');
