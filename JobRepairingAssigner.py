from scipy.optimize import linear_sum_assignment
from CostEstimator import EmployeeCostEstimator
import InputClasses as InputClasses

from dataclasses import dataclass, field
import numpy as np


BIG_M = 1_000_000
INF = float("inf")


@dataclass
class AssignmentResult:

    feasible: bool

    assignments: list = field(default_factory=list)

    unassigned_tasks: list = field(default_factory=list)

    total_cost: float = 0.0


@dataclass
class JobAssigner:

    scheduleData: InputClasses.ScheduleData
    unassigned_tasks: list[InputClasses.Task]

    estimator: EmployeeCostEstimator = field(
        default_factory=EmployeeCostEstimator
    )

    def assign_jobs(
        self,
        employees,
        tasks,
        neighborhood
    ):

        # -----------------------------------------------------
        # Build cost matrix
        # -----------------------------------------------------

        cost_matrix = self.estimator.build_cost_matrix(
            neighborhood=neighborhood
        )

        # -----------------------------------------------------
        # Basic impossibility check
        # -----------------------------------------------------

        #if len(tasks) > len(employees):

            #return AssignmentResult(
                #feasible=False,
                #assignments=[],
                #unassigned_tasks=list(tasks),
                #total_cost=INF
           # )

        # -----------------------------------------------------
        # Hungarian
        # -----------------------------------------------------

        row_ind, col_ind = linear_sum_assignment(
            cost_matrix
        )

        # -----------------------------------------------------
        # Convert result
        # -----------------------------------------------------

        result = self.results_to_assignments(
        row_ind=row_ind,
        col_ind=col_ind,
        cost_matrix=cost_matrix,
        employees=employees,
        tasks=tasks
        )

        if result.feasible:
            self._apply_assignments(result.assignments)


        return result

    # =========================================================
    # Convert Hungarian result
    # =========================================================

    def results_to_assignments(
        self,
        row_ind,
        col_ind,
        cost_matrix,
        employees,
        tasks
    ):

        assignments = []

        assigned_task_indices = set()

        total_cost = 0.0

        # -----------------------------------------------------
        # Examine every Hungarian assignment
        # -----------------------------------------------------

        for r, c in zip(row_ind, col_ind):

            cost = cost_matrix[r, c]

            # ---------------------------------------------
            # Infeasible employee-task pair
            # ---------------------------------------------

            if (not np.isfinite(cost)) or (cost >= BIG_M):
                continue

            employee = employees[r]
            task = tasks[c]

            assignments.append({
                "employee": employee,
                "task": task,
                "cost": cost
            })

            assigned_task_indices.add(c)

            total_cost += cost

        # -----------------------------------------------------
        # Determine which tasks were not successfully assigned
        # -----------------------------------------------------

        unassigned_tasks = [
            task
            for i, task in enumerate(tasks)
            if i not in assigned_task_indices
        ]

        # -----------------------------------------------------
        # Feasibility
        #
        # A repair is feasible only if EVERY task has been
        # assigned to a finite-cost employee.
        # -----------------------------------------------------

        feasible = (
            len(unassigned_tasks) == 0
        )

        return AssignmentResult(
            feasible=feasible,
            assignments=assignments,
            unassigned_tasks=unassigned_tasks,
            total_cost=total_cost
        )


    def _apply_assignments(self,assignments):
    

        for assignment in assignments:

            employee = assignment["employee"]
            task = assignment["task"]

            roster = employee.rosters.get(task.date)

            if roster is not None:
                roster.tasks.append(task)