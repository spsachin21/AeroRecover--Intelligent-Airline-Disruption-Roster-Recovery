from dataclasses import dataclass
from typing import Set, Optional
import numpy as np
from scipy.fftpack import shift
import Neighborhood

INF = float("inf")
BIG_M = 1_000_000

# ============================================================
# Configuration
# ============================================================

@dataclass
class CostConfig:

    # --------------------------------------------------------
    # Basic assignment costs
    # --------------------------------------------------------
    existing_job_cost: float = 0.0

    # --------------------------------------------------------
    # Skill costs
    # --------------------------------------------------------
    missing_skill_penalty: float = 1000.0

    # --------------------------------------------------------
    # Distance
    # --------------------------------------------------------
    distance_cost: float = 1.0
    max_distance_km: float = 100.0

    # --------------------------------------------------------
    # Flexibility costs
    # --------------------------------------------------------
    overtime_cost: float = 500.0
    max_overtime_minutes: float = 120.0
    recall_cost: float = 1000.0

    # --------------------------------------------------------
    # Feasibility
    # --------------------------------------------------------
    allow_skill_penalty: bool = False


# ============================================================
# Result of one employee-task evaluation
# ============================================================

@dataclass
class EmployeeJobCost:

    employee_id: str
    task_key: str

    total_cost: float

    # Cost components
    existing_job_cost: float
    skill_cost: float
    distance_cost: float

    overtime_cost: float
    recall_cost: float

    # Information
    distance: float

    has_required_skills: bool
    already_assigned: bool

    is_overtime: bool
    is_recall: bool

    feasible: bool

    assignment_mode: str


# ============================================================
# Cost Estimator
# ============================================================

