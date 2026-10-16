"""Transaction primitives for the RAAE repository / data-access layer.

This module provides the atomicity mechanism only. It contains no eviction,
Clock or Wound-Wait logic: those decisions belong to the OS modules, which call
the helpers here to make a group of writes commit or roll back together.

SQLite notes that drive the design:

* ``with conn:`` commits on a clean exit even inside an explicit ``BEGIN``, so
  repository helpers must not be allowed to open a context that ends the
  enclosing transaction early. ``TransactionConnection`` neutralizes ``__exit__``.
* ``conn.executescript()`` issues an implicit ``COMMIT`` before running, which
  would silently break atomicity, so it is blocked inside a transaction.
* ``conn.commit()`` reached through a proxy commits the whole transaction, so
  it is blocked too.
* SQLite has no nested ``BEGIN``; nesting is implemented with ``SAVEPOINT``,
  which gives inner blocks independent rollback.
"""

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Optional


class TransactionError(RuntimeError):
    """Raised when an operation is attempted that would break transaction boundaries."""


class TransactionConnection:
    """Connection proxy that keeps a caller from ending the enclosing transaction.

    It deliberately suppresses the context-manager commit that ``sqlite3``
    performs on a clean ``__exit__``, and refuses ``commit``, ``rollback``,
    ``close`` and ``executescript`` while a transaction is active.

    Attributes:
        _connection (sqlite3.Connection): The real underlying connection.
    """

    _BLOCKED = (
        "commit",
        "rollback",
        "close",
        "executescript",
        "backup",
    )

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> "TransactionConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        """Returns False so an exception propagates and the transaction rolls back."""
        return False

    @property
    def in_transaction(self) -> bool:
        """Returns whether SQLite currently has an open transaction."""
        return bool(self._connection.in_transaction)

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        """Executes a parameterized statement against the underlying connection."""
        return self._connection.execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Any) -> sqlite3.Cursor:
        """Executes a parameterized statement for every parameter set."""
        return self._connection.executemany(sql, seq_of_parameters)

    def cursor(self) -> sqlite3.Cursor:
        """Returns a raw cursor for callers that need to read results directly."""
        return self._connection.cursor()

    def __getattr__(self, name: str) -> Any:
        """Proxies attribute access, refusing calls that would end the transaction."""
        if name in TransactionConnection._BLOCKED:
            raise TransactionError(
                f"{name}() cannot be called inside a transaction; "
                "the transaction context manager owns commit and rollback."
            )
        return getattr(self._connection, name)


class TransactionManager:
    """Provides atomic units of work over a single SQLite connection.

    The outermost ``transaction()`` issues ``BEGIN``; nested calls join the
    existing transaction using ``SAVEPOINT`` so that an inner failure can roll
    back only its own work, while still propagating to the outer block.

    Attributes:
        _connection (sqlite3.Connection): Connection used for all statements.
        _depth (int): Current nesting depth.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._depth: int = 0

    @property
    def in_transaction(self) -> bool:
        """Returns True while any transaction or savepoint scope is open."""
        return self._depth > 0

    @property
    def depth(self) -> int:
        """Returns the current nesting depth."""
        return self._depth

    def connection(self) -> Any:
        """Returns a handle appropriate for the current transaction state.

        Outside a transaction this is the plain connection, so repository
        helpers behave normally. Inside one it is a ``TransactionConnection``
        that cannot end the transaction early.
        """
        if self._depth > 0:
            return TransactionConnection(self._connection)
        return self._connection

    @contextmanager
    def transaction(self) -> Iterator["TransactionManager"]:
        """Runs a block atomically, committing on success and rolling back on failure.

        Nested calls become ``SAVEPOINT`` scopes: a failure inside the inner
        block undoes the inner writes and re-raises, so the outer block can
        decide whether to continue or roll back everything.

        Yields:
            TransactionManager: This manager, so callers can nest or inspect state.

        Raises:
            BaseException: Re-raised after rollback so callers still see the
                original failure.
        """
        outermost = self._depth == 0
        savepoint = f"raae_sp_{self._depth}"

        if outermost:
            self._connection.execute("BEGIN;")
        else:
            self._connection.execute(f"SAVEPOINT {savepoint};")

        self._depth += 1
        try:
            yield self
        except BaseException:
            self._depth -= 1
            self._rollback(outermost, savepoint)
            raise
        else:
            self._depth -= 1
            self._commit(outermost, savepoint)

    def _commit(self, outermost: bool, savepoint: str) -> None:
        """Commits or releases the scope opened by this transaction block."""
        if outermost:
            self._connection.commit()
        else:
            self._connection.execute(f"RELEASE SAVEPOINT {savepoint};")

    def _rollback(self, outermost: bool, savepoint: str) -> None:
        """Rolls back the scope opened by this transaction block."""
        if outermost:
            self._connection.rollback()
        else:
            self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint};")
            self._connection.execute(f"RELEASE SAVEPOINT {savepoint};")


def ensure_no_open_transaction(manager: TransactionManager, operation: str) -> None:
    """Raises if a transaction is open, for operations that cannot run inside one.

    Args:
        manager (TransactionManager): Manager whose state is inspected.
        operation (str): Name of the operation being attempted.

    Raises:
        TransactionError: If a transaction is currently open.
    """
    if manager.in_transaction:
        raise TransactionError(
            f"{operation} cannot run inside an open transaction because SQLite "
            "commits implicitly; close the transaction first."
        )