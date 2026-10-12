"""Read-only analytics queries backing the Phase-II demonstration dashboards.

Every function here answers one question a dashboard asks and returns a plain
Python structure - an ``int``, a ``float``/``None``, or a ``list`` of ``dict``
with stable keys. Nothing returns a ``sqlite3.Row``, a cursor or a model object,
so the results can be handed straight to Streamlit widgets or to Matplotlib as
series data without an adapter in between.

This module only reads. It never writes, and it never re-implements a policy
decision: where a definition depends on how the simulation records something
(for example what counts as a freeze), that definition is documented against the
table and the marker the writers actually emit.

Definitions used throughout
----------------------------

Eviction
    A row in ``EvictionLog``. ``result`` distinguishes the outcomes: ``EVICTED``
    is a completed eviction, while ``WAIT`` and ``CONFLICT`` are decisions that
    blocked one. The two are counted separately wherever both matter.

Freeze
    A conflict whose waiter was stranded by a blind eviction. Both writers of
    that event - ``BaselineClockPolicy`` and ``PersistenceBridge.log_freeze`` -
    open ``ConflictLog.details`` with the literal marker ``FREEZE``. The marker,
    not ``resolution_strategy = 'NONE'``, is what identifies a freeze, so an
    unrelated unresolved conflict is never counted as a stranded waiter.

Acquisition and release
    Counted from ``LockRequests``, which records every attempt and its outcome.
    ``GRANTED`` is a successful acquisition and ``RELEASED`` is a release. This
    is used rather than ``ResourceLocks`` because a blocked request leaves a
    ``LockRequests`` row but no lock, and the queued attempts are part of the
    contention story the dashboards tell.

Index use
    Every query filters or groups on an indexed column: ``result`` and
    ``app_id`` on ``EvictionLog``, ``details`` and ``resource_id`` on
    ``ConflictLog``, ``outcome`` on ``LockRequests``, and ``timestamp`` on each
    log for the time-bucketed series. The indexes backing those access paths are
    declared at the bottom of ``schema.sql``.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from database.repositories import BaseRepository

FREEZE_MARKER = "FREEZE"
FREEZE_PREFIX = FREEZE_MARKER + "%"

# Kept as a SQL fragment rather than a bind parameter on purpose. SQLite can only
# match a partial index when the query's WHERE clause repeats the index predicate
# literally; a bound `?` is opaque at prepare time, so the freeze count would
# fall back to scanning ConflictLog. The value is a module constant, never caller
# input, so inlining it carries no injection risk.
FREEZE_PREDICATE = f"details LIKE '{FREEZE_PREFIX}'"

DEFAULT_BUCKET_SECONDS = 60

POLICY_BASELINE = "BASELINE_CLOCK"
POLICY_RAAE = "RAAE"

# The policy that produced a decision is stored as the algorithm name. Baseline
# writes the policy name unchanged; the RAAE engine writes its own algorithm
# string, "RAAE_CLOCK", so it is mapped here rather than forcing every caller to
# know the difference.
_ALGORITHM_POLICY = {
    POLICY_BASELINE: POLICY_BASELINE,
    "RAAE_CLOCK": POLICY_RAAE,
}


def _policy_for_algorithm(algorithm: str) -> str:
    """Returns the policy name behind an ``EvictionLog.algorithm`` value.

    Falls back to the algorithm string itself so an unknown or hand-written
    algorithm still produces a labelled row rather than being dropped from the
    comparison.
    """
    return _ALGORITHM_POLICY.get(algorithm, algorithm)


class AnalyticsRepository(BaseRepository):
    """Aggregations over the simulation log tables.

    Reached as ``db.analytics``. Every method is a read; the repository owns no
    state beyond the connection it borrows from its DatabaseManager.
    """

    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------

    def _scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        """Runs a query expected to yield one value and returns it.

        Returns:
            Any: The first column of the first row, or None when nothing matched.
        """
        with self._get_connection() as conn:
            row = conn.execute(sql, tuple(params)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _as_int(value: Any) -> int:
        """Coerces an aggregate result to int, mapping SQL NULL onto 0.

        A COUNT never returns NULL, but COALESCE-guarded expressions used by the
        bucketed series can, and a dashboard tile must show a number rather than
        an error.
        """
        return 0 if value is None else int(value)

    # ---------------------------------------------------------
    # Headline counters
    # ---------------------------------------------------------

    def total_eviction_count(self, result: Optional[str] = "EVICTED") -> int:
        """Returns the number of eviction records, by default the completed ones.

        Args:
            result (Optional[str]): Restricts to one ``EvictionLog.result``
                value. Pass None to count every recorded decision, including
                the ``WAIT`` and ``CONFLICT`` decisions that blocked an eviction.

        Returns:
            int: Matching eviction records.
        """
        if result is None:
            sql = "SELECT COUNT(*) FROM EvictionLog"
            params: Tuple[Any, ...] = ()
        else:
            sql = "SELECT COUNT(*) FROM EvictionLog WHERE result = ?"
            params = (str(result),)
        return self._as_int(self._scalar(sql, params))

    def total_evictions_by_result(self) -> Dict[str, int]:
        """Returns completed evictions and blocked decisions broken down by outcome.

        Returns:
            Dict[str, int]: ``result`` value mapped to its count, e.g.
                ``{'EVICTED': 2, 'CONFLICT': 1}``. A dashboard bar chart of this
                mapping shows refused evictions without a second query.
        """
        sql = """
            SELECT result, COUNT(*) AS total
            FROM EvictionLog
            GROUP BY result
            ORDER BY total DESC, result ASC
        """
        return {str(row["result"]): self._as_int(row["total"]) for row in self._execute_many_read(sql)}

    def total_freeze_incidents(self) -> int:
        """Returns how many waiting apps were stranded by an eviction.

        Returns:
            int: ConflictLog rows carrying the FREEZE marker.
        """
        sql = f"SELECT COUNT(*) FROM ConflictLog WHERE {FREEZE_PREDICATE}"
        return self._as_int(self._scalar(sql))

    def total_conflicts(self, freeze_only: bool = False) -> int:
        """Returns the number of recorded resource conflicts.

        Args:
            freeze_only (bool): Counts only stranded-waiter freezes rather than
                every conflict resolution attempt.

        Returns:
            int: Matching ConflictLog rows.
        """
        if freeze_only:
            return self.total_freeze_incidents()
        return self._as_int(self._scalar("SELECT COUNT(*) FROM ConflictLog"))

    def successful_resource_acquisitions(self) -> int:
        """Returns how many lock requests were granted.

        Returns:
            int: LockRequests rows with outcome ``GRANTED``.
        """
        return self._count_lock_requests("GRANTED")

    def resource_releases(self) -> int:
        """Returns how many held locks were released.

        Returns:
            int: LockRequests rows with outcome ``RELEASED``.
        """
        return self._count_lock_requests("RELEASED")

    def queued_resource_requests(self) -> int:
        """Returns how many lock requests were blocked and queued.

        Included alongside the granted and released counts so a dashboard can
        show contention, not just throughput.

        Returns:
            int: LockRequests rows with outcome ``QUEUED``.
        """
        return self._count_lock_requests("QUEUED")

    def _count_lock_requests(self, outcome: str) -> int:
        """Counts LockRequests rows carrying one outcome.

        The granted, released and queued counters are the same query with a
        different outcome, so they share this one statement rather than
        repeating it three times.
        """
        sql = "SELECT COUNT(*) FROM LockRequests WHERE outcome = ?"
        return self._as_int(self._scalar(sql, (str(outcome).upper(),)))

    # ---------------------------------------------------------
    # Breakdowns
    # ---------------------------------------------------------

    def evictions_by_app(self, result: Optional[str] = "EVICTED", limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Returns eviction activity per application, busiest first.

        Args:
            result (Optional[str]): Restricts to one ``EvictionLog.result`` value.
                None counts every decision, so ``attempts`` and ``evictions``
                differ and a blocked app is visible rather than absent.
            limit (Optional[int]): Caps the number of rows returned.

        Returns:
            List[Dict[str, Any]]: One row per app with keys ``app_id``,
                ``app_name``, ``evictions``, ``attempts``, ``blocked`` and
                ``memory_freed_mb``. Suitable as a Matplotlib bar-chart series
                (``app_name`` against ``evictions``).

        ``memory_freed_mb`` sums the ``memory_before - memory_after`` delta of
        the app's ``EVICT`` MemoryEvents. It cannot be read off ``Apps``, because
        eviction zeroes the persisted footprint in the same step that records the
        eviction, so by the time anything reads it the before-value is gone.
        """
        clauses: List[str] = []
        params: List[Any] = []

        if result is not None:
            clauses.append("e.result = ?")
            params.append(str(result))

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT e.app_id AS app_id,
                   a.name AS app_name,
                   SUM(CASE WHEN e.result = 'EVICTED' THEN 1 ELSE 0 END) AS evictions,
                   COUNT(*) AS attempts,
                   SUM(CASE WHEN e.result <> 'EVICTED' THEN 1 ELSE 0 END) AS blocked,
                   COALESCE((SELECT SUM(m.memory_before - m.memory_after)
                               FROM MemoryEvents m
                              WHERE m.app_id = e.app_id
                                AND m.action = 'EVICT'), 0) AS memory_freed_mb
            FROM EvictionLog e
            JOIN Apps a ON e.app_id = a.app_id{where}
            GROUP BY e.app_id, a.name
            ORDER BY evictions DESC, attempts DESC, e.app_id ASC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))

        rows = self._execute_many_read(sql, params)
        for row in rows:
            for key in ("evictions", "attempts", "blocked", "memory_freed_mb"):
                row[key] = self._as_int(row[key])
        return rows

    def evictions_by_resource(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Returns evictions attributed to each resource, busiest first.

        ``EvictionLog`` records which app was considered, not which resource it
        held, because one eviction may touch several resources. ``ResourceLocks``
        is the only normalized link between an app and the resources it held, so
        an eviction is attributed to every resource in that app's lock history.

        This deliberately counts lock *history* rather than locks live at the
        eviction instant, because the eviction that retires those locks clears
        the evidence at the same moment. The figure is therefore "evictions of
        apps that had held this resource", not "evictions caused by this
        resource".

        Args:
            limit (Optional[int]): Caps the number of rows returned.

        Returns:
            List[Dict[str, Any]]: One row per resource with keys ``resource_id``,
                ``resource_name``, ``evictions`` and ``apps_evicted``.
        """
        sql = """
            SELECT rl.resource_id AS resource_id,
                   r.name AS resource_name,
                   COUNT(DISTINCT e.event_id) AS evictions,
                   COUNT(DISTINCT e.app_id) AS apps_evicted
            FROM EvictionLog e
            JOIN ResourceLocks rl ON rl.app_id = e.app_id
            JOIN Resources r ON r.resource_id = rl.resource_id
            WHERE e.result = 'EVICTED'
            GROUP BY rl.resource_id, r.name
            ORDER BY evictions DESC, rl.resource_id ASC
        """
        params: Tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (int(limit),)

        rows = self._execute_many_read(sql, params)
        for row in rows:
            row["evictions"] = self._as_int(row["evictions"])
            row["apps_evicted"] = self._as_int(row["apps_evicted"])
        return rows

    def conflicts_by_resource(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Returns conflict and freeze counts per resource, busiest first.

        Args:
            limit (Optional[int]): Caps the number of rows returned.

        Returns:
            List[Dict[str, Any]]: One row per resource with keys ``resource_id``,
                ``resource_name``, ``conflicts`` and ``freezes``.
        """
        sql = f"""
            SELECT c.resource_id AS resource_id,
                   r.name AS resource_name,
                   COUNT(*) AS conflicts,
                   SUM(CASE WHEN c.{FREEZE_PREDICATE} THEN 1 ELSE 0 END) AS freezes
            FROM ConflictLog c
            LEFT JOIN Resources r ON r.resource_id = c.resource_id
            GROUP BY c.resource_id, r.name
            ORDER BY conflicts DESC, c.resource_id ASC
        """
        params: Tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (int(limit),)

        rows = self._execute_many_read(sql, params)
        for row in rows:
            row["conflicts"] = self._as_int(row["conflicts"])
            row["freezes"] = self._as_int(row["freezes"])
        return rows

    # ---------------------------------------------------------
    # Time series
    # ---------------------------------------------------------

    def events_over_time(
        self,
        bucket_seconds: int = DEFAULT_BUCKET_SECONDS,
        start: Optional[float] = None,
        end: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Returns a bucketed multi-series timeline for plotting.

        Each bucket reports how many of each event kind fell inside it, so a
        Streamlit line chart can plot ``evictions``, ``conflicts``, ``freezes``,
        ``grants`` and ``releases`` as five series against ``bucket_start``
        without further aggregation.

        Args:
            bucket_seconds (int): Width of a bucket in seconds. Must be positive.
            start (Optional[float]): Inclusive lower bound on the event
                timestamp. Omitted to start at the earliest event.
            end (Optional[float]): Exclusive upper bound on the event timestamp.
                Omitted to run to the latest event.

        Returns:
            List[Dict[str, Any]]: One row per non-empty bucket, oldest first,
                with keys ``bucket_start``, ``bucket_end``, ``evictions``,
                ``blocked_evictions``, ``conflicts``, ``freezes``, ``grants``,
                ``releases`` and ``memory_events``. ``bucket_start`` is an int
                epoch second, which Matplotlib accepts directly on a time axis.

        Raises:
            ValueError: If bucket_seconds is not positive.
        """
        width = int(bucket_seconds)
        if width <= 0:
            raise ValueError(f"bucket_seconds must be positive, got {bucket_seconds!r}.")

        branches: List[str] = []
        params: List[Any] = []

        self._append_time_branch(
            branches,
            params,
            table="EvictionLog",
            timestamp_column="timestamp",
            flags={"evictions": "result = 'EVICTED'", "blocked_evictions": "result <> 'EVICTED'"},
            width=width,
            start=start,
            end=end,
        )
        self._append_time_branch(
            branches,
            params,
            table="ConflictLog",
            timestamp_column="timestamp",
            flags={"conflicts": "1 = 1", "freezes": FREEZE_PREDICATE},
            width=width,
            start=start,
            end=end,
        )
        self._append_time_branch(
            branches,
            params,
            table="LockRequests",
            timestamp_column="requested_at",
            flags={"grants": "outcome = 'GRANTED'", "releases": "outcome = 'RELEASED'"},
            width=width,
            start=start,
            end=end,
        )
        self._append_time_branch(
            branches,
            params,
            table="MemoryEvents",
            timestamp_column="timestamp",
            flags={"memory_events": "1 = 1"},
            width=width,
            start=start,
            end=end,
        )

        sql = (
            "SELECT bucket_start, bucket_start + ? AS bucket_end, "
            + ", ".join(f"SUM({flag}) AS {flag}" for flag in self.TIME_SERIES_FLAGS)
            + " FROM (\n"
            + "\nUNION ALL\n".join(branches)
            + "\n) GROUP BY bucket_start ORDER BY bucket_start ASC"
        )
        params.insert(0, width)

        rows = self._execute_many_read(sql, params)
        for row in rows:
            row["bucket_start"] = self._as_int(row["bucket_start"])
            row["bucket_end"] = self._as_int(row["bucket_end"])
            for flag in self.TIME_SERIES_FLAGS:
                row[flag] = self._as_int(row[flag])
        return rows

    TIME_SERIES_FLAGS = (
        "evictions",
        "blocked_evictions",
        "conflicts",
        "freezes",
        "grants",
        "releases",
        "memory_events",
    )

    @staticmethod
    def _append_time_branch(
        branches: List[str],
        params: List[Any],
        table: str,
        timestamp_column: str,
        flags: Dict[str, str],
        width: int,
        start: Optional[float],
        end: Optional[float],
    ) -> None:
        """Appends one bucketed UNION ALL branch and its bind parameters.

        Every branch projects the whole flag set, zero-filling the series the
        table does not contribute: ``UNION ALL`` requires all branches to have
        the same column count, and a constant 0 keeps the outer SUM() correct
        without a join back to fill the gaps.

        Filters are appended in a fixed order and their binds collected in the
        same order, so the combined parameter list always matches the SQL. The
        timestamp range is expressed as ``>=`` / ``<`` so SQLite can use the
        per-table timestamp indexes as index ranges.
        """
        selected = ", ".join(
            f"CASE WHEN {flags[name]} THEN 1 ELSE 0 END AS {name}"
            if name in flags
            else f"0 AS {name}"
            for name in AnalyticsRepository.TIME_SERIES_FLAGS
        )
        sql = (
            f"SELECT CAST({timestamp_column} / ? AS INTEGER) * ? AS bucket_start, {selected} "
            f"FROM {table} WHERE 1 = 1"
        )
        branch_params: List[Any] = [width, width]

        if start is not None:
            sql += f" AND {timestamp_column} >= ?"
            branch_params.append(float(start))
        if end is not None:
            sql += f" AND {timestamp_column} < ?"
            branch_params.append(float(end))

        branches.append(sql)
        params.extend(branch_params)

    # ---------------------------------------------------------
    # Conflict latency
    # ---------------------------------------------------------

    def average_conflict_resolution_latency(self) -> Optional[float]:
        """Returns the mean time a blocked app waited before it got the resource.

        ``ConflictLog`` stores a single timestamp per incident, so it cannot by
        itself express how long a resolution took. The measurable wait does exist
        in ``LockRequests``: the app was queued at the ``QUEUED`` request, and it
        was served when a later ``GRANTED`` request for the same app and resource
        landed. The difference between those two instants is the contention
        latency the dashboard reports.

        Each queued request is paired with the first grant that follows it, so an
        app queued twice is charged for both waits rather than one.

        Returns:
            Optional[float]: Mean latency in seconds, or None when no queued
                request was ever followed by a grant - which is itself a finding,
                since it means every waiter was still stranded. None is returned
                rather than 0.0 so a caller cannot mistake "no contention" for
                "contention resolved instantly".
        """
        sql = """
            SELECT AVG(granted_at - queued_at) AS average_latency
            FROM (
                SELECT q.requested_at AS queued_at,
                       (SELECT MIN(g.requested_at)
                          FROM LockRequests g
                         WHERE g.app_id = q.app_id
                           AND g.resource_id = q.resource_id
                           AND g.outcome = 'GRANTED'
                           AND g.requested_at >= q.requested_at) AS granted_at
                  FROM LockRequests q
                 WHERE q.outcome = 'QUEUED'
            )
            WHERE granted_at IS NOT NULL
        """
        value = self._scalar(sql)
        return None if value is None else float(value)

    def conflict_resolution_samples(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Returns the individual queue-to-grant waits behind the latency average.

        Useful for a distribution plot or for showing the worst offenders behind
        the headline figure.

        Args:
            limit (Optional[int]): Caps the number of samples returned.

        Returns:
            List[Dict[str, Any]]: One row per resolved wait with keys
                ``app_id``, ``resource_id``, ``queued_at``, ``granted_at`` and
                ``latency_seconds``, longest wait first.
        """
        sql = """
            SELECT q.app_id AS app_id,
                   q.resource_id AS resource_id,
                   q.requested_at AS queued_at,
                   granted.requested_at AS granted_at,
                   granted.requested_at - q.requested_at AS latency_seconds
            FROM LockRequests q
            JOIN (
                SELECT g.app_id, g.resource_id, g.requested_at
                FROM LockRequests g
                WHERE g.outcome = 'GRANTED'
            ) AS granted
              ON granted.app_id = q.app_id
             AND granted.resource_id = q.resource_id
             AND granted.requested_at >= q.requested_at
            WHERE q.outcome = 'QUEUED'
            ORDER BY latency_seconds DESC, q.app_id ASC
        """
        params: Tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (int(limit),)
        return self._execute_many_read(sql, params)

    # ---------------------------------------------------------
    # Policy comparison
    # ---------------------------------------------------------

    def policy_comparison(self) -> List[Dict[str, Any]]:
        """Returns per-policy statistics for the Clock versus RAAE comparison.

        The active policy is recorded in ``EvictionLog.algorithm``: the baseline
        writes ``BASELINE_CLOCK`` and the RAAE engine writes ``RAAE_CLOCK``.
        Both map onto their policy name here so a caller does not have to know
        that the engine's algorithm string is suffixed.

        Returns:
            List[Dict[str, Any]]: One row per algorithm present in the log, with
                keys ``algorithm``, ``policy``, ``evictions``, ``attempts``,
                ``blocked_evictions``, ``safe_releases``,
                ``evictions_without_safe_release`` and ``freeze_incidents``.
                Empty when no row carries algorithm information, which is how a
                dashboard learns the database holds no comparable runs yet.
        """
        summary_sql = """
            SELECT algorithm,
                   SUM(CASE WHEN result = 'EVICTED' THEN 1 ELSE 0 END) AS evictions,
                   COUNT(*) AS attempts,
                   SUM(CASE WHEN result <> 'EVICTED' THEN 1 ELSE 0 END) AS blocked_evictions,
                   SUM(safe_release) AS safe_releases,
                   SUM(CASE WHEN result = 'EVICTED' AND safe_release = 0 THEN 1 ELSE 0 END)
                       AS evictions_without_safe_release
            FROM EvictionLog
            WHERE algorithm IS NOT NULL AND TRIM(algorithm) <> ''
            GROUP BY algorithm
            ORDER BY algorithm ASC
        """
        freeze_sql = f"""
            SELECT e.algorithm AS algorithm, COUNT(*) AS freeze_incidents
            FROM ConflictLog c
            JOIN (SELECT DISTINCT app_id, algorithm FROM EvictionLog WHERE result = 'EVICTED') e
              ON e.app_id = c.blocking_app_id
            WHERE c.{FREEZE_PREDICATE}
            GROUP BY e.algorithm
        """
        freezes = {
            str(row["algorithm"]): self._as_int(row["freeze_incidents"])
            for row in self._execute_many_read(freeze_sql)
        }

        rows = self._execute_many_read(summary_sql)
        for row in rows:
            algorithm = str(row["algorithm"])
            row["policy"] = _policy_for_algorithm(algorithm)
            row["freeze_incidents"] = freezes.get(algorithm, 0)
            for key in (
                "evictions",
                "attempts",
                "blocked_evictions",
                "safe_releases",
                "evictions_without_safe_release",
            ):
                row[key] = self._as_int(row[key])
        return rows

    def policy_delta(self) -> Dict[str, Any]:
        """Returns the baseline-versus-RAAE difference in one comparable mapping.

        Builds on :meth:`policy_comparison`, so it needs both policies present in
        the log. The comparison is reported as ``baseline - raae`` for the
        counters where fewer is better for RAAE (freezes, unsafe evictions) and
        is left to the caller to interpret for the rest; the raw per-policy rows
        remain the source of truth.

        Returns:
            Dict[str, Any]: Keys ``available`` (bool), ``baseline_policy``,
                ``raae_policy``, and one entry per compared metric holding the
                absolute values plus ``difference``. ``available`` is False with
                empty metrics when either policy is missing, so a dashboard can
                hide the panel instead of rendering misleading zeros.
        """
        comparison = {str(row["policy"]): row for row in self.policy_comparison()}
        baseline = comparison.get(POLICY_BASELINE)
        raae = comparison.get(POLICY_RAAE)

        metrics = (
            "evictions",
            "attempts",
            "blocked_evictions",
            "safe_releases",
            "evictions_without_safe_release",
            "freeze_incidents",
        )

        if baseline is None or raae is None:
            return {
                "available": False,
                "baseline_policy": baseline,
                "raae_policy": raae,
                "metrics": {},
            }

        return {
            "available": True,
            "baseline_policy": baseline,
            "raae_policy": raae,
            "metrics": {
                metric: {
                    "baseline": baseline[metric],
                    "raae": raae[metric],
                    "difference": baseline[metric] - raae[metric],
                }
                for metric in metrics
            },
        }

    # ---------------------------------------------------------
    # Dashboard bundle
    # ---------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Returns every headline counter in a single flat mapping.

        One call for a metric row: each key is a Streamlit metric tile and each
        value is already a number, so no query runs while the page renders.

        Returns:
            Dict[str, Any]: Keys ``total_evictions``, ``blocked_evictions``,
                ``total_freezes``, ``total_conflicts``,
                ``successful_acquisitions``, ``resource_releases``,
                ``queued_requests`` and ``average_conflict_latency``.
        """
        return {
            "total_evictions": self.total_eviction_count(),
            "blocked_evictions": self.total_eviction_count(result=None) - self.total_eviction_count(),
            "total_freezes": self.total_freeze_incidents(),
            "total_conflicts": self.total_conflicts(),
            "successful_acquisitions": self.successful_resource_acquisitions(),
            "resource_releases": self.resource_releases(),
            "queued_requests": self.queued_resource_requests(),
            "average_conflict_latency": self.average_conflict_resolution_latency(),
        }
