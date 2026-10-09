import os
import sys
import time
import pandas as pd
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
# DISRUPTION MODES, STATUS LABELS & CONSTANTS  (NEW)
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

# NEW: assignments that were only resolved at the final level (Level 5) + their cost
if "level5_assignments" not in st.session_state:
    st.session_state.level5_assignments = []

# NEW: what-if Level-5 results for tasks that were actually solved BEFORE level 5
if "level5_whatif" not in st.session_state:
    st.session_state.level5_whatif = []

# NEW: summary of the last run
if "last_run" not in st.session_state:
    st.session_state.last_run = {}


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

 
# ==========================================
# SIDEBAR CONTROLS (MULTI-EMPLOYEE SUPPORT)
# ==========================================
st.sidebar.title("🎛️ Simulation Setup")

schedule_data = st.session_state.schedule_data

# NEW: disruption type
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
current_offduty = get_current_offduty_employees(schedule_data, selected_date)
st.sidebar.markdown(f"**Current Off-Duty Staff ({selected_date}):** `{', '.join(sorted(list(current_offduty))) if current_offduty else 'None'}`")
st.sidebar.markdown("---")
temporal_window = st.sidebar.slider("Temporal Window (minutes)", min_value=10, max_value=120, value=30, step=5)

# NEW: delay-specific controls
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
        help="Adds a small cost when a delayed task goes to somebody else, so the "
             "original employee keeps it whenever the delayed time still works."
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
    st.session_state.last_run = {}
    st.rerun()


