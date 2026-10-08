"""Memory Manager module for RAAE simulation."""

from enum import Enum
import time
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
        db (Optional[Any]): Optional DatabaseManager repository instance for clean persistence hooks.
    """

    def __init__(self, total_memory: int = 1024, db: Optional[Any] = None) -> None:
        self.total_memory: int = max(1, int(total_memory))
        self.apps: Dict[str, App] = {}
        self.clock_hand: int = 0
        self.db: Optional[Any] = db

    def set_total_memory(self, total_memory: int) -> None:
        """Dynamically configures the total system RAM limit in MB."""
        self.total_memory = max(1, int(total_memory))

    def set_db_manager(self, db: Any) -> None:
        """Sets the DatabaseManager repository layer instance."""
        self.db = db

    def add_app(self, app: App) -> None:
        """Adds/registers a simulated application with the Memory Manager."""
        self.apps[app.app_id] = app
        if self.db and hasattr(self.db, "save_app"):
            self.db.save_app(app)

    def register_app(self, app: App) -> None:
        """Alias for add_app for backward compatibility."""
        self.add_app(app)

    def remove_app(self, app_id: str) -> Optional[App]:
        """Removes/unregisters an application by app_id."""
        removed = self.apps.pop(str(app_id), None)
        if removed and self.db and hasattr(self.db, "delete_app"):
            self.db.delete_app(removed.app_id)
        return removed

    def unregister_app(self, app_id: str) -> Optional[App]:
        """Alias for remove_app for backward compatibility."""
        return self.remove_app(app_id)

    def update_reference_bit(self, app_id: str, bit: int) -> bool:
        """Updates reference bit and last access timestamp for a specified application."""
        app = self.apps.get(str(app_id))
        if not app:
            return False
        app.reference_bit = 1 if bit else 0
        app.last_access_time = time.time()
        if self.db and hasattr(self.db, "save_app"):
            self.db.save_app(app)
        return True

    def update_last_access_time(self, app_id: str, timestamp: Optional[float] = None) -> bool:
        """Updates last-access timestamp for a specified application."""
        app = self.apps.get(str(app_id))
        if not app:
            return False
        app.last_access_time = float(timestamp) if timestamp is not None else time.time()
        if self.db and hasattr(self.db, "save_app"):
            self.db.save_app(app)
        return True

    def touch_app(self, app_id: str) -> bool:
        """Touches an application, updating reference bit to 1 and last access timestamp."""
        app = self.apps.get(str(app_id))
        if not app:
            return False
        app.touch()
        if self.db and hasattr(self.db, "save_app"):
            self.db.save_app(app)
        return True

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
            level = MemoryPressureLevel.GREEN
        elif pct < 75.0:
            level = MemoryPressureLevel.YELLOW
        elif pct < 90.0:
            level = MemoryPressureLevel.ORANGE
        else:
            level = MemoryPressureLevel.RED

        if level in (MemoryPressureLevel.ORANGE, MemoryPressureLevel.RED) and self.db and hasattr(self.db, "log_memory_pressure"):
            self.db.log_memory_pressure(level.value, self.get_used_memory(), self.total_memory)

        return level

    def is_under_pressure(self) -> bool:
        """Returns True if memory pressure is ORANGE or RED (usage >= 75%)."""
        return self.get_pressure_level() in (MemoryPressureLevel.ORANGE, MemoryPressureLevel.RED)

    def allocate_memory(self, app_id: str, amount: int) -> bool:
        """Allocates memory to a specific app if sufficient memory is available."""
        app = self.apps.get(str(app_id))
        if not app or app.is_evicted:
            return False

        if amount <= 0:
            return True

        if self.get_free_memory() < amount:
            return False

        before = app.memory_footprint
        app.memory_footprint += amount
        app.touch()

        if self.db:
            if hasattr(self.db, "save_app"):
                self.db.save_app(app)
            if hasattr(self.db, "log_memory_action"):
                self.db.log_memory_action(app.app_id, "ALLOCATE", before, app.memory_footprint, self.get_pressure_level().value)

        return True

    def deallocate_memory(self, app_id: str, amount: Optional[int] = None) -> int:
        """Frees memory from an app."""
        app = self.apps.get(str(app_id))
        if not app:
            return 0

        before = app.memory_footprint
        if amount is None or amount >= app.memory_footprint:
            freed = app.memory_footprint
            app.memory_footprint = 0
        else:
            freed = max(0, int(amount))
            app.memory_footprint -= freed

        app.touch()

        if self.db:
            if hasattr(self.db, "save_app"):
                self.db.save_app(app)
            if hasattr(self.db, "log_memory_action"):
                self.db.log_memory_action(app.app_id, "DEALLOCATE", before, app.memory_footprint, self.get_pressure_level().value)

        return freed

    def get_active_apps(self) -> List[App]:
        """Returns a list of currently active (non-evicted) applications."""
        return [app for app in self.apps.values() if app.is_active]

    def get_eligible_background_apps(self) -> List[App]:
        """Returns a list of active background applications eligible for eviction."""
        return [
            app for app in self.apps.values()
            if app.is_active and app.state != AppState.FOREGROUND
        ]

    def get_eviction_candidates(self) -> List[App]:
        """Alias for get_eligible_background_apps."""
        return self.get_eligible_background_apps()

    def select_eviction_candidate(self) -> Optional[App]:
        """Selects an app candidate for eviction using Clock / Second-Chance algorithm."""
        candidate, _ = self.clock_step()
        return candidate

    def clock_step(self) -> Tuple[Optional[App], List[Dict[str, Any]]]:
        """Executes one pass/step of the Clock (Second-Chance) eviction algorithm."""
        eligible_apps = self.get_eligible_background_apps()
        if not eligible_apps:
            return None, []

        logs = []
        n = len(eligible_apps)
        
        for _ in range(2 * n):
            idx = self.clock_hand % n
            app = eligible_apps[idx]

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
                if self.db and hasattr(self.db, "save_app"):
                    self.db.save_app(app)
            else:
                inspection["action"] = "selected_for_eviction"
                logs.append(inspection)
                self.clock_hand = (idx + 1) % n
                return app, logs

        idx = self.clock_hand % n
        app = eligible_apps[idx]
        self.clock_hand = (idx + 1) % n
        return app, logs

    def evict_app(self, app_id: str) -> bool:
        """Evicts an app, resetting its footprint to 0 and transitioning state to EVICTED."""
        app = self.apps.get(str(app_id))
        if not app or app.is_evicted:
            return False

        app.memory_footprint = 0
        app.evict()
        if self.db and hasattr(self.db, "save_app"):
            self.db.save_app(app)
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
            "eligible_background_apps_count": len(self.get_eligible_background_apps()),
            "clock_hand": self.clock_hand
        }


