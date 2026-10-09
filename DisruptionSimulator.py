# class DisruptionSimulator:

#     def __init__(self, schedule_data):
#         self.schedule_data = schedule_data

#     def remove_task(self, task_key):
#         """
#         Simulate a task becoming unassigned.
#         """

#         task = self._find_task(task_key)

#         original_employee = None

#         for employee in self.schedule_data.employees.values():

#             for roster in employee.rosters.values():

#                 for existing_task in roster.tasks:

#                     if existing_task.task_key == task_key:

#                         original_employee = employee

#                         roster.tasks.remove(existing_task)

#                         task.status = "UNASSIGNED"
#                         task.blocked_employee = employee.employee_id

#                         return task, original_employee

#         raise ValueError(
#             f"Task {task_key} not found in roster"
#         )

#     def sick_leave(self, employee_id):

#         employee = (
#             self.schedule_data
#             .employees[employee_id]
#         )

#         employee.on_leave = True

#         disrupted_tasks = []

#         for roster in employee.rosters.values():

#             disrupted_tasks.extend(
#                 roster.tasks
#             )

#             roster.tasks.clear()

#         return disrupted_tasks

#     def _find_task(self, task_key):

#         for task in self.schedule_data.tasks.values():

#             if task.task_key == task_key:
#                 return task

#         raise ValueError(
#             f"Task {task_key} not found"
#         )


