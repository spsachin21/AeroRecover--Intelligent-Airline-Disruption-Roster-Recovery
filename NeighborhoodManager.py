import Neighborhood
from datetime import datetime


class NeighborhoodManager:

    def __init__(
        self,
        schedule_data,
        temporal_window=30,
        physical_radius=1.0,
        now_line_time="06:00"
    ):

        self.schedule_data = schedule_data

        self.temporal_window = temporal_window
        self.physical_radius = physical_radius
        self.now_line_time = now_line_time

    # =========================================================
    # MAIN METHOD
    # =========================================================

    def create_next_neighborhood(
        self,
        level,
        seed_tasks,
        employees,
        assigned_employees
    ):
        """
        Create exactly one neighbourhood corresponding
        to the requested level.

        Level:
        0 -> Regular
        1 -> Temporal destruction
        2 -> Overtime
        3 -> Temporal destruction + overtime
        4 -> Recall
        5 -> Recall + destruction + overtime

        """

        # -----------------------------------------------------
        # First create the local task neighbourhood.
        # This is common to N0, N1, N2, N3, N4 and N5.
        # -----------------------------------------------------

        local_tasks = self._get_local_tasks(
            seed_tasks
        )

        # -----------------------------------------------------
        # N0 - Regular local repair
        # -----------------------------------------------------

        if level == 0:

            candidates = self._get_candidate_employees(
                tasks=seed_tasks,
                employees=employees,
                recall=False
            )

            return Neighborhood.Neighborhood(
                level=0,
                name="N0-Regular",

                seed_tasks=list(seed_tasks),
                tasks=seed_tasks,
                released_tasks=[],

                candidate_employees=candidates,

                temporal_window=self.temporal_window,
                physical_radius=0,

                overtime_enabled=False,
                recall_enabled=False,
                temporal_destruction=False
            )
            
        # -----------------------------------------------------
        # N1 - Temporal destruction
        # -----------------------------------------------------

        elif level == 1:

            released_tasks = (
                self._get_temporally_overlapping_tasks(
                    local_tasks,
                    employees
                )
            )

            

            # candidates = self._get_candidate_employees(
            #     tasks=local_tasks,
            #     employees=employees,
            #     recall=False
            # ) 

            # if len(released_tasks)<len(candidates):
            #     candidates= candidates[:len(released_tasks)+len(local_tasks)]
            # else:
            #     released_tasks= released_tasks[:len(candidates)]

            # print("RELEASED TASKS:")
            # for task in released_tasks:
            #     print(task.task_key)
            # print("ALL ROSTER TASKS:")
            # for employee in employees:
            #     for roster in employee.rosters.values():
            #         print(
            #             employee,
            #             roster,
            #             [task.task_key for task in roster.tasks]
            #         )    
            removed_assignments = self._release_tasks_from_rosters(
            released_tasks,
            employees
            )
            print(f"length of removed assignments {len(removed_assignments)}")
            tasks = self._combine_tasks(
                local_tasks,
                released_tasks
            )

            candidates = self._get_candidate_employees(
                tasks=tasks,
                employees=employees,
                recall=False
            ) 

        
            neighborhood= Neighborhood.Neighborhood(
                level=1,
                name="N1-Temporal-Destruction",

                seed_tasks=list(seed_tasks),
                tasks=tasks,
                released_tasks=released_tasks,

                candidate_employees=candidates,

                temporal_window=self.temporal_window,
                physical_radius=self.physical_radius,

                overtime_enabled=False,
                recall_enabled=False,
                temporal_destruction=True
            )

            neighborhood.removed_assignments = removed_assignments
            return neighborhood

        
        # -----------------------------------------------------
        # N2 - Enable overtime
        # -----------------------------------------------------

        elif level == 2:

            candidates = self._get_candidate_employees(
                tasks=local_tasks,
                employees=employees,
                recall=False
            )

            return Neighborhood.Neighborhood(
                level=2,
                name="N2-Overtime",

                seed_tasks=list(seed_tasks),
                tasks=local_tasks,
                released_tasks=[],

                candidate_employees=candidates,

                temporal_window=self.temporal_window,
                physical_radius=self.physical_radius,

                overtime_enabled=True,
                recall_enabled=False,
                temporal_destruction=False
            )

        # -----------------------------------------------------
        # N3 - Temporal destruction + overtime
        # -----------------------------------------------------

        elif level == 3:

            released_tasks = (
                self._get_temporally_overlapping_tasks(
                    local_tasks,
                    employees
                )
            )

            removed_assignments = self._release_tasks_from_rosters(
            released_tasks,
            employees
            )

            tasks = self._combine_tasks(
                local_tasks,
                released_tasks
            )

            candidates = self._get_candidate_employees(
                tasks=tasks,
                employees=employees,
                recall=False
            )

            neighborhood= Neighborhood.Neighborhood(
                level=3,
                name="N3-Destruction-Overtime",

                seed_tasks=list(seed_tasks),
                tasks=tasks,
                released_tasks=released_tasks,

                candidate_employees=candidates,

                temporal_window=self.temporal_window,
                physical_radius=self.physical_radius,

                overtime_enabled=True,
                recall_enabled=False,
                temporal_destruction=True
            )

            neighborhood.removed_assignments = removed_assignments
            return neighborhood

        # -----------------------------------------------------
        # N4 - Recall 
        # -----------------------------------------------------

        elif level == 4:

            candidates = self._get_candidate_employees(
                tasks=local_tasks,
                employees=employees,
                recall=True
            )

            return Neighborhood.Neighborhood(
                level=4,
                name="N4-Recall",

                seed_tasks=list(seed_tasks),
                tasks=local_tasks,
                released_tasks=[],

                candidate_employees=candidates,

                temporal_window=self.temporal_window,
                physical_radius=self.physical_radius,

                overtime_enabled=False,
                recall_enabled=True,
                temporal_destruction=False
            )

        # -----------------------------------------------------
        # N5 - Recall + temporal destruction + overtime
        # -----------------------------------------------------

        elif level == 5:

            released_tasks = (
                self._get_temporally_overlapping_tasks(
                    local_tasks,
                    employees
                )
            )

            removed_assignments = self._release_tasks_from_rosters(
            released_tasks,
            employees
            )

            tasks = self._combine_tasks(
                local_tasks,
                released_tasks
            )

            candidates = self._get_candidate_employees(
                tasks=tasks,
                employees=employees,
                recall=True,
                assigned_employees=assigned_employees
            )

            neighborhood= Neighborhood.Neighborhood(
                level=5,
                name="N5-Recall-Destruction-Overtime",

                seed_tasks=list(seed_tasks),
                tasks=tasks,
                released_tasks=released_tasks,

                candidate_employees=candidates,

                temporal_window=self.temporal_window,
                physical_radius=self.physical_radius,

                overtime_enabled=True,
                recall_enabled=True,
                temporal_destruction=True
            )

            neighborhood.removed_assignments = removed_assignments

            return neighborhood

        # -----------------------------------------------------
        # No more neighbourhoods
        # -----------------------------------------------------

        return None

    # =========================================================
    # LOCAL NEIGHBOURHOOD
    # =========================================================

    def _get_local_tasks(self, seed_tasks):

        all_tasks = self._get_all_tasks_on_day(seed_tasks)

        selected = []

        for task in all_tasks:
            for seed in seed_tasks:
                if (task != seed):
                    if self._is_temporally_close(
                        task,
                        seed
                    ) and self._is_physically_close(
                        task,
                        seed
                    ):
                        selected.append(task)
                        break

        # Make sure seed tasks are always included
        selected.extend(seed_tasks)

        return self._unique_tasks(selected)

    # =========================================================
    # TEMPORAL DESTRUCTION
    # =========================================================

    # def _get_temporally_overlapping_tasks(
    #     self,
    #     local_tasks,
    #     employees
    # ):
    #     """
    #     Find currently assigned tasks which overlap
    #     the local repair region.

    #     These tasks become additional tasks in N1/N3/N5.
    #     """

    #     released = []

    #     local_task_keys = {
    #         task.task_key
    #         for task in local_tasks
    #     }
    #     get_date= local_tasks[-1].date
    #     # same_day_tasks= self._get_all_tasks_on_day(local_tasks[-1])
    #     for employee in employees:

    #         for roster in employee.rosters.values():

    #             for existing_task in roster.tasks:

    #                 if existing_task.date != get_date:
    #                     continue
    #                 # Do not release the tasks already
    #                 # belonging to the local neighbourhood.
    #                 if (
    #                     existing_task.task_key
    #                     in local_task_keys
    #                 ):
    #                     continue

    #                 for local_task in local_tasks:

    #                     if self._tasks_overlap(
    #                         existing_task,
    #                         local_task
    #                     ):

    #                         released.append(
    #                             existing_task
    #                         )

    #                         break


    #     return self._unique_tasks(released)
    
    def _get_temporally_overlapping_tasks(self, local_tasks, employees):
        released = []

        if not local_tasks:
            return released

        # Convert now-line time into a comparable time object
        now_time = datetime.strptime(
            self.now_line_time, "%H:%M"
        ).time()

        local_task_keys = {
            task.task_key for task in local_tasks
        }

        get_date = local_tasks[-1].date
        get_tasks = self._get_all_tasks_on_day(list(local_tasks))

        for task in get_tasks:

            # Skip tasks already in the local neighbourhood
            if task.task_key in local_task_keys:
                continue

            # Check whether the task has already started
            task_start_time = datetime.strptime(
                task.start_time, "%H:%M"
            ).time()

            if task_start_time < now_time:
                print(
                    f"Task already started: {task.task_key} "
                    f"(start={task.start_time}, "
                    f"now_line_time={self.now_line_time})"
                )
                continue

            # Only consider tasks that overlap local tasks
            for local_task in local_tasks:
                if self._tasks_overlap(task, local_task):
                    released.append(task)
                    break

        result = self._unique_tasks(released)

        print("LOCAL TASKS:", len(local_tasks))
        print("TARGET DATE:", get_date)
        print("RELEASED BEFORE UNIQUE:", len(released))
        print("FINAL RESULT:", len(result))

        return result

