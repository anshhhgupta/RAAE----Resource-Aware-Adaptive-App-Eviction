"""Conflict Manager package for RAAE simulation."""

from backend.conflict_manager.conflict_manager import (
    ConflictManager,
    ConflictDecision,
    ConflictResult,
    WOUND_HOLDER,
    WOUND,
    WAIT,
    NO_CONFLICT,
)

__all__ = [
    "ConflictManager",
    "ConflictDecision",
    "ConflictResult",
    "WOUND_HOLDER",
    "WOUND",
    "WAIT",
    "NO_CONFLICT",
]
