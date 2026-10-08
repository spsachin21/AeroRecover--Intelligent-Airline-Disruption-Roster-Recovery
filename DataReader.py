import json
from InputClasses import ScheduleData
from InputClasses import Shift
from InputClasses import Task
from InputClasses import ResourceRoster
from InputClasses import Employee
from InputClasses import RosterDay
import csv
from dataclasses import asdict



# ============================================================
# JSON LOADER
# ============================================================

class ScheduleLoader:

    @staticmethod
    def load(filename: str) -> ScheduleData:

        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        schedule = ScheduleData()

        # ----------------------------------------------------
        # 1. LOAD SHIFTS
        # ----------------------------------------------------

        for shift_id, value in data.get("shifts", {}).items():

            shift = Shift(
                shift_id=shift_id,
                start_time=value["Shift_Start_Time"],
                end_time=value["Shift_End_Time"],
                length=value["Shift_Length"],
            )

            schedule.shifts[shift_id] = shift

        # ----------------------------------------------------
        # 2. LOAD TASKS
        # ----------------------------------------------------

        for task_key, value in data.get("tasks_details", {}).items():

            task = Task(
                task_key=task_key,
                unique_turn_id=value["unique_turn_id"],
                task_id=value["task_id"],
                turn_id=value["turn_id"],
                service=value["service"],
                start_time=value["start_time"],
                end_time=value["end_time"],
                date=value["date"],
                demand=value["demand"],
                coverage=value["coverage"],
                status=value["status"],
            )

            schedule.tasks[task_key] = task

        # ----------------------------------------------------
        # 3. LOAD RESOURCE ROSTERS
        # ----------------------------------------------------

        for resource, resource_data in data.get("rosters", {}).items():

            resource_roster = ResourceRoster(
                resource=resource_data["resource"],
                planning_horizon_start=
                    resource_data["planning_horizon_start"],
                planning_horizon_end=
                    resource_data["planning_horizon_end"],
                headcount=resource_data["headcount"],
            )

            # ----------------------------------------------------
            # 4. LOAD INDIVIDUAL ROSTERS
            # ----------------------------------------------------

            for employee_index, roster_data in enumerate(
                    resource_data.get("rosters", []), start=1):

                employee_id = generate_employee_id(employee_index)

                roster = Employee(
                    roster_id=roster_data["roster_id"],
                    employee_id=employee_id,
                )

                roster.skills = set()
                roster.overtime_eligible = True
                roster.on_leave = False

                # Load days/tasks as before
                for day_data in roster_data.get("days", []):

                    shift_id = day_data.get("shift_id")

                    if shift_id == "OFF" or shift_id is None:
                        shift = None
                    else:
                        shift = schedule.shifts.get(shift_id)

                    tasks = [
                        schedule.tasks[task_key]
                        for task_key in day_data.get("task_list", [])
                    ]

                    roster_day = RosterDay(
                        date=day_data["date"],
                        day=day_data["day"],
                        shift=shift,
                        tasks=tasks,
                        ot_hours=day_data.get("ot_hours", 0),
                    )

                    roster.add_day(roster_day)

                # Store employee using generated ID
                schedule.employees[employee_id] = roster

                # Also retain it under the resource roster
                resource_roster.add_roster(roster)



            schedule.resources[resource] = resource_roster

        return schedule

def generate_employee_id(index: int) -> str:
    """
    Convert 1 -> A
           2 -> B
           ...
          26 -> Z
          27 -> AA
          28 -> AB
    """

    result = ""

    while index > 0:
        index -= 1
        result = chr(ord("A") + (index % 26)) + result
        index //= 26

    return result

def tasks_to_csv(tasks, filename="tasks.csv"):
    if not tasks:
        return

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=asdict(tasks[0]).keys())
        writer.writeheader()
        writer.writerows(asdict(task) for task in tasks)
        
if __name__ == "__main__":

    schedule = ScheduleLoader.load("Shift_Task_View_ctl.json")
    
    emp = next(iter(schedule.employees.values()))

    # Day having maximum number of tasks
    data = max(
        emp.rosters.values(),
        key=lambda day: len(day.tasks)
    )

    tasks_to_csv(data.tasks)

    # --------------------------------------------------------
    # Dictionaries containing OBJECTS
    # --------------------------------------------------------

    # print("\nSHIFTS")
    # print("------")

    # print(schedule.shifts)

    # print("\nTASKS")
    # print("-----")

    # print(schedule.tasks)

    # print("\nRESOURCES")
    # print("---------")

    # print(schedule.resources)

    # --------------------------------------------------------
    # Access a particular shift
    # --------------------------------------------------------

    # shift = schedule.shifts["10H_0600"]

    # print("\nSelected shift:")
    # print(shift)

    # # --------------------------------------------------------
    # # Access a particular task
    # # --------------------------------------------------------

    # task = schedule.tasks["T1"]

    # print("\nSelected task:")
    # print(task)

    # # --------------------------------------------------------
    # # Access CTL resource
    # # --------------------------------------------------------

    # ctl = schedule.resources["CTL"]

    # print("\nCTL:")
    # print(ctl)

    # # --------------------------------------------------------
    # # Access roster 1
    # # --------------------------------------------------------

    # roster = ctl.rosters[1]

    # print("\nRoster 1:")
    # print(roster)

    # # --------------------------------------------------------
    # # Access a particular date
    # # --------------------------------------------------------

    # day = roster.get_day("2026-09-07")

    # print("\nRoster 1 on 2026-09-07:")
    # print(day)

    # # --------------------------------------------------------
    # # Access shift through roster day
    # # --------------------------------------------------------

    # print("\nShift:")
    # print(day.shift)

    # # --------------------------------------------------------
    # # Access tasks through roster day
    # # --------------------------------------------------------

    # print("\nTasks:")
    # for task in day.tasks:
    #     print(task)

    # # --------------------------------------------------------
    # # Get all tasks assigned to roster 1
    # # --------------------------------------------------------

    # print("\nAll tasks for roster 1:")
    # for task in roster.get_tasks():
    #     print(task)
