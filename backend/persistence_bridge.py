"""Fault-tolerant adapter between the simulation modules and the repository layer.

The Memory Manager, Resource Manager, SemaphoreLock and Conflict Manager already
contain persistence hooks of the form ``if self.db and hasattr(self.db, "...")``.
Two of those hooks had no matching repository, so they silently persisted
nothing:

* ``SemaphoreLock`` calls ``db.log_lock_request(app_id, resource_id, outcome)``
* ``MemoryManager.get_pressure_level`` calls ``db.log_memory_pressure(...)``

Those two methods are supplied here rather than by editing those modules.

The adapter also enforces one rule the managers cannot express on their own: a
database failure must never corrupt in-memory simulation state. Every write goes
through :meth:`PersistenceBridge._write`, which swallows the exception, records
it in :attr:`failures`, and returns ``None``. The managers mutate their objects
*before* persisting, so letting an exception escape would abort the simulation
halfway through a change it has already made.

``PersistenceBridge`` is a transparent proxy: anything it does not override is
forwarded to the wrapped ``DatabaseManager``, so it can be handed to code that
expects the real manager (``db.transaction()``, ``db.apps``, ...).
"""

from typing import Any, Callable, Dict, List, Optional

from backend.models.app import App
from backend.models.resource import Resource

REPOSITORY_ATTRIBUTES = (
    "apps",
    "resources",
    "resource_locks",
    "memory_events",
    "eviction_log",
    "conflict_log",
    "lock_requests",
    "system_events",
    "events",
)


class SafeRepository:
    """Fault-tolerant proxy around one ``DatabaseManager`` repository.

    Any statement reached through ``bridge.<repository>.<method>(...)`` runs
    inside a containment guard, so a repository the bridge does not name
    explicitly is still unable to break the simulation.

    Attributes:
        bridge (PersistenceBridge): Bridge recording any failure.
        name (str): Repository attribute name on the DatabaseManager.
    """

    def __init__(self, bridge: "PersistenceBridge", name: str) -> None:
        self.bridge = bridge
        self.name = name

    def _repository(self) -> Any:
        """Returns the live repository, resolved on each call so the db may change."""
        db = self.bridge.unwrapped
        return getattr(db, self.name) if db is not None else None

    def __getattr__(self, method: str) -> Any:
        """Wraps a repository method so its failures are recorded, not raised."""
        if method.startswith("_"):
            raise AttributeError(method)

        repository = self._repository()
        if repository is None:
            return lambda *args, **kwargs: None

        call = getattr(repository, method)

        def guarded(*args: Any, **kwargs: Any) -> Any:
            try:
                return call(*args, **kwargs)
            except Exception as error:  # noqa: BLE001 - persistence must never propagate
                self.bridge._record_failure(f"{self.name}.{method}", error)
                return None

        return guarded


