from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime, time

def parse_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()

@dataclass
class Shift:
    """
    Represents a shift definition.
    Example:
        10H_0600 -> 06:00 to 16:00
    """
    shift_id: str
    start_time: str
    end_time: str
    length: str
    @property
    def start(self) -> time:
        return parse_time(self.start_time)

    @property
    def end(self) -> time:
        return parse_time(self.end_time)

    def __repr__(self):
        return (
            f"Shift("
            f"id={self.shift_id}, "
            f"{self.start_time}-{self.end_time}, "
            f"length={self.length})"
        )


@dataclass
class Task:
    """
    Represents an individual task/turn.
    """
    task_key: str
    unique_turn_id: int
    task_id: int
    turn_id: str
    service: str
    start_time: str
    end_time: str
    date: str
    demand: int
    coverage: int
    status: str
    
    @property
    def start(self) -> time:
        return parse_time(self.start_time)
    
    @property
    def end(self) -> time:
        return parse_time(self.end_time)

    def __repr__(self):
        return (
            f"Task("
            f"key={self.task_key}, "
            f"turn={self.turn_id}, "
            f"{self.date} {self.start_time}-{self.end_time}, "
            f"status={self.status})"
        )


@dataclass
class RosterDay:
    """
    Represents one employee's assignment for one day.

    A RosterDay can be:
        - OFF
        - assigned to a Shift
        - assigned a list of Tasks
    """
    date: str
    day: str
    shift: Optional[Shift] = None
    tasks: List[Task] = field(default_factory=list)
    ot_hours: float = 0.0

    @property
    def is_off(self) -> bool:
        return self.shift is None

    def __repr__(self):
        if self.is_off:
            return f"RosterDay({self.date}, OFF)"

        return (
            f"RosterDay("
            f"{self.date}, "
            f"shift={self.shift.shift_id}, "
            f"tasks={len(self.tasks)}, "
            f"OT={self.ot_hours})"
        )


@dataclass
class Employee:

    roster_id: int
    employee_id: str
    rosters: Dict[str, RosterDay] = field(default_factory=dict)

    def add_day(self, roster_day: RosterDay):
        self.rosters[roster_day.date] = roster_day

    def get_day(self, date: str) -> Optional[RosterDay]:
        return self.rosters.get(date)

    def get_working_days(self) -> List[RosterDay]:
        return [
            day for day in self.rosters.values()
            if not day.is_off
        ]

    def get_tasks(self) -> List[Task]:
        tasks = []

        for day in self.rosters.values():
            tasks.extend(day.tasks)

        return tasks

    def __repr__(self):
        return (
            f"Roster("
            f"id={self.roster_id}, "
            f"employee={self.employee_id}, "
            f"days={len(self.rosters)})"
        )

@dataclass
class ResourceRoster:
    """
    Represents the complete roster for a resource/service.

    Example:
        CTL
    """
    resource: str
    planning_horizon_start: str
    planning_horizon_end: str
    headcount: int

    rosters: Dict[int, Employee] = field(default_factory=dict)

    def add_roster(self, roster: Employee):
        self.rosters[roster.roster_id] = roster

    def get_roster(self, roster_id: int) -> Optional[Employee]:
        return self.rosters.get(roster_id)

    def __repr__(self):
        return (
            f"ResourceRoster("
            f"resource={self.resource}, "
            f"headcount={self.headcount}, "
            f"rosters={len(self.rosters)})"
        )


# ============================================================
# DATA CONTAINER
# ============================================================
@dataclass
class ScheduleData:
    shifts: Dict[str, Shift] = field(default_factory=dict)
    tasks: Dict[str, Task] = field(default_factory=dict)
    resources: Dict[str, ResourceRoster] = field(default_factory=dict)
    employees: Dict[str, Employee] = field(default_factory=dict)

    def get_shift(self, shift_id: str) -> Optional[Shift]:
        return self.shifts.get(shift_id)

    def get_task(self, task_key: str) -> Optional[Task]:
        return self.tasks.get(task_key)

    def get_resource(self, resource: str) -> Optional[ResourceRoster]:
        return self.resources.get(resource)

    def get_employee(self, employee_id: str) -> Optional[Employee]:
        return self.employees.get(employee_id)


