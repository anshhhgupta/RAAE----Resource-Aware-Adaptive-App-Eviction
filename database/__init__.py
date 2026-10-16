"""Database package for RAAE simulation.

All SQL lives in this package. ``DatabaseManager`` owns connections and exposes
one repository per table.
"""

from .db import DatabaseManager
from .analytics import (
    DEFAULT_BUCKET_SECONDS,
    FREEZE_MARKER,
    POLICY_BASELINE,
    POLICY_RAAE,
    AnalyticsRepository,
)
from .event_feed import (
    CONFLICT_EVENT,
    EVENT_TYPES,
    EVICTION_EVENT,
    MEMORY_EVENT,
    EventRepository,
)
from .repositories import (
    AppRepository,
    BaseRepository,
    ConflictLogRepository,
    EvictionLogRepository,
    MemoryEventRepository,
    ResourceLockRepository,
    ResourceRepository,
)
from .transaction import (
    TransactionConnection,
    TransactionError,
    TransactionManager,
    ensure_no_open_transaction,
)

__all__ = [
    "DatabaseManager",
    "AnalyticsRepository",
    "BaseRepository",
    "AppRepository",
    "ResourceRepository",
    "ResourceLockRepository",
    "MemoryEventRepository",
    "EvictionLogRepository",
    "ConflictLogRepository",
    "EventRepository",
    "TransactionManager",
    "TransactionConnection",
    "TransactionError",
    "ensure_no_open_transaction",
    "EVENT_TYPES",
    "MEMORY_EVENT",
    "EVICTION_EVENT",
    "CONFLICT_EVENT",
    "FREEZE_MARKER",
    "DEFAULT_BUCKET_SECONDS",
    "POLICY_BASELINE",
    "POLICY_RAAE",
]