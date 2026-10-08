from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from input_classes import (
    Shift,
    Task,
    RosterDay,
    Employee,
    ResourceRoster,
    ScheduleData,
)



@dataclass
class GraphNode:
    """
    Generic node in the improvement graph.

    Types:
        TASK      -> existing task
        ADDITION  -> possible assignment of a task to an employee
        REMOVAL   -> release of a task from its current employee
    """

    node_id: str
    node_type: str

    task_key: Optional[str] = None
    employee_id: Optional[str] = None

    # # For an addition node, this identifies the task currently
    # # occupying the target employee/time position, if any.
    # displaced_task_key: Optional[str] = None

    attributes: Dict = field(default_factory=dict)



@dataclass
class GraphArc:
    """
    Directed arc in the improvement graph.

    Arc types:
        ADDITION
        REMOVAL
        EXCHANGE
        CYCLE
    """

    from_node: GraphNode
    to_node: GraphNode
    arc_type: str

    # task_key: Optional[str] = None
    # employee_id: Optional[str] = None
    # displaced_task_key: Optional[str] = None

    attributes: Dict = field(default_factory=dict)




class ImprovementGraph:

    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}
        self.arcs: List[GraphArc] = []

        # Useful indexes
        self.task_nodes: Dict[str, str] = {}
        self.addition_nodes: Dict[Tuple[str, str], str] = {}
        self.removal_nodes: Dict[Tuple[str, str], str] = {}

    # --------------------------------------------------------
    # NODE METHODS
    # --------------------------------------------------------

    def add_node(self, node: GraphNode):

        if node.node_id not in self.nodes:
            self.nodes[node.node_id] = node

    def add_task_node(self, task: Task):

        node_id = f"T:{task.task_key}"

        node = GraphNode(
            node_id=node_id,
            node_type="TASK",
            task_key=task.task_key,
        )

        self.add_node(node)

        self.task_nodes[task.task_key] = node_id

        return node_id

    def add_addition_node(
        self,
        employee_id: str,
        displaced_task_key: Optional[str] = None,
    ):

        node_id = f"A:{employee_id}"

        node = GraphNode(
            node_id=node_id,
            node_type="ADDITION",
            employee_id=employee_id,
            displaced_task_key=displaced_task_key,
        )

        self.add_node(node)

        self.addition_nodes[employee_id] = node_id

        return node_id

    def add_removal_node(
        self,
        task_key: str,
        employee_id: str,
    ):

        node_id = f"R:{task_key}:{employee_id}"

        node = GraphNode(
            node_id=node_id,
            node_type="REMOVAL",
        )

        self.add_node(node)

        self.removal_nodes[
            node_id
        ] = node_id

        return node_id

    # --------------------------------------------------------
    # ARC METHODS
    # --------------------------------------------------------

    def add_arc(self, arc: GraphArc):
        self.arcs.append(arc)

    def add_addition_arc(
        self,
        task_node: GraphNode,
        addition_node:GraphNode,
        displaced_task_key: Optional[str] = None,
    ):
        self.add_arc(
            GraphArc(
                from_node=task_node,
                to_node=addition_node,
                arc_type="ADDITION",
            )
        )

    def add_removal_arc(
        self,
        task_key: str,
        employee_id: str,
    ):

        task_node = self.task_nodes[task_key]

        removal_node = self.removal_nodes[
            (task_key, employee_id)
        ]

        self.add_arc(
            GraphArc(
                from_node=task_node,
                to_node=removal_node,
                arc_type="REMOVAL",
                task_key=task_key,
                employee_id=employee_id,
            )
        )

    def add_exchange_arc(
        self,
        incoming_task_key: str,
        employee_id: str,
        displaced_task_key: str,
    ):

        task_node = self.task_nodes[incoming_task_key]

        addition_node = self.addition_nodes[
            (incoming_task_key, employee_id)
        ]

        self.add_arc(
            GraphArc(
                from_node=task_node,
                to_node=addition_node,
                arc_type="EXCHANGE",
                task_key=incoming_task_key,
                employee_id=employee_id,
                displaced_task_key=displaced_task_key,
            )
        )

    def add_cycle_arc(
        self,
        incoming_task_key: str,
        employee_id: str,
        displaced_task_key: str,
    ):

        addition_node = self.addition_nodes[
            (incoming_task_key, employee_id)
        ]

        removal_node = self.removal_nodes[
            (displaced_task_key, employee_id)
        ]

        self.add_arc(
            GraphArc(
                from_node=addition_node,
                to_node=removal_node,
                arc_type="CYCLE",
                task_key=incoming_task_key,
                employee_id=employee_id,
                displaced_task_key=displaced_task_key,
            )
        )

    # --------------------------------------------------------
    # DISPLAY
    # --------------------------------------------------------

    def print_graph(self):

        print("\n================ GRAPH NODES ================\n")

        for node in self.nodes.values():

            print(
                f"{node.node_id:30}"
                f" type={node.node_type:10}"
                f" task={node.task_key}"
                f" employee={node.employee_id}"
                f" displaced={node.displaced_task_key}"
            )

        print("\n================ GRAPH ARCS ================\n")

        for arc in self.arcs:

            print(
                f"{arc.from_node:30}"
                f" -> {arc.to_node:30}"
                f" type={arc.arc_type:10}"
                f" task={arc.task_key}"
                f" employee={arc.employee_id}"
                f" displaced={arc.displaced_task_key}"
            )

    def summary(self):

        print("\nImprovement Graph")
        print("-----------------")
        print(f"Nodes : {len(self.nodes)}")
        print(f"Arcs  : {len(self.arcs)}")

        node_count = {}

        for node in self.nodes.values():

            node_count[node.node_type] = (
                node_count.get(node.node_type, 0) + 1
            )

        arc_count = {}

        for arc in self.arcs:

            arc_count[arc.arc_type] = (
                arc_count.get(arc.arc_type, 0) + 1
            )

        print("\nNodes by type:")

        for k, v in node_count.items():
            print(f"  {k:10}: {v}")

        print("\nArcs by type:")

        for k, v in arc_count.items():
            print(f"  {k:10}: {v}")


