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
from .controller import (
    AppSpec,
    SimulationController,
    SimulationResult,
    Workload,
    run_simulation,
)

__all__ = [
    "AppSpec",
    "BaselineClockPolicy",
    "EvictionPolicy",
    "EvictionPolicyDecision",
    "EvictionPolicyResult",
    "EvictionPolicyType",
    "RAAEEvictionPolicy",
    "SimulationConfig",
    "SimulationController",
    "SimulationEngine",
    "SimulationMetrics",
    "SimulationResult",
    "SimulationTickResult",
    "Workload",
    "run_deterministic_comparison",
    "run_simulation",
    "setup_deterministic_simulation",
]
