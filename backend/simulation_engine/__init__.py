"""Simulation engine package for RAAE (Resource-Aware Adaptive App Eviction)."""

from .policies import (
    BaselineClockPolicy,
    EvictionPolicy,
    EvictionPolicyDecision,
    EvictionPolicyResult,
    EvictionPolicyType,
    RAAEEvictionPolicy,
)
from .simulation_engine import (
    SimulationConfig,
    SimulationEngine,
    SimulationMetrics,
    SimulationTickResult,
)
from .runner import (
    run_deterministic_comparison,
    setup_deterministic_simulation,
)

__all__ = [
    "BaselineClockPolicy",
    "EvictionPolicy",
    "EvictionPolicyDecision",
    "EvictionPolicyResult",
    "EvictionPolicyType",
    "RAAEEvictionPolicy",
    "SimulationConfig",
    "SimulationEngine",
    "SimulationMetrics",
    "SimulationTickResult",
    "run_deterministic_comparison",
    "setup_deterministic_simulation",
]
