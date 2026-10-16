"""Interface adapters bridging differences across teammates' models and abstractions."""

from typing import Any, List, Optional, Sequence, Tuple, Union

from backend.models.app import App, AppState, AppStatus
from backend.models.resource import Resource


class AppCandidateAdapter:
    """Adapts generic candidate representations into the standard App model."""

    @staticmethod
    def to_app(candidate: Any) -> App:
        """Converts or passes through an object as an App instance."""
        if isinstance(candidate, App):
            return candidate

        # Handle object with candidate_id or page_id
        app_id = getattr(candidate, "candidate_id", None) or getattr(candidate, "page_id", None) or getattr(candidate, "app_id", None) or str(candidate)
        name = getattr(candidate, "name", None) or getattr(candidate, "candidate_type", None) or str(app_id)
        owner_id = getattr(candidate, "owner_id", None)
        priority = int(getattr(candidate, "priority", 1))
        memory = int(getattr(candidate, "memory_footprint", 0) or getattr(candidate, "memory", 0))

        return App(
            app_id=str(app_id),
            name=name,
            priority=priority,
            memory_footprint=memory
        )

    @staticmethod
    def to_resource_handles(resources: Sequence[Union[Resource, str]]) -> Tuple[str, ...]:
        """Extracts resource IDs from Resource models or string IDs."""
        res_ids = []
        for r in resources:
            if isinstance(r, str):
                res_ids.append(r)
            elif hasattr(r, "resource_id"):
                res_ids.append(str(r.resource_id))
        return tuple(res_ids)