#     def _get_temporally_overlapping_tasks(
#     self,
#     local_tasks,
#     employees
# ):
#         released = []

#         if not local_tasks:
#             return released

#         local_task_keys = {
#             task.task_key
#             for task in local_tasks
#         }

#         get_date = local_tasks[-1].date
#         get_tasks= self._get_all_tasks_on_day(list(local_tasks))
#         # get_tasks_keys= {task.task_key for task in get_tasks}

#         for task in get_tasks:
#             if task.task_key in local_task_keys:
#                 continue
#             for local_task in local_tasks:
#                  if self._tasks_overlap(task,local_task):
#                     released.append(task)
#                     break


#         print("LOCAL TASKS:", len(local_tasks))
#         print("LOCAL KEYS:", local_task_keys)
#         print("TARGET DATE:", get_date)
#         print("EMPLOYEES:", len(employees))

# #         # for employee in employees:
# #         #     print("EMPLOYEE:", employee)

# #         #     for roster in employee.rosters.values():
# #         #         print("ROSTER TASKS:", len(roster.tasks))

# #         #         for existing_task in roster.tasks:

# #         #             print(
# #         #                 "CHECKING:",
# #         #                 existing_task.task_key,
# #         #                 "date=", existing_task.date
# #         #             )

# #         #             if existing_task.date != get_date:
# #         #                 print("  -> SKIP: date mismatch")
# #         #                 continue

