import time

import DisruptionSimulator
import DataReader
import ScheduleRecovery
import NeighborhoodManager
import CostEstimator
import JobRepairingAssigner


start = time.time()

# ============================================================
# 1. LOAD SCHEDULE
# ============================================================

schedule_data = DataReader.ScheduleLoader.load(
    "data/Shift_Task_View_ctl.json"
)

simulator = DisruptionSimulator.DisruptionSimulator(
    schedule_data
)

# ============================================================
# 2. SELECT EMPLOYEE AND DATE TO DISRUPT
# ============================================================

employee_id = "A"
date = "2026-09-08"

employee = schedule_data.employees[employee_id]

tasks = employee.rosters[date].tasks

print("\n========== ORIGINAL TASKS ==========")

for task in tasks:
    print(task.task_key)

# Get the task keys that will be disrupted
disrupted_task_keys = [
    task.task_key
    for task in tasks
]

# ============================================================
# 3. CREATE RECOVERY COMPONENTS
# ============================================================

neighborhoodManager = NeighborhoodManager.NeighborhoodManager(
    schedule_data=schedule_data,
    temporal_window=30
)

costEstimator = CostEstimator.EmployeeCostEstimator()

scheduleRecovery = ScheduleRecovery.ScheduleRecovery(
    schedule_data=schedule_data,
    neighborhood_manager=neighborhoodManager,
    cost_estimator=costEstimator
)

# ============================================================
# 4. APPLY DISRUPTION
#
# IMPORTANT:
# Remove each task ONLY ONCE.
# At this point we also remember the employee who originally
# owned the task.
# ============================================================

disrupted_tasks = []

print("\n========== APPLYING DISRUPTION ==========")

for task_key in disrupted_task_keys:

    task, original_employee = simulator.remove_task(task_key)

    # The employee who originally had this task
    task.blocked_employee = original_employee.employee_id

    # Keep the task for the recovery phase
    disrupted_tasks.append(task)

    print(
        f"Task {task.task_key} removed from "
        f"Employee {original_employee.employee_id}"
    )

    print(
        f"Blocked Employee: {task.blocked_employee}"
    )

# ============================================================
# 5. NOW START RECOVERY
# ============================================================

print("\n========== STARTING RECOVERY ==========")

for task in disrupted_tasks:

    print("\n========== DISRUPTION ==========")

    print(f"Removed Task      : {task.task_key}")
    print(f"Blocked Employee  : {task.blocked_employee}")
    print(f"Task Time         : {task.date} {task.start_time}-{task.end_time}")

    result = scheduleRecovery.recover(
        disrupted_tasks=[task],
        employees=list(
            schedule_data.employees.values()
        )
    )

    if result is None:

        print("All recovery levels failed.")

        continue

    print("\n========== FINAL RECOVERY ==========")

    print(f"Task      : {task.task_key}")
    print(f"Feasible  : {result.feasible}")
    print(f"Total Cost: {result.total_cost}")

    print("\nAssignments:")

    for assignment in result.assignments:

        print(
            f"{assignment['task'].task_key} -> "
            f"{assignment['employee'].employee_id} "
            f"(Cost: {assignment['cost']})"
        )

    if result.unassigned_tasks:

        print("\nStill Unassigned:")

        for unassigned_task in result.unassigned_tasks:

            print(
                unassigned_task.task_key
            )

    # ========================================================
    # 6. ROSTER VERIFICATION
    # ========================================================

    print("\n========== ROSTER VERIFICATION ==========")

    for assignment in result.assignments:

        assigned_employee = assignment["employee"]
        assigned_task = assignment["task"]

        roster = assigned_employee.rosters[
            assigned_task.date
        ]

        assigned = any(
            t.task_key == assigned_task.task_key
            for t in roster.tasks
        )

        print(
            f"{assigned_employee.employee_id} | "
            f"{assigned_task.task_key} | "
            f"Stored in roster: {assigned}"
        )


# ============================================================
# 7. TOTAL TIME
# ============================================================

end = time.time()

print(
    f"\nTotal Execution Time: "
    f"{end - start:.2f} seconds"
)