# ============================================================
# FEASIBILITY CHECKER
# ============================================================

class AssignmentFeasibility:

    """
    Base feasibility interface.

    The current data model does not contain employee skills,
    qualifications, locations, etc.

    Therefore those constraints should be implemented here
    when the corresponding data becomes available.
    """

    def can_assign(
        self,
        task: Task,
        employee_id: str,
        schedule: ScheduleData,
    ) -> bool:

        return True

    def can_exchange(
        self,
        incoming_task: Task,
        displaced_task: Task,
        employee_id: str,
        schedule: ScheduleData,
    ) -> bool:

        return self.can_assign(
            incoming_task,
            employee_id,
            schedule,
        )


# ============================================================
# BASIC FEASIBILITY CHECKER
# ============================================================

class BasicAssignmentFeasibility(AssignmentFeasibility):

    """
    Feasibility checks that can be performed using the
    existing classes.

    Checks:

        1. Employee has a roster day for the task date.
        2. Employee is working that day.
        3. Employee's shift covers the task.
        4. Task does not overlap another task already
           assigned to the employee.

    Additional business constraints can be added later.
    """

    def can_assign(
        self,
        task: Task,
        employee_id: str,
        schedule: ScheduleData,
    ) -> bool:

        roster = schedule.get_employee(employee_id)

        if roster is None:
            return False

        day = roster.get_day(task.date)

        if day is None:
            return False

        if day.is_off:
            return False

        if not self.shift_covers_task(day.shift, task):
            return False

        for existing_task in day.tasks:

            if existing_task.task_key == task.task_key:
                continue

            if self.overlaps(task, existing_task):
                return False

        return True

    def can_exchange(
        self,
        incoming_task: Task,
        displaced_task: Task,
        employee_id: str,
        schedule: ScheduleData,
    ) -> bool:

        roster = schedule.get_employee(employee_id)

        if roster is None:
            return False

        day = roster.get_day(incoming_task.date)

        if day is None or day.is_off:
            return False

        if not self.shift_covers_task(
            day.shift,
            incoming_task,
        ):
            return False

        # Remove the displaced task conceptually,
        # then test the incoming task.
        for existing_task in day.tasks:

            if existing_task.task_key in {
                displaced_task.task_key,
                incoming_task.task_key,
            }:
                continue

            if self.overlaps(
                incoming_task,
                existing_task,
            ):
                return False

        return True

    @staticmethod
    def parse_datetime(
        date_value: str,
        time_value: str,
    ) -> datetime:

        formats = [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d/%m/%Y %H:%M",
            "%Y/%m/%d %H:%M",
        ]

        value = f"{date_value} {time_value}"

        for fmt in formats:

            try:
                return datetime.strptime(
                    value,
                    fmt,
                )

            except ValueError:
                continue

        raise ValueError(
            f"Cannot parse date/time: {value}"
        )

    def overlaps(
        self,
        task_1: Task,
        task_2: Task,
    ) -> bool:

        start_1 = self.parse_datetime(
            task_1.date,
            task_1.start_time,
        )

        end_1 = self.parse_datetime(
            task_1.date,
            task_1.end_time,
        )

        start_2 = self.parse_datetime(
            task_2.date,
            task_2.start_time,
        )

        end_2 = self.parse_datetime(
            task_2.date,
            task_2.end_time,
        )

        return (
            start_1 < end_2
            and start_2 < end_1
        )

    def shift_covers_task(
        self,
        shift: Optional[Shift],
        task: Task,
    ) -> bool:

        if shift is None:
            return False

        task_start = self.time_to_minutes(
            task.start_time
        )

        task_end = self.time_to_minutes(
            task.end_time
        )

        shift_start = self.time_to_minutes(
            shift.start_time
        )

        shift_end = self.time_to_minutes(
            shift.end_time
        )

        return (
            shift_start <= task_start
            and task_end <= shift_end
        )

    @staticmethod
    def time_to_minutes(
        value: str,
    ) -> int:

        parts = value.split(":")

        hour = int(parts[0])
        minute = int(parts[1])

        return hour * 60 + minute


