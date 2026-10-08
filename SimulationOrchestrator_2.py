import DisruptionSimulator
import DataReader
import ScheduleRecovery
import NeighborhoodManager
import CostEstimator
import JobRepairingAssigner
import time as time


start=time.time()
schedule_data = DataReader.ScheduleLoader.load(
    "data/Shift_Task_View_ctl.json"
)

simulator = DisruptionSimulator.DisruptionSimulator(schedule_data)

disrupted_task_keys = ["T1", "T2", "T3"]

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

end1=time.time()
print(f"\nTotal Execution Time: {end1-start:.2f} seconds")

for task_key in disrupted_task_keys:

    task, original_employee = simulator.remove_task(task_key)

    print("\n========== DISRUPTION ==========")
    print(f"Removed Task : {task.task_key}")
    print(f"Original Employee : {original_employee.employee_id}")
    print(f"Task Time : {task.date} {task.start_time}-{task.end_time}")

    result = scheduleRecovery.recover(
        disrupted_tasks=[task],
        employees=list(
            schedule_data.employees.values()
        )
    )

    #assert result is not None

    if result is None:
            print("All recovery levels failed.")
            continue

    print("\n========== FINAL RECOVERY ==========")
    print(f"Task : {task.task_key}")
    print(f"Feasible : {result.feasible}")
    print(f"Total Cost : {result.total_cost}")

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
            print(unassigned_task.task_key)

    print("\n========== ROSTER VERIFICATION ==========")

    for assignment in result.assignments:

        employee = assignment["employee"]
        assigned_task = assignment["task"]

        roster = employee.rosters[assigned_task.date]

        assigned = any(
            t.task_key == assigned_task.task_key
            for t in roster.tasks
        )

        print(
            f"{employee.employee_id} | {assigned_task.task_key} | "
            f"Stored in roster: {assigned}"
        )
end=time.time()
print(f"\nTotal Execution Time: {end-start:.2f} seconds")