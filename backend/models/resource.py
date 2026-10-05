"""Resource model for RAAE simulation."""

from enum import Enum
from typing import Dict, List, Any, Optional


class ResourceStatus(str, Enum):
    """Resource status states."""
    FREE = "FREE"
    LOCKED = "LOCKED"
    WAITING = "WAITING"


class Resource:
    """Represents a shared hardware or software resource in RAAE.
    
    Attributes:
        resource_id (str): Unique resource identifier (e.g. 'GPS', 'MIC', 'DB_LOCK').
        name (str): Human readable resource name.
        capacity (int): Total capacity/units available for semaphore locking (default 1 = Mutex).
        available_units (int): Number of currently available units.
        holders (Dict[str, int]): Map of app_id -> count of units held by that app.
        waiting_queue (List[str]): List of app_ids waiting to acquire this resource.
    """

    def __init__(
        self,
        resource_id: str,
        name: str,
        capacity: int = 1,
        available_units: Optional[int] = None,
        holders: Optional[Dict[str, int]] = None,
        waiting_queue: Optional[List[str]] = None
    ) -> None:
        self.resource_id: str = str(resource_id)
        self.name: str = str(name)
        self.capacity: int = max(1, int(capacity))
        self.available_units: int = int(available_units) if available_units is not None else self.capacity
        self.holders: Dict[str, int] = dict(holders) if holders else {}
        self.waiting_queue: List[str] = list(waiting_queue) if waiting_queue else []

    @property
    def is_locked(self) -> bool:
        """Returns True if at least one unit is currently locked by an app."""
        return len(self.holders) > 0

    @property
    def status(self) -> ResourceStatus:
        """Computes current resource status."""
        if self.waiting_queue:
            return ResourceStatus.WAITING
        elif self.is_locked:
            return ResourceStatus.LOCKED
        else:
            return ResourceStatus.FREE

    @property
    def primary_holder_id(self) -> Optional[str]:
        """Returns the first app_id holding this resource, or None."""
        if self.holders:
            return next(iter(self.holders.keys()))
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the resource for JSON/SQLite storage."""
        return {
            "resource_id": self.resource_id,
            "name": self.name,
            "capacity": self.capacity,
            "available_units": self.available_units,
            "holders": self.holders,
            "waiting_queue": self.waiting_queue,
            "status": self.status.value,
            "held_by_app_id": self.primary_holder_id
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Resource":
        """Deserializes resource dictionary."""
        return cls(
            resource_id=data["resource_id"],
            name=data["name"],
            capacity=data.get("capacity", 1),
            available_units=data.get("available_units"),
            holders=data.get("holders"),
            waiting_queue=data.get("waiting_queue")
        )

    def __repr__(self) -> str:
        return (
            f"<Resource id={self.resource_id!r} name={self.name!r} status={self.status.value} "
            f"avail={self.available_units}/{self.capacity} holders={list(self.holders.keys())}>"
        )
