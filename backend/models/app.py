"""App model for RAAE simulation."""

from enum import Enum
import time
from typing import Set, Dict, Any, Optional


class AppState(str, Enum):
    """Supported application execution states."""
    FOREGROUND = "FOREGROUND"
    BACKGROUND = "BACKGROUND"
    WAITING = "WAITING"
    EVICTED = "EVICTED"


class AppStatus(str, Enum):
    """Active/Evicted status of an application."""
    ACTIVE = "ACTIVE"
    EVICTED = "EVICTED"


class App:
    """Represents a simulated application in the RAAE system.
    
    Attributes:
        app_id (str): Unique identifier for the application.
        name (str): Human-readable name of the application.
        priority (int): Priority level (higher value indicates higher priority).
        state (AppState): Current execution state (FOREGROUND, BACKGROUND, WAITING, EVICTED).
        memory_footprint (int): Memory used by the application in MB.
        reference_bit (int): Reference bit used for the Clock/Second-Chance eviction algorithm (0 or 1).
        last_access_time (float): UNIX timestamp of the last access/activity time.
        held_resources (Set[str]): Set of resource IDs currently held by this application.
        status (AppStatus): Active/Evicted status of the application.
    """

    def __init__(
        self,
        app_id: str,
        name: str,
        priority: int = 1,
        state: AppState = AppState.BACKGROUND,
        memory_footprint: int = 0,
        reference_bit: int = 1,
        last_access_time: Optional[float] = None,
        held_resources: Optional[Set[str]] = None,
        status: Optional[AppStatus] = None
    ) -> None:
        self.app_id: str = str(app_id)
        self.name: str = name
        self.priority: int = int(priority)
        
        # Ensure state is valid AppState enum
        if isinstance(state, str):
            state = AppState(state)
        self.state: AppState = state

        self.memory_footprint: int = max(0, int(memory_footprint))
        self.reference_bit: int = 1 if reference_bit else 0
        self.last_access_time: float = float(last_access_time) if last_access_time is not None else time.time()
        self.held_resources: Set[str] = set(held_resources) if held_resources else set()

        # Determine status automatically if not explicitly provided
        if status is not None:
            if isinstance(status, str):
                status = AppStatus(status)
            self.status: AppStatus = status
        else:
            self.status: AppStatus = AppStatus.EVICTED if self.state == AppState.EVICTED else AppStatus.ACTIVE

    @property
    def is_active(self) -> bool:
        """Returns True if the application is currently active (not evicted)."""
        return self.status == AppStatus.ACTIVE and self.state != AppState.EVICTED

    @property
    def is_evicted(self) -> bool:
        """Returns True if the application is evicted."""
        return self.status == AppStatus.EVICTED or self.state == AppState.EVICTED

    def change_state(self, new_state: AppState) -> None:
        """Changes the state of the app and updates status and timestamp accordingly."""
        if isinstance(new_state, str):
            new_state = AppState(new_state)
            
        self.state = new_state
        self.last_access_time = time.time()

        if self.state == AppState.EVICTED:
            self.status = AppStatus.EVICTED
            self.reference_bit = 0
        else:
            self.status = AppStatus.ACTIVE
            self.reference_bit = 1

    def evict(self) -> None:
        """Helper method to mark the app as evicted."""
        self.change_state(AppState.EVICTED)

    def activate(self, state: AppState = AppState.FOREGROUND) -> None:
        """Activates an evicted app or moves an existing app to active state."""
        if state == AppState.EVICTED:
            raise ValueError("Cannot activate an app into EVICTED state.")
        self.change_state(state)

    def touch(self) -> None:
        """Updates last_access_time and sets reference_bit to 1 (indicating recent activity)."""
        self.last_access_time = time.time()
        self.reference_bit = 1

    def acquire_resource(self, resource_id: str) -> None:
        """Registers a held resource for this app."""
        self.held_resources.add(str(resource_id))

    def release_resource(self, resource_id: str) -> None:
        """Removes a held resource from this app."""
        self.held_resources.discard(str(resource_id))

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the App model into a dictionary suitable for JSON/SQLite storage."""
        return {
            "app_id": self.app_id,
            "name": self.name,
            "priority": self.priority,
            "state": self.state.value,
            "memory_footprint": self.memory_footprint,
            "reference_bit": self.reference_bit,
            "last_access_time": self.last_access_time,
            "held_resources": sorted(list(self.held_resources)),
            "status": self.status.value,
            "is_active": self.is_active
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "App":
        """Deserializes dictionary data into an App model instance."""
        held_resources = data.get("held_resources")
        if isinstance(held_resources, str):
            # Handle comma-separated resource string from SQLite text column
            held_resources = {r.strip() for r in held_resources.split(",") if r.strip()}
        elif isinstance(held_resources, (list, tuple, set)):
            held_resources = set(held_resources)
        else:
            held_resources = set()

        return cls(
            app_id=str(data["app_id"]),
            name=str(data["name"]),
            priority=int(data.get("priority", 1)),
            state=AppState(data.get("state", AppState.BACKGROUND.value)),
            memory_footprint=int(data.get("memory_footprint", 0)),
            reference_bit=int(data.get("reference_bit", 1)),
            last_access_time=float(data.get("last_access_time", time.time())),
            held_resources=held_resources,
            status=AppStatus(data["status"]) if "status" in data and data["status"] else None
        )

    def __repr__(self) -> str:
        return (
            f"<App id={self.app_id!r} name={self.name!r} priority={self.priority} "
            f"state={self.state.value} mem={self.memory_footprint}MB status={self.status.value}>"
        )
