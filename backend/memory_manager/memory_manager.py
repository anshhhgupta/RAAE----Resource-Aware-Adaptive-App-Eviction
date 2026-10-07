"""Memory Manager module for RAAE simulation."""

from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
from backend.models.app import App, AppState, AppStatus


class MemoryPressureLevel(str, Enum):
    """Memory pressure levels defined in RAAE system."""
    GREEN = "GREEN"    # < 60% memory usage
    YELLOW = "YELLOW"  # 60% - 75% memory usage
    ORANGE = "ORANGE"  # 75% - 90% memory usage
    RED = "RED"        # > 90% memory usage


class MemoryManager:
    """Manages system memory allocation, pressure levels, and baseline Clock algorithm.
    
    Attributes:
        total_memory (int): Total system RAM limit in MB.
        apps (Dict[str, App]): Map of app_id to registered App objects.
        clock_hand (int): Index pointer for the Clock/Second-Chance eviction algorithm.
    """

    def __init__(self, total_memory: int = 1024) -> None:
        self.total_memory: int = max(1, int(total_memory))
        self.apps: Dict[str, App] = {}
        self.clock_hand: int = 0

    def register_app(self, app: App) -> None:
        """Registers an application with the Memory Manager."""
        self.apps[app.app_id] = app

    def unregister_app(self, app_id: str) -> Optional[App]:
        """Unregisters an application."""
        return self.apps.pop(str(app_id), None)

    def get_used_memory(self) -> int:
        """Calculates total memory used across all ACTIVE (non-evicted) applications."""
        return sum(app.memory_footprint for app in self.apps.values() if app.is_active)

    def get_free_memory(self) -> int:
        """Calculates free remaining memory in the system."""
        return max(0, self.total_memory - self.get_used_memory())

    def get_usage_percentage(self) -> float:
        """Returns memory usage as a percentage (0.0 - 100.0)."""
        if self.total_memory == 0:
            return 100.0
        return (self.get_used_memory() / self.total_memory) * 100.0

    def get_pressure_level(self) -> MemoryPressureLevel:
        """Calculates current memory pressure level based on system memory usage."""
        pct = self.get_usage_percentage()
        if pct < 60.0:
            return MemoryPressureLevel.GREEN
        elif pct < 75.0:
            return MemoryPressureLevel.YELLOW
        elif pct < 90.0:
            return MemoryPressureLevel.ORANGE
        else:
            return MemoryPressureLevel.RED

    def is_under_pressure(self) -> bool:
        """Returns True if memory pressure is ORANGE or RED."""
        return self.get_pressure_level() in (MemoryPressureLevel.ORANGE, MemoryPressureLevel.RED)

    def allocate_memory(self, app_id: str, amount: int) -> bool:
        """Allocates memory to a specific app if sufficient memory is available.
        
        Args:
            app_id: ID of the target app.
            amount: Memory amount in MB to add to footprint.
            
        Returns:
            bool: True if allocation succeeded, False if insufficient memory or app inactive.
        """
        app = self.apps.get(str(app_id))
        if not app or app.is_evicted:
            return False

        if amount <= 0:
            return True

        if self.get_free_memory() < amount:
            return False

        app.memory_footprint += amount
        app.touch()
        return True

    def deallocate_memory(self, app_id: str, amount: Optional[int] = None) -> int:
        """Frees memory from an app.
        
        Args:
            app_id: ID of the target app.
            amount: Amount to free, or None to free all allocated memory.
            
        Returns:
            int: Actual memory freed in MB.
        """
        app = self.apps.get(str(app_id))
        if not app:
            return 0

        if amount is None or amount >= app.memory_footprint:
            freed = app.memory_footprint
            app.memory_footprint = 0
        else:
            freed = max(0, int(amount))
            app.memory_footprint -= freed

        app.touch()
        return freed

    def get_active_apps(self) -> List[App]:
        """Returns a list of currently active (non-evicted) applications."""
        return [app for app in self.apps.values() if app.is_active]

    def clock_step(self) -> Tuple[Optional[App], List[Dict[str, Any]]]:
        """Executes one pass/step of the Clock (Second-Chance) eviction algorithm.
        
        Scans active applications starting at clock_hand index:
        - If reference_bit == 1: set reference_bit = 0, advance clock_hand.
        - If reference_bit == 0: candidate selected for eviction!
        
        Returns:
            Tuple[Optional[App], List[Dict]]:
                (selected_candidate_app, log_of_inspections)
        """
        active_apps = self.get_active_apps()
        if not active_apps:
            return None, []

        logs = []
        n = len(active_apps)
        
        # Ensure clock_hand points to valid index
        self.clock_hand %= n
        
        # We perform up to 2 full cycles around active apps (to handle case where all ref_bits are 1)
        for _ in range(2 * n):
            idx = self.clock_hand % n
            app = active_apps[idx]

            inspection = {
                "clock_hand": idx,
                "app_id": app.app_id,
                "app_name": app.name,
                "initial_reference_bit": app.reference_bit
            }

            if app.reference_bit == 1:
                app.reference_bit = 0
                inspection["action"] = "cleared_ref_bit"
                logs.append(inspection)
                self.clock_hand = (idx + 1) % n
            else:
                inspection["action"] = "selected_for_eviction"
                logs.append(inspection)
                self.clock_hand = (idx + 1) % n
                return app, logs

        # Fallback: if all ref bits were 1, after first loop all are 0, so candidate at hand is chosen
        idx = self.clock_hand % n
        app = active_apps[idx]
        self.clock_hand = (idx + 1) % n
        return app, logs

    def evaluate_clock_candidate_with_raae(
        self,
        raae_engine: Any
    ) -> Tuple[Optional[Any], List[Dict[str, Any]]]:
        """Selects a Clock candidate and asks RAAE Engine for an eviction decision."""
        candidate, logs = self.clock_step()
        if candidate is None:
            return None, logs

        return raae_engine.orchestrate_safe_eviction(candidate), logs

    def evict_app(self, app_id: str) -> bool:
        """Evicts an app, resetting its footprint to 0 and transitioning state to EVICTED."""
        app = self.apps.get(str(app_id))
        if not app or app.is_evicted:
            return False

        app.memory_footprint = 0
        app.evict()
        return True

    def to_dict(self) -> Dict[str, Any]:
        """Serializes current memory state."""
        return {
            "total_memory": self.total_memory,
            "used_memory": self.get_used_memory(),
            "free_memory": self.get_free_memory(),
            "usage_percentage": self.get_usage_percentage(),
            "pressure_level": self.get_pressure_level().value,
            "registered_apps_count": len(self.apps),
            "active_apps_count": len(self.get_active_apps()),
            "clock_hand": self.clock_hand
        }
