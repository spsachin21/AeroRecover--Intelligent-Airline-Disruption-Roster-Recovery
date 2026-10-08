from dataclasses import dataclass

import InputClasses
import NeighborhoodManager
import JobRepairingAssigner
import CostEstimator
import Neighborhood

@dataclass
class ScheduleRecovery:

    schedule_data: InputClasses.ScheduleData
    neighborhood_manager: NeighborhoodManager
    cost_estimator: CostEstimator.EmployeeCostEstimator
    
    
    

    # =========================================================
    # MAIN RECOVERY
    # =========================================================

    def recover(
        self,
        disrupted_tasks,
        employees
        ):
        
         # neighborhood search
        # if unassigned is None:
        #     seed_tasks = list(disrupted_tasks)
        # else:
        #     seed_tasks = list(disrupted_tasks)    

        seed_tasks = list(disrupted_tasks)
        default= seed_tasks.copy()
        assigned_employees = set()

        for level in range(6):


            neighborhood = (
                self.neighborhood_manager
                .create_next_neighborhood(
                    level=level,
                    seed_tasks=seed_tasks,
                    employees=employees,
                    assigned_employees=assigned_employees
                )
            )

            if neighborhood is None:
                break

            print(f"\n========== {neighborhood.name} ==========")
            print(f"Level: {neighborhood.level}")
            print(f"Seed tasks: {len(neighborhood.seed_tasks)}")
            print(f"Local tasks: {len(neighborhood.tasks) - len(neighborhood.released_tasks)}")
            print(f"Released tasks: {len(neighborhood.released_tasks)}")
            print(f"Repair bundle: {len(neighborhood.tasks)}")
            print(f"Candidate employees: {len(neighborhood.candidate_employees)}")
            print(
                f"Overtime: {neighborhood.overtime_enabled}, "
                f"Recall: {neighborhood.recall_enabled}, "
                f"Temporal destruction: {neighborhood.temporal_destruction}"
                )           
            
            #self.cost_estimator.build_cost_matrix(neighborhood=neighborhood)
            self.assigner = JobRepairingAssigner.JobAssigner(scheduleData=self.schedule_data, unassigned_tasks=neighborhood.tasks, estimator=self.cost_estimator)

            result = self.assigner.assign_jobs(
                employees=neighborhood.candidate_employees,
                tasks=neighborhood.tasks,
                neighborhood=neighborhood
            )

            for assignment in result.assignments:
                assigned_employees.add(
                    assignment["employee"].employee_id)

            if result.feasible:

                print(
                    f"{neighborhood.name}: "
                    f"Recovery successful"
                )

            
                return result

            print(
                f"{neighborhood.name}: "
                f"Recovery failed. "
                f"Unassigned tasks: "
                f"{len(result.unassigned_tasks)}"
            )

            # seed_tasks = result.unassigned_tasks
            seed_tasks = default

            if neighborhood.temporal_destruction and neighborhood.removed_assignments:
                self.neighborhood_manager._restore_tasks_to_rosters(neighborhood.removed_assignments)

            print(
                f"Recovery failed: {len(result.unassigned_tasks)} "
                "tasks could not be assigned."
                f"unassigned tasks: {[task.task_key for task in result.unassigned_tasks]}"
            )

            print("\nEmployee Assignments (Assigned):")
            if len(result.assignments)==0:
                print("No assignement")
            else:        
                for assignment in result.assignments:
                    task = assignment["task"]
                    employee = assignment["employee"]

                    print(
                        f"Employee {employee.employee_id} -> "
                        f"Task {task.task_key} "
                        f"(Cost: {assignment['cost']})"
                    )

        return None
        