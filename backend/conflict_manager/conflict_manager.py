"""Conflict Manager module for RAAE simulation.

Implements the Wound-Wait conflict resolution algorithm between an
eviction candidate (or requesting app) and the current resource holder.
"""

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Optional, Dict, Any, Union, Tuple
from backend.models.app import App
from backend.models.resource import Resource, ResourceStatus


class ConflictDecision(str, Enum):
    """Possible conflict decisions produced by the Conflict Manager."""
    WOUND_HOLDER = "WOUND_HOLDER"
    WAIT = "WAIT"
    NO_CONFLICT = "NO_CONFLICT"


# Convenience constants
WOUND_HOLDER = ConflictDecision.WOUND_HOLDER
WAIT = ConflictDecision.WAIT
NO_CONFLICT = ConflictDecision.NO_CONFLICT


@dataclass
class ConflictResult:
    """Structured conflict decision returned by ConflictManager.
    
    Attributes:
        decision (ConflictDecision): The resolution outcome (WOUND_HOLDER, WAIT, NO_CONFLICT).
        candidate_id (Optional[str]): Identifier of the eviction candidate/requesting app.
        holder_id (Optional[str]): Identifier of the current resource holder.
        resource_id (Optional[str]): Identifier of the requested resource.
        candidate_priority (Optional[int]): Priority value of the candidate.
        candidate_timestamp (Optional[float]): Timestamp value of the candidate.
        holder_priority (Optional[int]): Priority value of the holder.
        holder_timestamp (Optional[float]): Timestamp value of the holder.
        reason (str): Human-readable explanation of why this decision was reached.
        timestamp (float): UNIX timestamp when the decision was evaluated.
        strategy (str): The algorithm name applied (default: 'WOUND_WAIT').
    """
    decision: ConflictDecision
    candidate_id: Optional[str] = None
    holder_id: Optional[str] = None
    resource_id: Optional[str] = None
    candidate_priority: Optional[int] = None
    candidate_timestamp: Optional[float] = None
    holder_priority: Optional[int] = None
    holder_timestamp: Optional[float] = None
    reason: str = ""
    timestamp: float = field(default_factory=time.time)
    strategy: str = "WOUND_WAIT"

    @property
    def is_wound(self) -> bool:
        """Returns True if the decision is WOUND_HOLDER."""
        return self.decision == ConflictDecision.WOUND_HOLDER

    @property
    def is_wait(self) -> bool:
        """Returns True if the decision is WAIT."""
        return self.decision == ConflictDecision.WAIT

    @property
    def is_no_conflict(self) -> bool:
        """Returns True if the decision is NO_CONFLICT."""
        return self.decision == ConflictDecision.NO_CONFLICT

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the decision into a dictionary suitable for logging or JSON APIs."""
        return {
            "decision": self.decision.value,
            "candidate_id": self.candidate_id,
            "holder_id": self.holder_id,
            "resource_id": self.resource_id,
            "candidate_priority": self.candidate_priority,
            "candidate_timestamp": self.candidate_timestamp,
            "holder_priority": self.holder_priority,
            "holder_timestamp": self.holder_timestamp,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "strategy": self.strategy
        }

    def __eq__(self, other: Any) -> bool:
        """Permits direct comparison with ConflictDecision enum or strings like 'WOUND_HOLDER'."""
        if isinstance(other, ConflictDecision):
            return self.decision == other
        if isinstance(other, str):
            return self.decision.value == other
        if isinstance(other, ConflictResult):
            return self.decision == other.decision and self.candidate_id == other.candidate_id and self.holder_id == other.holder_id and self.resource_id == other.resource_id
        return False

    def __getitem__(self, key: str) -> Any:
        """Allows dictionary-style key access (e.g. result['decision'])."""
        data = self.to_dict()
        if key in data:
            return data[key]
        raise KeyError(key)

    def __repr__(self) -> str:
        return (
            f"<ConflictResult decision={self.decision.value!r} candidate={self.candidate_id!r} "
            f"holder={self.holder_id!r} resource={self.resource_id!r} reason={self.reason!r}>"
        )


class ConflictManager:
    """Manages resource contention analysis and applies Wound-Wait resolution.
    
    Wound-Wait Rules:
    - If Candidate has higher precedence (higher priority, or older timestamp) than Holder:
        Decision: WOUND_HOLDER (the holder is preempted/wounded to release resource).
    - If Candidate has lower precedence (lower priority, or younger timestamp) than Holder:
        Decision: WAIT (the candidate must wait for the holder).
    - If no contention exists (resource free, no holder, candidate already holds, etc.):
        Decision: NO_CONFLICT.
    
    Attributes:
        db (Optional[Any]): Optional DatabaseManager instance for automatic conflict logging.
        default_strategy (str): Name of the algorithm strategy (default 'WOUND_WAIT').
        default_mode (str): Resolution mode: 'hybrid' (priority with timestamp tie-break),
                            'priority' (priority only), or 'timestamp' (timestamp only).
    """

    WOUND_HOLDER = ConflictDecision.WOUND_HOLDER
    WAIT = ConflictDecision.WAIT
    NO_CONFLICT = ConflictDecision.NO_CONFLICT

    def __init__(
        self,
        db: Optional[Any] = None,
        default_strategy: str = "WOUND_WAIT",
        default_mode: str = "hybrid"
    ) -> None:
        self.db: Optional[Any] = db
        self.default_strategy: str = default_strategy
        self.default_mode: str = default_mode

    def resolve_conflict(
        self,
        *args: Any,
        candidate: Optional[Union[App, str, Dict[str, Any]]] = None,
        candidate_priority: Optional[int] = None,
        candidate_timestamp: Optional[float] = None,
        holder: Optional[Union[App, str, Dict[str, Any]]] = None,
        holder_priority: Optional[int] = None,
        holder_timestamp: Optional[float] = None,
        requested_resource: Optional[Union[Resource, str, Dict[str, Any]]] = None,
        **kwargs: Any
    ) -> ConflictResult:
        """Evaluates resource contention and computes a structured conflict decision.
        
        Supports flexible calling conventions:
        1. 5 positional arguments:
           resolve_conflict(candidate, cand_priority_or_timestamp, holder, holder_priority_or_timestamp, requested_resource)
        2. 3 positional arguments:
           resolve_conflict(candidate, holder, requested_resource)
        3. Keyword arguments:
           resolve_conflict(
               candidate=..., candidate_priority=..., candidate_timestamp=...,
               holder=..., holder_priority=..., holder_timestamp=...,
               requested_resource=...
           )
        
        Args:
            *args: Positional arguments (3 or 5 arguments).
            candidate: Eviction candidate / requesting app (App instance, str ID, or dict).
            candidate_priority: Priority of candidate (higher number = higher priority).
            candidate_timestamp: Creation / access timestamp of candidate.
            holder: Current resource holder (App instance, str ID, or dict).
            holder_priority: Priority of holder.
            holder_timestamp: Creation / access timestamp of holder.
            requested_resource: The requested resource (Resource instance, str ID, or dict).
            **kwargs: Extra parameters (aliases like 'eviction_candidate', 'resource', 'mode').
            
        Returns:
            ConflictResult: Structured decision with decision enum, IDs, and reason.
        """
        # Parse positional arguments if provided
        cand_arg, cand_metric, holder_arg, holder_metric, res_arg = self._parse_positional_args(args)

        # Merge candidate
        cand_input = candidate if candidate is not None else cand_arg
        if cand_input is None:
            cand_input = kwargs.get("eviction_candidate")

        # Merge holder
        holder_input = holder if holder is not None else holder_arg
        if holder_input is None:
            holder_input = kwargs.get("current_resource_holder", kwargs.get("current_holder", kwargs.get("resource_holder")))

        # Merge requested resource
        res_input = requested_resource if requested_resource is not None else res_arg
        if res_input is None:
            res_input = kwargs.get("resource")

        # Parse metrics from candidate_input and cand_metric
        c_id, c_p, c_ts = self._extract_app_metadata(cand_input, cand_metric, candidate_priority, candidate_timestamp)

        # Parse metrics from holder_input and holder_metric
        h_id, h_p, h_ts = self._extract_app_metadata(holder_input, holder_metric, holder_priority, holder_timestamp)

        # Parse resource ID
        r_id = self._extract_resource_id(res_input)

        # 1. Check for NO_CONFLICT conditions
        no_conflict_result = self._check_no_conflict(cand_input, c_id, holder_input, h_id, res_input, r_id, c_p, c_ts, h_p, h_ts)
        if no_conflict_result is not None:
            return no_conflict_result

        # 2. Evaluate active contention using Wound-Wait
        mode = kwargs.get("mode", self.default_mode)
        decision, reason = self._apply_wound_wait(c_id, c_p, c_ts, h_id, h_p, h_ts, r_id, mode)

        result = ConflictResult(
            decision=decision,
            candidate_id=c_id,
            holder_id=h_id,
            resource_id=r_id,
            candidate_priority=c_p,
            candidate_timestamp=c_ts,
            holder_priority=h_p,
            holder_timestamp=h_ts,
            reason=reason,
            timestamp=time.time(),
            strategy=self.default_strategy
        )

        # 3. Optional persistence logging to SQLite if DatabaseManager is provided
        self._log_to_db(result)

        return result

    # Aliases
    evaluate_conflict = resolve_conflict
    check_conflict = resolve_conflict

    def __call__(self, *args: Any, **kwargs: Any) -> ConflictResult:
        """Allows instance to be called directly like a function."""
        return self.resolve_conflict(*args, **kwargs)

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    def _parse_positional_args(
        self,
        args: Tuple[Any, ...]
    ) -> Tuple[Optional[Any], Optional[Any], Optional[Any], Optional[Any], Optional[Any]]:
        """Normalizes positional arguments."""
        if not args:
            return None, None, None, None, None
        
        if len(args) == 5:
            return args[0], args[1], args[2], args[3], args[4]
        
        if len(args) == 3:
            # Format: (candidate, holder, requested_resource)
            return args[0], None, args[1], None, args[2]

        if len(args) == 4:
            # Format: (candidate, cand_metric, holder, requested_resource)
            return args[0], args[1], args[2], None, args[3]

        # Fallback for variable lengths
        cand = args[0] if len(args) > 0 else None
        cand_m = args[1] if len(args) > 1 else None
        holder = args[2] if len(args) > 2 else None
        holder_m = args[3] if len(args) > 3 else None
        res = args[4] if len(args) > 4 else None
        return cand, cand_m, holder, holder_m, res

    def _extract_app_metadata(
        self,
        app_obj: Any,
        metric: Any,
        explicit_priority: Optional[int],
        explicit_timestamp: Optional[float]
    ) -> Tuple[Optional[str], Optional[int], Optional[float]]:
        """Extracts app_id, priority, and timestamp from inputs."""
        app_id: Optional[str] = None
        priority: Optional[int] = explicit_priority
        timestamp: Optional[float] = explicit_timestamp

        # 1. From app_obj
        if isinstance(app_obj, App):
            app_id = app_obj.app_id
            if priority is None:
                priority = app_obj.priority
            if timestamp is None:
                timestamp = app_obj.last_access_time
        elif isinstance(app_obj, dict):
            app_id = str(app_obj.get("app_id", app_obj.get("id", ""))) or None
            if priority is None and "priority" in app_obj:
                priority = int(app_obj["priority"])
            if timestamp is None:
                ts = app_obj.get("timestamp", app_obj.get("last_access_time"))
                if ts is not None:
                    timestamp = float(ts)
        elif app_obj is not None:
            app_id = str(app_obj)

        # 2. From metric argument if provided and not yet explicitly set
        if metric is not None:
            if isinstance(metric, (tuple, list)) and len(metric) >= 2:
                if priority is None and metric[0] is not None:
                    priority = int(metric[0])
                if timestamp is None and metric[1] is not None:
                    timestamp = float(metric[1])
            elif isinstance(metric, dict):
                if priority is None and "priority" in metric:
                    priority = int(metric["priority"])
                if timestamp is None:
                    ts = metric.get("timestamp", metric.get("last_access_time"))
                    if ts is not None:
                        timestamp = float(ts)
            elif isinstance(metric, float):
                if timestamp is None:
                    timestamp = float(metric)
            elif isinstance(metric, int):
                # Distinguish UNIX timestamps from priorities
                if metric > 100000:
                    if timestamp is None:
                        timestamp = float(metric)
                else:
                    if priority is None:
                        priority = int(metric)

        return app_id, priority, timestamp

    def _extract_resource_id(self, res_obj: Any) -> Optional[str]:
        """Extracts resource ID string from Resource object, dict, or string."""
        if isinstance(res_obj, Resource):
            return res_obj.resource_id
        if isinstance(res_obj, dict):
            return str(res_obj.get("resource_id", res_obj.get("name", ""))) or None
        if res_obj is not None:
            return str(res_obj)
        return None

    def _check_no_conflict(
        self,
        cand_obj: Any,
        cand_id: Optional[str],
        holder_obj: Any,
        holder_id: Optional[str],
        res_obj: Any,
        res_id: Optional[str],
        c_p: Optional[int],
        c_ts: Optional[float],
        h_p: Optional[int],
        h_ts: Optional[float]
    ) -> Optional[ConflictResult]:
        """Checks whether NO_CONFLICT applies."""
        # 1. No requested resource
        if not res_id:
            return ConflictResult(
                decision=ConflictDecision.NO_CONFLICT,
                candidate_id=cand_id,
                holder_id=holder_id,
                resource_id=res_id,
                candidate_priority=c_p,
                candidate_timestamp=c_ts,
                holder_priority=h_p,
                holder_timestamp=h_ts,
                reason="No requested resource specified; no conflict."
            )

        # 2. No candidate
        if not cand_id:
            return ConflictResult(
                decision=ConflictDecision.NO_CONFLICT,
                candidate_id=None,
                holder_id=holder_id,
                resource_id=res_id,
                candidate_priority=c_p,
                candidate_timestamp=c_ts,
                holder_priority=h_p,
                holder_timestamp=h_ts,
                reason="No candidate specified; no conflict."
            )

        # 3. No current holder (resource is free)
        if not holder_id:
            return ConflictResult(
                decision=ConflictDecision.NO_CONFLICT,
                candidate_id=cand_id,
                holder_id=None,
                resource_id=res_id,
                candidate_priority=c_p,
                candidate_timestamp=c_ts,
                holder_priority=h_p,
                holder_timestamp=h_ts,
                reason=f"Resource '{res_id}' has no current holder; resource is free."
            )

        # 4. Candidate is already the holder
        if cand_id == holder_id:
            return ConflictResult(
                decision=ConflictDecision.NO_CONFLICT,
                candidate_id=cand_id,
                holder_id=holder_id,
                resource_id=res_id,
                candidate_priority=c_p,
                candidate_timestamp=c_ts,
                holder_priority=h_p,
                holder_timestamp=h_ts,
                reason=f"Candidate '{cand_id}' already holds resource '{res_id}'."
            )

        # 5. Candidate is evicted
        if isinstance(cand_obj, App) and cand_obj.is_evicted:
            return ConflictResult(
                decision=ConflictDecision.NO_CONFLICT,
                candidate_id=cand_id,
                holder_id=holder_id,
                resource_id=res_id,
                candidate_priority=c_p,
                candidate_timestamp=c_ts,
                holder_priority=h_p,
                holder_timestamp=h_ts,
                reason=f"Candidate '{cand_id}' is evicted and cannot compete for resources."
            )

        # 6. Holder does not actually hold the requested resource
        if isinstance(holder_obj, App) and res_id not in holder_obj.held_resources:
            return ConflictResult(
                decision=ConflictDecision.NO_CONFLICT,
                candidate_id=cand_id,
                holder_id=holder_id,
                resource_id=res_id,
                candidate_priority=c_p,
                candidate_timestamp=c_ts,
                holder_priority=h_p,
                holder_timestamp=h_ts,
                reason=f"Holder '{holder_id}' does not hold resource '{res_id}'."
            )

        # 7. Resource object has available capacity and holder is not locking all units
        if isinstance(res_obj, Resource):
            if not res_obj.is_locked:
                return ConflictResult(
                    decision=ConflictDecision.NO_CONFLICT,
                    candidate_id=cand_id,
                    holder_id=holder_id,
                    resource_id=res_id,
                    candidate_priority=c_p,
                    candidate_timestamp=c_ts,
                    holder_priority=h_p,
                    holder_timestamp=h_ts,
                    reason=f"Resource '{res_id}' is completely free."
                )
            if res_obj.available_units > 0 and holder_id not in res_obj.holders:
                return ConflictResult(
                    decision=ConflictDecision.NO_CONFLICT,
                    candidate_id=cand_id,
                    holder_id=holder_id,
                    resource_id=res_id,
                    candidate_priority=c_p,
                    candidate_timestamp=c_ts,
                    holder_priority=h_p,
                    holder_timestamp=h_ts,
                    reason=f"Resource '{res_id}' has available units; holder does not block candidate."
                )

        return None

    def _apply_wound_wait(
        self,
        c_id: Optional[str],
        c_p: Optional[int],
        c_ts: Optional[float],
        h_id: Optional[str],
        h_p: Optional[int],
        h_ts: Optional[float],
        r_id: Optional[str],
        mode: str
    ) -> Tuple[ConflictDecision, str]:
        """Executes the Wound-Wait comparison logic."""
        mode_lower = mode.lower()

        # Pure timestamp mode
        if mode_lower == "timestamp":
            return self._compare_timestamps(c_id, c_ts, h_id, h_ts, r_id)

        # Pure priority mode
        if mode_lower == "priority":
            return self._compare_priorities(c_id, c_p, h_id, h_p, r_id)

        # Hybrid mode (default): Priority first, timestamp tie-breaker
        if c_p is not None and h_p is not None and c_p != h_p:
            if c_p > h_p:
                return (
                    ConflictDecision.WOUND_HOLDER,
                    f"Candidate '{c_id}' (priority={c_p}) > Holder '{h_id}' (priority={h_p}). Holder wounded for resource '{r_id}'."
                )
            else:
                return (
                    ConflictDecision.WAIT,
                    f"Candidate '{c_id}' (priority={c_p}) < Holder '{h_id}' (priority={h_p}). Candidate must wait for resource '{r_id}'."
                )

        # If priorities are equal or missing, evaluate timestamps (older wounds younger)
        if c_ts is not None and h_ts is not None and c_ts != h_ts:
            if c_ts < h_ts:
                return (
                    ConflictDecision.WOUND_HOLDER,
                    f"Candidate '{c_id}' (timestamp={c_ts:.4f}) is older than Holder '{h_id}' (timestamp={h_ts:.4f}). Holder wounded for resource '{r_id}'."
                )
            else:
                return (
                    ConflictDecision.WAIT,
                    f"Candidate '{c_id}' (timestamp={c_ts:.4f}) is younger than Holder '{h_id}' (timestamp={h_ts:.4f}). Candidate must wait for resource '{r_id}'."
                )

        # If neither priority nor timestamp differ, use deterministic tie-breaking
        if c_id and h_id and c_id < h_id:
            return (
                ConflictDecision.WOUND_HOLDER,
                f"Candidate '{c_id}' tied with Holder '{h_id}'; tie-breaker wounded holder."
            )

        return (
            ConflictDecision.WAIT,
            f"Candidate '{c_id}' tied with Holder '{h_id}'; candidate waits."
        )

    def _compare_priorities(
        self,
        c_id: Optional[str],
        c_p: Optional[int],
        h_id: Optional[str],
        h_p: Optional[int],
        r_id: Optional[str]
    ) -> Tuple[ConflictDecision, str]:
        """Helper to compare priority values."""
        cand_p = c_p if c_p is not None else 0
        hold_p = h_p if h_p is not None else 0

        if cand_p > hold_p:
            return (
                ConflictDecision.WOUND_HOLDER,
                f"Priority mode: Candidate '{c_id}' (priority={cand_p}) > Holder '{h_id}' (priority={hold_p}). Holder wounded."
            )
        elif cand_p < hold_p:
            return (
                ConflictDecision.WAIT,
                f"Priority mode: Candidate '{c_id}' (priority={cand_p}) < Holder '{h_id}' (priority={hold_p}). Candidate must wait."
            )
        else:
            return (
                ConflictDecision.WAIT,
                f"Priority mode: Candidate '{c_id}' and Holder '{h_id}' have equal priority ({cand_p}). Candidate waits."
            )

    def _compare_timestamps(
        self,
        c_id: Optional[str],
        c_ts: Optional[float],
        h_id: Optional[str],
        h_ts: Optional[float],
        r_id: Optional[str]
    ) -> Tuple[ConflictDecision, str]:
        """Helper to compare timestamp values (older wounds younger)."""
        cand_ts = c_ts if c_ts is not None else 0.0
        hold_ts = h_ts if h_ts is not None else 0.0

        if cand_ts < hold_ts:
            return (
                ConflictDecision.WOUND_HOLDER,
                f"Timestamp mode: Candidate '{c_id}' ({cand_ts:.4f}) is older than Holder '{h_id}' ({hold_ts:.4f}). Holder wounded."
            )
        elif cand_ts > hold_ts:
            return (
                ConflictDecision.WAIT,
                f"Timestamp mode: Candidate '{c_id}' ({cand_ts:.4f}) is younger than Holder '{h_id}' ({hold_ts:.4f}). Candidate must wait."
            )
        else:
            return (
                ConflictDecision.WAIT,
                f"Timestamp mode: Candidate '{c_id}' and Holder '{h_id}' have identical timestamps. Candidate waits."
            )

    def _log_to_db(self, result: ConflictResult) -> None:
        """Best-effort logging of conflict to SQLite database if db is attached."""
        if not self.db or not hasattr(self.db, "conflict_log"):
            return

        try:
            resolved_by = "WOUND" if result.decision == ConflictDecision.WOUND_HOLDER else "WAIT"
            self.db.conflict_log.log_conflict(
                waiting_app_id=result.candidate_id or "UNKNOWN",
                blocking_app_id=result.holder_id or "UNKNOWN",
                resource_id=result.resource_id or "UNKNOWN",
                resolution_strategy=result.strategy,
                resolved_by=resolved_by,
                details=result.reason,
                timestamp=result.timestamp
            )
        except Exception:
            # Logging failure should not disrupt core conflict resolution
            pass
