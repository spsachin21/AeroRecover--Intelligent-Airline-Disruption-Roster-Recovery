from dataclasses import dataclass, field


@dataclass
class Neighborhood:

    level: int
    name: str

    # Original disrupted tasks
    seed_tasks: list = field(default_factory=list)

    # Tasks actually given to Hungarian
    tasks: list = field(default_factory=list)

    # Tasks released because of destruction
    released_tasks: list = field(default_factory=list)

    # Original assignments removed during destruction
    removed_assignments: list = field(default_factory=list)

    # Employees given to Hungarian
    candidate_employees: list = field(default_factory=list)

    # Proximity parameters
    temporal_window: float | None = None
    physical_radius: float | None = None

    # Relaxations
    overtime_enabled: bool = False
    recall_enabled: bool = False
    temporal_destruction: bool = False

    @property
    def size(self):
        return len(self.tasks)

    @property
    def num_candidates(self):
        return len(self.candidate_employees)

    def __repr__(self):

        return (
            f"Neighborhood("
            f"level={self.level}, "
            f"name='{self.name}', "
            f"tasks={len(self.tasks)}, "
            f"employees={len(self.candidate_employees)}, "
            f"overtime={self.overtime_enabled}, "
            f"recall={self.recall_enabled}, "
            f"destruction={self.temporal_destruction})"
        )