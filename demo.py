"""Demonstration script for RAAE components.

Demonstrates:
- App Model creation and state transitions (FOREGROUND, BACKGROUND, WAITING, EVICTED)
- Memory Manager allocation & pressure levels (GREEN, YELLOW, ORANGE, RED)
- Resource Manager and Semaphore-style locking with WAITING state queueing
- Workload Generator synthetic workload generation
- SQLite Database persistence
"""

from backend.models.app import App, AppState, AppStatus
from backend.memory_manager.memory_manager import MemoryManager
from backend.resource_manager.resource_manager import ResourceManager
from backend.workload_generator.workload_generator import WorkloadGenerator
from database.db import DatabaseManager


def main():
    print("=" * 70)
    print(" RAAE — Resource-Aware Adaptive App Eviction (Component Demo)")
    print("=" * 70)

    # 1. Initialize SQLite Database Manager
    db = DatabaseManager(":memory:")
    print("[1] Database initialized.")

    # 2. Initialize Managers & Workload Generator
    mem_mgr = MemoryManager(total_memory=1000)
    res_mgr = ResourceManager()
    res_mgr.create_default_resources()
    gen = WorkloadGenerator(seed=100)

    print(f"[2] Memory Manager initialized with {mem_mgr.total_memory} MB total RAM.")
    print(f"    Available default resources: {[r.resource_id for r in res_mgr.get_all_resources()]}")

    # 3. Create Sample Apps using Workload Generator
    apps = gen.create_sample_apps(count=4)
    print("\n[3] Created Sample Applications:")
    for app in apps:
        mem_mgr.register_app(app)
        db.save_app(app)
        print(f"    - {app}")

    # 4. Demonstrate Memory Allocation and Pressure Levels
    print("\n[4] Allocating Memory & Checking Memory Pressure Levels:")
    print(f"    Initial Usage: {mem_mgr.get_used_memory()} MB ({mem_mgr.get_usage_percentage():.1f}%) -> Level: {mem_mgr.get_pressure_level().value}")
    
    # Increase footprint of app 1
    mem_mgr.allocate_memory(apps[0].app_id, 350)
    print(f"    Allocated +350MB to {apps[0].name}: Total Used={mem_mgr.get_used_memory()}MB ({mem_mgr.get_usage_percentage():.1f}%) -> Level: {mem_mgr.get_pressure_level().value}")

    # Drive pressure to ORANGE
    gen.trigger_memory_pressure(mem_mgr, target_percentage=80.0)
    print(f"    Triggered Pressure Target 80%: Total Used={mem_mgr.get_used_memory()}MB ({mem_mgr.get_usage_percentage():.1f}%) -> Level: {mem_mgr.get_pressure_level().value}")

    # 5. Demonstrate Semaphore Resource Locking & WAITING State Transition
    print("\n[5] Demonstrating Semaphore Locking & State Transitions:")
    app1, app2 = apps[0], apps[1]
    
    # App 1 acquires GPS
    success1 = res_mgr.acquire_resource(app1, "GPS")
    print(f"    App '{app1.name}' (ID: {app1.app_id}) requested GPS: Acquired = {success1}")
    print(f"    App '{app1.name}' state: {app1.state.value}, held_resources: {app1.held_resources}")

    # App 2 requests GPS (which is already held by App 1) -> enters WAITING state
    success2 = res_mgr.acquire_resource(app2, "GPS")
    print(f"    App '{app2.name}' (ID: {app2.app_id}) requested GPS: Acquired = {success2}")
    print(f"    App '{app2.name}' state updated to: {app2.state.value} (Blocked & Queued)")

    # Inspect GPS resource state
    gps = res_mgr.get_resource("GPS")
    print(f"    GPS Resource Status: {gps.status.value}, Waiting Queue: {gps.waiting_queue}")

    # App 1 releases GPS -> App 2 gets unblocked and state changes from WAITING
    res_mgr.release_resource(app1, "GPS", registered_apps=mem_mgr.apps)
    print(f"    App '{app1.name}' released GPS.")
    print(f"    App '{app2.name}' state restored to: {app2.state.value}, held_resources: {app2.held_resources}")

    # 6. App Eviction State Change
    print("\n[6] Demonstrating App Eviction State Transition:")
    app3 = apps[2]
    print(f"    Before Eviction: {app3}")
    app3.evict()
    db.save_app(app3)
    print(f"    After Eviction:  {app3}")
    print(f"    Status: {app3.status.value}, is_active: {app3.is_active}, is_evicted: {app3.is_evicted}")

    # 7. Query SQLite Persistence
    print("\n[7] Querying SQLite Database Records:")
    all_db_apps = db.get_all_apps()
    for dba in all_db_apps:
        print(f"    DB App Record: ID={dba.app_id}, Name={dba.name}, Priority={dba.priority}, State={dba.state.value}, Status={dba.status.value}")

    print("\n=" * 70)
    print(" RAAE Components Execution Completed Successfully!")
    print("=" * 70)


if __name__ == "__main__":
    main()
