"""Repository classes for RAAE persistence layer.

Provides clean CRUD and query interfaces for all 6 database tables:
1. Apps
2. Resources
3. ResourceLocks
4. MemoryEvents
5. EvictionLog
6. ConflictLog
"""

import time
import sqlite3
from typing import List, Optional, Dict, Any, Union
from backend.models.app import App, AppState, AppStatus
from backend.models.resource import Resource, ResourceStatus


class BaseRepository:
    """Base repository with database connection management."""

    def __init__(self, db_manager: Any) -> None:
        self.db = db_manager

    def _get_connection(self) -> sqlite3.Connection:
        return self.db.get_connection()


class AppRepository(BaseRepository):
    """Repository for managing Apps persistence in SQLite."""

    def save(self, app: App) -> None:
        """Inserts or updates an App record in SQLite."""
        sql = """
            INSERT INTO Apps (
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
            str(app.app_id),
            app.name,
            app.priority,
            app.state.value if isinstance(app.state, AppState) else str(app.state),
            app.memory_footprint,
            app.reference_bit,
            app.last_access_time,
            held_str,
            app.status.value if isinstance(app.status, AppStatus) else str(app.status)
        )
        with self._get_connection() as conn:
            conn.execute(sql, params)

    def get_by_id(self, app_id: str) -> Optional[App]:
        """Retrieves an App by app_id from SQLite."""
        sql = "SELECT * FROM Apps WHERE app_id = ?"
        with self._get_connection() as conn:
            row = conn.execute(sql, (str(app_id),)).fetchone()
        if not row:
            return None
        return App.from_dict(dict(row))

    def get_all(self) -> List[App]:
        """Retrieves all App records from SQLite."""
        sql = "SELECT * FROM Apps ORDER BY priority DESC, app_id ASC"
        with self._get_connection() as conn:
            rows = conn.execute(sql).fetchall()
        return [App.from_dict(dict(row)) for row in rows]

    def get_active(self) -> List[App]:
        """Retrieves active (non-evicted) App records."""
        sql = "SELECT * FROM Apps WHERE status = 'ACTIVE' AND state != 'EVICTED' ORDER BY priority DESC"
        with self._get_connection() as conn:
            rows = conn.execute(sql).fetchall()
        return [App.from_dict(dict(row)) for row in rows]

    def get_by_state(self, state: Union[AppState, str]) -> List[App]:
        """Retrieves apps by state (e.g. FOREGROUND, BACKGROUND, WAITING)."""
        state_val = state.value if isinstance(state, AppState) else str(state)
        sql = "SELECT * FROM Apps WHERE state = ?"
        with self._get_connection() as conn:
            rows = conn.execute(sql, (state_val,)).fetchall()
        return [App.from_dict(dict(row)) for row in rows]

    def delete(self, app_id: str) -> bool:
        """Deletes an App record by app_id."""
        sql = "DELETE FROM Apps WHERE app_id = ?"
        with self._get_connection() as conn:
            cursor = conn.execute(sql, (str(app_id),))
            return cursor.rowcount > 0

    def update_state(self, app_id: str, state: Union[AppState, str], status: Optional[Union[AppStatus, str]] = None) -> bool:
        """Updates the state and status of an App."""
        state_val = state.value if isinstance(state, AppState) else str(state)
        if status is None:
            status_val = "EVICTED" if state_val == "EVICTED" else "ACTIVE"
        else:
            status_val = status.value if isinstance(status, AppStatus) else str(status)

        sql = """
            UPDATE Apps 
            SET state = ?, status = ?, last_access_time = ?
            WHERE app_id = ?
        """
        with self._get_connection() as conn:
            cursor = conn.execute(sql, (state_val, status_val, time.time(), str(app_id)))
            return cursor.rowcount > 0


class ResourceRepository(BaseRepository):
    """Repository for managing Resources persistence in SQLite."""

    def save(self, resource: Resource) -> None:
        """Inserts or updates a Resource record in SQLite."""
        sql = """
            INSERT INTO Resources (
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
            str(resource.resource_id),
            resource.name,
            resource.capacity,
            resource.available_units,
            resource.status.value if isinstance(resource.status, ResourceStatus) else str(resource.status),
            resource.primary_holder_id,
            waiting_str
        )
        with self._get_connection() as conn:
            conn.execute(sql, params)

    def get_by_id(self, resource_id: str) -> Optional[Resource]:
        """Retrieves a Resource by resource_id from SQLite."""
        sql = "SELECT * FROM Resources WHERE resource_id = ? COLLATE NOCASE"
        with self._get_connection() as conn:
            row = conn.execute(sql, (str(resource_id),)).fetchone()
        if not row:
            return None
        d = dict(row)
        wq = [x.strip() for x in d["waiting_queue"].split(",") if x.strip()] if d.get("waiting_queue") else []
        return Resource(
            resource_id=d["resource_id"],
            name=d["name"],
            capacity=d["capacity"],
            available_units=d["available_units"],
            waiting_queue=wq
        )

    def get_all(self) -> List[Resource]:
        """Retrieves all Resource records from SQLite."""
        sql = "SELECT * FROM Resources ORDER BY resource_id ASC"
        with self._get_connection() as conn:
            rows = conn.execute(sql).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            wq = [x.strip() for x in d["waiting_queue"].split(",") if x.strip()] if d.get("waiting_queue") else []
            result.append(Resource(
                resource_id=d["resource_id"],
                name=d["name"],
                capacity=d["capacity"],
                available_units=d["available_units"],
                waiting_queue=wq
            ))
        return result

    def get_by_status(self, status: Union[ResourceStatus, str]) -> List[Resource]:
        """Retrieves resources filtered by status (FREE, LOCKED, WAITING)."""
        status_val = status.value if isinstance(status, ResourceStatus) else str(status)
        sql = "SELECT * FROM Resources WHERE status = ?"
        with self._get_connection() as conn:
            rows = conn.execute(sql, (status_val,)).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            wq = [x.strip() for x in d["waiting_queue"].split(",") if x.strip()] if d.get("waiting_queue") else []
            result.append(Resource(
                resource_id=d["resource_id"],
                name=d["name"],
                capacity=d["capacity"],
                available_units=d["available_units"],
                waiting_queue=wq
            ))
        return result

    def delete(self, resource_id: str) -> bool:
        """Deletes a Resource record by resource_id."""
        sql = "DELETE FROM Resources WHERE resource_id = ? COLLATE NOCASE"
        with self._get_connection() as conn:
            cursor = conn.execute(sql, (str(resource_id),))
            return cursor.rowcount > 0