class DisruptionSimulator:
    """
    Applies disruptions to a loaded ScheduleData object.

    Two disruption families are supported:

    1. Employee / task-removal disruption
       remove_task(...)   -> the task is taken away from its employee and the
                             caller normally blocks that employee from taking
                             it back (task.blocked_employee).

    2. Task-delay disruption
       delay_task(...)    -> the task still exists but its start and end time
                             are pushed later by `delay_minutes`. Nobody is
                             blocked, so the original employee may well be able
                             to perform the delayed task.
    """

    def __init__(self, schedule_data):
        self.schedule_data = schedule_data

    # =========================================================
    # EMPLOYEE / REMOVAL DISRUPTION
    # =========================================================

    def remove_task(self, task_key, employee_id=None):
        """
        Simulate a task becoming unassigned.

        employee_id (optional) restricts the search to one employee's roster.
        Useful when several employees are disrupted at the same time.
        """

        task = self._find_task(task_key)

        employee, roster = self._find_owner(task_key, employee_id)

        roster.tasks.remove(
            next(t for t in roster.tasks if t.task_key == task_key)
        )

        task.status = "UNASSIGNED"
        task.blocked_employee = employee.employee_id
        task.original_employee = employee.employee_id

        return task, employee

    def remove_employee_tasks(self, employee_ids, date, task_keys=None):
        """
        Remove tasks of several employees on one date.

        Returns a list of (task, original_employee) tuples.
        If task_keys is given, only those tasks are removed.
        """

        disrupted = []

        for employee_id in employee_ids:

            employee = self.schedule_data.employees[employee_id]
            roster = employee.rosters.get(date)

            if roster is None:
                continue

            keys = list(dict.fromkeys(t.task_key for t in roster.tasks))

            for key in keys:
                if task_keys is not None and key not in task_keys:
                    continue

                disrupted.append(self.remove_task(key, employee_id))

        return disrupted

    def mark_absent(self, employee_id, date):
        """
        Employee is not coming in on `date`: they may not be assigned ANY
        task on that date (not just the ones that were removed from them).
        """

        employee = self.schedule_data.employees[employee_id]

        if not hasattr(employee, "absent_dates"):
            employee.absent_dates = set()

        employee.absent_dates.add(date)

    def fully_disrupted_employees(self, task_keys, date, employee_ids=None):
        """
        Employees whose ENTIRE task list on `date` is contained in
        `task_keys` - i.e. everything they were doing is disrupted, which
        means they are not coming in. Call this BEFORE removing the tasks.
        """

        task_keys = set(task_keys)
        ids = employee_ids or list(self.schedule_data.employees.keys())
        absent = []

        for employee_id in ids:
            roster = self.schedule_data.employees[employee_id].rosters.get(date)
            if roster is None or not roster.tasks:
                continue
            if {t.task_key for t in roster.tasks} <= task_keys:
                absent.append(employee_id)

        return absent

    # =========================================================
    # DELAY DISRUPTION
    # =========================================================

    def delay_task(self, task_key, delay_minutes, employee_id=None):
        """
        Delay a task: start_time and end_time both move later by
        `delay_minutes`.

        * The delay is always applied relative to the ORIGINAL times, so
          delaying the same task twice replaces the first delay instead of
          stacking on top of it.
        * The task is lifted out of its current roster so that the recovery
          engine can decide who performs it at its new time. The original
          employee is NOT blocked.
        * The delay is clamped so the task does not run past 23:59 (task
          times are plain HH:MM strings on a single date).

        Returns (task, original_employee, effective_delay_minutes).
        original_employee is None if the task was not in any roster.
        """

        task = self._find_task(task_key)

        # Remember the original window the first time the task is delayed
        if not hasattr(task, "original_start_time"):
            task.original_start_time = task.start_time
            task.original_end_time = task.end_time

        orig_start = self._to_minutes(task.original_start_time)
        orig_end = self._to_minutes(task.original_end_time)

        # Clamp so that the delayed task stays inside the day
        max_delay = max(0, (23 * 60 + 59) - max(orig_start, orig_end))
        effective_delay = max(0, min(int(delay_minutes), max_delay))

        # Take the task out of whichever roster currently holds it
        original_employee = None
        try:
            original_employee, roster = self._find_owner(
                task_key, employee_id
            )
            roster.tasks = [
                t for t in roster.tasks if t.task_key != task_key
            ]
        except ValueError:
            pass

        task.start_time = self._to_hhmm(orig_start + effective_delay)
        task.end_time = self._to_hhmm(orig_end + effective_delay)

        task.delay_minutes = effective_delay
        task.status = "DELAYED"

        # Delay disruption never blocks anyone
        task.blocked_employee = None
        task.original_employee = (
            original_employee.employee_id
            if original_employee is not None
            else getattr(task, "original_employee", None)
        )

        return task, original_employee, effective_delay

    def delay_tasks(self, task_keys, delay_minutes):
        """
        Delay several tasks (wherever they currently sit).

        Returns a list of (task, original_employee, effective_delay).
        """

        return [
            self.delay_task(key, delay_minutes)
            for key in task_keys
        ]

    # =========================================================
    # SICK LEAVE (unchanged)
    # =========================================================

    def sick_leave(self, employee_id):

        employee = (
            self.schedule_data
            .employees[employee_id]
        )

        employee.on_leave = True

        disrupted_tasks = []

        for roster in employee.rosters.values():

            disrupted_tasks.extend(
                roster.tasks
            )

            roster.tasks.clear()

        return disrupted_tasks

    # =========================================================
    # HELPERS
    # =========================================================

    def _find_task(self, task_key):

        for task in self.schedule_data.tasks.values():

            if task.task_key == task_key:
                return task

        raise ValueError(
            f"Task {task_key} not found"
        )

    def _find_owner(self, task_key, employee_id=None):
        """
        Locate (employee, roster_day) currently holding the task.
        """

        if employee_id is not None:
            employees = [self.schedule_data.employees[employee_id]]
        else:
            employees = self.schedule_data.employees.values()

        for employee in employees:
            for roster in employee.rosters.values():
                for existing_task in roster.tasks:
                    if existing_task.task_key == task_key:
                        return employee, roster

        raise ValueError(
            f"Task {task_key} not found in roster"
        )

    @staticmethod
    def _to_minutes(hhmm):
        hours, minutes = hhmm.split(":")
        return int(hours) * 60 + int(minutes)

    @staticmethod
    def _to_hhmm(total_minutes):
        return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"