class EmployeeCostEstimator:

    def __init__(
        self,
        config: Optional[CostConfig] = None
    ):
        self.config = config or CostConfig()

    # ========================================================
    # Main estimator
    # ========================================================

    def estimate(
        self,
        employee,
        task,
        neighborhood,
        employee_skills: Set[str],
        required_skills: Set[str],
        employee_location: tuple[float, float] | None = None,
        task_location: tuple[float, float] | None = None,
    ) -> EmployeeJobCost:

        # ----------------------------------------------------
        # 1. Existing assignment
        # ----------------------------------------------------

        already_assigned = self._is_already_assigned(
            employee,
            task
        )

        existing_cost = (
            0.0
            if already_assigned
            else self.config.existing_job_cost
        )

        # ----------------------------------------------------
        # 2. Skills
        # ----------------------------------------------------

        missing_skills = required_skills - employee_skills

        has_required_skills = (
            len(missing_skills) == 0
        )

        skill_cost = (
            0.0
            if has_required_skills
            else (
                self.config.missing_skill_penalty
                * len(missing_skills)
            )
        )

        # ----------------------------------------------------
        # 3. Distance
        # ----------------------------------------------------

        distance = self._get_distance(
            employee_location,
            task_location
        )

        distance_cost = (
            distance *
            self.config.distance_cost
        )

        # ----------------------------------------------------
        # 4. Determine employee assignment mode
        # ----------------------------------------------------

        mode = self._determine_assignment_mode(
            employee=employee,
            task=task,
            neighborhood=neighborhood
        )

        is_overtime = mode == "overtime"
        is_recall = mode == "recall"

        # ----------------------------------------------------
        # 5. Flexibility costs
        # ----------------------------------------------------

        overtime_cost = (
            self.config.overtime_cost
            if is_overtime
            else 0.0
        )

        recall_cost = (
            self.config.recall_cost
            if is_recall
            else 0.0
        )

        # ----------------------------------------------------
        # 6. Feasibility
        # ----------------------------------------------------

        feasible = self._check_feasibility(
            employee=employee,
            task=task,
            neighborhood=neighborhood,
            has_required_skills=has_required_skills,
            distance=distance,
            mode=mode
        )

        # ----------------------------------------------------
        # 7. Total cost
        # ----------------------------------------------------

        total_cost = (
            existing_cost
            + skill_cost
            + distance_cost
            + overtime_cost
            + recall_cost
        )

        if not feasible:
            total_cost = BIG_M

        # ----------------------------------------------------
        # 8. Return
        # ----------------------------------------------------

        return EmployeeJobCost(

            employee_id=employee.employee_id,
            task_key=task.task_key,

            total_cost=total_cost,

            existing_job_cost=existing_cost,
            skill_cost=skill_cost,
            distance_cost=distance_cost,

            overtime_cost=overtime_cost,
            recall_cost=recall_cost,

            distance=distance,

            has_required_skills=has_required_skills,
            already_assigned=already_assigned,

            is_overtime=is_overtime,
            is_recall=is_recall,

            feasible=feasible,

            assignment_mode=mode
        )

    # ========================================================
    # Assignment mode
    # ========================================================

    # def _determine_assignment_mode(
    #     self,
    #     employee,
    #     task,
    #     neighborhood
    # ) -> str:

    #     if getattr(task, "blocked_employee", None) == employee.employee_id:
    #         print(f"{employee.employee_id}: blocked")
    #         return "infeasible"

    #     # ----------------------------------------------------
    #     # Employee currently on leave
    #     # ----------------------------------------------------

    #     if self._is_on_leave(employee):

    #         if neighborhood.recall_enabled:

    #             return "recall"

    #         print(f"{employee.employee_id}: on leave")
    #         return "infeasible"

    #     # ----------------------------------------------------
    #     # Normal employee
    #     # ----------------------------------------------------

    #     if self._fits_normal_shift(employee, task):

    #         return "regular"

    #     # ----------------------------------------------------
    #     # Assignment requires extended shift
    #     # ----------------------------------------------------

    #     if neighborhood.overtime_enabled:

    #         if self._can_work_overtime(employee, task):

    #             return "overtime"
    #     # ----------------------------------------------------
    #     # Recall: OFF employee
    #     # ----------------------------------------------------
  
    #     try:
    #         shift = employee.rosters[task.date].shift
    #     except KeyError:
    #         return "infeasible"

    #     if shift is None and neighborhood.recall_enabled:
    #         return "recall"   

    #     # ----------------------------------------------------
    #     # No allowed relaxation
    #     # ----------------------------------------------------  
        
    #     try:
    #         shift = employee.rosters[task.date].shift
    #     except KeyError:
    #         print(f"{employee.employee_id}: no roster for {task.date}")
    #         return "infeasible"

    #     if shift is None:
    #         print(f"{employee.employee_id}: OFF day")
    #         return "infeasible"

    #     if task.start < shift.start:
    #         minutes = (shift.start.hour*60 + shift.start.minute) - (task.start.hour*60 + task.start.minute)
    #         print(f"{employee.employee_id}: task starts {minutes} min before shift ({shift.start.strftime('%H:%M')})")

    #     elif task.end > shift.end:
    #         minutes = (task.end.hour*60 + task.end.minute) - (shift.end.hour*60 + shift.end.minute)
    #         print(f"{employee.employee_id}: task ends {minutes} min after shift ({shift.end.strftime('%H:%M')})")

    #     else:
    #         print(f"{employee.employee_id}: task does not fit scheduled shift")

    #     return "infeasible"


    def _determine_assignment_mode(
        self,
        employee,
        task,
        neighborhood
    ) -> str:

        # =========================================================
        # BLOCKED EMPLOYEE
        # =========================================================

        if getattr(task, "blocked_employee", None) == employee.employee_id:

            print(
                f"{employee.employee_id}: blocked"
            )

            return "infeasible"


        # =========================================================
        # EMPLOYEE ON LEAVE
        # =========================================================

        if self._is_on_leave(employee):

            if neighborhood.recall_enabled:
                return "recall"

            print(
                f"{employee.employee_id}: on leave"
            )

            return "infeasible"


        # =========================================================
        # GET SHIFT
        # =========================================================

        try:

            roster = employee.rosters[task.date]
            shift = roster.shift

        except KeyError:

            print(
                f"{employee.employee_id}: "
                f"no roster for {task.date}"
            )

            return "infeasible"


        # =========================================================
        # OFF DAY
        # =========================================================

        if shift is None:

            if neighborhood.recall_enabled:
                return "recall"

            print(
                f"{employee.employee_id}: OFF day"
            )

            return "infeasible"


        # =========================================================
        # CONVERT HH:MM / datetime.time TO MINUTES
        # =========================================================

        shift_start = (
            shift.start.hour * 60
            + shift.start.minute
        )

        shift_end = (
            shift.end.hour * 60
            + shift.end.minute
        )

        task_start = (
            task.start.hour * 60
            + task.start.minute
        )

        task_end = (
            task.end.hour * 60
            + task.end.minute
        )


        # =========================================================
        # HANDLE CROSS-MIDNIGHT SHIFT
        #
        # Example:
        #     22:00 -> 06:00
        #
        # Since:
        #     06:00 <= 22:00
        #
        # shift_end becomes:
        #     06:00 + 1440 = 1500
        #
        # =========================================================

        shift_crosses_midnight = (
            shift_end <= shift_start
        )

        if shift_crosses_midnight:

            shift_end += 24 * 60


        # =========================================================
        # HANDLE CROSS-MIDNIGHT TASK
        #
        # Example:
        #     23:00 -> 01:00
        #
        # =========================================================

        task_crosses_midnight = (
            task_end <= task_start
        )

        if task_crosses_midnight:

            task_end += 24 * 60


        # =========================================================
        # IMPORTANT:
        #
        # If the shift crosses midnight, a task occurring after
        # midnight may also need to be moved onto the next day.
        #
        # Example:
        #
        # Shift:
        #       22:00 -> 06:00
        #
        # Task:
        #       01:00 -> 02:00
        #
        # Raw:
        #       shift = 1320 -> 1800
        #       task  = 0060 -> 0120
        #
        # The task must become:
        #       1500 -> 1560
        #
        # =========================================================

        if shift_crosses_midnight:

            # Task is on the after-midnight portion
            if task_start < (
                shift_end - 24 * 60
            ):

                task_start += 24 * 60
                task_end += 24 * 60


        # =========================================================
        # NORMAL SHIFT FIT
        # =========================================================

        if (
            task_start >= shift_start
            and
            task_end <= shift_end
        ):

            return "regular"


        # =========================================================
        # OVERTIME
        # =========================================================

        if neighborhood.overtime_enabled:

            if self._can_work_overtime(
                employee,
                task
            ):

                return "overtime"


        # =========================================================
        # RECALL
        # =========================================================
        #
        # Recall applies to an OFF employee.
        #
        # The OFF case is already handled above, but we keep the
        # fallback here for compatibility with your existing logic.
        # =========================================================

        if shift is None:

            if neighborhood.recall_enabled:
                return "recall"

            return "infeasible"


        # =========================================================
        # TASK STARTS BEFORE SHIFT
        # =========================================================

        if task_start < shift_start:

            minutes = (
                shift_start
                - task_start
            )

            print(
                f"{employee.employee_id}: "
                f"task starts {minutes} min before shift "
                f"({shift.start.strftime('%H:%M')})"
            )

            return "infeasible"


        # =========================================================
        # TASK ENDS AFTER SHIFT
        # =========================================================

        if task_end > shift_end:

            minutes = (
                task_end
                - shift_end
            )

            print(
                f"{employee.employee_id}: "
                f"task ends {minutes} min after shift "
                f"({shift.end.strftime('%H:%M')})"
            )

            return "infeasible"


        # =========================================================
        # DOES NOT FIT
        # =========================================================

        print(
            f"{employee.employee_id}: "
            f"task does not fit scheduled shift"
        )

        return "infeasible"

            

    # ========================================================
    # Feasibility
    # ========================================================

    def _check_feasibility(
        self,
        employee,
        task,
        neighborhood,
        has_required_skills,
        distance,
        mode
    ) -> bool:

        # Skills
        #if not has_required_skills:
            #if not self.config.allow_skill_penalty:
                #return False

        # Distance
        if distance > self.config.max_distance_km:
            return False

        # Assignment mode
        if mode == "infeasible":
            return False

        return True

    # ========================================================
    # Existing assignment
    # ========================================================

    def _is_already_assigned(
        self,
        employee,
        task
    ) -> bool:

        for day in employee.rosters.values():

            for existing_task in day.tasks:

                if existing_task.task_key == task.task_key:
                    return True

        return False

    # ========================================================
    # Leave
    # ========================================================

    def _is_on_leave(self, employee) -> bool:

        return getattr(
            employee,
            "on_leave",
            False
        )

    # ========================================================
    # Normal shift
    # ========================================================

    def _fits_normal_shift(
        self,
        employee,
        task
    ) -> bool:

        #get shift for task day
        date = task.date
        try:
            employee_shift = employee.rosters[date].shift
            if (employee_shift is not None):
                fits_normal_shift = (task.start >= employee_shift.start and task.end <= employee_shift.end)
                return fits_normal_shift
            return False
        except KeyError:
            return False
        
        


    # ========================================================
    # Overtime eligibility
    # ========================================================

    def _can_work_overtime(self,employee,task) -> bool:

        try:
            shift = employee.rosters[task.date].shift
        except KeyError:
            return False

        if shift is None:
            return False

        shift_start = shift.start.hour * 60 + shift.start.minute
        shift_end = shift.end.hour * 60 + shift.end.minute

        task_start = task.start.hour * 60 + task.start.minute
        task_end = task.end.hour * 60 + task.end.minute

    # Handle overnight shifts
        if shift_end <= shift_start:
            shift_end += 24 * 60

        if task_end <= task_start:
            task_end += 24 * 60

        overtime_before = max(0, shift_start - task_start)
        overtime_after = max(0, task_end - shift_end)

        total_overtime = overtime_before + overtime_after

        return total_overtime <= 120

    # ========================================================
    # Distance
    # ========================================================

    def _get_distance(
        self,
        employee_location,
        task_location
    ) -> float:
        return 0.0
        
        

    # ========================================================
    # Cost matrix
    # ========================================================

    def build_cost_matrix(
        self,
        neighborhood:Neighborhood.Neighborhood
    ):
        employees = neighborhood.candidate_employees
        tasks = neighborhood.tasks
        matrix = np.full(
            (len(employees), len(tasks)),
            BIG_M,
            dtype=float
        )

        for i, employee in enumerate(employees):

            for j, task in enumerate(tasks):

                cost = self.estimate(
                employee=employee,
                task=task,
                neighborhood=neighborhood,
                employee_skills=set(getattr(employee, "skills", [])),
                required_skills=set(getattr(task, "required_skills", [])),
                employee_location=getattr(employee, "location", None),
                task_location=getattr(task, "location", None),
                )

                print(
                    f"{neighborhood.name} | "
                    f"Emp={employee.employee_id} | "
                    f"Mode={cost.assignment_mode} | "
                    f"Feasible={cost.feasible} | "
                    f"Cost={cost.total_cost}"
    
                    )

                if cost.feasible:
                    matrix[i, j] = cost.total_cost

        return matrix