# #         #             if existing_task.task_key in local_task_keys:
# #         #                 print("  -> SKIP: already local task")
# #         #                 continue

# #         #             for local_task in local_tasks:

# #         #                 overlap = self._tasks_overlap(
# #         #                     existing_task,
# #         #                     local_task
# #         #                 )

# #         #                 print(
# #         #                     "  OVERLAP:",
# #         #                     existing_task.task_key,
# #         #                     local_task.task_key,
# #         #                     overlap
# #         #                 )

# #         #                 if overlap:
# #         #                     print("  -> APPENDING:", existing_task.task_key)

# #         #                     released.append(existing_task)
# #         #                     break

#         result = self._unique_tasks(released)

#         print("RELEASED BEFORE UNIQUE:", len(released))
#         print("FINAL RESULT:", len(result))

#         return result

    def _release_tasks_from_rosters(self,released_tasks,employees):
   

        released_keys = {
        task.task_key
        for task in released_tasks
        }

        removed_assignments = []

        for employee in employees:

            for roster in employee.rosters.values():

                remaining_tasks = []

                for task in roster.tasks:

                    if task.task_key in released_keys:

                        removed_assignments.append(
                        (employee, roster, task)
                        )

                    else:
                        remaining_tasks.append(task)

                roster.tasks = remaining_tasks

        return removed_assignments


    def _restore_tasks_to_rosters(self,removed_assignments):
   
        for employee, roster, task in removed_assignments:
            if not any (existing_task.task_key == task.task_key for existing_task in roster.tasks):
                roster.tasks.append(task)


    # =========================================================
    # CANDIDATE EMPLOYEES
    # =========================================================

    def _get_candidate_employees(
        self,
        tasks,
        employees,
        recall=False,
        assigned_employees=None
    ):
        """
        Candidate filtering is deliberately kept relatively
        broad.

        Skill and employee-task feasibility should ultimately
        be handled by CostEstimator because feasibility is
        pair-specific.
        """

        candidates = []

        if assigned_employees is None:
                assigned_employees = set()

        for employee in employees:

            if employee.employee_id in assigned_employees:
                continue

            # ---------------------------------------------
            # Employees on leave
            # ---------------------------------------------
            roster_day= employee.rosters.get(tasks[0].date)
            if roster_day is not None and roster_day.is_off and not recall:
                continue
            # on_leave = getattr(
            #     employee,
            #     "on_leave",
            #     False
            # )

            # if on_leave and not recall:
            #     continue

            # ---------------------------------------------
            # Employee must have at least one potentially
            # relevant skill.
            # ---------------------------------------------

            if not self._has_any_relevant_skill(
                employee,
                tasks
            ):
                continue

            candidates.append(employee)

        return candidates

    # =========================================================
    # SKILLS
    # =========================================================

    def _has_any_relevant_skill(
        self,
        employee,
        tasks
    ):

        employee_skills = set(
            getattr(
                employee,
                "skills",
                set()
            )
        )

        # If no skill information is available,
        # do not eliminate the employee here.
        if not employee_skills:
            return True

        for task in tasks:

            required_skills = set(
                getattr(
                    task,
                    "required_skills",
                    set()
                )
            )

            if not required_skills:
                return True

            if employee_skills.intersection(
                required_skills
            ):
                return True

        return False

    # =========================================================
    # TIME
    # =========================================================

    def _is_temporally_close(
        self,
        task,
        seed
    ):

        task_time = datetime.strptime(task.start_time, "%H:%M").time()
        seed_time = datetime.strptime(seed.start_time, "%H:%M").time()

        difference = abs(
                task_time.hour * 60 + task_time.minute
                - (seed_time.hour * 60 + seed_time.minute)
            )

        return (
            difference
            <= self.temporal_window
        )

    def _tasks_overlap(
        self,
        task1,
        task2
    ):

        return (
            task1.start_time < task2.end_time
            and
            task2.start_time < task1.end_time
        )

    # =========================================================
    # PHYSICAL
    # =========================================================

    def _is_physically_close(
        self,
        task,
        seed
    ):

        if (
            not hasattr(task, "location")
            or
            not hasattr(seed, "location")
        ):
            return True

        if (
            task.location is None
            or
            seed.location is None
        ):
            return True

        distance = self._distance(
            task.location,
            seed.location
        )

        return distance <= self.physical_radius

    def _distance(
        self,
        location1,
        location2
    ):
        """
        Replace this with  existing distance function.
        """

        return 0

    # =========================================================
    # UTILITIES
    # =========================================================

    def _combine_tasks(
        self,
        tasks1,
        tasks2
    ):

        return self._unique_tasks(
            list(tasks1) + list(tasks2)
        )

    def _unique_tasks(self, tasks):

        seen = set()
        result = []

        for task in tasks:

            if task.task_key not in seen:

                seen.add(task.task_key)
                result.append(task)

        return result

    def _get_all_tasks_on_day(self, seed_tasks):
        all_tasks_on_day = []
        seed_task = seed_tasks[0]
        seed_task_day = seed_task.date
        for task in  self.schedule_data.tasks.values():
            if (task.date == seed_task_day):
                all_tasks_on_day.append(task)
                
        return all_tasks_on_day
            