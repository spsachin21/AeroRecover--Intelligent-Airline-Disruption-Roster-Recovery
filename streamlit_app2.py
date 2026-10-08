import os
import sys
import time
import pandas as pd
import streamlit as st
import plotly.express as px

# Ensure module imports resolve from local directory or hungarianNS subfolder
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

# ==========================================
# STREAMLIT UI CONFIGURATION (LIGHT THEME)
# ==========================================
st.set_page_config(
    page_title="Roster Task Recovery Engine",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded"
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
        padding: 3px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 12px;
    }
    .badge-danger {
        background-color: #FDE8E8;
        color: #9B1C1C;
        padding: 3px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 12px;
    }
    .badge-info {
        background-color: #E1EFFE;
        color: #1E429F;
        padding: 3px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 12px;
    }
</style>
""", unsafe_allow_html=True)


# ==========================================
# DATA LOADING & SESSION STATE
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

if "task_recovery_traces" not in st.session_state:
    st.session_state.task_recovery_traces = []

if "recovery_metadata" not in st.session_state:
    st.session_state.recovery_metadata = {}

if "unassigned_pool" not in st.session_state:
    st.session_state.unassigned_pool = []


# ==========================================
# ROSTER EXTRACTION HELPER
# ==========================================
def extract_roster_dataframe(schedule_data, target_date=None, target_employee=None):
    rows = []
    meta = st.session_state.recovery_metadata

    for emp_id, emp in schedule_data.employees.items():
        if target_employee and emp_id != target_employee:
            continue
        for r_date, roster in emp.rosters.items():
            if target_date and r_date != target_date:
                continue
            for t in roster.tasks:
                task_k = getattr(t, "task_key", "N/A")
                m = meta.get(task_k, {})

                rows.append({
                    "Employee ID": emp_id,
                    "Date": getattr(t, "date", r_date),
                    "Task Key": task_k,
                    "Task Name": getattr(t, "task_name", getattr(t, "name", "Task")),
                    "Start Time": getattr(t, "start_time", "N/A"),
                    "End Time": getattr(t, "end_time", "N/A"),
                    "Shift Window": f"{getattr(t, 'start_time', '')} - {getattr(t, 'end_time', '')}",
                    "Required Skill": getattr(t, "required_skill", getattr(t, "skill", "General")),
                    "Assignment Status": m.get("status", "Original Assignment"),
                    "Recovery Level": m.get("level", "—"),
                    "Original (Blocked) Resource": m.get("original_employee", "—")
                })
    return pd.DataFrame(rows)


# ==========================================
# SIDEBAR CONTROLS
# ==========================================
st.sidebar.title("🎛️ Simulation Setup")

schedule_data = st.session_state.schedule_data

emp_list = sorted(list(schedule_data.employees.keys()))
default_emp_idx = emp_list.index("A") if "A" in emp_list else 0
selected_emp_id = st.sidebar.selectbox("Target Employee", emp_list, index=default_emp_idx)

employee_obj = schedule_data.employees[selected_emp_id]
avail_dates = sorted(list(employee_obj.rosters.keys()))
default_date_idx = avail_dates.index("2026-09-07") if "2026-09-07" in avail_dates else 0
selected_date = st.sidebar.selectbox("Target Date", avail_dates, index=default_date_idx)

st.sidebar.markdown("---")
temporal_window = st.sidebar.slider("Temporal Window (minutes)", min_value=10, max_value=120, value=30, step=5)
random_seed = st.sidebar.number_input("Simulator Seed", min_value=0, max_value=9999, value=42, step=1)

# Deduplicated task key selection
current_roster_tasks = employee_obj.rosters.get(selected_date, None)
current_task_keys = list(dict.fromkeys([t.task_key for t in current_roster_tasks.tasks])) if current_roster_tasks else []

selected_task_keys = st.sidebar.multiselect(
    "Select Tasks to Disrupt",
    options=current_task_keys,
    default=current_task_keys,
    help="These tasks will be removed in Stage 4, setting blocked_employee, then recovered in Stage 5."
)

run_recovery_btn = st.sidebar.button("🚀 Run Disruption & Recovery", use_container_width=True, type="primary")

if st.sidebar.button("🔄 Reset Original Schedule", use_container_width=True):
    st.cache_resource.clear()
    st.session_state.schedule_data = load_base_schedule(st.session_state.data_file_path)
    st.session_state.task_recovery_traces = []
    st.session_state.recovery_metadata = {}
    st.session_state.unassigned_pool = []
    st.rerun()


# ==========================================
# LEVEL-BY-LEVEL RECOVERY TRACER
# ==========================================
def recover_task_levels(schedule_recovery, task, employees):
    """
    Executes ScheduleRecovery loop (Level 0 to 5) for a single disrupted task
    and returns intermediate levels, final assignments, and whether it succeeded.
    """
    seed_tasks = [task]
    assigned_employees = set()
    level_traces = []
    final_result = None
    solved_level = None

    for level in range(6):
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

        seed_tasks = result.unassigned_tasks
        if neighborhood.temporal_destruction:
            schedule_recovery.neighborhood_manager._restore_tasks_to_rosters(neighborhood.removed_assignments)

    return final_result, level_traces, solved_level


# ==========================================
# SIMULATION ORCHESTRATOR EXECUTION
# ==========================================
if run_recovery_btn:
    if not selected_task_keys:
        st.sidebar.warning("No tasks selected for disruption.")
    else:
        with st.spinner("Executing Disruption & Recovery Engine..."):
            start_t = time.time()

            # 1 & 3: Component setup
            simulator = DisruptionSimulator.DisruptionSimulator(schedule_data)
            neighborhood_manager = NeighborhoodManager.NeighborhoodManager(
                schedule_data=schedule_data,
                temporal_window=temporal_window
            )
            cost_estimator = CostEstimator.EmployeeCostEstimator()
            schedule_recovery = ScheduleRecovery.ScheduleRecovery(
                schedule_data=schedule_data,
                neighborhood_manager=neighborhood_manager,
                cost_estimator=cost_estimator
            )

            # 4: APPLY DISRUPTION (Batch removal + mark blocked_employee)
            disrupted_tasks = []
            for task_key in selected_task_keys:
                task_obj, original_emp = simulator.remove_task(task_key)
                task_obj.blocked_employee = original_emp.employee_id
                disrupted_tasks.append((task_obj, original_emp))

            # 5 & 6: START RECOVERY LOOP FOR EACH DISRUPTED TASK
            all_employees_list = list(schedule_data.employees.values())
            st.session_state.task_recovery_traces = []

            for task_obj, original_emp in disrupted_tasks:
                final_res, traces, solved_lvl = recover_task_levels(
                    schedule_recovery=schedule_recovery,
                    task=task_obj,
                    employees=all_employees_list
                )

                is_feasible = final_res.feasible if final_res else False

                # Verification check in rosters
                verification = []
                if is_feasible and final_res.assignments:
                    for a in final_res.assignments:
                        assigned_emp = a["employee"]
                        assigned_t = a["task"]
                        roster = assigned_emp.rosters[assigned_t.date]

                        is_stored = any(t.task_key == assigned_t.task_key for t in roster.tasks)
                        verification.append({
                            "employee_id": assigned_emp.employee_id,
                            "task_key": assigned_t.task_key,
                            "stored_in_roster": is_stored
                        })

                        st.session_state.recovery_metadata[assigned_t.task_key] = {
                            "status": "Reassigned (Recovered)",
                            "level": f"Level {solved_lvl} ({traces[solved_lvl]['neighborhood_name']})",
                            "original_employee": task_obj.blocked_employee,
                            "new_employee": assigned_emp.employee_id
                        }
                else:
                    # Clean up: ensure unassigned tasks do not remain under original employee
                    target_roster = original_emp.rosters.get(task_obj.date, None)
                    if target_roster:
                        target_roster.tasks = [t for t in target_roster.tasks if t.task_key != task_obj.task_key]

                    st.session_state.unassigned_pool.append({
                        "task_key": task_obj.task_key,
                        "date": task_obj.date,
                        "window": f"{task_obj.start_time} - {task_obj.end_time}",
                        "original_employee": task_obj.blocked_employee,
                        "status": "Unassigned / Disrupted"
                    })

                    st.session_state.recovery_metadata[task_obj.task_key] = {
                        "status": "Unassigned / Disrupted",
                        "level": "Failed Across All Levels",
                        "original_employee": task_obj.blocked_employee,
                        "new_employee": "None"
                    }

                st.session_state.task_recovery_traces.append({
                    "task_key": task_obj.task_key,
                    "blocked_employee": task_obj.blocked_employee,
                    "time_window": f"{task_obj.date} {task_obj.start_time}-{task_obj.end_time}",
                    "feasible": is_feasible,
                    "solved_level": solved_lvl,
                    "levels": traces,
                    "verification": verification,
                    "seed": random_seed
                })

            elapsed = time.time() - start_t
            st.toast(f"Recovery finished in {elapsed:.2f} seconds!", icon="✅")
            st.rerun()


# ==========================================
# MAIN DASHBOARD INTERFACE
# ==========================================
st.title("🛫 Airline Roster Task Recovery Engine")

tab_roster, tab_levels, tab_diagnostics = st.tabs([
    "📅 Day-Wise Roster Assignments",
    "🔄 Task Recovery & Neighborhood Levels",
    "📊 Schedule Gantt & Verification"
])

# ----------------------------------------------------
# TAB 1: DAY-WISE ROSTER ASSIGNMENTS
# ----------------------------------------------------
with tab_roster:
    st.subheader(f"Roster Matrix: Date `{selected_date}`")

    df_all_today = extract_roster_dataframe(schedule_data, target_date=selected_date)
    df_emp_today = extract_roster_dataframe(schedule_data, target_date=selected_date, target_employee=selected_emp_id)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Active Staff Today", df_all_today["Employee ID"].nunique() if not df_all_today.empty else 0)
    col2.metric("Total Assigned Tasks", len(df_all_today))
    col3.metric(f"Tasks for Employee {selected_emp_id}", len(df_emp_today))
    col4.metric("Unassigned Disrupted Tasks", len(st.session_state.unassigned_pool))

    view_scope = st.radio("Display Scope", ["Current Target Employee Only", "All Employees Today"], horizontal=True)
    display_df = df_emp_today if view_scope == "Current Target Employee Only" else df_all_today

    if not display_df.empty:
        st.dataframe(
            display_df,
            column_config={
                "Assignment Status": st.column_config.TextColumn("Assignment Status", width="medium"),
                "Recovery Level": st.column_config.TextColumn("Recovery Level", width="medium"),
                "Original (Blocked) Resource": st.column_config.TextColumn("Original Resource", width="small")
            },
            use_container_width=True,
            height=380
        )
    else:
        st.info(f"No active tasks assigned to Employee {selected_emp_id} on {selected_date}.")

    if st.session_state.unassigned_pool:
        st.markdown("#### ⚠️ Unassigned Tasks Pool (Disrupted & Not Recovered)")
        st.caption("These tasks could not find a feasible assignment across any neighborhood level and were removed from original rosters.")
        df_unassigned = pd.DataFrame(st.session_state.unassigned_pool)
        st.dataframe(df_unassigned, use_container_width=True)

# ----------------------------------------------------
# TAB 2: TASK-SPECIFIC TABS & EXPANDERS PER LEVEL
# ----------------------------------------------------
with tab_levels:
    st.subheader("Disrupted Task Traces & Neighborhood Progression")
    traces = st.session_state.task_recovery_traces

    if not traces:
        st.info("No disruption simulations executed yet. Run a recovery from the sidebar.")
    else:
        task_tabs = st.tabs([f"🎯 Task: {t['task_key']}" for t in traces])

        for t_idx, t_data in enumerate(traces):
            with task_tabs[t_idx]:
                if t_data["feasible"]:
                    status_badge = f'<span class="badge-success">Successfully Recovered at Level {t_data["solved_level"]}</span>'
                else:
                    status_badge = '<span class="badge-danger">Failed Across All Levels</span>'

                # Light container overview card
                st.markdown(f"""
                <div class="metric-card-light">
                    <b>Task Key:</b> <code>{t_data['task_key']}</code> &nbsp;|&nbsp;
                    <b>Blocked Employee:</b> <code>{t_data['blocked_employee']}</code> &nbsp;|&nbsp;
                    <b>Window:</b> <code>{t_data['time_window']}</code> &nbsp;|&nbsp;
                    <b>Status:</b> {status_badge}
                </div>
                """, unsafe_allow_html=True)

                levels_list = t_data["levels"]
                num_levels = len(levels_list)

                for l_idx, lvl in enumerate(levels_list):
                    is_last_level = (l_idx == num_levels - 1)
                    level_success = lvl["feasible"]

                    badge_text = "SUCCESS" if level_success else ("FAILED → Proceeding to Next Level" if not is_last_level else "FAILED (Exhausted)")
                    icon = "🟢" if level_success else "⚪"

                    expander_label = (
                        f"{icon} Level {lvl['level']} — [{lvl['neighborhood_name']}] | {badge_text} | "
                        f"Assigned: {len(lvl['assignments'])} | Unassigned: {len(lvl['unassigned_keys'])}"
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
                            st.write("**Assignments Made at this Level:**")
                            if lvl["assignments"]:
                                st.dataframe(pd.DataFrame(lvl["assignments"]), use_container_width=True)
                            else:
                                st.caption("No valid assignments found in this neighborhood.")

                        with c_right:
                            st.write("**Unassigned Task Keys:**")
                            if lvl["unassigned_keys"]:
                                st.code(", ".join(lvl["unassigned_keys"]))
                            else:
                                st.caption("All tasks successfully assigned.")

                if t_data["verification"]:
                    st.write("##### Roster Verification for Recovered Tasks")
                    st.dataframe(pd.DataFrame(t_data["verification"]), use_container_width=True)

# ----------------------------------------------------
# TAB 3: SCHEDULE GANTT & VERIFICATION
# ----------------------------------------------------
with tab_diagnostics:
    st.subheader(f"Schedule Gantt Chart: {selected_date}")
    df_timeline = extract_roster_dataframe(schedule_data, target_date=selected_date)

    if not df_timeline.empty and "Start Time" in df_timeline.columns:
        try:
            df_timeline["Start_DT"] = pd.to_datetime(df_timeline["Date"] + " " + df_timeline["Start Time"])
            df_timeline["End_DT"] = pd.to_datetime(df_timeline["Date"] + " " + df_timeline["End Time"])

            fig = px.timeline(
                df_timeline,
                x_start="Start_DT",
                x_end="End_DT",
                y="Employee ID",
                color="Assignment Status",
                hover_data=["Task Key", "Shift Window", "Recovery Level", "Original (Blocked) Resource"],
                title=f"Shift Schedule ({selected_date})"
            )
            fig.update_yaxes(autorange="reversed")
            fig.update_layout(height=430, margin=dict(l=20, r=20, t=40, b=20), template="plotly_white")
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.warning(f"Could not render Gantt chart: {e}")
            st.dataframe(df_timeline)
    else:
        st.info("No timeline data available.")