# ==========================================
# ROSTER EXTRACTION HELPER
# ==========================================
def extract_roster_dataframe(schedule_data, target_date=None, target_employees=None):
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

                # NEW: delay information carried on the task object
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
                    "Original (Blocked) Resource": m.get("original_employee", "—")
                })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["Task Key"], keep="last")
        df = df.sort_values(by=["Employee ID", "Start Time"]).reset_index(drop=True)
    return df


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

        assigner = JobRepairingAssigner.JobAssigner(
            scheduleData=schedule_recovery.schedule_data,
            unassigned_tasks=neighborhood.tasks,
            estimator=schedule_recovery.cost_estimator
        )

        result = assigner.assign_jobs(
            employees=neighborhood.candidate_employees,
            tasks=neighborhood.tasks,
            neighborhood=neighborhood
        )

        for assignment in result.assignments:
            assigned_employees.add(assignment["employee"].employee_id)
            emp_assigned_id = assignment["employee"].employee_id
            task_assigned = assignment["task"]

            intermediate_assigned_map[task_assigned.task_key] = {
                "employee": assignment["employee"],
                "task": task_assigned,
                "level": level,
                "level_name": neighborhood.name,
                "cost": assignment.get("cost", 0.0)          # NEW: keep cost for the Level-5 table
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

        if result.feasible:
            final_result = result
            solved_level = level
            break

        seed_tasks = default
        if neighborhood.temporal_destruction and neighborhood.removed_assignments:
            schedule_recovery.neighborhood_manager._restore_tasks_to_rosters(neighborhood.removed_assignments)

    return final_result, level_traces, solved_level, intermediate_assigned_map


# ==========================================
# NEW: LEVEL-5 WHAT-IF PROBE (READ-ONLY)
# ==========================================
def probe_final_level(schedule_recovery, task, employees):
    """
    Solve the task directly at the final level (Level 5) WITHOUT applying anything,
    so we can show what the assignment/cost would have been had recovery gone that far.
    Any tasks the neighborhood temporarily removed are restored afterwards.
    """
    neighborhood = None
    try:
        neighborhood = schedule_recovery.neighborhood_manager.create_next_neighborhood(
            level=FINAL_LEVEL,
            seed_tasks=[task],
            employees=employees,
            assigned_employees=set()
        )
        if neighborhood is None:
            return {"error": "Level 5 neighborhood could not be created"}

        assigner = JobRepairingAssigner.JobAssigner(
            scheduleData=schedule_recovery.schedule_data,
            unassigned_tasks=neighborhood.tasks,
            estimator=schedule_recovery.cost_estimator
        )
        result = assigner.assign_jobs(
            employees=neighborhood.candidate_employees,
            tasks=neighborhood.tasks,
            neighborhood=neighborhood
        )
        return {
            "feasible": result.feasible,
            "total_cost": getattr(result, "total_cost", 0.0),
            "bundle_size": len(neighborhood.tasks),
            "neighborhood_name": neighborhood.name,
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
        # always leave the rosters exactly as we found them
        try:
            if neighborhood is not None and neighborhood.temporal_destruction and neighborhood.removed_assignments:
                schedule_recovery.neighborhood_manager._restore_tasks_to_rosters(neighborhood.removed_assignments)
        except Exception:
            pass


# ==========================================
# SIMULATION ORCHESTRATION PIPELINE
# ==========================================
if run_recovery_btn:
    if not selected_task_keys:
        st.sidebar.warning("No tasks selected for disruption.")
    else:
        with st.spinner("Executing disruption & multi-level recovery..."):
            start_t = time.time()

            simulator = DisruptionSimulator.DisruptionSimulator(schedule_data)
            neighborhood_manager = NeighborhoodManager.NeighborhoodManager(
                schedule_data=schedule_data,
                temporal_window=temporal_window
            )
            # NEW: optional small cost so delayed tasks prefer their original employee
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
            #   - Employee mode: remove task + block original owner (unchanged)
            #   - Delay mode:    shift task later, nobody is blocked (NEW)
            disrupted_tasks = []   # (task_obj, original_emp, effective_delay)
            run_notes = []
            for task_key in selected_task_keys:
                if is_delay_mode:
                    owner_id = task_to_owner_map.get(task_key)
                    task_obj, original_emp, eff_delay = simulator.delay_task(
                        task_key, delay_minutes, employee_id=owner_id
                    )
                    task_obj.original_employee = original_emp.employee_id
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

            for task_obj, original_emp, eff_delay in disrupted_tasks:
                original_id = original_emp.employee_id if original_emp is not None else None
                original_window = (
                    f"{getattr(task_obj, 'original_start_time', task_obj.start_time)}-"
                    f"{getattr(task_obj, 'original_end_time', task_obj.end_time)}"
                    if is_delay_mode else f"{task_obj.start_time}-{task_obj.end_time}"
                )

                # NEW: read-only Level-5 what-if (state is restored afterwards)
                whatif = probe_final_level(schedule_recovery, task_obj, all_employees_list)

                final_res, traces, solved_lvl, inter_map = recover_task_levels(
                    schedule_recovery=schedule_recovery,
                    task=task_obj,
                    employees=all_employees_list
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

                        is_stored = any(t.task_key == assigned_t.task_key for t in target_roster.tasks)
                        verification.append({
                            "employee_id": assigned_emp.employee_id,
                            "task_key": assigned_t.task_key,
                            "stored_in_roster": is_stored
                        })

                        is_seed = assigned_t.task_key == task_obj.task_key
                        if is_seed:
                            new_emp_id = assigned_emp.employee_id
                            seed_cost = a_cost
                            if is_delay_mode:
                                status = STATUS_DELAY_SAME if new_emp_id == original_id else STATUS_DELAY_REASSIGNED
                            else:
                                status = STATUS_REASSIGNED
                        else:
                            status = STATUS_REASSIGNED

                        st.session_state.recovery_metadata[assigned_t.task_key] = {
                            "status": status,
                            "level": f"Level {lvl_num} ({lvl_name})",
                            "original_employee": original_id,
                            "new_employee": assigned_emp.employee_id
                        }

                        # NEW: anything that needed the FINAL level goes to its own table with cost
                        if lvl_num == FINAL_LEVEL:
                            st.session_state.level5_assignments.append({
                                "Task Key": assigned_t.task_key,
                                "Role": "Disrupted Task" if is_seed else "Displaced Neighbour Task",
                                "Triggered By": task_obj.task_key,
                                "Date": task_date,
                                "Window": f"{getattr(assigned_t, 'start_time', '')} - {getattr(assigned_t, 'end_time', '')}",
                                "Original Employee": original_id if is_seed else "—",
                                "Assigned Employee": assigned_emp.employee_id,
                                "Off-Duty Recalled": assigned_emp.employee_id in st.session_state.master_daily_offduty.get(task_date, set()),
                                "Recovery Level": f"Level {lvl_num} ({lvl_name})",
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

                # NEW: recovered BEFORE Level 5 -> keep the "what if it had gone to Level 5" result
                seed_lvl = solved_lvl if solved_lvl is not None else inter_map.get(task_obj.task_key, {}).get("level")
                if was_recovered and isinstance(seed_lvl, int) and seed_lvl < FINAL_LEVEL:
                    st.session_state.level5_whatif.append({
                        "task_key": task_obj.task_key,
                        "original_employee": original_id,
                        "actual_level": seed_lvl,
                        "actual_employee": new_emp_id,
                        "actual_cost": seed_cost,
                        "actual_level_total_cost": traces[solved_lvl]["total_cost"] if solved_lvl is not None else None,
                        "whatif": whatif,
                    })

                if not was_recovered:
                    outcome = "Failed Across All Levels"
                elif is_delay_mode and new_emp_id == original_id:
                    outcome = f"Handled by the ORIGINAL employee {new_emp_id} at Level {lvl_txt}"
                elif is_delay_mode:
                    outcome = f"Delayed task reassigned to Employee {new_emp_id} at Level {lvl_txt}"
                else:
                    outcome = f"Successfully Recovered at Level {lvl_txt}"

                st.session_state.task_recovery_traces.append({
                    "task_key": task_obj.task_key,
                    "mode": "delay" if is_delay_mode else "employee",
                    "delay_minutes": eff_delay,
                    "original_window": original_window,
                    "blocked_employee": original_id if not is_delay_mode else "— (not blocked)",
                    "original_employee": original_id,
                    "new_employee": new_emp_id,
                    "time_window": f"{task_obj.date} {task_obj.start_time}-{task_obj.end_time}",
                    "feasible": was_recovered,
                    "solved_level": lvl_txt,
                    "outcome": outcome,
                    "levels": traces,
                    "verification": verification
                })

            st.session_state.last_run = {
                "mode": "delay" if is_delay_mode else "employee",
                "employees": list(selected_emp_ids),
                "date": selected_date,
                "delay_minutes": delay_minutes,
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

    df_all_today = extract_roster_dataframe(schedule_data, target_date=selected_date)
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

    # NEW: Level-5 assignment table with cost
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

    # NEW: what-if table for tasks that were recovered BEFORE Level 5
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
            wi_rows.append({
                "Disrupted Task": w["task_key"],
                "Original Employee": w["original_employee"] or "—",
                "Actual Level": w["actual_level"],
                "Actual Employee": w["actual_employee"],
                "Actual Cost": act_cost,
                f"Level {FINAL_LEVEL} Employee": seed_wf["employee_id"] if seed_wf else ("—" if "error" not in wf else "n/a"),
                f"Level {FINAL_LEVEL} Cost": wf_cost,
                "Cost Difference": (wf_cost - act_cost) if (wf_cost is not None and act_cost is not None) else None,
                f"Level {FINAL_LEVEL} Feasible": wf.get("feasible", False),
                f"Level {FINAL_LEVEL} Bundle Total Cost": wf.get("total_cost"),
                "Note": wf.get("error", ""),
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
                f"Level {FINAL_LEVEL} Cost": st.column_config.NumberColumn(format="%.2f"),
                "Cost Difference": st.column_config.NumberColumn(format="%.2f"),
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
                    f"{last_run['date']}. Original employees were not blocked."
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
            "Outcome": t["outcome"],
        } for t in traces]), use_container_width=True)

        task_tabs = st.tabs([f"🎯 Task: {t['task_key']}" for t in traces])

        for t_idx, t_data in enumerate(traces):
            with task_tabs[t_idx]:
                if t_data["feasible"]:
                    status_badge = f'<span class="badge-success">{t_data["outcome"]}</span>'
                else:
                    status_badge = '<span class="badge-danger">Failed Across All Levels</span>'

                is_delay_trace = t_data["mode"] == "delay"
                emp_label = "Original Employee" if is_delay_trace else "Blocked Employee"
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