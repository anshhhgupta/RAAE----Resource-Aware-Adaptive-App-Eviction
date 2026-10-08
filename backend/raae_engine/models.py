"""Decision models for the Resource-Aware Adaptive App Eviction engine."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence, Tuple

from backend.models.app import App
from backend.models.resource import Resource


class EvictionDecisionType(str, Enum):
    """Final eviction decisions returned by the RAAE Engine."""

    ALLOW_EVICTION = "ALLOW_EVICTION"
    WAIT = "WAIT"
    CONFLICT = "CONFLICT"
    RELEASE_THEN_EVICT = "RELEASE_THEN_EVICT"


class ConflictStatus(str, Enum):
    """Conflict statuses returned by the Conflict Manager boundary."""

    NO_CONFLICT = "NO_CONFLICT"
    WAIT = "WAIT"
    RESOLVE_REQUIRED = "RESOLVE_REQUIRED"
    BLOCKED = "BLOCKED"
    NOT_SAFE = "NOT_SAFE"


@dataclass(frozen=True)
class ConflictDecision:
    """Conflict Manager response for a candidate eviction."""

    status: ConflictStatus
    reason: str
    resource_ids: Tuple[str, ...] = field(default_factory=tuple)
    waiting_app_ids: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def no_conflict(
        cls,
        reason: str = "No eviction conflict found.",
        resource_ids: Sequence[str] = (),
        waiting_app_ids: Sequence[str] = ()
    ) -> "ConflictDecision":
        return cls(ConflictStatus.NO_CONFLICT, reason, tuple(resource_ids), tuple(waiting_app_ids))

    @classmethod
    def wait(
        cls,
        reason: str,
        resource_ids: Sequence[str] = (),
        waiting_app_ids: Sequence[str] = ()
    ) -> "ConflictDecision":
        return cls(ConflictStatus.WAIT, reason, tuple(resource_ids), tuple(waiting_app_ids))

    @classmethod
    def resolve_required(
        cls,
        reason: str,
        resource_ids: Sequence[str] = (),
        waiting_app_ids: Sequence[str] = ()
    ) -> "ConflictDecision":
        return cls(ConflictStatus.RESOLVE_REQUIRED, reason, tuple(resource_ids), tuple(waiting_app_ids))

    @classmethod
    def blocked(
        cls,
        reason: str,
        resource_ids: Sequence[str] = (),
        waiting_app_ids: Sequence[str] = ()
    ) -> "ConflictDecision":
        return cls(ConflictStatus.BLOCKED, reason, tuple(resource_ids), tuple(waiting_app_ids))

    @classmethod
    def not_safe(
        cls,
        reason: str,
        resource_ids: Sequence[str] = (),
        waiting_app_ids: Sequence[str] = ()
    ) -> "ConflictDecision":
        return cls(ConflictStatus.NOT_SAFE, reason, tuple(resource_ids), tuple(waiting_app_ids))


@dataclass(frozen=True)
class EvictionDecision:
    """Final RAAE eviction decision."""

    decision_type: EvictionDecisionType
    reason: str

    @property
    def can_evict(self) -> bool:
        return self.decision_type == EvictionDecisionType.ALLOW_EVICTION

    @property
    def requires_resource_release(self) -> bool:
        return self.decision_type == EvictionDecisionType.RELEASE_THEN_EVICT


@dataclass(frozen=True)
class EvictionEvent:
    """Eviction workflow event that can be persisted by the persistence layer."""

    app_id: str
    algorithm: str
    reason: str
    decision_type: EvictionDecisionType
    memory_before: int
    memory_after: int
    released_resource_ids: Tuple[str, ...] = field(default_factory=tuple)
    lock_checked: bool = True
    safe_release: bool = False
    result: str = "PENDING"


@dataclass(frozen=True)
class RAAEEngineResult:
    """Complete result returned by the RAAE Engine."""

    candidate: App
    held_resources: Tuple[Resource, ...]
    conflict_decision: ConflictDecision
    eviction_decision: EvictionDecision
    released_resource_ids: Tuple[str, ...] = field(default_factory=tuple)
    memory_released: int = 0
    eviction_event: Optional[EvictionEvent] = None
    persisted_event_id: Optional[int] = None

    @property
    def decision_type(self) -> EvictionDecisionType:
        return self.eviction_decision.decision_type

    @property
    def can_evict(self) -> bool:
        return self.eviction_decision.can_evict

    @property
    def eviction_performed(self) -> bool:
        return self.eviction_event is not None and self.eviction_event.result == "EVICTED"
