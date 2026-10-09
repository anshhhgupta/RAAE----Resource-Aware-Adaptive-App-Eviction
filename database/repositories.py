"""Repository classes for the RAAE repository / data-access layer.

Every SQL statement in the project lives in this package. Repositories own
parameterized queries for each of the tables defined in ``schema.sql``:

1. Apps
2. Resources
3. ResourceLocks
4. MemoryEvents
5. EvictionLog
6. ConflictLog
7. LockRequests
8. SystemEvents

Naming follows the canonical data-access API: ``create_*`` inserts a new row,
``update_*`` mutates an existing row, ``get_*`` reads, and ``delete_*`` removes.
Older names (``save``, ``get_by_id``, ``log_event``, ...) are kept as thin
aliases so existing backend callers keep working.
"""

import sqlite3
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from backend.models.app import App, AppState, AppStatus
from backend.models.resource import Resource, ResourceStatus


def _encode_csv(values: Sequence[str]) -> str:
    """Serializes a collection of strings into the comma-separated TEXT layout used by the schema."""
    return ",".join(str(v) for v in values)


def _decode_csv(raw: Optional[str]) -> List[str]:
    """Deserializes a comma-separated TEXT column into a list of strings."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _app_state_value(state: Union[AppState, str]) -> str:
    """Normalizes an AppState enum or string into its TEXT column value."""
    return state.value if isinstance(state, AppState) else str(state)


def _app_status_value(status: Union[AppStatus, str]) -> str:
    """Normalizes an AppStatus enum or string into its TEXT column value."""
    return status.value if isinstance(status, AppStatus) else str(status)


def _resource_status_value(status: Union[ResourceStatus, str]) -> str:
    """Normalizes a ResourceStatus enum or string into its TEXT column value."""
    return status.value if isinstance(status, ResourceStatus) else str(status)


def _bool_to_int(value: Any) -> int:
    """Normalizes a boolean-like value into the INTEGER 0/1 layout used by the schema."""
    return 1 if value else 0


def _resource_from_row(row: Any) -> Resource:
    """Rebuilds a Resource model from a Resources row.

    ``Resources`` stores a single ``held_by_app_id`` plus ``available_units``, so the
    ``holders`` map is reconstructed from the units a resource has handed out.
    """
    data = dict(row)
    resource = Resource(
        resource_id=data["resource_id"],
        name=data["name"],
        capacity=data["capacity"],
        available_units=data["available_units"],
        waiting_queue=_decode_csv(data.get("waiting_queue")),
    )

    holder_id = data.get("held_by_app_id")
    if holder_id and data["available_units"] < data["capacity"]:
        resource.holders[str(holder_id)] = data["capacity"] - data["available_units"]

    return resource


class BaseRepository:
    """Base repository providing connection access for derived repositories.

    Attributes:
        db (Any): Owning DatabaseManager used to obtain SQLite connections.
    """

    def __init__(self, db_manager: Any) -> None:
        self.db = db_manager

    def _get_connection(self) -> Any:
        """Returns a connection from the owning DatabaseManager."""
        return self.db.get_connection()

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """Executes a parameterized statement on the managed connection."""
        with self._get_connection() as conn:
            return conn.execute(sql, tuple(params))

    def _execute_many_read(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        """Executes a parameterized query and returns all rows as dictionaries."""
        with self._get_connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]


class AppRepository(BaseRepository):
    """Repository for Apps persistence."""

    _COLUMNS = (
        "app_id, name, priority, state, memory_footprint, "
        "reference_bit, last_access_time, held_resources, status"
    )

    def _to_params(self, app: App) -> Tuple[Any, ...]:
        """Converts an App model into a flat parameter tuple matching _COLUMNS."""
        return (
            str(app.app_id),
            app.name,
            int(app.priority),
            _app_state_value(app.state),
            int(app.memory_footprint),
            int(app.reference_bit),
            float(app.last_access_time),
            _encode_csv(sorted(app.held_resources)),
            _app_status_value(app.status),
        )

    def _to_update_params(self, app: App) -> Tuple[Any, ...]:
        """Converts an App model into a parameter tuple for an UPDATE, keyed last."""
        params = self._to_params(app)
        return params[1:] + (params[0],)

    def create_app(self, app: App) -> App:
        """Inserts a new App record.

        Args:
            app (App): Application model to persist.

        Returns:
            App: The persisted application.

        Raises:
            sqlite3.IntegrityError: If an App with the same app_id already exists.
        """
        sql = f"INSERT INTO Apps ({self._COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        self._execute(sql, self._to_params(app))
        return app

    def update_app(self, app: App) -> bool:
        """Updates an existing App record.

        Args:
            app (App): Application model carrying the new column values.

        Returns:
            bool: True if a row was updated, False if no App matched app_id.
        """
        sql = """
            UPDATE Apps
            SET name = ?, priority = ?, state = ?, memory_footprint = ?,
                reference_bit = ?, last_access_time = ?, held_resources = ?, status = ?
            WHERE app_id = ?
        """
        return self._execute(sql, self._to_update_params(app)).rowcount > 0

    def save(self, app: App) -> None:
        """Inserts or updates an App record (upsert on app_id)."""
        sql = f"""
            INSERT INTO Apps ({self._COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        self._execute(sql, self._to_params(app))

    def get_app(self, app_id: str) -> Optional[App]:
        """Retrieves a single App by app_id.

        Args:
            app_id (str): Application identifier.

        Returns:
            Optional[App]: The App, or None if no row matched.
        """
        sql = "SELECT * FROM Apps WHERE app_id = ?"
        with self._get_connection() as conn:
            row = conn.execute(sql, (str(app_id),)).fetchone()
        return App.from_dict(dict(row)) if row else None

    def get_all_apps(self, status: Optional[Union[AppStatus, str]] = None) -> List[App]:
        """Retrieves all Apps, optionally filtered by status.

        Args:
            status (Optional[Union[AppStatus, str]]): Restricts results to a single status.

        Returns:
            List[App]: Apps ordered by descending priority then app_id.
        """
        if status is None:
            sql = "SELECT * FROM Apps ORDER BY priority DESC, app_id ASC"
            params: Tuple[Any, ...] = ()
        else:
            sql = "SELECT * FROM Apps WHERE status = ? ORDER BY priority DESC, app_id ASC"
            params = (_app_status_value(status),)

        with self._get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [App.from_dict(dict(row)) for row in rows]

    def delete_app(self, app_id: str) -> bool:
        """Deletes an App by app_id.

        Related ResourceLocks, MemoryEvents, EvictionLog and ConflictLog rows are
        removed by the schema's ON DELETE CASCADE rules.

        Args:
            app_id (str): Application identifier.

        Returns:
            bool: True if a row was deleted, False if no App matched.
        """
        sql = "DELETE FROM Apps WHERE app_id = ?"
        return self._execute(sql, (str(app_id),)).rowcount > 0

    def get_active(self) -> List[App]:
        """Retrieves Apps that are neither evicted in state nor status."""
        sql = "SELECT * FROM Apps WHERE status = 'ACTIVE' AND state != 'EVICTED' ORDER BY priority DESC"
        with self._get_connection() as conn:
            rows = conn.execute(sql).fetchall()
        return [App.from_dict(dict(row)) for row in rows]

    def get_by_state(self, state: Union[AppState, str]) -> List[App]:
        """Retrieves Apps matching a given execution state."""
        sql = "SELECT * FROM Apps WHERE state = ? ORDER BY priority DESC"
        with self._get_connection() as conn:
            rows = conn.execute(sql, (_app_state_value(state),)).fetchall()
        return [App.from_dict(dict(row)) for row in rows]

    def update_state(
        self,
        app_id: str,
        state: Union[AppState, str],
        status: Optional[Union[AppStatus, str]] = None,
    ) -> bool:
        """Updates an App's state, status and last access time.

        Args:
            app_id (str): Application identifier.
            state (Union[AppState, str]): New execution state.
            status (Optional[Union[AppStatus, str]]): New status; derived from state when omitted.

        Returns:
            bool: True if a row was updated, False if no App matched.
        """
        state_val = _app_state_value(state)
        if status is None:
            status_val = "EVICTED" if state_val == AppState.EVICTED.value else "ACTIVE"
        else:
            status_val = _app_status_value(status)

        sql = """
            UPDATE Apps
            SET state = ?, status = ?, last_access_time = ?
            WHERE app_id = ?
        """
        return self._execute(sql, (state_val, status_val, time.time(), str(app_id))).rowcount > 0

    def get_by_id(self, app_id: str) -> Optional[App]:
        """Alias for get_app."""
        return self.get_app(app_id)

    def get_all(self) -> List[App]:
        """Alias for get_all_apps."""
        return self.get_all_apps()

    def delete(self, app_id: str) -> bool:
        """Alias for delete_app."""
        return self.delete_app(app_id)


class ResourceRepository(BaseRepository):
    """Repository for Resources persistence."""

    _COLUMNS = (
        "resource_id, name, capacity, available_units, status, held_by_app_id, waiting_queue"
    )

    def _to_params(self, resource: Resource) -> Tuple[Any, ...]:
        """Converts a Resource model into a flat parameter tuple matching _COLUMNS."""
        return (
            str(resource.resource_id),
            resource.name,
            int(resource.capacity),
            int(resource.available_units),
            _resource_status_value(resource.status),
            resource.primary_holder_id,
            _encode_csv(resource.waiting_queue),
        )

    def _to_update_params(self, resource: Resource) -> Tuple[Any, ...]:
        """Converts a Resource model into a parameter tuple for an UPDATE, keyed last."""
        params = self._to_params(resource)
        return params[1:] + (params[0],)

    def create_resource(self, resource: Resource) -> Resource:
        """Inserts a new Resource record.

        Args:
            resource (Resource): Resource model to persist.

        Returns:
            Resource: The persisted resource.

        Raises:
            sqlite3.IntegrityError: If the resource_id exists, or held_by_app_id is unknown.
        """
        sql = f"INSERT INTO Resources ({self._COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)"
        self._execute(sql, self._to_params(resource))
        return resource

    def update_resource(self, resource: Resource) -> bool:
        """Updates an existing Resource record.

        Args:
            resource (Resource): Resource model carrying the new column values.

        Returns:
            bool: True if a row was updated, False if no Resource matched.
        """
        sql = """
            UPDATE Resources
            SET name = ?, capacity = ?, available_units = ?, status = ?,
                held_by_app_id = ?, waiting_queue = ?
            WHERE resource_id = ?
        """
        return self._execute(sql, self._to_update_params(resource)).rowcount > 0

    def save(self, resource: Resource) -> None:
        """Inserts or updates a Resource record (upsert on resource_id)."""
        sql = f"""
            INSERT INTO Resources ({self._COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(resource_id) DO UPDATE SET
                name=excluded.name,
                capacity=excluded.capacity,
                available_units=excluded.available_units,
                status=excluded.status,
                held_by_app_id=excluded.held_by_app_id,
                waiting_queue=excluded.waiting_queue;
        """
        self._execute(sql, self._to_params(resource))

    def get_resource(self, resource_id: str) -> Optional[Resource]:
        """Retrieves a single Resource by resource_id (case-insensitive).

        Args:
            resource_id (str): Resource identifier.

        Returns:
            Optional[Resource]: The Resource, or None if no row matched.
        """
        sql = "SELECT * FROM Resources WHERE resource_id = ? COLLATE NOCASE"
        with self._get_connection() as conn:
            row = conn.execute(sql, (str(resource_id),)).fetchone()
        return _resource_from_row(row) if row else None

    def get_resources(
        self,
        resource_id: Optional[str] = None,
        status: Optional[Union[ResourceStatus, str]] = None,
        held_by_app_id: Optional[str] = None,
    ) -> List[Resource]:
        """Retrieves Resources matching the supplied filters.

        Args:
            resource_id (Optional[str]): Restricts to a single resource_id.
            status (Optional[Union[ResourceStatus, str]]): Restricts to a single status.
            held_by_app_id (Optional[str]): Restricts to resources held by this app.

        Returns:
            List[Resource]: Matching resources ordered by resource_id.
        """
        clauses: List[str] = []
        params: List[Any] = []

        if resource_id is not None:
            clauses.append("resource_id = ? COLLATE NOCASE")
            params.append(str(resource_id))
        if status is not None:
            clauses.append("status = ?")
            params.append(_resource_status_value(status))
        if held_by_app_id is not None:
            clauses.append("held_by_app_id = ?")
            params.append(str(held_by_app_id))

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM Resources{where} ORDER BY resource_id ASC"

        with self._get_connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_resource_from_row(row) for row in rows]

    def delete_resource(self, resource_id: str) -> bool:
        """Deletes a Resource by resource_id (case-insensitive)."""
        sql = "DELETE FROM Resources WHERE resource_id = ? COLLATE NOCASE"
        return self._execute(sql, (str(resource_id),)).rowcount > 0

    def get_by_id(self, resource_id: str) -> Optional[Resource]:
        """Alias for get_resource."""
        return self.get_resource(resource_id)

    def get_all(self) -> List[Resource]:
        """Alias for get_resources with no filters."""
        return self.get_resources()

    def get_by_status(self, status: Union[ResourceStatus, str]) -> List[Resource]:
        """Alias for get_resources filtered by status."""
        return self.get_resources(status=status)

    def delete(self, resource_id: str) -> bool:
        """Alias for delete_resource."""
        return self.delete_resource(resource_id)


class ResourceLockRepository(BaseRepository):
    """Repository for ResourceLocks persistence.

    ``ResourceLocks`` is the authoritative ledger of lock ownership; the
    lock-dependent columns of ``Resources`` are re-derived from these rows by
    the triggers declared in ``schema.sql``. Writing a row here is therefore
    also how the Resources view of a lock is kept in step.

    This repository records and retires locks. It never decides whether one may
    be granted: that judgement belongs to the Python resource manager.
    """

    def acquire_resource_lock(
        self,
        app_id: str,
        resource_id: str,
        units: int = 1,
        status: str = "HELD",
        acquired_at: Optional[float] = None,
    ) -> int:
        """Records a resource lock acquisition.

        Args:
            app_id (str): Application acquiring the lock.
            resource_id (str): Resource being locked.
            units (int): Number of units held.
            status (str): Lock status; 'HELD' for an active lock.
            acquired_at (Optional[float]): Acquisition timestamp; defaults to now.

        Returns:
            int: The new lock_id.

        Raises:
            sqlite3.IntegrityError: If app_id or resource_id violates a foreign key,
                units is not positive, status is not a known lock status, or a
                second concurrent HELD lock already exists for the same
                (app_id, resource_id) pair.
        """
        sql = """
            INSERT INTO ResourceLocks (app_id, resource_id, units, status, acquired_at)
            VALUES (?, ?, ?, ?, ?)
        """
        ts = acquired_at if acquired_at is not None else time.time()
        return self._execute(
            sql, (str(app_id), str(resource_id), int(units), str(status), float(ts))
        ).lastrowid

    def release_resource_lock(self, lock_id: int, released_at: Optional[float] = None) -> bool:
        """Marks a lock as released.

        Args:
            lock_id (int): Identifier of the lock to release.
            released_at (Optional[float]): Release timestamp; defaults to now.

        Returns:
            bool: True if an active lock was transitioned, False otherwise.
        """
        sql = """
            UPDATE ResourceLocks
            SET status = 'RELEASED', released_at = ?
            WHERE lock_id = ? AND status != 'RELEASED'
        """
        ts = released_at if released_at is not None else time.time()
        return self._execute(sql, (float(ts), int(lock_id))).rowcount > 0

    def release_lock_for(
        self,
        app_id: str,
        resource_id: str,
        released_at: Optional[float] = None,
    ) -> bool:
        """Marks the app's single live lock on a resource as released.

        This is the counterpart the semaphore uses on release, where the holder
        is known but the lock_id is not.

        Args:
            app_id (str): Application releasing the lock.
            resource_id (str): Resource the lock was taken on.
            released_at (Optional[float]): Release timestamp; defaults to now.

        Returns:
            bool: True if a live lock was transitioned, False if the app held none.
        """
        sql = """
            UPDATE ResourceLocks
            SET status = 'RELEASED', released_at = ?
            WHERE app_id = ? AND resource_id = ? AND status = 'HELD'
        """
        ts = released_at if released_at is not None else time.time()
        return self._execute(sql, (float(ts), str(app_id), str(resource_id))).rowcount > 0

    def release_all_for_app(self, app_id: str, released_at: Optional[float] = None) -> int:
        """Marks every active lock held by an app as released.

        Args:
            app_id (str): Application whose locks are released.
            released_at (Optional[float]): Release timestamp; defaults to now.

        Returns:
            int: Number of locks transitioned to RELEASED.
        """
        sql = """
            UPDATE ResourceLocks
            SET status = 'RELEASED', released_at = ?
            WHERE app_id = ? AND status = 'HELD'
        """
        ts = released_at if released_at is not None else time.time()
        return self._execute(sql, (float(ts), str(app_id))).rowcount

    def get_active_locks(self) -> List[Dict[str, Any]]:
        """Retrieves all currently held locks joined with app and resource names."""
        sql = """
            SELECT rl.*, a.name AS app_name, r.name AS resource_name
            FROM ResourceLocks rl
            JOIN Apps a ON rl.app_id = a.app_id
            JOIN Resources r ON rl.resource_id = r.resource_id
            WHERE rl.status = 'HELD'
            ORDER BY rl.acquired_at ASC
        """
        return self._execute_many_read(sql)

    def get_active_lock(self, app_id: str, resource_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the live lock an app holds on a resource, if any.

        Args:
            app_id (str): Application identifier.
            resource_id (str): Resource identifier.

        Returns:
            Optional[Dict[str, Any]]: The HELD lock row, or None.
        """
        sql = """
            SELECT * FROM ResourceLocks
            WHERE app_id = ? AND resource_id = ? AND status = 'HELD'
            ORDER BY acquired_at DESC, lock_id DESC
            LIMIT 1
        """
        with self._get_connection() as conn:
            row = conn.execute(sql, (str(app_id), str(resource_id))).fetchone()
        return dict(row) if row else None

    def get_locks_by_app(self, app_id: str) -> List[Dict[str, Any]]:
        """Retrieves full lock history for an app."""
        sql = "SELECT * FROM ResourceLocks WHERE app_id = ? ORDER BY acquired_at DESC"
        return self._execute_many_read(sql, (str(app_id),))

    def get_locks_by_resource(self, resource_id: str) -> List[Dict[str, Any]]:
        """Retrieves full lock history for a resource."""
        sql = "SELECT * FROM ResourceLocks WHERE resource_id = ? ORDER BY acquired_at DESC"
        return self._execute_many_read(sql, (str(resource_id),))

    def acquire_lock(
        self,
        app_id: str,
        resource_id: str,
        units: int = 1,
        status: str = "HELD",
        acquired_at: Optional[float] = None,
    ) -> int:
        """Alias for acquire_resource_lock."""
        return self.acquire_resource_lock(app_id, resource_id, units, status, acquired_at)

    def release_lock(self, lock_id: int, released_at: Optional[float] = None) -> bool:
        """Alias for release_resource_lock."""
        return self.release_resource_lock(lock_id, released_at)


class MemoryEventRepository(BaseRepository):
    """Repository for MemoryEvents persistence."""

    def insert_memory_event(
        self,
        app_id: str,
        action: str,
        memory_before: int,
        memory_after: int,
        pressure_level: str,
        timestamp: Optional[float] = None,
    ) -> int:
        """Logs a memory allocation, deallocation or pressure event.

        Args:
            app_id (str): Application the event belongs to.
            action (str): Event action, e.g. 'ALLOCATE' or 'DEALLOCATE'.
            memory_before (int): App memory footprint before the action.
            memory_after (int): App memory footprint after the action.
            pressure_level (str): Memory pressure level at event time.
            timestamp (Optional[float]): Event timestamp; defaults to now.

        Returns:
            int: The new event_id.

        Raises:
            sqlite3.IntegrityError: If app_id violates the Apps foreign key.
        """
        sql = """
            INSERT INTO MemoryEvents (app_id, action, memory_before, memory_after, pressure_level, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        ts = timestamp if timestamp is not None else time.time()
        return self._execute(
            sql,
            (
                str(app_id),
                str(action),
                int(memory_before),
                int(memory_after),
                str(pressure_level),
                float(ts),
            ),
        ).lastrowid

    def get_events_by_app(self, app_id: str) -> List[Dict[str, Any]]:
        """Retrieves memory event history for an app, newest first."""
        sql = "SELECT * FROM MemoryEvents WHERE app_id = ? ORDER BY timestamp DESC"
        return self._execute_many_read(sql, (str(app_id),))

    def get_events_by_action(self, action: str) -> List[Dict[str, Any]]:
        """Retrieves memory events of a given action type, newest first."""
        sql = "SELECT * FROM MemoryEvents WHERE action = ? ORDER BY timestamp DESC"
        return self._execute_many_read(sql, (str(action),))

    def get_all_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves the most recent memory events, newest first."""
        sql = "SELECT * FROM MemoryEvents ORDER BY timestamp DESC LIMIT ?"
        return self._execute_many_read(sql, (int(limit),))

    def log_event(
        self,
        app_id: str,
        action: str,
        memory_before: int,
        memory_after: int,
        pressure_level: str,
        timestamp: Optional[float] = None,
    ) -> int:
        """Alias for insert_memory_event."""
        return self.insert_memory_event(
            app_id, action, memory_before, memory_after, pressure_level, timestamp
        )


class EvictionLogRepository(BaseRepository):
    """Repository for EvictionLog persistence."""

    def insert_eviction_log(
        self,
        app_id: str,
        algorithm: str,
        reason: str,
        lock_checked: bool,
        safe_release: bool,
        result: str,
        timestamp: Optional[float] = None,
    ) -> int:
        """Logs an eviction decision and its outcome.

        Args:
            app_id (str): Application considered for eviction.
            algorithm (str): Algorithm that produced the decision.
            reason (str): Human readable reason for the decision.
            lock_checked (bool): Whether held resource locks were inspected.
            safe_release (bool): Whether releasing the app's locks is safe.
            result (str): Decision outcome, e.g. 'EVICTED' or 'SKIPPED'.
            timestamp (Optional[float]): Event timestamp; defaults to now.

        Returns:
            int: The new event_id.

        Raises:
            sqlite3.IntegrityError: If app_id violates the Apps foreign key.
        """
        sql = """
            INSERT INTO EvictionLog (app_id, algorithm, reason, lock_checked, safe_release, result, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        ts = timestamp if timestamp is not None else time.time()
        return self._execute(
            sql,
            (
                str(app_id),
                str(algorithm),
                str(reason),
                _bool_to_int(lock_checked),
                _bool_to_int(safe_release),
                str(result),
                float(ts),
            ),
        ).lastrowid

    def get_evictions_by_app(self, app_id: str) -> List[Dict[str, Any]]:
        """Retrieves eviction records for an app, newest first."""
        sql = "SELECT * FROM EvictionLog WHERE app_id = ? ORDER BY timestamp DESC"
        return self._execute_many_read(sql, (str(app_id),))

    def get_evictions_by_algorithm(self, algorithm: str) -> List[Dict[str, Any]]:
        """Retrieves eviction records produced by a given algorithm, newest first."""
        sql = "SELECT * FROM EvictionLog WHERE algorithm = ? ORDER BY timestamp DESC"
        return self._execute_many_read(sql, (str(algorithm),))

    def get_all_evictions(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves the most recent eviction records, newest first."""
        sql = "SELECT * FROM EvictionLog ORDER BY timestamp DESC LIMIT ?"
        return self._execute_many_read(sql, (int(limit),))

    def log_eviction(
        self,
        app_id: str,
        algorithm: str,
        reason: str,
        lock_checked: bool,
        safe_release: bool,
        result: str,
        timestamp: Optional[float] = None,
    ) -> int:
        """Alias for insert_eviction_log."""
        return self.insert_eviction_log(
            app_id, algorithm, reason, lock_checked, safe_release, result, timestamp
        )


class LockRequestRepository(BaseRepository):
    """Repository for LockRequests persistence.

    LockRequests records lock *attempts* and the outcome the resource manager
    gave them. It complements ResourceLocks: ownership intervals live in the
    ledger, while a blocked request that never obtained a lock still leaves a
    record here.
    """

    OUTCOMES = ("GRANTED", "QUEUED", "RELEASED")

    def insert_lock_request(
        self,
        app_id: str,
        resource_id: str,
        outcome: str = "GRANTED",
        requested_at: Optional[float] = None,
    ) -> int:
        """Records one lock request and the outcome it received.

        Args:
            app_id (str): Application that made the request.
            resource_id (str): Resource that was requested.
            outcome (str): One of 'GRANTED', 'QUEUED' or 'RELEASED'.
            requested_at (Optional[float]): Request timestamp; defaults to now.

        Returns:
            int: The new request_id.

        Raises:
            sqlite3.IntegrityError: If a foreign key is violated or outcome is
                not one of the permitted values.
        """
        sql = """
            INSERT INTO LockRequests (app_id, resource_id, outcome, requested_at)
            VALUES (?, ?, ?, ?)
        """
        ts = requested_at if requested_at is not None else time.time()
        return self._execute(
            sql, (str(app_id), str(resource_id), str(outcome).upper(), float(ts))
        ).lastrowid

    def get_requests_by_app(self, app_id: str) -> List[Dict[str, Any]]:
        """Retrieves a app's request history, newest first."""
        sql = "SELECT * FROM LockRequests WHERE app_id = ? ORDER BY requested_at DESC"
        return self._execute_many_read(sql, (str(app_id),))

    def get_requests_by_resource(self, resource_id: str) -> List[Dict[str, Any]]:
        """Retrieves a resource's request history, newest first."""
        sql = "SELECT * FROM LockRequests WHERE resource_id = ? ORDER BY requested_at DESC"
        return self._execute_many_read(sql, (str(resource_id),))

    def get_requests_by_outcome(self, outcome: str) -> List[Dict[str, Any]]:
        """Retrieves every request that received a given outcome, newest first."""
        sql = "SELECT * FROM LockRequests WHERE outcome = ? ORDER BY requested_at DESC"
        return self._execute_many_read(sql, (str(outcome).upper(),))

    def get_all_requests(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves the most recent requests, newest first."""
        sql = "SELECT * FROM LockRequests ORDER BY requested_at DESC LIMIT ?"
        return self._execute_many_read(sql, (int(limit),))

    def log_lock_request(
        self,
        app_id: str,
        resource_id: str,
        outcome: str = "GRANTED",
        requested_at: Optional[float] = None,
    ) -> int:
        """Alias for insert_lock_request."""
        return self.insert_lock_request(app_id, resource_id, outcome, requested_at)


class SystemEventRepository(BaseRepository):
    """Repository for SystemEvents persistence.

    Holds machine-scoped samples such as memory pressure readings, which belong
    to the whole system rather than to any single application.
    """

    def insert_system_event(
        self,
        pressure_level: str,
        used_memory: int,
        total_memory: int,
        event_type: str = "MEMORY_PRESSURE",
        timestamp: Optional[float] = None,
    ) -> int:
        """Records a system-wide event sample.

        Args:
            pressure_level (str): One of 'GREEN', 'YELLOW', 'ORANGE' or 'RED'.
            used_memory (int): Memory in use at sample time.
            total_memory (int): Total system memory.
            event_type (str): Event category; defaults to 'MEMORY_PRESSURE'.
            timestamp (Optional[float]): Sample timestamp; defaults to now.

        Returns:
            int: The new event_id.

        Raises:
            sqlite3.IntegrityError: If pressure_level is not a known level.
        """
        sql = """
            INSERT INTO SystemEvents (event_type, pressure_level, used_memory, total_memory, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """
        ts = timestamp if timestamp is not None else time.time()
        return self._execute(
            sql,
            (
                str(event_type),
                str(pressure_level).upper(),
                int(used_memory),
                int(total_memory),
                float(ts),
            ),
        ).lastrowid

    def log_memory_pressure(
        self,
        pressure_level: str,
        used_memory: int,
        total_memory: int,
        event_type: str = "MEMORY_PRESSURE",
        timestamp: Optional[float] = None,
    ) -> int:
        """Alias for insert_system_event used by the memory manager."""
        return self.insert_system_event(
            pressure_level, used_memory, total_memory, event_type, timestamp
        )

    def get_events_by_type(self, event_type: str) -> List[Dict[str, Any]]:
        """Retrieves samples of one event type, newest first."""
        sql = "SELECT * FROM SystemEvents WHERE event_type = ? ORDER BY timestamp DESC"
        return self._execute_many_read(sql, (str(event_type),))

    def get_pressure_samples(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves the most recent memory pressure samples, newest first."""
        sql = """
            SELECT * FROM SystemEvents
            WHERE event_type = 'MEMORY_PRESSURE'
            ORDER BY timestamp DESC LIMIT ?
        """
        return self._execute_many_read(sql, (int(limit),))


class ConflictLogRepository(BaseRepository):
    """Repository for ConflictLog persistence."""

    def insert_conflict_log(
        self,
        waiting_app_id: str,
        blocking_app_id: str,
        resource_id: str,
        resolution_strategy: str = "WOUND_WAIT",
        resolved_by: Optional[str] = None,
        details: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> int:
        """Logs a resource contention incident.

        Args:
            waiting_app_id (str): Application that is waiting for the resource.
            blocking_app_id (str): Application currently holding the resource.
            resource_id (str): Resource under contention.
            resolution_strategy (str): Strategy applied; defaults to 'WOUND_WAIT'.
            resolved_by (Optional[str]): Resolving party, if any.
            details (Optional[str]): Free-form detail text.
            timestamp (Optional[float]): Event timestamp; defaults to now.

        Returns:
            int: The new conflict_id.

        Raises:
            sqlite3.IntegrityError: If a foreign key is violated.
        """
        sql = """
            INSERT INTO ConflictLog (
                waiting_app_id, blocking_app_id, resource_id,
                resolution_strategy, timestamp, resolved_by, details
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        ts = timestamp if timestamp is not None else time.time()
        return self._execute(
            sql,
            (
                str(waiting_app_id),
                str(blocking_app_id),
                str(resource_id),
                str(resolution_strategy),
                float(ts),
                str(resolved_by) if resolved_by else None,
                str(details) if details else None,
            ),
        ).lastrowid

    def get_conflicts_by_resource(self, resource_id: str) -> List[Dict[str, Any]]:
        """Retrieves conflicts involving a resource, newest first."""
        sql = "SELECT * FROM ConflictLog WHERE resource_id = ? ORDER BY timestamp DESC"
        return self._execute_many_read(sql, (str(resource_id),))

    def get_conflicts_by_app(self, app_id: str) -> List[Dict[str, Any]]:
        """Retrieves conflicts where an app is either the waiter or the blocker."""
        sql = """
            SELECT * FROM ConflictLog
            WHERE waiting_app_id = ? OR blocking_app_id = ?
            ORDER BY timestamp DESC
        """
        return self._execute_many_read(sql, (str(app_id), str(app_id)))

    def get_all_conflicts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves the most recent conflicts, newest first."""
        sql = "SELECT * FROM ConflictLog ORDER BY timestamp DESC LIMIT ?"
        return self._execute_many_read(sql, (int(limit),))

    def log_conflict(
        self,
        waiting_app_id: str,
        blocking_app_id: str,
        resource_id: str,
        resolution_strategy: str = "WOUND_WAIT",
        resolved_by: Optional[str] = None,
        details: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> int:
        """Alias for insert_conflict_log."""
        return self.insert_conflict_log(
            waiting_app_id,
            blocking_app_id,
            resource_id,
            resolution_strategy,
            resolved_by,
            details,
            timestamp,
        )