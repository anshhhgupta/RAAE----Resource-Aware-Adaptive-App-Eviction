"""SimulationController for executing workload traces and verifying SQLite persistence consistency."""

from typing import Dict, List, Optional, Any
from backend.models.app import App, AppState
from backend.models.resource import Resource
from backend.simulation.simulation_state import SimulationState
from backend.workload_generator.workload_generator import WorkloadTrace, WorkloadEvent
from database.db import DatabaseManager


class SimulationController:
    """Controls simulation execution over ticks and verifies state consistency with SQLite database.
    
    Attributes:
        sim_state (SimulationState): Integrated simulation state.
        db (DatabaseManager): Database manager repository instance.
    """

    def __init__(
        self,
        sim_state: Optional[SimulationState] = None,
        db: Optional[DatabaseManager] = None
    ) -> None:
        self.db: DatabaseManager = db or DatabaseManager(":memory:")
        self.sim_state: SimulationState = sim_state or SimulationState(db=self.db)
        self.sim_state.set_db_manager(self.db)

    def run_workload(self, trace: WorkloadTrace) -> Dict[str, Any]:
        """Executes a generated workload trace over discrete simulation ticks.
        
        Args:
            trace: Generated workload trace containing setup apps and events.
            
        Returns:
            Dict[str, Any]: Summary execution statistics.
        """
        # Configure total system RAM
        self.sim_state.memory_manager.set_total_memory(trace.total_memory)

        # 1. Setup initial apps
        for app in trace.initial_apps:
            self.sim_state.add_app(app)

        processed_events_count = 0

        # 2. Process events tick by tick
        for event in trace.events:
            self._process_event(event)
            processed_events_count += 1

        return {
            "scenario": trace.scenario,
            "total_ticks": max([e.tick for e in trace.events], default=0) + 1 if trace.events else 0,
            "processed_events": processed_events_count,
            "apps_count": len(self.sim_state.memory_manager.apps),
            "memory_usage_pct": self.sim_state.memory_manager.get_usage_percentage(),
            "pressure_level": self.sim_state.memory_manager.get_pressure_level().value
        }

    def _process_event(self, event: WorkloadEvent) -> None:
        """Processes a single WorkloadEvent."""
        etype = event.event_type
        app_id = event.app_id
        payload = event.payload

        if etype == "ALLOCATE_MEMORY" and app_id:
            amount = payload.get("amount", 20)
            self.sim_state.allocate_memory(app_id, amount)

        elif etype == "DEALLOCATE_MEMORY" and app_id:
            amount = payload.get("amount", 10)
            self.sim_state.memory_manager.deallocate_memory(app_id, amount)

        elif etype == "ACCESS_APP" and app_id:
            ref_bit = payload.get("reference_bit", 1)
            self.sim_state.update_reference_bit(app_id, ref_bit)

        elif etype == "STATE_CHANGE" and app_id:
            app = self.sim_state.memory_manager.apps.get(app_id)
            if app:
                new_state = payload.get("new_state", AppState.BACKGROUND.value)
                app.change_state(AppState(new_state))
                self.db.save_app(app)

        elif etype == "ACQUIRE_RESOURCE" and app_id:
            res_id = payload.get("resource_id", "Camera")
            self.sim_state.request_resource(app_id, res_id)

        elif etype == "RELEASE_RESOURCE" and app_id:
            res_id = payload.get("resource_id", "Camera")
            self.sim_state.release_resource(app_id, res_id)

        elif etype == "MEMORY_PRESSURE":
            target_pct = payload.get("target_percentage", 85.0)
            self.sim_state.trigger_memory_pressure(target_pct)

    def verify_state_consistency(self) -> bool:
        """Verifies that in-memory objects and SQLite database records remain 100% consistent.
        
        Returns:
            bool: True if in-memory state matches SQLite database state for all apps and resources.
            
        Raises:
            AssertionError: If any discrepancy is found between memory and database state.
        """
        # Verify Apps
        db_apps = {app.app_id: app for app in self.db.get_all_apps()}
        mem_apps = self.sim_state.memory_manager.apps

        for app_id, mem_app in mem_apps.items():
            db_app = db_apps.get(app_id)
            if not db_app:
                raise AssertionError(f"App '{app_id}' present in memory but missing from database.")

            assert mem_app.memory_footprint == db_app.memory_footprint, (
                f"Memory mismatch for app '{app_id}': mem={mem_app.memory_footprint}, db={db_app.memory_footprint}"
            )
            assert mem_app.state == db_app.state, (
                f"State mismatch for app '{app_id}': mem={mem_app.state}, db={db_app.state}"
            )
            assert mem_app.reference_bit == db_app.reference_bit, (
                f"Ref bit mismatch for app '{app_id}': mem={mem_app.reference_bit}, db={db_app.reference_bit}"
            )
            assert mem_app.status == db_app.status, (
                f"Status mismatch for app '{app_id}': mem={mem_app.status}, db={db_app.status}"
            )
            assert mem_app.held_resources == db_app.held_resources, (
                f"Held resources mismatch for app '{app_id}': mem={mem_app.held_resources}, db={db_app.held_resources}"
            )

        # Verify Resources
        db_resources = {res.resource_id: res for res in self.db.get_all_resources()}
        mem_resources = self.sim_state.resource_manager.resources

        for res_id, mem_res in mem_resources.items():
            db_res = db_resources.get(res_id)
            if not db_res:
                raise AssertionError(f"Resource '{res_id}' present in memory but missing from database.")

            assert mem_res.available_units == db_res.available_units, (
                f"Available units mismatch for resource '{res_id}': mem={mem_res.available_units}, db={db_res.available_units}"
            )
            assert mem_res.status == db_res.status, (
                f"Status mismatch for resource '{res_id}': mem={mem_res.status}, db={db_res.status}"
            )
            assert mem_res.waiting_queue == db_res.waiting_queue, (
                f"Waiting queue mismatch for resource '{res_id}': mem={mem_res.waiting_queue}, db={db_res.waiting_queue}"
            )
            assert mem_res.primary_holder_id == db_res.primary_holder_id, (
                f"Holder mismatch for resource '{res_id}': mem={mem_res.primary_holder_id}, db={db_res.primary_holder_id}"
            )

        return True
