"""SQLite Database Manager for RAAE Simulation.

Owns connection handling, schema initialization (with ``PRAGMA foreign_keys = ON``),
transactions, and the repositories that hold every SQL statement in the project.

Callers should use the repositories (``db.apps``, ``db.resources``, ...) or the
convenience delegates below. No SQL belongs outside the ``database`` package.
"""

import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from backend.models.app import App
from backend.models.resource import Resource
from database.event_feed import EventRepository
from database.repositories import (
    AppRepository,
    ConflictLogRepository,
    EvictionLogRepository,
    MemoryEventRepository,
    ResourceLockRepository,
    ResourceRepository,
)
from database.transaction import TransactionManager, ensure_no_open_transaction


class DatabaseManager:
    """Manages SQLite connections, schema initialization, and repository access.

    Attributes:
        db_path (str): File path to the SQLite database file, or ':memory:'.
    """

    REQUIRED_TABLES = [
        "Apps",
        "Resources",
        "ResourceLocks",
        "MemoryEvents",
        "EvictionLog",
        "ConflictLog",
    ]

    def __init__(self, db_path: str = "database/raae.db") -> None:
        self.db_path: str = db_path
        self._shared_conn: Optional[sqlite3.Connection] = None

        if db_path != ":memory:":
            db_dir = os.path.dirname(os.path.abspath(db_path))
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

        self._shared_conn = self._open_connection()
        self._tx = TransactionManager(self._shared_conn)

        self.apps = AppRepository(self)
        self.resources = ResourceRepository(self)
        self.resource_locks = ResourceLockRepository(self)
        self.memory_events = MemoryEventRepository(self)
        self.eviction_log = EvictionLogRepository(self)
        self.conflict_log = ConflictLogRepository(self)
        self.events = EventRepository(self)

        self.init_db()

    def _open_connection(self) -> sqlite3.Connection:
        """Opens a connection configured with Row factory and foreign key enforcement."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def get_connection(self) -> Any:
        """Returns the connection, wrapped so callers cannot end a transaction early."""
        return self._tx.connection()

    @contextmanager
    def transaction(self) -> Iterator["DatabaseManager"]:
        """Runs repository operations in one atomic database transaction.

        Nested calls join the enclosing transaction via SAVEPOINT, so an inner
        failure rolls back only its own writes before propagating.

        Yields:
            DatabaseManager: This manager, so callers can nest transactions.

        Raises:
            BaseException: Re-raised after rollback so callers still see the
                original failure.
        """
        with self._tx.transaction():
            yield self

    @property
    def in_transaction(self) -> bool:
        """Returns True while a transaction or savepoint scope is open."""
        return self._tx.in_transaction

    def init_db(self) -> None:
        """Applies schema.sql, which is fully idempotent."""
        ensure_no_open_transaction(self._tx, "init_db()")
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        if not os.path.exists(schema_path):
            return

        with open(schema_path, "r", encoding="utf-8") as f:
            schema_sql = f.read()

        conn = self.get_connection()
        conn.executescript(schema_sql)
        conn.commit()

    def get_table_names(self) -> List[str]:
        """Returns all user table names present in the database."""
        sql = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name ASC;"
        with self.get_connection() as conn:
            rows = conn.execute(sql).fetchall()
        return [row["name"] for row in rows]

    def get_index_names(self) -> List[str]:
        """Returns all index names present in the database."""
        sql = "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name ASC;"
        with self.get_connection() as conn:
            rows = conn.execute(sql).fetchall()
        return [row["name"] for row in rows]

    # ---------------------------------------------------------
    # Apps
    # ---------------------------------------------------------

    def create_app(self, app: App) -> App:
        """Inserts a new App record."""
        return self.apps.create_app(app)

    def update_app(self, app: App) -> bool:
        """Updates an existing App record."""
        return self.apps.update_app(app)

    def save_app(self, app: App) -> None:
        """Inserts or updates an App record."""
        self.apps.save(app)

    def get_app(self, app_id: str) -> Optional[App]:
        """Retrieves an App by app_id."""
        return self.apps.get_app(app_id)

    def get_all_apps(self) -> List[App]:
        """Retrieves all App records."""
        return self.apps.get_all_apps()

    def delete_app(self, app_id: str) -> bool:
        """Deletes an App record by app_id."""
        return self.apps.delete_app(app_id)

    # ---------------------------------------------------------
    # Resources
    # ---------------------------------------------------------

    def create_resource(self, resource: Resource) -> Resource:
        """Inserts a new Resource record."""
        return self.resources.create_resource(resource)

    def save_resource(self, resource: Resource) -> None:
        """Inserts or updates a Resource record."""
        self.resources.save(resource)

    def get_resource(self, resource_id: str) -> Optional[Resource]:
        """Retrieves a Resource by resource_id."""
        return self.resources.get_resource(resource_id)

    def get_resources(self, **filters: Any) -> List[Resource]:
        """Retrieves Resources matching the supplied filters."""
        return self.resources.get_resources(**filters)

    def get_all_resources(self) -> List[Resource]:
        """Retrieves all Resource records."""
        return self.resources.get_resources()

    # ---------------------------------------------------------
    # Resource locks
    # ---------------------------------------------------------

    def acquire_resource_lock(self, app_id: str, resource_id: str, units: int = 1) -> int:
        """Records a resource lock acquisition and returns the new lock_id."""
        return self.resource_locks.acquire_resource_lock(app_id, resource_id, units)

    def release_resource_lock(self, lock_id: int) -> bool:
        """Marks a resource lock as released."""
        return self.resource_locks.release_resource_lock(lock_id)

    # ---------------------------------------------------------
    # Logs
    # ---------------------------------------------------------

    def insert_memory_event(
        self,
        app_id: str,
        action: str,
        memory_before: int,
        memory_after: int,
        pressure_level: str,
        timestamp: Optional[float] = None,
    ) -> int:
        """Logs a memory event and returns the new event_id."""
        return self.memory_events.insert_memory_event(
            app_id, action, memory_before, memory_after, pressure_level, timestamp
        )

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
        """Logs an eviction decision and returns the new event_id."""
        return self.eviction_log.insert_eviction_log(
            app_id, algorithm, reason, lock_checked, safe_release, result, timestamp
        )

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
        """Logs a resource conflict and returns the new conflict_id."""
        return self.conflict_log.insert_conflict_log(
            waiting_app_id,
            blocking_app_id,
            resource_id,
            resolution_strategy,
            resolved_by,
            details,
            timestamp,
        )

    def get_recent_events(self, limit: int = 50, **filters: Any) -> List[Dict[str, Any]]:
        """Retrieves a merged, newest-first timeline across the log tables."""
        return self.events.get_recent_events(limit=limit, **filters)

    # ---------------------------------------------------------
    # Backward compatible helpers
    # ---------------------------------------------------------

    def log_memory_action(
        self,
        app_id: str,
        action: str,
        before: int,
        after: int,
        pressure: str,
        timestamp: Optional[float] = None,
    ) -> int:
        """Logs a memory allocation or deallocation event."""
        return self.memory_events.insert_memory_event(
            app_id, action, before, after, pressure, timestamp
        )

    def get_all_memory_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves recent MemoryEvents rows."""
        return self.memory_events.get_all_events(limit)

    def close(self) -> None:
        """Closes the managed database connection."""
        if self._shared_conn is not None:
            self._shared_conn.close()
            self._shared_conn = None