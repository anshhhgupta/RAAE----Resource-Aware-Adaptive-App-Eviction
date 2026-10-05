-- SQLite Schema for RAAE (Resource-Aware Adaptive App Eviction) Simulation

CREATE TABLE IF NOT EXISTS apps (
    app_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    priority INTEGER NOT NULL,
    state TEXT NOT NULL,
    memory_footprint INTEGER NOT NULL,
    reference_bit INTEGER NOT NULL,
    last_access_time REAL NOT NULL,
    held_resources TEXT,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resources (
    resource_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    available_units INTEGER NOT NULL,
    status TEXT NOT NULL,
    held_by_app_id TEXT,
    waiting_queue TEXT
);

CREATE TABLE IF NOT EXISTS lock_requests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    request_time REAL NOT NULL,
    status TEXT NOT NULL,
    FOREIGN KEY(app_id) REFERENCES apps(app_id),
    FOREIGN KEY(resource_id) REFERENCES resources(resource_id)
);

CREATE TABLE IF NOT EXISTS memory_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    action TEXT NOT NULL,
    memory_before INTEGER NOT NULL,
    memory_after INTEGER NOT NULL,
    pressure_level TEXT NOT NULL,
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS eviction_logs (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    reason TEXT NOT NULL,
    lock_checked INTEGER NOT NULL,
    safe_release INTEGER NOT NULL,
    result TEXT NOT NULL,
    timestamp REAL NOT NULL
);
