"""Dashboard resource aliases required by the Phase-II demonstration."""

from backend.models.resource import Resource
from backend.resource_manager.resource_manager import ResourceManager


def register_demo_resources(resource_manager: ResourceManager) -> None:
    """Register the canonical dashboard resource IDs without replacing defaults."""
    for resource_id, name in (
        ("Camera", "Camera"),
        ("Microphone", "Microphone"),
        ("Audio", "Audio Output"),
    ):
        if resource_manager.get_resource(resource_id) is None:
            resource_manager.register_resource(
                Resource(resource_id=resource_id, name=name)
            )
