"""Eviction policy abstraction for RAAE simulation.

Supports:
1. BASELINE_CLOCK: Plain Clock / Second-Chance eviction without resource awareness.
   Candidates are evicted blindly without checking held resources or safe release.
2. RAAE: Resource-Aware Adaptive App Eviction.
   Candidates pass through resource checks, conflict resolution, and safe release.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.models.app import App, AppState
from backend.models.resource import Resource
from backend.memory_manager.memory_manager import MemoryManager
from backend.resource_manager.resource_manager import ResourceManager
from backend.raae_engine.raae_engine import RAAEEngine
from backend.raae_engine.conflict_manager import ResourceConflictManager
from backend.raae_engine.models import EvictionDecisionType, RAAEEngineResult
from backend.raae_engine.persistence import ConflictRecord, DatabaseEvictionPersistence
from database.db import DatabaseManager


class EvictionPolicyType(str, Enum):
    """Supported eviction policy modes."""
    BASELINE_CLOCK = "BASELINE_CLOCK"
    RAAE = "RAAE"


@dataclass(frozen=True)
class EvictionPolicyDecision:
    """Policy evaluation decision for a clock-selected candidate."""
    candidate: App
    policy_type: EvictionPolicyType
    allow_eviction: bool
    requires_safe_release: bool
    held_resources: Tuple[Resource, ...] = field(default_factory=tuple)
    conflict_detected: bool = False
    reason: str = ""
    waiting_app_ids: Tuple[str, ...] = field(default_factory=tuple)
    raae_result: Optional[RAAEEngineResult] = None


@dataclass(frozen=True)
class EvictionPolicyResult:
    """Outcome of an eviction policy execution."""
    candidate: App
    policy_type: EvictionPolicyType
    evicted: bool
    is_unsafe: bool = False
    safe_release_performed: bool = False
    released_resource_ids: Tuple[str, ...] = field(default_factory=tuple)
    memory_freed: int = 0
    reason: str = ""
    conflict_detected: bool = False
    waiting_app_ids: Tuple[str, ...] = field(default_factory=tuple)
    persisted_event_id: Optional[int] = None


class EvictionPolicy(ABC):
    """Abstract interface defining an eviction policy decision boundary."""

    @property
    @abstractmethod
    def policy_type(self) -> EvictionPolicyType:
        """Returns the policy identifier."""

    @abstractmethod
    def evaluate_candidate(
        self,
        candidate: App,
        memory_manager: MemoryManager,
        resource_manager: ResourceManager,
    ) -> EvictionPolicyDecision:
        """Evaluates whether the candidate may be evicted under this policy."""

    @abstractmethod
    def execute_eviction(
        self,
        candidate: App,
        decision: EvictionPolicyDecision,
        memory_manager: MemoryManager,
        resource_manager: ResourceManager,
        db_manager: Optional[DatabaseManager] = None,
        pressure_level: str = "ORANGE",
    ) -> EvictionPolicyResult:
        """Executes the eviction decision according to this policy."""


class BaselineClockPolicy(EvictionPolicy):
    """Baseline Plain Clock / Second-Chance eviction policy.

    The selected candidate is evicted blindly without resource-aware checking.
    If the candidate holds a shared resource, that resource is not safely released,
    leaving locks orphaned and waiting applications blocked/frozen.
    """

    @property
    def policy_type(self) -> EvictionPolicyType:
        return EvictionPolicyType.BASELINE_CLOCK

    def evaluate_candidate(
        self,
        candidate: App,
        memory_manager: MemoryManager,
        resource_manager: ResourceManager,
    ) -> EvictionPolicyDecision:
        # Baseline Plain Clock performs NO resource-aware checking
        return EvictionPolicyDecision(
            candidate=candidate,
            policy_type=EvictionPolicyType.BASELINE_CLOCK,
            allow_eviction=True,
            requires_safe_release=False,
            held_resources=(),
            conflict_detected=False,
            reason="Baseline Plain Clock: Evict candidate without resource-aware checking."
        )

    def execute_eviction(
        self,
        candidate: App,
        decision: EvictionPolicyDecision,
        memory_manager: MemoryManager,
        resource_manager: ResourceManager,
        db_manager: Optional[DatabaseManager] = None,
        pressure_level: str = "ORANGE",
    ) -> EvictionPolicyResult:
        memory_before = candidate.memory_footprint

        # Inspect if candidate actually held resources for telemetry and freeze tracking
        held_resource_ids = tuple(sorted(candidate.held_resources))
        held_resources = tuple(
            resource_manager.get_resource(rid)
            for rid in held_resource_ids
            if resource_manager.get_resource(rid) is not None
        )

        is_unsafe = len(held_resource_ids) > 0
        waiting_apps_impacted = []
        for res in held_resources:
            for waiter_id in res.waiting_queue:
                if waiter_id != candidate.app_id and waiter_id not in waiting_apps_impacted:
                    waiting_apps_impacted.append(waiter_id)

        # Baseline blindly evicts the app WITHOUT releasing locks in resource_manager
        memory_manager.evict_app(candidate.app_id)
        memory_freed = memory_before

        persisted_id = None
        if db_manager is not None:
            with db_manager.transaction():
                # Save apps state to DB
                for app in memory_manager.apps.values():
                    db_manager.save_app(app)

                # Save resources (locks remain orphaned)
                for res in resource_manager.get_all_resources():
                    db_manager.save_resource(res)

                # Eviction log without lock check and without safe release
                persisted_id = db_manager.eviction_log.log_eviction(
                    app_id=candidate.app_id,
                    algorithm="BASELINE_CLOCK",
                    reason="Baseline Clock eviction without resource-aware check",
                    lock_checked=False,
                    safe_release=False,
                    result="EVICTED"
                )

                # Memory event log
                db_manager.memory_events.log_event(
                    app_id=candidate.app_id,
                    action="EVICT",
                    memory_before=memory_before,
                    memory_after=0,
                    pressure_level=pressure_level
                )

                # If candidate held resources that other apps were waiting for:
                # Log freeze incident to ConflictLog
                if is_unsafe and waiting_apps_impacted:
                    for waiter_id in waiting_apps_impacted:
                        for res in held_resources:
                            if waiter_id in res.waiting_queue:
                                db_manager.conflict_log.log_conflict(
                                    waiting_app_id=waiter_id,
                                    blocking_app_id=candidate.app_id,
                                    resource_id=res.resource_id,
                                    resolution_strategy="NONE",
                                    resolved_by=None,
                                    details=(
                                        f"FREEZE: App {candidate.name} ({candidate.app_id}) was blindly "
                                        f"killed while holding {res.resource_id}. Waiting app {waiter_id} "
                                        "is permanently frozen."
                                    )
                                )

        reason = "Blind eviction by BASELINE_CLOCK"
        if is_unsafe:
            reason += f" (UNSAFE: held resources {held_resource_ids} were not released)"

        return EvictionPolicyResult(
            candidate=candidate,
            policy_type=EvictionPolicyType.BASELINE_CLOCK,
            evicted=True,
            is_unsafe=is_unsafe,
            safe_release_performed=False,
            released_resource_ids=(),
            memory_freed=memory_freed,
            reason=reason,
            conflict_detected=len(waiting_apps_impacted) > 0,
            waiting_app_ids=tuple(waiting_apps_impacted),
            persisted_event_id=persisted_id
        )


class RAAEEvictionPolicy(EvictionPolicy):
    """Resource-Aware Adaptive App Eviction policy.

    The selected candidate passes through resource checks and conflict resolution.
    If the candidate holds a resource with waiting applications, eviction is prevented
    to avoid freezing the waiting apps. If held resources have no waiting applications,
    they are safely released before eviction.
    """

    def __init__(
        self,
        raae_engine: Optional[RAAEEngine] = None,
        conflict_manager: Optional[ResourceConflictManager] = None
    ) -> None:
        self._raae_engine = raae_engine
        self._conflict_manager = conflict_manager

    @property
    def policy_type(self) -> EvictionPolicyType:
        return EvictionPolicyType.RAAE

    def _get_engine(
        self,
        memory_manager: MemoryManager,
        resource_manager: ResourceManager,
        db_manager: Optional[DatabaseManager] = None
    ) -> RAAEEngine:
        if self._raae_engine is not None:
            return self._raae_engine

        cm = self._conflict_manager or ResourceConflictManager(resource_manager)
        persistence = DatabaseEvictionPersistence(db_manager) if db_manager else None
        return RAAEEngine(
            conflict_manager=cm,
            memory_manager=memory_manager,
            resource_manager=resource_manager,
            persistence=persistence
        )

    def evaluate_candidate(
        self,
        candidate: App,
        memory_manager: MemoryManager,
        resource_manager: ResourceManager,
    ) -> EvictionPolicyDecision:
        engine = self._get_engine(memory_manager, resource_manager)
        raae_result = engine.evaluate_eviction_candidate(candidate)

        waiting_app_ids = tuple(raae_result.conflict_decision.waiting_app_ids)
        conflict_detected = raae_result.decision_type in (
            EvictionDecisionType.CONFLICT,
            EvictionDecisionType.WAIT
        )

        allow_eviction = raae_result.decision_type in (
            EvictionDecisionType.ALLOW_EVICTION,
            EvictionDecisionType.RELEASE_THEN_EVICT
        )
        requires_safe_release = raae_result.decision_type == EvictionDecisionType.RELEASE_THEN_EVICT

        return EvictionPolicyDecision(
            candidate=candidate,
            policy_type=EvictionPolicyType.RAAE,
            allow_eviction=allow_eviction,
            requires_safe_release=requires_safe_release,
            held_resources=tuple(raae_result.held_resources),
            conflict_detected=conflict_detected,
            reason=raae_result.eviction_decision.reason,
            waiting_app_ids=waiting_app_ids,
            raae_result=raae_result
        )

    def execute_eviction(
        self,
        candidate: App,
        decision: EvictionPolicyDecision,
        memory_manager: MemoryManager,
        resource_manager: ResourceManager,
        db_manager: Optional[DatabaseManager] = None,
        pressure_level: str = "ORANGE",
    ) -> EvictionPolicyResult:
        engine = self._get_engine(memory_manager, resource_manager, db_manager)

        # Build conflict records before persisting so the conflict log is written
        # inside the same transaction as the eviction itself.
        conflict_records = []
        if db_manager is not None and decision.conflict_detected:
            for res in decision.held_resources:
                for waiter_id in decision.waiting_app_ids:
                    if waiter_id in res.waiting_queue:
                        conflict_records.append(
                            ConflictRecord(
                                waiting_app_id=waiter_id,
                                blocking_app_id=candidate.app_id,
                                resource_id=res.resource_id,
                                resolution_strategy="RAAE_PROTECT",
                                resolved_by="RAAE_ENGINE",
                                details=(
                                    f"PROTECTED: Eviction of {candidate.name} ({candidate.app_id}) was "
                                    f"prevented to avoid freezing waiting app {waiter_id}."
                                )
                            )
                        )

        persistence = (
            DatabaseEvictionPersistence(db_manager, conflict_records=conflict_records)
            if db_manager
            else None
        )

        # Execute safe eviction workflow through RAAEEngine
        raae_result = engine.execute_safe_eviction(
            candidate=candidate,
            memory_manager=memory_manager,
            resource_manager=resource_manager,
            persistence=persistence
        )

        return EvictionPolicyResult(
            candidate=candidate,
            policy_type=EvictionPolicyType.RAAE,
            evicted=raae_result.eviction_performed,
            is_unsafe=False,  # RAAE NEVER evicts unsafely!
            safe_release_performed=len(raae_result.released_resource_ids) > 0,
            released_resource_ids=tuple(raae_result.released_resource_ids),
            memory_freed=raae_result.memory_released,
            reason=raae_result.eviction_decision.reason,
            conflict_detected=decision.conflict_detected,
            waiting_app_ids=decision.waiting_app_ids,
            persisted_event_id=raae_result.persisted_event_id
        )
