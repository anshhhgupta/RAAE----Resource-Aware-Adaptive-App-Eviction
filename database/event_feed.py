"""Unified event feed queries across the RAAE log tables.

``get_recent_events`` merges the three append-only log tables (MemoryEvents,
EvictionLog, ConflictLog) into a single timeline. Each row is normalized to a
common shape so callers can render one list without knowing which table an event
came from.
"""

from typing import Any, Dict, List, Optional, Sequence

from database.repositories import BaseRepository

MEMORY_EVENT = "MEMORY"
EVICTION_EVENT = "EVICTION"
CONFLICT_EVENT = "CONFLICT"

EVENT_TYPES = (MEMORY_EVENT, EVICTION_EVENT, CONFLICT_EVENT)

_MEMORY_SQL = """
    SELECT 'MEMORY' AS event_type,
           event_id AS id,
           app_id,
           NULL AS resource_id,
           action AS code,
           pressure_level AS detail,
           memory_before AS value_before,
           memory_after AS value_after,
           timestamp
    FROM MemoryEvents
    WHERE 1 = 1
"""

_EVICTION_SQL = """
    SELECT 'EVICTION' AS event_type,
           event_id AS id,
           app_id,
           NULL AS resource_id,
           algorithm AS code,
           reason AS detail,
           lock_checked AS value_before,
           safe_release AS value_after,
           timestamp
    FROM EvictionLog
    WHERE 1 = 1
"""

_CONFLICT_SQL = """
    SELECT 'CONFLICT' AS event_type,
           conflict_id AS id,
           blocking_app_id AS app_id,
           resource_id,
           resolution_strategy AS code,
           COALESCE(details, '') AS detail,
           waiting_app_id AS value_before,
           NULL AS value_after,
           timestamp
    FROM ConflictLog
    WHERE 1 = 1
"""


class EventRepository(BaseRepository):
    """Repository for cross-table event timeline queries."""

    def get_recent_events(
        self,
        limit: int = 50,
        app_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        event_types: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves the most recent events across all log tables.

        Args:
            limit (int): Maximum number of events to return.
            app_id (Optional[str]): Restricts to events involving this app. For
                conflicts, both the waiting and the blocking app match.
            resource_id (Optional[str]): Restricts to events on this resource.
                Only ConflictLog carries a resource_id, so memory and eviction
                branches are excluded when this filter is supplied.
            event_types (Optional[Sequence[str]]): Subset of EVENT_TYPES to include.

        Returns:
            List[Dict[str, Any]]: Events ordered newest first, each with keys
            event_type, id, app_id, resource_id, code, detail, value_before,
            value_after and timestamp.

        Raises:
            ValueError: If an unrecognized event type is requested.
        """
        selected = self._validate_event_types(event_types)

        branches: List[str] = []
        params: List[Any] = []

        if MEMORY_EVENT in selected and resource_id is None:
            sql, binds = self._memory_branch(app_id)
            branches.append(sql)
            params.extend(binds)

        if EVICTION_EVENT in selected and resource_id is None:
            sql, binds = self._eviction_branch(app_id)
            branches.append(sql)
            params.extend(binds)

        if CONFLICT_EVENT in selected:
            sql, binds = self._conflict_branch(app_id, resource_id)
            branches.append(sql)
            params.extend(binds)

        if not branches:
            return []

        params.append(int(limit))
        sql = "\nUNION ALL\n".join(branches) + "\nORDER BY timestamp DESC\nLIMIT ?"
        return self._execute_many_read(sql, params)

    @staticmethod
    def _memory_branch(app_id: Optional[str]) -> "tuple[str, List[Any]]":
        """Builds the MemoryEvents branch and its bind parameters."""
        if app_id is None:
            return _MEMORY_SQL, []
        return _MEMORY_SQL + " AND app_id = ?", [str(app_id)]

    @staticmethod
    def _eviction_branch(app_id: Optional[str]) -> "tuple[str, List[Any]]":
        """Builds the EvictionLog branch and its bind parameters."""
        if app_id is None:
            return _EVICTION_SQL, []
        return _EVICTION_SQL + " AND app_id = ?", [str(app_id)]

    @staticmethod
    def _conflict_branch(
        app_id: Optional[str], resource_id: Optional[str]
    ) -> "tuple[str, List[Any]]":
        """Builds the ConflictLog branch and its bind parameters.

        Filters are appended in a fixed order and their binds collected in the
        same order, so the combined parameter list always matches the SQL.
        """
        sql = _CONFLICT_SQL
        binds: List[Any] = []

        if resource_id is not None:
            sql += " AND resource_id = ?"
            binds.append(str(resource_id))

        if app_id is not None:
            sql += " AND (waiting_app_id = ? OR blocking_app_id = ?)"
            binds.extend([str(app_id), str(app_id)])

        return sql, binds

    @staticmethod
    def _validate_event_types(event_types: Optional[Sequence[str]]) -> Dict[str, bool]:
        """Validates and normalizes a requested set of event types.

        Args:
            event_types (Optional[Sequence[str]]): Requested types; None means all.

        Returns:
            Dict[str, bool]: Mapping of each present event type to True.

        Raises:
            ValueError: If an unrecognized event type is requested.
        """
        if event_types is None:
            return {name: True for name in EVENT_TYPES}

        selected: Dict[str, bool] = {}
        for name in event_types:
            normalized = str(name).strip().upper()
            if normalized not in EVENT_TYPES:
                raise ValueError(
                    f"Unknown event type {name!r}. Expected one of {', '.join(EVENT_TYPES)}."
                )
            selected[normalized] = True
        return selected