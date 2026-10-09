-- SQLite Schema for RAAE (Resource-Aware Adaptive App Eviction) Simulation
-- Enables relational integrity with Foreign Key constraints

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
CREATE TABLE IF NOT EXISTS ResourceLocks (
    lock_id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    units INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'HELD',
    acquired_at REAL NOT NULL,
    released_at REAL,
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

-- Initial Seed Data for Resources (Idempotent: INSERT OR IGNORE)
INSERT OR IGNORE INTO Resources (resource_id, name, capacity, available_units, status, held_by_app_id, waiting_queue) VALUES
('Camera', 'Camera', 1, 1, 'FREE', NULL, ''),
('Microphone', 'Microphone', 1, 1, 'FREE', NULL, ''),
('GPS', 'GPS', 1, 1, 'FREE', NULL, ''),
('Audio', 'Audio', 1, 1, 'FREE', NULL, '');