class PersistenceBridge:
    """Drop-in ``DatabaseManager`` stand-in that cannot break the simulation.

    Attributes:
        db (Optional[Any]): The wrapped DatabaseManager, or None when
            persistence is disabled.
        failures (List[Dict[str, Any]]): One record per swallowed write failure,
            in the order they happened.
    """

    def __init__(self, db: Optional[Any] = None) -> None:
        self.db = db
        self.failures: List[Dict[str, Any]] = []

    # -----------------------------------------------------------------
    # Construction
    # -----------------------------------------------------------------

    @classmethod
    def wrap(cls, db: Optional[Any]) -> Optional["PersistenceBridge"]:
        """Returns a bridge for ``db``, or ``db`` itself when it needs none.

        Wrapping is idempotent and leaves a plain DatabaseManager untouched, so
        callers may pass either form without tracking which they hold.

        Args:
            db (Optional[Any]): DatabaseManager, an existing bridge, or None.

        Returns:
            Optional[PersistenceBridge]: A bridge, or None when db is None.
        """
        if db is None or isinstance(db, cls):
            return db
        return cls(db)

    @property
    def unwrapped(self) -> Optional[Any]:
        """Returns the underlying DatabaseManager, unwrapping nested bridges."""
        db = self.db
        while isinstance(db, PersistenceBridge):
            db = db.db
        return db

    @property
    def failure_count(self) -> int:
        """Returns how many writes have been dropped because of a DB failure."""
        return len(self.failures)

    # -----------------------------------------------------------------
    # Transparent proxy
    # -----------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Forwards every non-overridden attribute to the wrapped manager.

        Repository attributes come back as fault-tolerant proxies, so a write made
        through ``bridge.resource_locks.release_all_for_app(...)`` is contained
        just like ``bridge.save_app(...)``.
        """
        if name == "db":
            raise AttributeError(name)
        db = self.__dict__["db"]
        if db is None:
            raise AttributeError(
                f"No database is attached, so '{name}' is unavailable."
            )

        attribute = getattr(db, name)
        if name not in REPOSITORY_ATTRIBUTES:
            return attribute

        proxies = self.__dict__.setdefault("_proxies", {})
        if name not in proxies:
            proxies[name] = SafeRepository(self, name)
        return proxies[name]

    # -----------------------------------------------------------------
    # Failure containment
    # -----------------------------------------------------------------

    def _write(self, operation: str, call: Callable[[], Any]) -> Any:
        """Runs a repository write, containing any database failure.

        Args:
            operation (str): Label recorded against a failure, for diagnostics.
            call (Callable[[], Any]): Zero-argument callable performing the write.

        Returns:
            Any: The write's result on success, or None if it failed or no
            database is attached.
        """
        db = self.unwrapped
        if db is None:
            return None

        try:
            return call()
        except Exception as error:  # noqa: BLE001 - persistence must never propagate
            self._record_failure(operation, error)
            return None

    def _write_ok(self, operation: str, call: Callable[[], Any]) -> bool:
        """Runs a write whose caller only needs to know whether it landed."""
        db = self.unwrapped
        if db is None:
            return False

        try:
            call()
        except Exception as error:  # noqa: BLE001 - persistence must never propagate
            self._record_failure(operation, error)
            return False
        return True

    def _record_failure(self, operation: str, error: BaseException) -> None:
        """Appends a swallowed failure to the diagnostic record."""
        self.failures.append(
            {"operation": operation, "error": f"{type(error).__name__}: {error}"}
        )

    def transaction(self) -> "TolerantTransaction":
        """Returns a transaction scope that cannot break the caller.

        Repository writes group several rows that must land together. The
        in-memory simulation step has already been applied by the time these run,
        so a database failure while opening or committing the transaction is
        recorded and swallowed rather than propagated: the simulation keeps
        running with correct memory state and a gap in its history.

        An exception raised by the caller's own body is *not* swallowed. That is
        a simulation bug, not a persistence problem, and hiding it would be worse
        than losing a row.
        """
        return TolerantTransaction(self)

    # -----------------------------------------------------------------
    # App and resource state
    # -----------------------------------------------------------------

    def save_app(self, app: App) -> bool:
        """Inserts or updates an App row, containing any database failure."""
        return self._write_ok("save_app", lambda: self.unwrapped.apps.save(app))

    def delete_app(self, app_id: str) -> bool:
        """Deletes an App row, containing any database failure."""
        return self._write_ok("delete_app", lambda: self.unwrapped.apps.delete_app(app_id))

    def save_resource(self, resource: Resource) -> bool:
        """Inserts or updates a Resources row, containing any database failure."""
        return self._write_ok("save_resource", lambda: self.unwrapped.resources.save(resource))

    # -----------------------------------------------------------------
    # Event hooks used by the simulation modules
    # -----------------------------------------------------------------

    def log_memory_action(
        self,
        app_id: str,
        action: str,
        before: int,
        after: int,
        pressure: str,
        timestamp: Optional[float] = None,
    ) -> Optional[int]:
        """Logs an allocation or deallocation against an app.

        Supplies the hook ``MemoryManager.allocate_memory`` already calls.
        """
        return self._write(
            "log_memory_action",
            lambda: self.unwrapped.memory_events.insert_memory_event(
                app_id, action, before, after, pressure, timestamp
            ),
        )

    def log_memory_pressure(
        self,
        pressure_level: str,
        used_memory: int,
        total_memory: int,
        timestamp: Optional[float] = None,
    ) -> Optional[int]:
        """Logs a system-wide memory pressure sample.

        Supplies the hook ``MemoryManager.get_pressure_level`` already calls.
        The sample is machine-scoped, so it lands in SystemEvents rather than
        against any one app.

        Args:
            pressure_level (str): 'GREEN', 'YELLOW', 'ORANGE' or 'RED'.
            used_memory (int): Memory in use at sample time.
            total_memory (int): Total system memory.
            timestamp (Optional[float]): Sample timestamp; defaults to now.

        Returns:
            Optional[int]: The new event_id, or None if the write failed.
        """
        return self._write(
            "log_memory_pressure",
            lambda: self.unwrapped.system_events.insert_system_event(
                pressure_level, used_memory, total_memory, "MEMORY_PRESSURE", timestamp
            ),
        )

    def log_lock_request(
        self,
        app_id: str,
        resource_id: str,
        outcome: str = "GRANTED",
        requested_at: Optional[float] = None,
    ) -> Optional[int]:
        """Records a lock request outcome, supplying the hook SemaphoreLock calls.

        This is also the single owner of the ResourceLocks ledger write, so an
        acquisition and its release are recorded in exactly one place. Writes are
        idempotent: re-reporting an already-held lock, or releasing one twice,
        leaves the ledger untouched instead of failing.

        Args:
            app_id (str): Application making the request.
            resource_id (str): Resource requested.
            outcome (str): 'GRANTED', 'QUEUED' or 'RELEASED'.
            requested_at (Optional[float]): Request timestamp; defaults to now.

        Returns:
            Optional[int]: The new request_id, or None if the write failed.
        """
        db = self.unwrapped
        if db is None:
            return None

        decision = str(outcome).upper()

        # The ledger is state, so it is only touched when ownership changes.
        if decision == "GRANTED" and not self._owns_lock(app_id, resource_id):
            self._write(
                "acquire_resource_lock",
                lambda: db.resource_locks.acquire_resource_lock(
                    app_id, resource_id, acquired_at=requested_at
                ),
            )
        elif decision == "RELEASED" and self._owns_lock(app_id, resource_id):
            self._write(
                "release_lock_for",
                lambda: db.resource_locks.release_lock_for(
                    app_id, resource_id, released_at=requested_at
                ),
            )

        # QUEUED never owns the resource, so it produces no lock row; the
        # waiting queue itself lives on the Resources row.
        return self._write(
            "log_lock_request",
            lambda: db.lock_requests.insert_lock_request(
                app_id, resource_id, decision, requested_at
            ),
        )

    def log_eviction(
        self,
        app_id: str,
        algorithm: str,
        reason: str,
        lock_checked: bool,
        safe_release: bool,
        result: str,
        timestamp: Optional[float] = None,
    ) -> Optional[int]:
        """Logs an eviction attempt together with the outcome it produced."""
        return self._write(
            "log_eviction",
            lambda: self.unwrapped.eviction_log.insert_eviction_log(
                app_id, algorithm, reason, lock_checked, safe_release, result, timestamp
            ),
        )

    def log_conflict(
        self,
        waiting_app_id: str,
        blocking_app_id: str,
        resource_id: str,
        resolution_strategy: str = "WOUND_WAIT",
        resolved_by: Optional[str] = None,
        details: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> Optional[int]:
        """Logs a Wound-Wait conflict resolution."""
        return self._write(
            "log_conflict",
            lambda: self.unwrapped.conflict_log.insert_conflict_log(
                waiting_app_id,
                blocking_app_id,
                resource_id,
                resolution_strategy,
                resolved_by,
                details,
                timestamp,
            ),
        )

    def log_freeze(
        self,
        waiting_app_id: str,
        blocking_app_id: str,
        resource_id: str,
        details: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> Optional[int]:
        """Logs a freeze incident: a waiting app stranded behind a dead holder.

        A freeze is a conflict whose resolution failed, so it is recorded in
        ConflictLog with a 'NONE' strategy and a 'FREEZE' marker.
        """
        return self.log_conflict(
            waiting_app_id=waiting_app_id,
            blocking_app_id=blocking_app_id,
            resource_id=resource_id,
            resolution_strategy="NONE",
            resolved_by=None,
            details=f"FREEZE: {details}" if details else "FREEZE",
            timestamp=timestamp,
        )

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _owns_lock(self, app_id: str, resource_id: str) -> bool:
        """Returns True when the app already holds a live lock on the resource.

        A read, so it is not recorded as a failure: if the ledger cannot be read
        the safe answer is "not held", and the write that follows will report its
        own failure.
        """
        db = self.unwrapped
        if db is None:
            return False
        try:
            return db.resource_locks.get_active_lock(app_id, resource_id) is not None
        except Exception as error:  # noqa: BLE001 - a failed read must not propagate
            self._record_failure("get_active_lock", error)
            return False


class TolerantTransaction:
    """Transaction scope whose database failures cannot abort the caller.

    Written as a class rather than a generator on purpose. A generator-based
    context manager that swallows an exception raised *before* its ``yield``
    finishes without yielding, and ``contextlib`` then reports that as
    ``RuntimeError: generator didn't yield`` - turning a contained database
    error into a new, more confusing failure.

    Attributes:
        bridge (PersistenceBridge): Bridge owning the real transaction.
        _scope (Optional[Any]): The underlying transaction scope, or None when
            no database is attached or the scope could not be opened.
    """

    def __init__(self, bridge: PersistenceBridge) -> None:
        self.bridge = bridge
        self._scope: Optional[Any] = None

    def __enter__(self) -> PersistenceBridge:
        db = self.bridge.unwrapped
        if db is None:
            return self.bridge

        scope = db.transaction()
        try:
            scope.__enter__()
        except Exception as error:  # noqa: BLE001 - persistence must never propagate
            self.bridge._record_failure("transaction_begin", error)
            self._scope = None
            return self.bridge

        self._scope = scope
        return self.bridge

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        """Closes the transaction, containing database failures on a clean exit.

        An exception from the caller's body is handed to the real scope so it
        rolls back and re-raises unchanged: a simulation bug must not be hidden.
        """
        if self._scope is None:
            return False

        try:
            self._scope.__exit__(exc_type, exc, tb)
        except Exception as error:  # noqa: BLE001 - persistence must never propagate
            if exc_type is None:
                self.bridge._record_failure("transaction_commit", error)
                return False
            raise
        finally:
            self._scope = None

        return False