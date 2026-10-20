import os
import sys
import time
import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Ensure module imports resolve
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.append(CURRENT_DIR)
hungarian_dir = os.path.join(CURRENT_DIR, "hungarianNS")
if os.path.exists(hungarian_dir) and hungarian_dir not in sys.path:
    sys.path.append(hungarian_dir)

import DataReader
import DisruptionSimulator
import NeighborhoodManager
import CostEstimator
import ScheduleRecovery
import JobRepairingAssigner
import InputClasses

# ==========================================
# DISRUPTION MODES, STATUS LABELS & CONSTANTS
# ==========================================
MODE_EMPLOYEE = "👤 Employee Disruption (remove tasks)"
MODE_DELAY = "⏱️ Task Delay (shift start/end later)"

STATUS_ORIGINAL = "Original Assignment"
STATUS_REASSIGNED = "Reassigned (Recovered)"
STATUS_UNASSIGNED = "Unassigned / Disrupted"
STATUS_DELAY_SAME = "Delayed (Same Employee)"
STATUS_DELAY_REASSIGNED = "Delayed (Reassigned)"

# statuses meaning "a different employee ended up with the task"
RECOVERED_STATUSES = {STATUS_REASSIGNED, STATUS_DELAY_REASSIGNED}

# small tie-break cost so a delayed task prefers its original employee
PREFER_ORIGINAL_COST = 10.0

# Recovery level at which results go to the dedicated "Level 5" table
FINAL_LEVEL = 5

