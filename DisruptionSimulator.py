class DisruptionSimulator:

    def __init__(self, schedule_data):
        self.schedule_data = schedule_data

    def remove_task(self, task_key):
        """
        Simulate a task becoming unassigned.
        """

        task = self._find_task(task_key)

        original_employee = None

        for employee in self.schedule_data.employees.values():

            for roster in employee.rosters.values():

                for existing_task in roster.tasks:

                    if existing_task.task_key == task_key:

                        original_employee = employee

                        roster.tasks.remove(existing_task)

                        task.status = "UNASSIGNED"
                        task.blocked_employee = employee.employee_id

                        return task, original_employee

        raise ValueError(
            f"Task {task_key} not found in roster"
        )

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

    def _find_task(self, task_key):

        for task in self.schedule_data.tasks.values():

            if task.task_key == task_key:
                return task

        raise ValueError(
            f"Task {task_key} not found"
        )