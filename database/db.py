"""SQLite Database Manager for RAAE Simulation.

Handles connection pooling, schema initialization with foreign key support (PRAGMA foreign_keys = ON),
idempotent migrations, and access to table repositories.
"""

import os
import sqlite3
from typing import List, Optional, Dict, Any
from backend.models.app import App
from backend.models.resource import Resource
from database.repositories import (
    AppRepository,
    ResourceRepository,
    ResourceLockRepository,
    MemoryEventRepository,
    EvictionLogRepository,
    ConflictLogRepository,
)


class DatabaseManager:
    """Manages SQLite database connections, schema initialization, and persistence operations.
    
    Attributes:
        db_path (str): File path to SQLite database file or ':memory:'.
    """

    REQUIRED_TABLES = [
        "Apps",
        "Resources",
        "ResourceLocks",
        "MemoryEvents",
        "EvictionLog",
        "ConflictLog"
    ]

    def __init__(self, db_path: str = "database/raae.db") -> None:
        self.db_path: str = db_path
        self._shared_conn: Optional[sqlite3.Connection] = None

        if db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:")
            self._shared_conn.row_factory = sqlite3.Row
            self._shared_conn.execute("PRAGMA foreign_keys = ON;")
        else:
            db_dir = os.path.dirname(os.path.abspath(db_path))
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

        # Repositories
        self.apps = AppRepository(self)
        self.resources = ResourceRepository(self)
        self.resource_locks = ResourceLockRepository(self)
        self.memory_events = MemoryEventRepository(self)
        self.eviction_log = EvictionLogRepository(self)
        self.conflict_log = ConflictLogRepository(self)

        # Initialize schema and seed data
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Returns a connection to the SQLite database with Row factory and foreign keys enabled."""
        if self._shared_conn is not None:
            return self._shared_conn

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_db(self) -> None:
        """Initializes tables and idempotent seed data from schema.sql."""
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        if os.path.exists(schema_path):
            with open(schema_path, "r", encoding="utf-8") as f:
                schema_sql = f.read()

            if self._shared_conn is not None:
                self._shared_conn.executescript(schema_sql)
                self._shared_conn.commit()
            else:
                with self.get_connection() as conn:
                    conn.executescript(schema_sql)

    def get_table_names(self) -> List[str]:
        """Returns list of all user tables present in SQLite database."""
        sql = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name ASC;"
        with self.get_connection() as conn:
            rows = conn.execute(sql).fetchall()
            return [row["name"] for row in rows]

    # ---------------------------------------------------------
    # Backward Compatible Helper Methods
    # ---------------------------------------------------------

    def save_app(self, app: App) -> None:
        """Inserts or updates an App record."""
        self.apps.save(app)

    def get_app(self, app_id: str) -> Optional[App]:
        """Retrieves an App by app_id."""
        return self.apps.get_by_id(app_id)

    def get_all_apps(self) -> List[App]:
        """Retrieves all App records."""
        return self.apps.get_all()

    def delete_app(self, app_id: str) -> bool:
        """Deletes an App record by app_id."""
        return self.apps.delete(app_id)

    def save_resource(self, resource: Resource) -> None:
        """Inserts or updates a Resource record."""
        self.resources.save(resource)

    def get_resource(self, resource_id: str) -> Optional[Resource]:
feature/ansh-memory-resource
        """Retrieves a Resource by resource_id from SQLite."""
        sql = "SELECT * FROM resources WHERE resource_id = ?"
        if self._shared_conn is not None:
            row = self._shared_conn.execute(sql, (str(resource_id),)).fetchone()
        else:
            with self.get_connection() as conn:
                row = conn.execute(sql, (str(resource_id),)).fetchone()

        if not row:
            return None
        d = dict(row)
        wq = [x.strip() for x in d["waiting_queue"].split(",") if x.strip()] if d.get("waiting_queue") else []
        res = Resource(
            resource_id=d["resource_id"],
            name=d["name"],
            capacity=d["capacity"],
            available_units=d["available_units"],
            waiting_queue=wq
        )
        if d.get("held_by_app_id") and d["available_units"] < d["capacity"]:
            res.holders[d["held_by_app_id"]] = d["capacity"] - d["available_units"]
        return res

=======
        """Retrieves a Resource by resource_id."""
        return self.resources.get_by_id(resource_id)
 main

    def log_memory_action(
        self,
        app_id: str,
        action: str,
        before: int,
        after: int,
        pressure: str,
        timestamp: Optional[float] = None
    ) -> None:
        """Logs a memory allocation or deallocation event."""
 feature/ansh-memory-resource
        import time
        sql = """
            INSERT INTO memory_logs (app_id, action, memory_before, memory_after, pressure_level, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        params = (app_id, action, before, after, pressure, timestamp if timestamp is not None else time.time())
        if self._shared_conn is not None:
            self._shared_conn.execute(sql, params)
            self._shared_conn.commit()
        else:
            with self.get_connection() as conn:
                conn.execute(sql, params)

    def log_lock_request(self, app_id: str, resource_id: str, status: str) -> None:
        """Logs a resource lock request event (GRANTED, QUEUED, RELEASED)."""
        import time
        sql = """
            INSERT INTO lock_requests (app_id, resource_id, request_time, status)
            VALUES (?, ?, ?, ?)
        """
        params = (str(app_id), str(resource_id), time.time(), str(status))
        if self._shared_conn is not None:
            self._shared_conn.execute(sql, params)
            self._shared_conn.commit()
        else:
            with self.get_connection() as conn:
                conn.execute(sql, params)
=======
        self.memory_events.log_event(
            app_id=app_id,
            action=action,
            memory_before=before,
            memory_after=after,
            pressure_level=pressure,
            timestamp=timestamp
        )
 main

    def log_memory_pressure(self, pressure_level: str, memory_used: int, total_memory: int) -> None:
        """Logs a memory pressure detection event."""
        import time
        sql = """
            INSERT INTO memory_logs (app_id, action, memory_before, memory_after, pressure_level, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        params = ("SYSTEM", "PRESSURE_DETECTED", memory_used, total_memory, str(pressure_level), time.time())
        if self._shared_conn is not None:
            self._shared_conn.execute(sql, params)
            self._shared_conn.commit()
        else:
            with self.get_connection() as conn:
                conn.execute(sql, params)

    def get_all_resources(self) -> List[Resource]:
        """Retrieves all Resource records from SQLite."""
        sql = "SELECT * FROM resources"
        if self._shared_conn is not None:
            rows = self._shared_conn.execute(sql).fetchall()
        else:
            with self.get_connection() as conn:
                rows = conn.execute(sql).fetchall()

        resources = []
        for row in rows:
            d = dict(row)
            wq = [x.strip() for x in d["waiting_queue"].split(",") if x.strip()] if d.get("waiting_queue") else []
            res = Resource(
                resource_id=d["resource_id"],
                name=d["name"],
                capacity=d["capacity"],
                available_units=d["available_units"],
                waiting_queue=wq
            )
            if d.get("held_by_app_id") and d["available_units"] < d["capacity"]:
                res.holders[d["held_by_app_id"]] = d["capacity"] - d["available_units"]
            resources.append(res)

        return resources

    def get_all_lock_requests(self) -> List[Dict[str, Any]]:
        """Retrieves all lock_request log records."""
        sql = "SELECT * FROM lock_requests"
        if self._shared_conn is not None:
            rows = self._shared_conn.execute(sql).fetchall()
        else:
            with self.get_connection() as conn:
                rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]

    def get_all_memory_logs(self) -> List[Dict[str, Any]]:
        """Retrieves all memory log records."""
        sql = "SELECT * FROM memory_logs"
        if self._shared_conn is not None:
            rows = self._shared_conn.execute(sql).fetchall()
        else:
            with self.get_connection() as conn:
                rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        """Closes any open shared database connections."""
        if self._shared_conn is not None:
            self._shared_conn.close()
            self._shared_conn = None