class ResourceLockRepository(BaseRepository):
    """Repository for managing ResourceLocks records in SQLite."""

    def acquire_lock(
        self,
        app_id: str,
        resource_id: str,
        units: int = 1,
        status: str = "HELD",
        acquired_at: Optional[float] = None
    ) -> int:
        """Records a new resource lock acquisition."""
        sql = """
            INSERT INTO ResourceLocks (app_id, resource_id, units, status, acquired_at)
            VALUES (?, ?, ?, ?, ?)
        """
        ts = acquired_at if acquired_at is not None else time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(sql, (str(app_id), str(resource_id), int(units), str(status), float(ts)))
            return cursor.lastrowid

    def release_lock(self, lock_id: int, released_at: Optional[float] = None) -> bool:
        """Marks a specific resource lock as released."""
        sql = """
            UPDATE ResourceLocks 
            SET status = 'RELEASED', released_at = ?
            WHERE lock_id = ? AND status != 'RELEASED'
        """
        ts = released_at if released_at is not None else time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(sql, (float(ts), int(lock_id)))
            return cursor.rowcount > 0

    def release_all_for_app(self, app_id: str, released_at: Optional[float] = None) -> int:
        """Marks all active locks held by an app as released."""
        sql = """
            UPDATE ResourceLocks 
            SET status = 'RELEASED', released_at = ?
            WHERE app_id = ? AND status = 'HELD'
        """
        ts = released_at if released_at is not None else time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(sql, (float(ts), str(app_id)))
            return cursor.rowcount

    def get_active_locks(self) -> List[Dict[str, Any]]:
        """Retrieves all currently active (HELD) resource locks."""
        sql = """
            SELECT rl.*, a.name AS app_name, r.name AS resource_name
            FROM ResourceLocks rl
            JOIN Apps a ON rl.app_id = a.app_id
            JOIN Resources r ON rl.resource_id = r.resource_id
            WHERE rl.status = 'HELD'
            ORDER BY rl.acquired_at ASC
        """
        with self._get_connection() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]

    def get_locks_by_app(self, app_id: str) -> List[Dict[str, Any]]:
        """Retrieves all lock history for an app."""
        sql = "SELECT * FROM ResourceLocks WHERE app_id = ? ORDER BY acquired_at DESC"
        with self._get_connection() as conn:
            rows = conn.execute(sql, (str(app_id),)).fetchall()
        return [dict(row) for row in rows]

    def get_locks_by_resource(self, resource_id: str) -> List[Dict[str, Any]]:
        """Retrieves lock records for a specific resource."""
        sql = "SELECT * FROM ResourceLocks WHERE resource_id = ? ORDER BY acquired_at DESC"
        with self._get_connection() as conn:
            rows = conn.execute(sql, (str(resource_id),)).fetchall()
        return [dict(row) for row in rows]