# ============================================================
# IMPROVEMENT GRAPH BUILDER
# ============================================================

class ImprovementGraphBuilder:

    def __init__(
        self,
        schedule: ScheduleData,
        feasibility_checker: Optional[
            AssignmentFeasibility
        ] = None,
        proximity_minutes: int = 120,
    ):

        self.schedule = schedule

        self.feasibility_checker = (
            feasibility_checker
            or BasicAssignmentFeasibility()
        )

        self.proximity_minutes = proximity_minutes

        # ----------------------------------------------------
        # Assignment index
        #
        # task_key -> employee_id
        # ----------------------------------------------------

        self.task_assignment: Dict[
            str,
            str
        ] = {}

        # ----------------------------------------------------
        # Employee task layers
        #
        # employee_id -> date -> ordered tasks
        # ----------------------------------------------------

        self.layers: Dict[
            str,
            Dict[str, List[Task]]
        ] = {}

        self._build_indexes()

    # --------------------------------------------------------
    # BUILD ASSIGNMENT INDEX
    # --------------------------------------------------------

    def _build_indexes(self):

        for employee_id, roster in (
            self.schedule.employees.items()
        ):

            self.layers[employee_id] = {}

            for day in roster.get_working_days():

                tasks = sorted(
                    day.tasks,
                    key=lambda t: self._task_start(t)
                )

                self.layers[employee_id][
                    day.date
                ] = tasks

                for task in tasks:

                    self.task_assignment[
                        task.task_key
                    ] = employee_id

    # --------------------------------------------------------
    # TIME HELPERS
    # --------------------------------------------------------

    def _task_start(
        self,
        task: Task,
    ) -> datetime:

        return self._parse_datetime(
            task.date,
            task.start_time,
        )

    def _parse_datetime(
        self,
        date_value: str,
        time_value: str,
    ) -> datetime:

        return (
            self.feasibility_checker
            .parse_datetime(
                date_value,
                time_value,
            )
            if hasattr(
                self.feasibility_checker,
                "parse_datetime",
            )
            else datetime.strptime(
                f"{date_value} {time_value}",
                "%Y-%m-%d %H:%M",
            )
        )

    # --------------------------------------------------------
    # TEMPORAL PROXIMITY
    # --------------------------------------------------------

    def is_temporally_close(
        self,
        task_1: Task,
        task_2: Task,
    ) -> bool:

        start_1 = self._task_start(task_1)
        start_2 = self._task_start(task_2)

        difference = abs(
            (start_1 - start_2).total_seconds()
            / 60
        )

        return difference <= self.proximity_minutes

    # --------------------------------------------------------
    # BUILD GRAPH
    # --------------------------------------------------------

    def build(
        self,
        disrupted_task_key: str,
    ) -> ImprovementGraph:

        graph = ImprovementGraph()

        disrupted_task = self.schedule.get_task(
            disrupted_task_key
        )

        if disrupted_task is None:

            raise ValueError(
                f"Task '{disrupted_task_key}' "
                f"not found in ScheduleData.tasks"
            )

        # ====================================================
        # 1. CREATE TASK NODES
        # ====================================================

        for task in self.schedule.tasks.values():

            graph.add_task_node(task)

        # ====================================================
        # 2. CREATE REMOVAL NODES
        #
        # Every currently assigned task has a possible
        # removal relationship.
        # ====================================================

        for task_key, employee_id in (
            self.task_assignment.items()
        ):

            graph.add_removal_node(
                task_key,
                employee_id,
            )

            graph.add_removal_arc(
                task_key,
                employee_id,
            )

        # ====================================================
        # 3. ADDITION POSSIBILITIES
        #
        # For each task, test assignment to every other
        # employee.
        #
        # The disrupted task is especially important because
        # it starts from the Unassigned layer.
        # ====================================================

        for task in self.schedule.tasks.values():

            current_employee = (
                self.task_assignment.get(
                    task.task_key
                )
            )

            for employee_id in (
                self.schedule.employees.keys()
            ):

                # No reason to add the task to the same
                # employee unless this is the disrupted task
                # being reinserted.
                if (
                    current_employee == employee_id
                    and task.task_key != disrupted_task_key
                ):
                    continue

                # =================================================
                # Check whether another task currently occupies
                # this employee at the same time.
                # =================================================

                displaced_tasks = (
                    self.find_conflicting_tasks(
                        task,
                        employee_id,
                    )
                )

                # -------------------------------------------------
                # Case 1: Empty feasible position
                # -------------------------------------------------

                if not displaced_tasks:

                    if self.feasibility_checker.can_assign(
                        task,
                        employee_id,
                        self.schedule,
                    ):

                        graph.add_addition_node(
                            task.task_key,
                            employee_id,
                        )

                        graph.add_addition_arc(
                            task.task_key,
                            employee_id,
                        )

                # -------------------------------------------------
                # Case 2: Occupied position
                #
                # The task may be inserted by displacing the
                # current task.
                # -------------------------------------------------

                else:

                    for displaced_task in displaced_tasks:

                        if not self.is_temporally_close(
                            task,
                            displaced_task,
                        ):
                            continue

                        if self.feasibility_checker.can_exchange(
                            task,
                            displaced_task,
                            employee_id,
                            self.schedule,
                        ):

                            graph.add_addition_node(
                                task.task_key,
                                employee_id,
                                displaced_task.task_key,
                            )

                            graph.add_exchange_arc(
                                task.task_key,
                                employee_id,
                                displaced_task.task_key,
                            )

                            # Cycle arc:
                            #
                            # incoming task takes the assignment
                            # occupied by displaced_task.
                            #
                            # displaced_task must subsequently
                            # obtain another assignment.

                            graph.add_cycle_arc(
                                task.task_key,
                                employee_id,
                                displaced_task.task_key,
                            )

        return graph

    # --------------------------------------------------------
    # FIND CURRENTLY OCCUPIED TASKS
    # --------------------------------------------------------

    def find_conflicting_tasks(
        self,
        task: Task,
        employee_id: str,
    ) -> List[Task]:

        employee_layers = self.layers.get(
            employee_id,
            {}
        )

        day_tasks = employee_layers.get(
            task.date,
            []
        )

        result = []

        for existing_task in day_tasks:

            if (
                existing_task.task_key
                == task.task_key
            ):
                continue

            if self.feasibility_checker.overlaps(
                task,
                existing_task,
            ):

                result.append(existing_task)

        return result

    # --------------------------------------------------------
    # GET NEXT EXCHANGE CANDIDATES
    # --------------------------------------------------------

    def get_exchange_candidates(
        self,
        task: Task,
    ) -> List[Tuple[Task, str]]:

        """
        Returns:

            [
                (occupied_task, employee_id),
                ...
            ]

        These are tasks that are temporally close enough
        to potentially participate in an exchange chain.
        """

        candidates = []

        for employee_id, date_layers in (
            self.layers.items()
        ):

            tasks = date_layers.get(
                task.date,
                []
            )

            for existing_task in tasks:

                if (
                    existing_task.task_key
                    == task.task_key
                ):
                    continue

                if self.is_temporally_close(
                    task,
                    existing_task,
                ):

                    candidates.append(
                        (
                            existing_task,
                            employee_id,
                        )
                    )

        return candidates


# ============================================================
# EXAMPLE USAGE
# ============================================================

if __name__ == "__main__":

    # --------------------------------------------------------
    # schedule_data should already be populated using your
    # existing classes.py model.
    # --------------------------------------------------------

    schedule_data = ScheduleData()

    # Example:
    #
    # schedule_data.employees["A"] = roster_a
    # schedule_data.employees["B"] = roster_b
    # schedule_data.employees["C"] = roster_c
    #
    # schedule_data.tasks["T001"] = task_001
    # schedule_data.tasks["T002"] = task_002
    #
    # etc.

    disrupted_task_key = "T001"

    builder = ImprovementGraphBuilder(
        schedule=schedule_data,
        feasibility_checker=BasicAssignmentFeasibility(),
        proximity_minutes=120,
    )

    graph = builder.build(
        disrupted_task_key
    )

    graph.summary()

    graph.print_graph()