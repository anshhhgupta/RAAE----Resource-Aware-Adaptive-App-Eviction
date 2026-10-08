"""SQLite Database Helper for RAAE Simulation."""

import os
import sqlite3
import json
from typing import List, Optional, Dict, Any
from backend.models.app import App, AppState, AppStatus
from backend.models.resource import Resource, ResourceStatus


class DatabaseManager:
    """Manages SQLite database connections, schema initialization, and persistence operations.
    
    Attributes:
        db_path (str): File path to SQLite database file or ':memory:'.
    """

    def __init__(self, db_path: str = "database/raae.db") -> None:
        self.db_path: str = db_path
        self._shared_conn: Optional[sqlite3.Connection] = None

        if db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:")
            self._shared_conn.row_factory = sqlite3.Row
        else:
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Returns a connection to the SQLite database with Row factory enabled."""
        if self._shared_conn is not None:
            return self._shared_conn

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Initializes tables from schema.sql."""
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

    # ---------------------------------------------------------
    # App CRUD Operations
    # ---------------------------------------------------------

    def save_app(self, app: App) -> None:
        """Inserts or updates an App record in SQLite."""
        sql = """
            INSERT INTO apps (
                app_id, name, priority, state, memory_footprint,
                reference_bit, last_access_time, held_resources, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(app_id) DO UPDATE SET
                name=excluded.name,
                priority=excluded.priority,
                state=excluded.state,
                memory_footprint=excluded.memory_footprint,
                reference_bit=excluded.reference_bit,
                last_access_time=excluded.last_access_time,
                held_resources=excluded.held_resources,
                status=excluded.status;
        """
        held_str = ",".join(sorted(list(app.held_resources)))
        params = (
            app.app_id,
            app.name,
            app.priority,
            app.state.value,
            app.memory_footprint,
            app.reference_bit,
            app.last_access_time,
            held_str,
            app.status.value
        )

        if self._shared_conn is not None:
            self._shared_conn.execute(sql, params)
            self._shared_conn.commit()
        else:
            with self.get_connection() as conn:
                conn.execute(sql, params)

    def get_app(self, app_id: str) -> Optional[App]:
        """Retrieves an App by app_id from SQLite."""
        sql = "SELECT * FROM apps WHERE app_id = ?"
        if self._shared_conn is not None:
            row = self._shared_conn.execute(sql, (str(app_id),)).fetchone()
        else:
            with self.get_connection() as conn:
                row = conn.execute(sql, (str(app_id),)).fetchone()

        if not row:
            return None
        return App.from_dict(dict(row))

    def get_all_apps(self) -> List[App]:
        """Retrieves all App records from SQLite."""
        sql = "SELECT * FROM apps"
        if self._shared_conn is not None:
            rows = self._shared_conn.execute(sql).fetchall()
        else:
            with self.get_connection() as conn:
                rows = conn.execute(sql).fetchall()

        return [App.from_dict(dict(row)) for row in rows]

    def delete_app(self, app_id: str) -> bool:
        """Deletes an App record by app_id."""
        sql = "DELETE FROM apps WHERE app_id = ?"
        if self._shared_conn is not None:
            cursor = self._shared_conn.execute(sql, (str(app_id),))
            self._shared_conn.commit()
            return cursor.rowcount > 0
        else:
            with self.get_connection() as conn:
                cursor = conn.execute(sql, (str(app_id),))
                return cursor.rowcount > 0

    # ---------------------------------------------------------
    # Resource Persistence Operations
    # ---------------------------------------------------------

    def save_resource(self, resource: Resource) -> None:
        """Inserts or updates a Resource record in SQLite."""
        sql = """
            INSERT INTO resources (
                resource_id, name, capacity, available_units, status, held_by_app_id, waiting_queue
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(resource_id) DO UPDATE SET
                name=excluded.name,
                capacity=excluded.capacity,
                available_units=excluded.available_units,
                status=excluded.status,
                held_by_app_id=excluded.held_by_app_id,
                waiting_queue=excluded.waiting_queue;
        """
        waiting_str = ",".join(resource.waiting_queue)
        params = (
            resource.resource_id,
            resource.name,
            resource.capacity,
            resource.available_units,
            resource.status.value,
            resource.primary_holder_id,
            waiting_str
        )

        if self._shared_conn is not None:
            self._shared_conn.execute(sql, params)
            self._shared_conn.commit()
        else:
            with self.get_connection() as conn:
                conn.execute(sql, params)

    def get_resource(self, resource_id: str) -> Optional[Resource]:
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


    def log_memory_action(self, app_id: str, action: str, before: int, after: int, pressure: str) -> None:
        """Logs a memory allocation or deallocation event."""
        import time
        sql = """
            INSERT INTO memory_logs (app_id, action, memory_before, memory_after, pressure_level, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        params = (app_id, action, before, after, pressure, time.time())
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
        """Closes any open database connections."""
        if self._shared_conn is not None:
            self._shared_conn.close()
            self._shared_conn = None