class MemoryEventRepository(BaseRepository):
    """Repository for managing MemoryEvents records in SQLite."""

    def log_event(
        self,
        app_id: str,
        action: str,
        memory_before: int,
        memory_after: int,
        pressure_level: str,
        timestamp: Optional[float] = None
    ) -> int:
        """Logs a memory allocation, deallocation, or pressure event."""
        sql = """
            INSERT INTO MemoryEvents (app_id, action, memory_before, memory_after, pressure_level, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        ts = timestamp if timestamp is not None else time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(
                sql,
                (str(app_id), str(action), int(memory_before), int(memory_after), str(pressure_level), float(ts))
            )
            return cursor.lastrowid

    def get_events_by_app(self, app_id: str) -> List[Dict[str, Any]]:
        """Retrieves memory event history for an app."""
        sql = "SELECT * FROM MemoryEvents WHERE app_id = ? ORDER BY timestamp DESC"
        with self._get_connection() as conn:
            rows = conn.execute(sql, (str(app_id),)).fetchall()
        return [dict(row) for row in rows]

    def get_all_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves recent memory events."""
        sql = "SELECT * FROM MemoryEvents ORDER BY timestamp DESC LIMIT ?"
        with self._get_connection() as conn:
            rows = conn.execute(sql, (int(limit),)).fetchall()
        return [dict(row) for row in rows]


class EvictionLogRepository(BaseRepository):
    """Repository for managing EvictionLog records in SQLite."""

    def log_eviction(
        self,
        app_id: str,
        algorithm: str,
        reason: str,
        lock_checked: bool,
        safe_release: bool,
        result: str,
        timestamp: Optional[float] = None
    ) -> int:
        """Logs an application eviction decision and its outcome."""
        sql = """
            INSERT INTO EvictionLog (app_id, algorithm, reason, lock_checked, safe_release, result, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        ts = timestamp if timestamp is not None else time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(
                sql,
                (
                    str(app_id),
                    str(algorithm),
                    str(reason),
                    1 if lock_checked else 0,
                    1 if safe_release else 0,
                    str(result),
                    float(ts)
                )
            )
            return cursor.lastrowid

    def get_evictions_by_app(self, app_id: str) -> List[Dict[str, Any]]:
        """Retrieves eviction records for a specific app."""
        sql = "SELECT * FROM EvictionLog WHERE app_id = ? ORDER BY timestamp DESC"
        with self._get_connection() as conn:
            rows = conn.execute(sql, (str(app_id),)).fetchall()
        return [dict(row) for row in rows]

    def get_all_evictions(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves recent eviction log entries."""
        sql = "SELECT * FROM EvictionLog ORDER BY timestamp DESC LIMIT ?"
        with self._get_connection() as conn:
            rows = conn.execute(sql, (int(limit),)).fetchall()
        return [dict(row) for row in rows]


class ConflictLogRepository(BaseRepository):
    """Repository for managing ConflictLog records in SQLite."""

    def log_conflict(
        self,
        waiting_app_id: str,
        blocking_app_id: str,
        resource_id: str,
        resolution_strategy: str = "WOUND_WAIT",
        resolved_by: Optional[str] = None,
        details: Optional[str] = None,
        timestamp: Optional[float] = None
    ) -> int:
        """Logs a resource contention or lock conflict incident."""
        sql = """
            INSERT INTO ConflictLog (
                waiting_app_id, blocking_app_id, resource_id,
                resolution_strategy, timestamp, resolved_by, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        ts = timestamp if timestamp is not None else time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(
                sql,
                (
                    str(waiting_app_id),
                    str(blocking_app_id),
                    str(resource_id),
                    str(resolution_strategy),
                    float(ts),
                    str(resolved_by) if resolved_by else None,
                    str(details) if details else None
                )
            )
            return cursor.lastrowid

    def get_conflicts_by_resource(self, resource_id: str) -> List[Dict[str, Any]]:
        """Retrieves conflict incidents involving a specific resource."""
        sql = "SELECT * FROM ConflictLog WHERE resource_id = ? ORDER BY timestamp DESC"
        with self._get_connection() as conn:
            rows = conn.execute(sql, (str(resource_id),)).fetchall()
        return [dict(row) for row in rows]

    def get_all_conflicts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves recent conflict incidents."""
        sql = "SELECT * FROM ConflictLog ORDER BY timestamp DESC LIMIT ?"
        with self._get_connection() as conn:
            rows = conn.execute(sql, (int(limit),)).fetchall()
        return [dict(row) for row in rows]