# ==========================================
# STREAMLIT UI CONFIGURATION (LIGHT THEME)
# ==========================================
st.set_page_config(
    page_title="AeroRecover",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown(
    """
    # ✈️ AeroRecover
    ### *Intelligent Airline Disruption & Roster Recovery*
    """
)

st.markdown("""
<style>
    .metric-card-light {
        background-color: #F8FAFC;
        padding: 14px 18px;
        border-radius: 8px;
        border: 1px solid #E2E8F0;
        color: #1E293B;
        margin-bottom: 15px;
    }
    .badge-success {
        background-color: #DEF7EC;
        color: #03543F;
        padding: 4px 10px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 13px;
    }
    .badge-danger {
        background-color: #FDE8E8;
        color: #9B1C1C;
        padding: 4px 10px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 13px;
    }
</style>
""", unsafe_allow_html=True)


# ==========================================
# DATA LOADING & PERSISTENT SESSION STATE
# ==========================================
def find_data_file():
    candidates = [
        "data/Shift_Task_View_ctl.json",
        "hungarianNS/data/Shift_Task_View_ctl.json",
        "data/multiple_shifts/dataset/Shift_Task_View.json",
        "hungarianNS/data/multiple_shifts/dataset/Shift_Task_View.json"
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "data/Shift_Task_View_ctl.json"

@st.cache_resource(show_spinner="Loading initial schedule data...")
def load_base_schedule(file_path):
    return DataReader.ScheduleLoader.load(file_path)

if "data_file_path" not in st.session_state:
    st.session_state.data_file_path = find_data_file()

if "schedule_data" not in st.session_state:
    if os.path.exists(st.session_state.data_file_path):
        st.session_state.schedule_data = load_base_schedule(st.session_state.data_file_path)
    else:
        st.error(f"Cannot find dataset file at {st.session_state.data_file_path}.")
        st.stop()

# =========================================================================
# IMMUTABLE BASELINE OFF-DUTY MAP (INTERNAL IMMUTABLE TRACKING ONLY)
# =========================================================================
if "master_daily_offduty" not in st.session_state:
    master_offduty = {}
    all_dates = set()
    for e_obj in st.session_state.schedule_data.employees.values():
        all_dates.update(e_obj.rosters.keys())

    for d in all_dates:
        off_for_d = set()
        for e_id, e_obj in st.session_state.schedule_data.employees.items():
            r = e_obj.rosters.get(d)
            if r is None or len(r.tasks) == 0:
                off_for_d.add(e_id)
        master_offduty[d] = off_for_d

    st.session_state.master_daily_offduty = master_offduty

if "task_recovery_traces" not in st.session_state:
    st.session_state.task_recovery_traces = []

if "recovery_metadata" not in st.session_state:
    st.session_state.recovery_metadata = {}

if "unassigned_pool" not in st.session_state:
    st.session_state.unassigned_pool = []

if "intermediate_offduty_assignments" not in st.session_state:
    st.session_state.intermediate_offduty_assignments = []

# assignments that were only resolved at the final level (Level 5) + their cost
if "level5_assignments" not in st.session_state:
    st.session_state.level5_assignments = []

# what-if Level-5 results for tasks that were actually solved BEFORE level 5
if "level5_whatif" not in st.session_state:
    st.session_state.level5_whatif = []

# employees marked off-duty for a whole day -> {date: {employee_id, ...}} (persists across runs)
if "offduty_blocked" not in st.session_state:
    st.session_state.offduty_blocked = {}

# bumping this resets the "mark off-duty" picker after it has been applied
if "offduty_widget_ver" not in st.session_state:
    st.session_state.offduty_widget_ver = 0

# summary of the last run
if "last_run" not in st.session_state:
    st.session_state.last_run = {}

# per-task "why wasn't employee X chosen" diagnostics
if "diagnostics" not in st.session_state:
    st.session_state.diagnostics = []

# recalled (originally off-duty) employees -> tasks they were given during recovery
if "recalled_tracker" not in st.session_state:
    st.session_state.recalled_tracker = {}


# ==========================================
# DYNAMIC CURRENT OFF-DUTY CALCULATOR
# ==========================================
def get_current_offduty_employees(schedule_data, target_date):
    current_off = set()
    for e_id, e_obj in schedule_data.employees.items():
        r = e_obj.rosters.get(target_date)
        if r is None or len(r.tasks) == 0:
            current_off.add(e_id)
    return current_off


def eligible_employees(all_employees, date):
    """Employees that may receive work on `date` (whole-day off-duty ones are excluded)."""
    blocked = st.session_state.offduty_blocked.get(date, set())
    return [e for e in all_employees if e.employee_id not in blocked]


# ==========================================
# ROSTER SNAPSHOT / RESTORE  (FIX: stops solver side-effects leaking into rosters)
# ==========================================
def snapshot_rosters(schedule_data):
    """Cheap copy of who owns what: {(employee_id, date): [tasks]}"""
    return {
        (e_id, d): list(r.tasks)
        for e_id, e in schedule_data.employees.items()
        for d, r in e.rosters.items()
    }


def restore_rosters(schedule_data, snap):
    """Put every roster back exactly as it was in the snapshot."""
    for e_id, e in schedule_data.employees.items():
        for d, r in e.rosters.items():
            # in place: keeps the same list object in case the engine holds a reference to it
            r.tasks[:] = list(snap.get((e_id, d), []))


BIG_M_APP = getattr(JobRepairingAssigner, "BIG_M", 1_000_000)


def _to_minutes(value):
    try:
        parts = str(value).split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return None


def windows_overlap(s1, e1, s2, e2):
    a1, b1, a2, b2 = (_to_minutes(x) for x in (s1, e1, s2, e2))
    if None in (a1, b1, a2, b2):
        return False
    if b1 < a1:
        b1 += 1440
    if b2 < a2:
        b2 += 1440
    return a1 < b2 and a2 < b1          # touching end/start is NOT an overlap


def recall_overlap_conflict(emp, task, bundle_keys):
    """
    Returns a description of the clashing task if `emp` must NOT be given `task`, else None.
    Rule: a recalled employee (off-duty in the ORIGINAL schedule) may never get a task that overlaps
    one they already hold (recalled-staff tracker + live roster). The sidebar switch extends the
    rule to every employee. Tasks inside the current repair bundle are being re-solved, so they
    are not treated as "already held".
    """
    date = getattr(task, "date", None)
    was_off = emp.employee_id in st.session_state.master_daily_offduty.get(date, set())
    if not was_off and not st.session_state.get("overlap_all", False):
        return None
    held = {}
    for rec in st.session_state.recalled_tracker.get(emp.employee_id, []):
        if rec["date"] == date:
            held[rec["task_key"]] = (rec["start"], rec["end"])
    roster = emp.rosters.get(date)
    if roster is not None:
        for t in roster.tasks:
            held[t.task_key] = (getattr(t, "start_time", None), getattr(t, "end_time", None))
    for key, (s, e) in held.items():
        if key == task.task_key or key in bundle_keys:
            continue
        if windows_overlap(task.start_time, task.end_time, s, e):
            return f"{key} ({s}-{e})"
    return None


class GuardedEstimator:
    """Wraps the real cost estimator; sets the cost to Big-M for pairs that break the overlap rule."""

    def __init__(self, base):
        self._base = base

    def __getattr__(self, name):
        if name == "_base":
            raise AttributeError(name)
        return getattr(self._base, name)

    def build_cost_matrix(self, neighborhood):
        matrix = np.array(self._base.build_cost_matrix(neighborhood=neighborhood), dtype=float)
        cands, tasks = list(neighborhood.candidate_employees), list(neighborhood.tasks)
        events = st.session_state.setdefault("overlap_guard_events", [])
        if matrix.shape != (len(cands), len(tasks)):
            events.append(f"Overlap guard skipped: cost matrix shape {matrix.shape} != "
                          f"({len(cands)} employees x {len(tasks)} tasks)")
            return matrix
        keys = {t.task_key for t in tasks}
        for i, emp in enumerate(cands):
            for j, t in enumerate(tasks):
                clash = recall_overlap_conflict(emp, t, keys)
                if clash:
                    matrix[i, j] = BIG_M_APP
                    events.append(f"{emp.employee_id} blocked from {t.task_key} "
                                  f"({t.start_time}-{t.end_time}): overlaps {clash} they already hold")
        return matrix


def guarded_estimator(schedule_recovery):
    return GuardedEstimator(schedule_recovery.cost_estimator)


def with_candidates(neighborhood, candidates):
    """
    JobAssigner builds its cost matrix from the NEIGHBORHOOD's candidate list, not from the
    `employees` argument (rows = neighborhood.candidate_employees). To really restrict who can be
    picked, hand it a shallow copy of the neighborhood whose candidate list matches `employees`.
    """
    import copy
    nb = copy.copy(neighborhood)
    try:
        nb.candidate_employees = list(candidates)
    except AttributeError:
        object.__setattr__(nb, "candidate_employees", list(candidates))
    return nb


def recovery_method(level, flags, employee_id, date):
    """
    Human-readable reason an assignment was possible, e.g.
    'Overtime (N2)' or 'Off-duty employee recalled (N4)'.
    `flags` is a level trace / probe dict holding overtime_enabled / recall_enabled.
    """
    was_off = employee_id in st.session_state.master_daily_offduty.get(date, set())
    if was_off:
        label = "Off-duty employee recalled"
    elif (flags or {}).get("overtime_enabled"):
        label = "Overtime"
    else:
        label = "Regular assignment"
    return f"{label} (N{level})"


def whatif_verdict(actual_level, act_total, act_cost, wf):
    """Which level is better, comparing total level cost (falls back to the task's own cost)."""
    cur, top = f"Level {actual_level}", f"Level {FINAL_LEVEL}"
    if "error" in wf:
        return f"{cur} stands - {top} probe failed ({wf['error']})."
    if not wf.get("feasible"):
        return f"{cur} is better - {top} was infeasible."

    seed_wf = wf.get("seed_cost")
    a = act_total if act_total is not None else act_cost
    b = wf.get("total_cost") if wf.get("total_cost") is not None else seed_wf
    if a is None or b is None:
        return f"Cannot compare costs ({cur} vs {top})."
    if abs(a - b) < 1e-9:
        return f"Equal cost - {cur} is better (smaller neighbourhood, less disruption)."
    if b < a:
        return f"{top} is better - cheaper by {a - b:,.2f}."
    return f"{cur} is better - {top} costs {b - a:,.2f} more."


# ==========================================
# SIDEBAR CONTROLS (MULTI-EMPLOYEE SUPPORT)
# ==========================================
st.sidebar.title("🎛️ Simulation Setup")

schedule_data = st.session_state.schedule_data

# disruption type
disruption_mode = st.sidebar.radio(
    "Disruption Type",
    [MODE_EMPLOYEE, MODE_DELAY],
    help=(
        "Employee Disruption: selected tasks are removed and the original employee "
        "is blocked from taking them back.\n\n"
        "Task Delay: selected tasks keep existing but start/end later by the chosen "
        "delay. Nobody is blocked - the original employee is simply no longer "
        "pre-assigned and recovery decides who can take the delayed task."
    )
)
is_delay_mode = disruption_mode == MODE_DELAY

# Date Selection First
all_available_dates = sorted(list({d for e in schedule_data.employees.values() for d in e.rosters.keys()}))
default_date_idx = all_available_dates.index("2026-09-07") if "2026-09-07" in all_available_dates else 0
selected_date = st.sidebar.selectbox("Target Date", all_available_dates, index=default_date_idx)

# Multiple Employee Selection
emp_list = sorted(list(schedule_data.employees.keys()))
default_selected_emps = ["A"] if "A" in emp_list else [emp_list[0]]
selected_emp_ids = st.sidebar.multiselect(
    "Target Employees to Disrupt",
    options=emp_list,
    default=default_selected_emps,
    help="Select one or multiple employees whose tasks you want to disrupt."
)

# Compute dynamic CURRENT off-duty staff
blocked_today = st.session_state.offduty_blocked.get(selected_date, set())
current_offduty = get_current_offduty_employees(schedule_data, selected_date) | set(blocked_today)
st.sidebar.markdown(f"**Current Off-Duty Staff ({selected_date}):** `{', '.join(sorted(list(current_offduty))) if current_offduty else 'None'}`")
if blocked_today:
    st.sidebar.markdown(f"🛌 **Marked off-duty (blocked all day):** `{', '.join(sorted(blocked_today))}`")

offduty_pick = st.sidebar.multiselect(
    f"🛌 Mark Employees Off-Duty ({selected_date})",
    options=[e for e in emp_list if e not in blocked_today],
    default=[],
    key=f"offduty_pick_{st.session_state.offduty_widget_ver}",
    help="Marked employees lose all their tasks for this date (recovered by others) and stay blocked "
         "from EVERY task that day in all later disruptions. Use 'Reset Original Schedule' to undo."
)
run_offduty_btn = st.sidebar.button(
    "🛌 Mark Off-Duty & Recover", use_container_width=True, disabled=not offduty_pick
)
st.sidebar.markdown("---")
temporal_window = st.sidebar.slider("Temporal Window (minutes)", min_value=10, max_value=120, value=30, step=5)

# delay-specific controls
delay_minutes = 0
prefer_original = False
pick_from_all = False

if is_delay_mode:
    delay_minutes = st.sidebar.slider(
        "Delay Amount (minutes)", min_value=5, max_value=240, value=30, step=5,
        help="Start AND end time of every selected task move later by this amount "
             "(measured from the task's original time; clamped at 23:59)."
    )
    prefer_original = st.sidebar.checkbox(
        "Prefer original employee when feasible",
        value=True,
        help="Ticked: adds a small cost when a delayed task goes to somebody else, so the "
             "original employee keeps it whenever the delayed time still works.\n\n"
             "Unticked: the original employee is BLOCKED from their delayed task, so it must go to someone else."
    )
    pick_from_all = st.sidebar.checkbox(
        "Choose delayed tasks from ALL employees",
        value=False,
        help="By default only tasks of the selected employees are listed."
    )

# Gather tasks scheduled across the relevant employees for this date
combined_task_options = []
task_to_owner_map = {}
task_to_window = {}

pool_ids = emp_list if (is_delay_mode and pick_from_all) else selected_emp_ids
for emp_id in pool_ids:
    e_obj = schedule_data.employees.get(emp_id)
    if e_obj and selected_date in e_obj.rosters:
        for t in e_obj.rosters[selected_date].tasks:
            combined_task_options.append(t.task_key)
            task_to_owner_map[t.task_key] = emp_id
            task_to_window[t.task_key] = f"{t.start_time}-{t.end_time}"

# Deduplicate task keys while preserving order
combined_task_options = list(dict.fromkeys(combined_task_options))

# default selection = tasks of the selected employees only
default_task_keys = [k for k in combined_task_options if task_to_owner_map.get(k) in selected_emp_ids]

selected_task_keys = st.sidebar.multiselect(
    "Select Tasks to Delay" if is_delay_mode else "Select Tasks to Disrupt",
    options=combined_task_options,
    default=default_task_keys,
    format_func=lambda k: f"{k} (Emp: {task_to_owner_map.get(k, '?')}) · {task_to_window.get(k, '')}",
    help=(
        "Selected tasks will be delayed, lifted from their rosters and re-solved by the "
        "recovery engine. The original employee is NOT blocked."
        if is_delay_mode else
        "Selected tasks will be removed in Stage 4, blocked from their respective original owners, and sequentially recovered."
    )
)

st.sidebar.checkbox(
    "Overlap guard for ALL employees",
    value=False, key="overlap_all",
    help="Always ON for recalled (originally off-duty) staff: they can never be given a task that overlaps "
         "one they already hold (cost set to Big-M). Tick this to apply the same rule to every employee."
)

diag_options = ["(off)"] + emp_list
diag_emp = st.sidebar.selectbox(
    "🔍 Diagnose why this employee isn't chosen",
    diag_options,
    index=diag_options.index("F") if "F" in diag_options else 0,
    help="Runs a read-only per-level check (candidate pool, who beats them, do they get the task "
         "once the winners are excluded) for each disrupted task."
)

run_recovery_btn = st.sidebar.button("🚀 Run Disruption & Recovery", use_container_width=True, type="primary")

if st.sidebar.button("🔄 Reset Original Schedule", use_container_width=True):
    st.cache_resource.clear()
    st.session_state.schedule_data = load_base_schedule(st.session_state.data_file_path)

    # Rebuild internal baseline map
    master_offduty = {}
    all_dates = set()
    for e_obj in st.session_state.schedule_data.employees.values():
        all_dates.update(e_obj.rosters.keys())
    for d in all_dates:
        off_for_d = set()
        for e_id, e_obj in st.session_state.schedule_data.employees.items():
            r = e_obj.rosters.get(d)
            if r is None or len(r.tasks) == 0:
                off_for_d.add(e_id)
        master_offduty[d] = off_for_d
    st.session_state.master_daily_offduty = master_offduty

    st.session_state.task_recovery_traces = []
    st.session_state.recovery_metadata = {}
    st.session_state.unassigned_pool = []
    st.session_state.intermediate_offduty_assignments = []
    st.session_state.level5_assignments = []
    st.session_state.level5_whatif = []
    st.session_state.offduty_blocked = {}
    st.session_state.last_run = {}
    st.session_state.recalled_tracker = {}
    st.session_state.diagnostics = []
    st.rerun()


# ==========================================
# ROSTER EXTRACTION HELPER
# ==========================================
def extract_roster_dataframe(schedule_data, target_date=None, target_employees=None, warn_duplicates=False):
    rows = []
    meta = st.session_state.recovery_metadata
    baseline_off_today = st.session_state.master_daily_offduty.get(target_date, set())

    for emp_id, emp in schedule_data.employees.items():
        if target_employees and emp_id not in target_employees:
            continue

        for r_date, roster in emp.rosters.items():
            if target_date and r_date != target_date:
                continue
            for t in roster.tasks:
                task_k = getattr(t, "task_key", "N/A")
                m = meta.get(task_k, {})
                status = m.get("status", STATUS_ORIGINAL)

                if emp_id in baseline_off_today:
                    duty_state = "Off-Duty (Recalled)"
                elif status in RECOVERED_STATUSES:
                    duty_state = "On-Duty (Reassigned)"
                elif status == STATUS_DELAY_SAME:
                    duty_state = "On-Duty (Delayed Task)"
                else:
                    duty_state = "On-Duty (Scheduled)"

                # delay information carried on the task object
                delay = getattr(t, "delay_minutes", 0) or 0
                if delay:
                    orig_window = f"{getattr(t, 'original_start_time', '?')} - {getattr(t, 'original_end_time', '?')}"
                else:
                    orig_window = "—"

                rows.append({
                    "Employee ID": emp_id,
                    "Staff Duty State": duty_state,
                    "Date": getattr(t, "date", r_date),
                    "Task Key": task_k,
                    "Task Name": getattr(t, "task_name", getattr(t, "name", "Task")),
                    "Start Time": getattr(t, "start_time", "N/A"),
                    "End Time": getattr(t, "end_time", "N/A"),
                    "Shift Window": f"{getattr(t, 'start_time', '')} - {getattr(t, 'end_time', '')}",
                    "Delay (min)": delay,
                    "Original Window": orig_window,
                    "Required Skill": getattr(t, "required_skill", getattr(t, "skill", "General")),
                    "Assignment Status": status,
                    "Recovery Level": m.get("level", "—"),
                    "Recovery Method": m.get("method", "—"),
                    "Original (Blocked) Resource": m.get("original_employee", "—")
                })

    df = pd.DataFrame(rows)
    if not df.empty:
        # FIX: surface duplicates loudly instead of silently hiding them
        if warn_duplicates:
            dups = df[df.duplicated(subset=["Task Key"], keep=False)]
            if not dups.empty:
                st.warning(
                    f"Roster integrity: task(s) present on multiple employees: "
                    f"{sorted(dups['Task Key'].unique())}"
                )
        df = df.drop_duplicates(subset=["Task Key"], keep="last")
        df = df.sort_values(by=["Employee ID", "Start Time"]).reset_index(drop=True)
    return df


# ==========================================
# ENFORCE "BLOCKED EMPLOYEE" AT SOLVE TIME (ENGINE-INDEPENDENT)
# ==========================================
def assign_with_block(schedule_recovery, neighborhood, task):
    """
    Run the normal assignment. If the task carries a blocked employee and the engine
    still handed the task to them, re-solve with that employee removed from the
    candidates so the block is guaranteed.
    """
    sched = schedule_recovery.schedule_data
    snap0 = snapshot_rosters(sched)      # state right after the neighborhood was built

    def _solve(candidates):
        assigner = JobRepairingAssigner.JobAssigner(
            scheduleData=sched,
            unassigned_tasks=neighborhood.tasks,
            estimator=guarded_estimator(schedule_recovery)
        )
        return assigner.assign_jobs(
            employees=candidates,
            tasks=neighborhood.tasks,
            neighborhood=with_candidates(neighborhood, candidates)
        )

    result = _solve(neighborhood.candidate_employees)

    blocked_id = getattr(task, "blocked_employee", None)
    if blocked_id and any(
        a["task"].task_key == task.task_key and a["employee"].employee_id == blocked_id
        for a in result.assignments
    ):
        st.session_state.setdefault("block_guard_events", []).append(
            f"Task {task.task_key}: engine picked blocked employee {blocked_id}; "
            f"re-solved without them (block enforced)."
        )
        # JobAssigner appends tasks to rosters itself when feasible - undo that before re-solving
        restore_rosters(sched, snap0)
        result = _solve([e for e in neighborhood.candidate_employees if e.employee_id != blocked_id])
    return result


# ==========================================
# MULTI-LEVEL ENGINE TRACER WITH INTERMEDIATE LOGGING
# ==========================================
def recover_task_levels(schedule_recovery, task, employees):
    seed_tasks = [task]
    default = seed_tasks.copy()
    assigned_employees = set()
    level_traces = []
    final_result = None
    solved_level = None
    baseline_off_today = st.session_state.master_daily_offduty.get(task.date, set())
    intermediate_assigned_map = {}

    # FIX: clean state to return to after every level evaluation
    sched = schedule_recovery.schedule_data
    snap = snapshot_rosters(sched)

    try:
        for level in range(FINAL_LEVEL + 1):
            neighborhood = schedule_recovery.neighborhood_manager.create_next_neighborhood(
                level=level,
                seed_tasks=seed_tasks,
                employees=employees,
                assigned_employees=assigned_employees
            )

            if neighborhood is None:
                break

            local_tasks_count = len(neighborhood.tasks) - len(neighborhood.released_tasks)

            result = assign_with_block(schedule_recovery, neighborhood, task)

            for assignment in result.assignments:
                assigned_employees.add(assignment["employee"].employee_id)
                emp_assigned_id = assignment["employee"].employee_id
                task_assigned = assignment["task"]

                intermediate_assigned_map[task_assigned.task_key] = {
                    "employee": assignment["employee"],
                    "task": task_assigned,
                    "level": level,
                    "level_name": neighborhood.name,
                    "cost": assignment.get("cost", 0.0)
                }

                if emp_assigned_id in baseline_off_today:
                    st.session_state.intermediate_offduty_assignments.append({
                        "Employee ID": emp_assigned_id,
                        "Staff Duty State": "Off-Duty (Recalled)",
                        "Date": getattr(task_assigned, "date", task.date),
                        "Task Key": task_assigned.task_key,
                        "Task Name": getattr(task_assigned, "task_name", getattr(task_assigned, "name", "Task")),
                        "Start Time": getattr(task_assigned, "start_time", "N/A"),
                        "End Time": getattr(task_assigned, "end_time", "N/A"),
                        "Shift Window": f"{getattr(task_assigned, 'start_time', '')} - {getattr(task_assigned, 'end_time', '')}",
                        "Required Skill": getattr(task_assigned, "required_skill", getattr(task_assigned, "skill", "General")),
                        "Assignment Status": "Reassigned (Intermediate Lvl)",
                        "Recovery Level": f"Level {level} ({neighborhood.name})",
                        "Original (Blocked) Resource": (
                            getattr(task, "blocked_employee", None)
                            or getattr(task, "original_employee", "Unknown")
                        )
                    })

            trace_entry = {
                "level": level,
                "neighborhood_name": neighborhood.name,
                "seed_tasks_count": len(neighborhood.seed_tasks),
                "local_tasks_count": local_tasks_count,
                "released_tasks_count": len(neighborhood.released_tasks),
                "repair_bundle_count": len(neighborhood.tasks),
                "candidate_employees_count": len(neighborhood.candidate_employees),
                "candidate_ids": sorted(e.employee_id for e in neighborhood.candidate_employees),
                "overtime_enabled": neighborhood.overtime_enabled,
                "recall_enabled": neighborhood.recall_enabled,
                "temporal_destruction": neighborhood.temporal_destruction,
                "feasible": result.feasible,
                "total_cost": getattr(result, "total_cost", 0.0),
                "assignments": [
                    {
                        "task_key": a["task"].task_key,
                        "employee_id": a["employee"].employee_id,
                        "cost": a["cost"],
                        "window": f"{a['task'].start_time} - {a['task'].end_time}"
                    }
                    for a in result.assignments
                ],
                "unassigned_keys": [t.task_key for t in result.unassigned_tasks]
            }
            level_traces.append(trace_entry)

            # FIX: the apply-block in the main pipeline is the ONLY place rosters change,
            # so always return to the clean state after evaluating a level.
            restore_rosters(sched, snap)

            if result.feasible:
                final_result = result
                solved_level = level
                break

            seed_tasks = default
    finally:
        # belt and braces (covers the neighborhood-is-None break and exceptions)
        restore_rosters(sched, snap)

    return final_result, level_traces, solved_level, intermediate_assigned_map


# ==========================================
# LEVEL-5 WHAT-IF PROBE (TRULY READ-ONLY)
# ==========================================
def probe_final_level(schedule_recovery, task, employees):
    """
    Solve the task directly at the final level (Level 5) WITHOUT applying anything,
    so we can show what the assignment/cost would have been had recovery gone that far.
    Rosters are snapshotted and restored, so nothing the solver does can leak.
    """
    sched = schedule_recovery.schedule_data
    snap = snapshot_rosters(sched)
    try:
        neighborhood = schedule_recovery.neighborhood_manager.create_next_neighborhood(
            level=FINAL_LEVEL,
            seed_tasks=[task],
            employees=employees,
            assigned_employees=set()
        )
        if neighborhood is None:
            return {"error": "Level 5 neighborhood could not be created"}

        result = assign_with_block(schedule_recovery, neighborhood, task)
        return {
            "feasible": result.feasible,
            "total_cost": getattr(result, "total_cost", 0.0),
            "bundle_size": len(neighborhood.tasks),
            "neighborhood_name": neighborhood.name,
            "overtime_enabled": neighborhood.overtime_enabled,
            "recall_enabled": neighborhood.recall_enabled,
            "seed_cost": next(
                (a.get("cost", 0.0) for a in result.assignments if a["task"].task_key == task.task_key),
                None
            ),
            "assignments": [
                {
                    "task_key": a["task"].task_key,
                    "employee_id": a["employee"].employee_id,
                    "cost": a.get("cost", 0.0),
                    "window": f"{a['task'].start_time} - {a['task'].end_time}"
                }
                for a in result.assignments
            ],
            "unassigned_keys": [t.task_key for t in result.unassigned_tasks],
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        restore_rosters(sched, snap)


# ==========================================
# DIAGNOSTIC: WHY ISN'T EMPLOYEE X CHOSEN?  (READ-ONLY)
# ==========================================
def diagnose_candidate(schedule_recovery, task, target_id, employees):
    """
    Read-only. For every level: is `target_id` in the candidate pool, what does the REAL run pick
    (same solve, same blocked-employee guard, same overlap guard, same accumulated assigned_employees),
    and who beats the target? Rosters are restored after every solve / level.
    """
    import traceback
    sched = schedule_recovery.schedule_data
    target = next((e for e in employees if e.employee_id == target_id), None)
    emp_obj = sched.employees.get(target_id)
    roster = emp_obj.rosters.get(task.date) if emp_obj else None
    facts = {
        "Task window": f"{task.start_time}-{task.end_time}",
        "Task skill": getattr(task, "required_skill", getattr(task, "skill", "n/a")),
        f"{target_id} tasks that day": (len(roster.tasks) if roster is not None else "no roster"),
        f"{target_id} skills": str(getattr(emp_obj, "skills", getattr(emp_obj, "qualifications", "n/a"))),
        f"{target_id} eligible for this date": target is not None,
    }
    out = {"task_key": task.task_key, "target": target_id, "facts": facts, "rows": [], "note": ""}
    if target is None:
        out["note"] = (f"{target_id} was removed from the eligible list for this date "
                       f"(marked off-duty with the sidebar button), so no level can pick them.")
        return out

    blocked_id = getattr(task, "blocked_employee", None)

    def _where(ex):
        fr = traceback.extract_tb(ex.__traceback__)[-1]
        return f"{type(ex).__name__}: {ex} ({os.path.basename(fr.filename)}:{fr.lineno})"

    def _solve(nb, cands):
        assigner = JobRepairingAssigner.JobAssigner(
            scheduleData=sched, unassigned_tasks=nb.tasks, estimator=guarded_estimator(schedule_recovery)
        )
        return assigner.assign_jobs(employees=cands, tasks=nb.tasks, neighborhood=with_candidates(nb, cands))

    def _seed(res):
        return next((a for a in res.assignments if a["task"].task_key == task.task_key), None)

    assigned_acc = set()     # mirrors recover_task_levels: employees used at earlier levels
    solved_already = False   # real run stops at the first feasible level

    for level in range(FINAL_LEVEL + 1):
        snap = snapshot_rosters(sched)
        row = {"Level": level}
        try:
            # Levels after the first feasible one never happen in the real run. Show them "cold",
            # exactly like the Level-5 what-if probe (nothing passed in as already-assigned).
            passed = set() if solved_already else assigned_acc
            row["Scenario"] = "Hypothetical (cold, like what-if)" if solved_already else "Real path"
            nb = schedule_recovery.neighborhood_manager.create_next_neighborhood(
                level=level, seed_tasks=[task], employees=employees, assigned_employees=passed
            )
            if nb is None:
                row["Finding"] = "No neighborhood built at this level"
            else:
                pool = list(nb.candidate_employees)
                snap_nb = snapshot_rosters(sched)      # state right after the neighborhood was built
                in_pool = any(e.employee_id == target_id for e in pool)
                row.update({
                    "Neighborhood": nb.name,
                    "Overtime": nb.overtime_enabled,
                    "Recall": nb.recall_enabled,
                    "Bundle Size": len(nb.tasks),
                    "Pool Size": len(pool),
                    "Pool IDs": ", ".join(sorted(e.employee_id for e in pool)),
                    f"{target_id} In Candidate Pool": in_pool,
                })

                # 1) mirror the real run's solve for this level
                res0, real_pick, real_cost = None, None, None
                try:
                    res0 = _solve(nb, pool)
                    s0 = _seed(res0)
                    if blocked_id and s0 is not None and s0["employee"].employee_id == blocked_id:
                        restore_rosters(sched, snap_nb)
                        res0 = _solve(nb, [e for e in pool if e.employee_id != blocked_id])
                        s0 = _seed(res0)
                    real_pick = s0["employee"].employee_id if s0 else None
                    real_cost = s0.get("cost") if s0 else None
                    if not solved_already:
                        assigned_acc.update(a["employee"].employee_id for a in res0.assignments)
                except Exception as ex:
                    row["Real-Run Error"] = _where(ex)
                finally:
                    restore_rosters(sched, snap_nb)
                row["Real-Run Pick"] = real_pick or "—"
                row["Real-Run Feasible"] = bool(res0.feasible) if res0 is not None else None
                if res0 is not None and res0.feasible:
                    solved_already = True

                # 2) who beats the target?
                if not in_pool:
                    row["Finding"] = "Not in this level's candidate pool (neighborhood excludes them)"
                else:
                    excluded = {blocked_id} if blocked_id else set()
                    beaten, first_cost, target_cost = [], None, None
                    found, bundle_ok, err = False, None, None
                    for _ in range(len(pool)):
                        cands = [e for e in pool if e.employee_id not in excluded]
                        if not any(e.employee_id == target_id for e in cands):
                            break
                        try:
                            res = _solve(nb, cands)
                            restore_rosters(sched, snap_nb)   # assigner writes to rosters when feasible
                        except Exception as ex:
                            err = _where(ex)
                            break
                        s = _seed(res)
                        if s is None:
                            break
                        if first_cost is None:
                            # use the real recovery run's cost when it is the same winner
                            first_cost = (
                                real_cost
                                if (real_cost is not None and s["employee"].employee_id == real_pick)
                                else s.get("cost")
                            )
                        winner = s["employee"].employee_id
                        if winner == target_id:
                            found, target_cost, bundle_ok = True, s.get("cost"), bool(res.feasible)
                            break
                        beaten.append(winner)
                        excluded.add(winner)

                    row["Beaten By (in order)"] = " → ".join(beaten) or "—"
                    row[f"{target_id} Gets Task When Others Excluded"] = found
                    row["Cost (first winner)"] = first_cost
                    row[f"{target_id} Cost"] = target_cost
                    row["Bundle Feasible Then"] = bundle_ok
                    if err:
                        row["Finding"] = f"Solver error: {err}"
                    elif found and not beaten:
                        row["Finding"] = f"{target_id} is the solver's first choice here"
                    elif found:
                        row["Finding"] = (f"Eligible: beaten by {', '.join(beaten)}; "
                                          f"{target_id} gets it once they are excluded")
                    else:
                        row["Finding"] = (f"Even after excluding {', '.join(beaten) or 'nobody'}, the solver "
                                          f"does not give {target_id} the task: hard constraint for {target_id} "
                                          f"(shift / skill / rest / overlap)")
        except Exception as ex:
            row["Finding"] = f"Error: {_where(ex)}"
        finally:
            restore_rosters(sched, snap)
        out["rows"].append(row)
    return out


# ==========================================
# SIMULATION ORCHESTRATION PIPELINE
# ==========================================
run_mode = "delay" if is_delay_mode else "employee"
run_emp_ids = list(selected_emp_ids)

if run_offduty_btn:
    # lock the chosen employees out for the whole selected day (persists for later runs)
    offduty_task_keys = []
    for emp_id in offduty_pick:
        st.session_state.offduty_blocked.setdefault(selected_date, set()).add(emp_id)
        roster_today = schedule_data.employees[emp_id].rosters.get(selected_date)
        if roster_today is not None:
            offduty_task_keys.extend(t.task_key for t in roster_today.tasks)
    st.session_state.offduty_widget_ver += 1
    is_delay_mode = False
    selected_task_keys = offduty_task_keys
    run_emp_ids = list(offduty_pick)
    run_mode = "offduty"

if run_recovery_btn or run_offduty_btn:
    if not selected_task_keys:
        if run_offduty_btn:
            st.rerun()      # nothing to recover - they are simply marked off-duty now
        else:
            st.sidebar.warning("No tasks selected for disruption.")
    else:
        with st.spinner("Executing disruption & multi-level recovery..."):
            start_t = time.time()

            simulator = DisruptionSimulator.DisruptionSimulator(schedule_data)

            # re-apply every persistent whole-day off-duty block on the (new) simulator
            for b_date, b_emps in st.session_state.offduty_blocked.items():
                for b_emp in b_emps:
                    if hasattr(simulator, "mark_absent"):
                        simulator.mark_absent(b_emp, b_date)
            neighborhood_manager = NeighborhoodManager.NeighborhoodManager(
                schedule_data=schedule_data,
                temporal_window=temporal_window
            )
            # optional small cost so delayed tasks prefer their original employee
            if is_delay_mode and prefer_original:
                cost_estimator = CostEstimator.EmployeeCostEstimator(
                    CostEstimator.CostConfig(reassignment_cost=PREFER_ORIGINAL_COST)
                )
            else:
                cost_estimator = CostEstimator.EmployeeCostEstimator()
            schedule_recovery = ScheduleRecovery.ScheduleRecovery(
                schedule_data=schedule_data,
                neighborhood_manager=neighborhood_manager,
                cost_estimator=cost_estimator
            )

            # Stage 4: Batch disruption across all selected tasks
            #   - Employee mode: remove task + block original owner
            #   - Delay mode:    shift task later, nobody is blocked
            disrupted_tasks = []   # (task_obj, original_emp, effective_delay)
            run_notes = []
            if run_mode == "offduty":
                for emp_id in run_emp_ids:
                    run_notes.append(
                        f"Employee {emp_id} marked OFF-DUTY for {selected_date} → all their tasks were "
                        f"recovered by others and they stay blocked from ALL tasks that day in every later run."
                    )

            for task_key in selected_task_keys:
                if is_delay_mode:
                    owner_id = task_to_owner_map.get(task_key)
                    task_obj, original_emp, eff_delay = simulator.delay_task(
                        task_key, delay_minutes, employee_id=owner_id
                    )
                    task_obj.original_employee = original_emp.employee_id
                    if not prefer_original:
                        # unticked -> original employee is blocked from this delayed task
                        task_obj.blocked_employee = original_emp.employee_id
                    if eff_delay < delay_minutes:
                        run_notes.append(
                            f"Task {task_key}: delay clamped to {eff_delay} min (cannot run past 23:59)."
                        )
                else:
                    task_obj, original_emp = simulator.remove_task(task_key)
                    task_obj.blocked_employee = original_emp.employee_id
                    eff_delay = 0
                disrupted_tasks.append((task_obj, original_emp, eff_delay))

            # tasks re-run now are no longer in the old unassigned pool
            seed_keys = {t.task_key for t, _, _ in disrupted_tasks}
            st.session_state.unassigned_pool = [
                p for p in st.session_state.unassigned_pool if p["task_key"] not in seed_keys
            ]

            # Stage 5: Sequential recovery
            all_employees_list = list(schedule_data.employees.values())
            st.session_state.task_recovery_traces = []
            st.session_state.intermediate_offduty_assignments = []
            st.session_state.level5_assignments = []
            st.session_state.level5_whatif = []
            st.session_state.block_guard_events = []
            st.session_state.overlap_guard_events = []
            st.session_state.diagnostics = []

            for task_obj, original_emp, eff_delay in disrupted_tasks:
                original_id = original_emp.employee_id if original_emp is not None else None
                original_window = (
                    f"{getattr(task_obj, 'original_start_time', task_obj.start_time)}-"
                    f"{getattr(task_obj, 'original_end_time', task_obj.end_time)}"
                    if is_delay_mode else f"{task_obj.start_time}-{task_obj.end_time}"
                )

                # FIX: remember who owns every task BEFORE probing/solving, so displaced
                # neighbour tasks (e.g. T1) get their true original employee afterwards.
                owner_before = {
                    t.task_key: e_id
                    for (e_id, _d), tasks in snapshot_rosters(schedule_data).items()
                    for t in tasks
                }

                emps_for_task = eligible_employees(all_employees_list, task_obj.date)

                # read-only per-level diagnostic for the chosen employee (rosters restored inside)
                diag_out = None
                if diag_emp != "(off)":
                    diag_out = diagnose_candidate(schedule_recovery, task_obj, diag_emp, emps_for_task)
                    st.session_state.diagnostics.append(diag_out)

                # read-only Level-5 what-if (rosters restored inside)
                whatif = probe_final_level(schedule_recovery, task_obj, emps_for_task)

                final_res, traces, solved_lvl, inter_map = recover_task_levels(
                    schedule_recovery=schedule_recovery,
                    task=task_obj,
                    employees=emps_for_task
                )

                is_feasible = final_res.feasible if final_res else False
                verification = []

                # (employee, task, level, level_name, cost)
                assignments_to_apply = []
                if is_feasible and final_res.assignments:
                    assignments_to_apply = [
                        (a["employee"], a["task"], solved_lvl, traces[solved_lvl]["neighborhood_name"], a.get("cost", 0.0))
                        for a in final_res.assignments
                    ]
                elif task_obj.task_key in inter_map:
                    entry = inter_map[task_obj.task_key]
                    assignments_to_apply = [
                        (entry["employee"], entry["task"], entry["level"], entry["level_name"], entry.get("cost", 0.0))
                    ]

                new_emp_id = None
                seed_cost = None
                seed_method = None

                if assignments_to_apply:
                    for assigned_emp, assigned_t, lvl_num, lvl_name, a_cost in assignments_to_apply:
                        task_date = getattr(assigned_t, "date", selected_date)

                        # Prune this task from all other employee rosters to guarantee unique ownership
                        for other_emp in schedule_data.employees.values():
                            if task_date in other_emp.rosters:
                                other_emp.rosters[task_date].tasks = [
                                    t for t in other_emp.rosters[task_date].tasks
                                    if t.task_key != assigned_t.task_key
                                ]

                        # Ensure target roster exists
                        if task_date not in assigned_emp.rosters:
                            if hasattr(InputClasses, "Roster"):
                                assigned_emp.rosters[task_date] = InputClasses.Roster(date=task_date, tasks=[])
                            else:
                                class DynamicRoster:
                                    def __init__(self, d):
                                        self.date = d
                                        self.tasks = []
                                assigned_emp.rosters[task_date] = DynamicRoster(task_date)

                        # Place strictly onto new employee's roster
                        target_roster = assigned_emp.rosters[task_date]
                        target_roster.tasks.append(assigned_t)

                        # keep track of recalled (originally off-duty) staff and what they now hold
                        for _k in list(st.session_state.recalled_tracker):
                            st.session_state.recalled_tracker[_k] = [
                                r for r in st.session_state.recalled_tracker[_k]
                                if r["task_key"] != assigned_t.task_key
                            ]
                        if assigned_emp.employee_id in st.session_state.master_daily_offduty.get(task_date, set()):
                            st.session_state.recalled_tracker.setdefault(assigned_emp.employee_id, []).append({
                                "date": task_date,
                                "task_key": assigned_t.task_key,
                                "start": getattr(assigned_t, "start_time", None),
                                "end": getattr(assigned_t, "end_time", None),
                                "level": lvl_num,
                            })

                        is_stored = any(t.task_key == assigned_t.task_key for t in target_roster.tasks)
                        verification.append({
                            "employee_id": assigned_emp.employee_id,
                            "task_key": assigned_t.task_key,
                            "stored_in_roster": is_stored
                        })

                        # FIX: correct previous owner + status for seed AND displaced neighbours
                        is_seed = assigned_t.task_key == task_obj.task_key
                        prev_owner = original_id if is_seed else owner_before.get(assigned_t.task_key)

                        if is_seed and is_delay_mode:
                            status = STATUS_DELAY_SAME if assigned_emp.employee_id == prev_owner else STATUS_DELAY_REASSIGNED
                        elif is_seed:
                            status = STATUS_REASSIGNED
                        elif assigned_emp.employee_id == prev_owner:
                            status = STATUS_ORIGINAL
                        else:
                            status = STATUS_REASSIGNED

                        lvl_flags = traces[lvl_num] if lvl_num < len(traces) else {}
                        method = recovery_method(lvl_num, lvl_flags, assigned_emp.employee_id, task_date)

                        if is_seed:
                            new_emp_id = assigned_emp.employee_id
                            seed_cost = a_cost
                            seed_method = method

                        st.session_state.recovery_metadata[assigned_t.task_key] = {
                            "status": status,
                            "level": f"Level {lvl_num} ({lvl_name})",
                            "method": method,
                            "original_employee": prev_owner,
                            "new_employee": assigned_emp.employee_id
                        }

                        # anything that needed the FINAL level goes to its own table with cost
                        # (neighbours that simply stayed with their owner are not "displaced")
                        if lvl_num == FINAL_LEVEL and (is_seed or status != STATUS_ORIGINAL):
                            st.session_state.level5_assignments.append({
                                "Task Key": assigned_t.task_key,
                                "Role": "Disrupted Task" if is_seed else "Displaced Neighbour Task",
                                "Triggered By": task_obj.task_key,
                                "Date": task_date,
                                "Window": f"{getattr(assigned_t, 'start_time', '')} - {getattr(assigned_t, 'end_time', '')}",
                                "Original Employee": prev_owner or "—",
                                "Assigned Employee": assigned_emp.employee_id,
                                "Off-Duty Recalled": assigned_emp.employee_id in st.session_state.master_daily_offduty.get(task_date, set()),
                                "Recovery Level": f"Level {lvl_num} ({lvl_name})",
                                "Recovery Method": method,
                                "Assignment Cost": a_cost,
                                "Level Total Cost": traces[-1]["total_cost"] if traces else 0.0,
                            })
                else:
                    # Genuinely unassigned across all levels
                    orig_roster = original_emp.rosters.get(task_obj.date, None)
                    if orig_roster:
                        orig_roster.tasks = [t for t in orig_roster.tasks if t.task_key != task_obj.task_key]

                    st.session_state.unassigned_pool.append({
                        "task_key": task_obj.task_key,
                        "date": task_obj.date,
                        "window": f"{task_obj.start_time} - {task_obj.end_time}",
                        "original_employee": original_id,
                        "status": STATUS_UNASSIGNED
                    })

                    st.session_state.recovery_metadata[task_obj.task_key] = {
                        "status": STATUS_UNASSIGNED,
                        "level": "Failed Across All Levels",
                        "original_employee": original_id,
                        "new_employee": "None"
                    }

                was_recovered = len(assignments_to_apply) > 0
                lvl_txt = solved_lvl if solved_lvl is not None else inter_map.get(task_obj.task_key, {}).get("level", "—")

                # recovered BEFORE Level 5 -> keep the "what if it had gone to Level 5" result
                seed_lvl = solved_lvl if solved_lvl is not None else inter_map.get(task_obj.task_key, {}).get("level")
                if diag_out and diag_out.get("rows") and isinstance(seed_lvl, int):
                    drow = next((r for r in diag_out["rows"] if r.get("Level") == seed_lvl), None)
                    if drow and drow.get("Real-Run Pick") not in (None, "—", new_emp_id):
                        run_notes.append(
                            f"Task {task_obj.task_key}: diagnostic predicted {drow['Real-Run Pick']} at Level "
                            f"{seed_lvl} but the real run chose {new_emp_id} - state differed between the two."
                        )

                if was_recovered and isinstance(seed_lvl, int) and seed_lvl < FINAL_LEVEL:
                    st.session_state.level5_whatif.append({
                        "task_key": task_obj.task_key,
                        "original_employee": original_id,
                        "actual_level": seed_lvl,
                        "actual_employee": new_emp_id,
                        "actual_cost": seed_cost,
                        "actual_level_total_cost": traces[seed_lvl]["total_cost"] if seed_lvl < len(traces) else None,
                        "actual_method": seed_method,
                        "date": task_obj.date,
                        "whatif": whatif,
                    })

                method_sfx = f" · {seed_method}" if seed_method else ""
                if not was_recovered:
                    outcome = "Failed Across All Levels"
                elif is_delay_mode and new_emp_id == original_id:
                    outcome = f"Handled by the ORIGINAL employee {new_emp_id} at Level {lvl_txt}{method_sfx}"
                elif is_delay_mode:
                    outcome = f"Delayed task reassigned to Employee {new_emp_id} at Level {lvl_txt}{method_sfx}"
                else:
                    outcome = f"Successfully Recovered at Level {lvl_txt}{method_sfx}"

                st.session_state.task_recovery_traces.append({
                    "task_key": task_obj.task_key,
                    "mode": "delay" if is_delay_mode else "employee",
                    "delay_minutes": eff_delay,
                    "original_window": original_window,
                    "blocked_employee": original_id if (not is_delay_mode or not prefer_original) else "— (not blocked)",
                    "original_blocked": bool(is_delay_mode and not prefer_original),
                    "original_employee": original_id,
                    "new_employee": new_emp_id,
                    "time_window": f"{task_obj.date} {task_obj.start_time}-{task_obj.end_time}",
                    "feasible": was_recovered,
                    "solved_level": lvl_txt,
                    "method": seed_method,
                    "outcome": outcome,
                    "levels": traces,
                    "verification": verification
                })

            for ev in dict.fromkeys(st.session_state.get("block_guard_events", [])):
                run_notes.append(ev)

            guard_ev = list(dict.fromkeys(st.session_state.get("overlap_guard_events", [])))
            if guard_ev:
                run_notes.append(
                    f"Overlap guard: {len(guard_ev)} pair(s) set to Big-M. "
                    + "; ".join(guard_ev[:6]) + (" …" if len(guard_ev) > 6 else "")
                )

            st.session_state.last_run = {
                "mode": run_mode,
                "employees": run_emp_ids,
                "date": selected_date,
                "delay_minutes": delay_minutes,
                "prefer_original": prefer_original,
                "task_count": len(disrupted_tasks),
                "notes": run_notes,
            }

            elapsed = time.time() - start_t
            st.toast(f"Evaluated all tasks in {elapsed:.2f}s!", icon="✅")
            st.rerun()


# ==========================================
# MAIN DASHBOARD INTERFACE
# ==========================================
tab_roster, tab_levels, tab_gantt = st.tabs([
    "📅 Day-Wise Roster Assignments",
    "🔄 Disrupted Task Traces & Neighborhood Progression",
    "⏱️ Employee Shift Timelines"
])

# ----------------------------------------------------
# TAB 1: DAY-WISE ROSTER ASSIGNMENTS
# ----------------------------------------------------
with tab_roster:
    st.subheader(f"Roster Matrix: Date `{selected_date}`")

    # warn_duplicates=True only here so the integrity warning shows once per render
    df_all_today = extract_roster_dataframe(schedule_data, target_date=selected_date, warn_duplicates=True)
    df_selected_emps_today = extract_roster_dataframe(schedule_data, target_date=selected_date, target_employees=selected_emp_ids)

    # Filter strictly for baseline off-duty employees who got a task
    baseline_off_today = st.session_state.master_daily_offduty.get(selected_date, set())
    df_offduty_recalled = df_all_today[df_all_today["Employee ID"].isin(baseline_off_today)] if not df_all_today.empty else df_all_today

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Active Staff Today", df_all_today["Employee ID"].nunique() if not df_all_today.empty else 0)
    col2.metric("Total Assigned Tasks", len(df_all_today))
    col3.metric("Baseline Off-Duty Recalled", len(df_offduty_recalled))
    col4.metric("Unassigned Tasks", len(st.session_state.unassigned_pool))

    view_scope = st.radio(
        "Display Scope",
        [
            "All Employees Today",
            "Show Off-Duty Recalled Employees Only",
            f"Selected Target Employees Only ({', '.join(selected_emp_ids) if selected_emp_ids else 'None'})"
        ],
        horizontal=True
    )

    if view_scope.startswith("Selected Target Employees"):
        display_df = df_selected_emps_today
    elif view_scope == "Show Off-Duty Recalled Employees Only":
        display_df = df_offduty_recalled
    else:
        display_df = df_all_today

    # Tasks that needed Level 5 are shown in their own table below, not in the main table.
    level5_keys = {r["Task Key"] for r in st.session_state.level5_assignments}
    if level5_keys and not display_df.empty:
        display_df = display_df[~display_df["Task Key"].isin(level5_keys)]

    if not display_df.empty:
        st.dataframe(
            display_df,
            column_config={
                "Staff Duty State": st.column_config.TextColumn("Staff Duty State", width="medium"),
                "Assignment Status": st.column_config.TextColumn("Status", width="medium"),
                "Recovery Level": st.column_config.TextColumn("Recovery Level", width="medium"),
                "Original (Blocked) Resource": st.column_config.TextColumn("Original Resource", width="small"),
                "Delay (min)": st.column_config.NumberColumn("Delay (min)", width="small")
            },
            use_container_width=True,
            height=420
        )
    else:
        st.info("No matching roster records found for the chosen display scope.")

    # Level-5 assignment table with cost
    if st.session_state.level5_assignments:
        st.markdown(f"#### 🔴 Level {FINAL_LEVEL} Assignments & Cost")
        st.caption(
            f"Tasks that could only be resolved at Level {FINAL_LEVEL} (the widest neighborhood). "
            "Includes neighbouring tasks displaced by the repair bundle."
        )
        df_l5 = pd.DataFrame(st.session_state.level5_assignments)
        l1, l2, l3 = st.columns(3)
        l1.metric("Level-5 Assignments", len(df_l5))
        l2.metric("Total Assignment Cost", f"{df_l5['Assignment Cost'].sum():,.2f}")
        l3.metric("Off-Duty Staff Recalled", int(df_l5["Off-Duty Recalled"].sum()))
        st.dataframe(
            df_l5,
            column_config={
                "Assignment Cost": st.column_config.NumberColumn("Assignment Cost", format="%.2f"),
                "Level Total Cost": st.column_config.NumberColumn("Level Total Cost", format="%.2f"),
            },
            use_container_width=True
        )

    # what-if table for tasks that were recovered BEFORE Level 5
    if st.session_state.level5_whatif:
        st.markdown(f"#### 🔮 What-If: Had Recovery Gone to Level {FINAL_LEVEL}")
        st.caption(
            f"For tasks recovered before Level {FINAL_LEVEL}: the assignment and cost Level {FINAL_LEVEL} "
            "would have produced, next to what was actually applied. Nothing here changes the roster."
        )
        wi_rows, wi_detail = [], []
        for w in st.session_state.level5_whatif:
            wf = w["whatif"] or {}
            seed_wf = next((a for a in wf.get("assignments", []) if a["task_key"] == w["task_key"]), None)
            wf_cost = seed_wf["cost"] if seed_wf else None
            act_cost = w["actual_cost"]
            act_total = w.get("actual_level_total_cost")
            wf_total = wf.get("total_cost")
            wf_method = (
                recovery_method(FINAL_LEVEL, wf, seed_wf["employee_id"], w.get("date"))
                if seed_wf else "—"
            )
            wi_rows.append({
                "Disrupted Task": w["task_key"],
                "Original Employee": w["original_employee"] or "—",
                "Actual Level": w["actual_level"],
                "Actual Method": w.get("actual_method") or "—",
                "Actual Employee": w["actual_employee"],
                "Actual Cost": act_cost,
                "Actual Level Total Cost": act_total,
                f"Level {FINAL_LEVEL} Employee": seed_wf["employee_id"] if seed_wf else ("—" if "error" not in wf else "n/a"),
                f"Level {FINAL_LEVEL} Method": wf_method,
                f"Level {FINAL_LEVEL} Cost": wf_cost,
                f"Level {FINAL_LEVEL} Bundle Total Cost": wf_total,
                "Cost Difference": (wf_cost - act_cost) if (wf_cost is not None and act_cost is not None) else None,
                "Total Cost Difference": (wf_total - act_total) if (wf_total is not None and act_total is not None) else None,
                f"Level {FINAL_LEVEL} Feasible": wf.get("feasible", False),
                "Note": whatif_verdict(w["actual_level"], act_total, act_cost, wf),
            })
            for a in wf.get("assignments", []):
                wi_detail.append({
                    "Disrupted Task": w["task_key"],
                    "Task Key": a["task_key"],
                    "Role": "Disrupted Task" if a["task_key"] == w["task_key"] else "Displaced Neighbour Task",
                    "Assigned Employee": a["employee_id"],
                    "Window": a["window"],
                    "Cost": a["cost"],
                })
        st.dataframe(
            pd.DataFrame(wi_rows),
            column_config={
                "Actual Cost": st.column_config.NumberColumn(format="%.2f"),
                "Actual Level Total Cost": st.column_config.NumberColumn(format="%.2f"),
                f"Level {FINAL_LEVEL} Cost": st.column_config.NumberColumn(format="%.2f"),
                "Cost Difference": st.column_config.NumberColumn(format="%.2f"),
                "Total Cost Difference": st.column_config.NumberColumn(format="%.2f"),
                f"Level {FINAL_LEVEL} Bundle Total Cost": st.column_config.NumberColumn(format="%.2f"),
            },
            use_container_width=True
        )
        if wi_detail:
            with st.expander(f"Full Level {FINAL_LEVEL} what-if bundle assignments"):
                st.dataframe(
                    pd.DataFrame(wi_detail),
                    column_config={"Cost": st.column_config.NumberColumn(format="%.2f")},
                    use_container_width=True
                )

    if any(st.session_state.recalled_tracker.values()):
        st.markdown("#### 🛌 Recalled Off-Duty Employee Tracker")
        st.caption("Employees who were off-duty in the original schedule and now hold recovered tasks. "
                   "They can never be given another task that overlaps one of these (cost = Big-M).")
        trk_rows = [{
            "Employee ID": e, "Date": r["date"], "Task Key": r["task_key"],
            "Window": f"{r['start']} - {r['end']}", "Recovered At Level": r.get("level"),
        } for e, recs in st.session_state.recalled_tracker.items() for r in recs]
        st.dataframe(pd.DataFrame(trk_rows).sort_values(["Employee ID", "Window"]), use_container_width=True)

    if st.session_state.unassigned_pool:
        st.markdown("#### ⚠️ Unassigned Tasks Pool")
        st.caption("Tasks that could not be assigned at any level and remain unassigned.")
        df_unassigned = pd.DataFrame(st.session_state.unassigned_pool)
        st.dataframe(df_unassigned, use_container_width=True)


# ----------------------------------------------------
# TAB 2: DISRUPTED TASK TRACES & NEIGHBORHOOD PROGRESSION
# ----------------------------------------------------
with tab_levels:
    st.subheader("Disrupted Task Traces & Neighborhood Progression")
    traces = st.session_state.task_recovery_traces

    if not traces:
        st.info("No disruptions executed yet. Run disruption from the sidebar.")
    else:
        last_run = st.session_state.last_run
        if last_run:
            if last_run["mode"] == "delay":
                st.caption(
                    f"Last run: **Task Delay** of {last_run['delay_minutes']} min on {last_run['task_count']} task(s) · "
                    f"{last_run['date']}. "
                    + ("Original employees were not blocked." if last_run.get("prefer_original", True)
                       else "Original employees were BLOCKED from their delayed tasks.")
                )
            elif last_run["mode"] == "offduty":
                st.caption(
                    f"Last run: **Employee Off-Duty (whole day)** for {', '.join(last_run['employees'])} · "
                    f"{last_run['date']} · {last_run['task_count']} task(s) removed."
                )
            else:
                st.caption(
                    f"Last run: **Employee Disruption** of {last_run['task_count']} task(s) from "
                    f"{', '.join(last_run['employees'])} · {last_run['date']}."
                )
            for note in last_run.get("notes", []):
                st.warning(note)

        is_delay_run = traces[0]["mode"] == "delay"
        n_ok = sum(1 for t in traces if t["feasible"])
        n_same = sum(1 for t in traces if t["feasible"] and t["new_employee"] == t["original_employee"])
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Disrupted Tasks", len(traces))
        s2.metric("Recovered", n_ok)
        s3.metric("Unrecovered", len(traces) - n_ok)
        s4.metric("Kept by Original Employee", n_same if is_delay_run else "n/a")

        st.dataframe(pd.DataFrame([{
            "Task": t["task_key"],
            "Original Employee": t["original_employee"] or "—",
            "New Employee": t["new_employee"] or "None",
            "Original Window": t["original_window"],
            "Current Window": t["time_window"].split(" ", 1)[1],
            "Delay (min)": t["delay_minutes"],
            "Method": t.get("method") or "—",
            "Outcome": t["outcome"],
        } for t in traces]), use_container_width=True)

        diags = st.session_state.get("diagnostics", [])
        if diags:
            with st.expander(f"🔍 Why wasn't Employee {diags[0]['target']} chosen? (per-level diagnostic)"):
                st.caption(
                    "Read-only test run before recovery: is the employee in each level's candidate pool, who beats "
                    "them for the disrupted task, and do they get it once the winners are excluded."
                )
                for d in diags:
                    st.markdown(f"**Task {d['task_key']}**")
                    st.caption(" · ".join(f"{k}: {v}" for k, v in d["facts"].items()))
                    if d["rows"]:
                        st.dataframe(pd.DataFrame(d["rows"]), use_container_width=True)
                    else:
                        st.warning(d["note"])

        task_tabs = st.tabs([f"🎯 Task: {t['task_key']}" for t in traces])

        for t_idx, t_data in enumerate(traces):
            with task_tabs[t_idx]:
                if t_data["feasible"]:
                    status_badge = f'<span class="badge-success">{t_data["outcome"]}</span>'
                else:
                    status_badge = '<span class="badge-danger">Failed Across All Levels</span>'

                is_delay_trace = t_data["mode"] == "delay"
                emp_label = (
                    "Original Employee (blocked)" if t_data.get("original_blocked")
                    else "Original Employee" if is_delay_trace else "Blocked Employee"
                )
                emp_value = t_data["original_employee"] if is_delay_trace else t_data["blocked_employee"]
                delay_html = (
                    f"<b>Delay:</b> <code>+{t_data['delay_minutes']} min (was {t_data['original_window']})</code> &nbsp;|&nbsp;"
                    if is_delay_trace else ""
                )

                card_html = (
                    '<div class="metric-card-light">'
                    f'<b>Task Key:</b> <code>{t_data["task_key"]}</code> &nbsp;|&nbsp; '
                    f'<b>{emp_label}:</b> <code>{emp_value}</code> &nbsp;|&nbsp; '
                    f'<b>Window:</b> <code>{t_data["time_window"]}</code> &nbsp;|&nbsp; '
                    f'{delay_html}'
                    f'<b>Status:</b> {status_badge}'
                    '</div>'
                )
                st.markdown(card_html, unsafe_allow_html=True)

                levels_list = t_data["levels"]
                num_levels = len(levels_list)

                # Multi-metric chart: cost, employees, and tasks per level
                if levels_list:
                    prog_df = pd.DataFrame([{
                        "Level": f"L{lvl['level']}: {lvl['neighborhood_name'].split('_')[-1]}",
                        "Repair Bundle (Tasks)": lvl["repair_bundle_count"],
                        "Candidate Employees": lvl["candidate_employees_count"],
                        "Local Tasks": lvl["local_tasks_count"],
                        "Total Assignment Cost": lvl["total_cost"]
                    } for lvl in levels_list])

                    fig_trace = make_subplots(specs=[[{"secondary_y": True}]])

                    fig_trace.add_trace(
                        go.Bar(
                            name="Repair Bundle Size (Tasks)",
                            x=prog_df["Level"],
                            y=prog_df["Repair Bundle (Tasks)"],
                            marker_color="#93C5FD",
                            opacity=0.85
                        ),
                        secondary_y=False
                    )

                    fig_trace.add_trace(
                        go.Scatter(
                            name="Candidate Employees",
                            x=prog_df["Level"],
                            y=prog_df["Candidate Employees"],
                            mode="lines+markers",
                            marker=dict(size=8, color="#2563EB"),
                            line=dict(width=3, color="#2563EB")
                        ),
                        secondary_y=False
                    )

                    fig_trace.add_trace(
                        go.Scatter(
                            name="Total Assignment Cost",
                            x=prog_df["Level"],
                            y=prog_df["Total Assignment Cost"],
                            mode="lines+markers",
                            marker=dict(size=8, symbol="diamond", color="#DC2626"),
                            line=dict(width=2, dash="dot", color="#DC2626")
                        ),
                        secondary_y=True
                    )

                    fig_trace.update_layout(
                        title=f"Neighborhood Progression Telemetry for Task {t_data['task_key']}",
                        height=310,
                        margin=dict(l=20, r=20, t=40, b=20),
                        template="plotly_white",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                    )
                    fig_trace.update_yaxes(title_text="Tasks & Employees Count", secondary_y=False)
                    fig_trace.update_yaxes(title_text="Cost Delta", secondary_y=True)

                    st.plotly_chart(fig_trace, use_container_width=True, key=f"trace_chart_{t_idx}")

                st.write("##### Search History & Neighborhood Levels")

                for l_idx, lvl in enumerate(levels_list):
                    is_last_level = (l_idx == num_levels - 1)
                    level_success = lvl["feasible"]

                    badge_text = "SUCCESS" if level_success else ("FAILED → Expanding Neighborhood" if not is_last_level else "EXHAUSTED")
                    icon = "🟢" if level_success else "⚪"

                    expander_label = (
                        f"{icon} Level {lvl['level']} — [{lvl['neighborhood_name']}] | {badge_text} | "
                        f"Repair Bundle: {lvl['repair_bundle_count']} | Assigned: {len(lvl['assignments'])} | Unassigned: {len(lvl['unassigned_keys'])}"
                    )

                    with st.expander(expander_label, expanded=is_last_level):
                        m1, m2, m3, m4, m5 = st.columns(5)
                        m1.metric("Level Index", f"Level {lvl['level']}")
                        m2.metric("Seed Tasks", lvl["seed_tasks_count"])
                        m3.metric("Local Tasks", lvl["local_tasks_count"])
                        m4.metric("Repair Bundle", lvl["repair_bundle_count"])
                        m5.metric("Candidate Staff", lvl["candidate_employees_count"])

                        m6, m7, m8, m9 = st.columns(4)
                        m6.metric("Overtime Enabled", str(lvl["overtime_enabled"]))
                        m7.metric("Recall Enabled", str(lvl["recall_enabled"]))
                        m8.metric("Temporal Destruction", str(lvl["temporal_destruction"]))
                        m9.metric("Feasibility", "Feasible" if lvl["feasible"] else "Infeasible")

                        st.caption("Candidate employees: " + (", ".join(lvl.get("candidate_ids", [])) or "none"))

                        c_left, c_right = st.columns([1, 1])

                        with c_left:
                            st.write("**Assignments Made in this Neighborhood:**")
                            if lvl["assignments"]:
                                st.dataframe(pd.DataFrame(lvl["assignments"]), use_container_width=True)
                            else:
                                st.caption("No valid assignments found.")

                        with c_right:
                            st.write("**Unassigned Tasks Pending:**")
                            if lvl["unassigned_keys"]:
                                st.code(", ".join(lvl["unassigned_keys"]))
                            else:
                                st.caption("All tasks successfully assigned.")

                if t_data["verification"]:
                    st.write("##### Roster Verification Matrix")
                    st.dataframe(pd.DataFrame(t_data["verification"]), use_container_width=True)


# ----------------------------------------------------
# TAB 3: EMPLOYEE SHIFT TIMELINES (GANTT ONLY)
# ----------------------------------------------------
with tab_gantt:
    st.subheader(f"Employee Shift Timelines: {selected_date}")

    df_timeline = extract_roster_dataframe(schedule_data, target_date=selected_date)

    if not df_timeline.empty and "Start Time" in df_timeline.columns:
        color_map = {
            STATUS_ORIGINAL: "#60A5FA",
            STATUS_REASSIGNED: "#34D399",
            STATUS_UNASSIGNED: "#F87171",
            STATUS_DELAY_SAME: "#FBBF24",
            STATUS_DELAY_REASSIGNED: "#A78BFA"
        }

        try:
            df_timeline["Start_DT"] = pd.to_datetime(df_timeline["Date"] + " " + df_timeline["Start Time"])
            df_timeline["End_DT"] = pd.to_datetime(df_timeline["Date"] + " " + df_timeline["End Time"])

            fig_gantt = px.timeline(
                df_timeline,
                x_start="Start_DT",
                x_end="End_DT",
                y="Employee ID",
                color="Assignment Status",
                color_discrete_map=color_map,
                hover_data=["Task Key", "Shift Window", "Delay (min)", "Original Window",
                            "Staff Duty State", "Recovery Level", "Original (Blocked) Resource"],
                title="Employee Task Execution Gantt (Including Recalled Staff)"
            )
            fig_gantt.update_yaxes(autorange="reversed")
            fig_gantt.update_layout(height=480, margin=dict(l=20, r=20, t=40, b=20), template="plotly_white")
            st.plotly_chart(fig_gantt, use_container_width=True)
        except Exception as e:
            st.warning(f"Could not render Gantt chart: {e}")
    else:
        st.info("No task schedule records found for timeline visualization.")