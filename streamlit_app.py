import os
import sys
import time
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

# Ensure imports resolve from current directory or hungarianNS subfolder
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.append(CURRENT_DIR)
hungarian_dir = os.path.join(CURRENT_DIR, "hungarianNS")
if os.path.exists(hungarian_dir) and hungarian_dir not in sys.path:
    sys.path.append(hungarian_dir)

# Import project engine classes
import DataReader
import DisruptionSimulator
import NeighborhoodManager
import CostEstimator
import ScheduleRecovery

# ==========================================
# STREAMLIT UI CONFIGURATION
# ==========================================
st.set_page_config(
    page_title="Roster Task Recovery Engine",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .metric-card {
        background-color: #1E232F;
        padding: 14px;
        border-radius: 8px;
        border: 1px solid #2D3748;
    }
    .badge-feasible {
        background-color: #2E7D32;
        color: white;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: bold;
    }
    .badge-infeasible {
        background-color: #C62828;
        color: white;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)


# ==========================================
# STATE & CACHED LOADER
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

@st.cache_resource(show_spinner="Parsing Schedule Data...")
def load_base_schedule(file_path):
    return DataReader.ScheduleLoader.load(file_path)

# Initialize Session State
if "schedule_data" not in st.session_state:
    data_file = find_data_file()
    if os.path.exists(data_file):
        st.session_state.schedule_data = load_base_schedule(data_file)
        st.session_state.data_file_path = data_file
    else:
        st.error(f"Cannot find dataset file at {data_file}. Please check the path.")
        st.stop()

if "recovery_history" not in st.session_state:
    st.session_state.recovery_history = []

if "disrupted_keys" not in st.session_state:
    st.session_state.disrupted_keys = []


# ==========================================
# HELPER: EXTRACT CURRENT ROSTER AS DATAFRAME
# ==========================================
def extract_roster_dataframe(schedule_data, target_date=None, target_employee=None):
    rows = []
    for emp_id, emp in schedule_data.employees.items():
        if target_employee and emp_id != target_employee:
            continue
        for r_date, roster in emp.rosters.items():
            if target_date and r_date != target_date:
                continue
            for t in roster.tasks:
                rows.append({
                    "Employee ID": emp_id,
                    "Date": getattr(t, "date", r_date),
                    "Task Key": getattr(t, "task_key", "N/A"),
                    "Task Name": getattr(t, "task_name", getattr(t, "name", "Task")),
                    "Start Time": getattr(t, "start_time", "N/A"),
                    "End Time": getattr(t, "end_time", "N/A"),
                    "Shift Window": f"{getattr(t, 'start_time', '')} - {getattr(t, 'end_time', '')}",
                    "Required Skill": getattr(t, "required_skill", getattr(t, "skill", "General"))
                })
    return pd.DataFrame(rows)


# ==========================================
# SIDEBAR CONTROLS
# ==========================================
st.sidebar.title("🎛️ Simulation Parameters")

schedule_data = st.session_state.schedule_data

# Employee Selection
emp_list = sorted(list(schedule_data.employees.keys()))
default_emp_idx = emp_list.index("A") if "A" in emp_list else 0
selected_emp_id = st.sidebar.selectbox("Select Target Employee", emp_list, index=default_emp_idx)

# Date Selection based on selected employee
employee_obj = schedule_data.employees[selected_emp_id]
avail_dates = sorted(list(employee_obj.rosters.keys()))
default_date_idx = avail_dates.index("2026-09-07") if "2026-09-07" in avail_dates else 0
selected_date = st.sidebar.selectbox("Select Target Date", avail_dates, index=default_date_idx)

# Neighborhood / Solver Controls
st.sidebar.markdown("---")
temporal_window = st.sidebar.slider("Temporal Window (minutes)", min_value=10, max_value=120, value=30, step=5)
random_seed = st.sidebar.number_input("Simulator Seed", min_value=0, max_value=9999, value=42, step=1)

# Task selection for disruption
current_tasks = employee_obj.rosters[selected_date].tasks
task_key_options = [t.task_key for t in current_tasks]

selected_task_keys = st.sidebar.multiselect(
    "Disrupt Specific Tasks",
    options=task_key_options,
    default=task_key_options,
    help="Select which tasks to remove from the employee's roster"
)

# Run Recovery Button
run_recovery_btn = st.sidebar.button("🚀 Run Disruption & Recovery", use_container_width=True, type="primary")

# Reset Session Button
if st.sidebar.button("🔄 Reset Original Schedule", use_container_width=True):
    st.cache_resource.clear()
    st.session_state.schedule_data = load_base_schedule(st.session_state.data_file_path)
    st.session_state.recovery_history = []
    st.session_state.disrupted_keys = []
    st.rerun()


# ==========================================
# ORCHESTRATOR EXECUTION LOGIC
# ==========================================
if run_recovery_btn:
    if not selected_task_keys:
        st.sidebar.warning("No tasks selected for disruption.")
    else:
        with st.spinner("Executing Neighborhood Search & Recovery Engine..."):
            start_time = time.time()
            
            # Setup recovery architecture identical to your orchestrator script
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
            
            level_counter = len(st.session_state.recovery_history)
            
            for task_key in selected_task_keys:
                level_counter += 1
                # 1. Simulate disruption
                removed_task, original_employee = simulator.remove_task(task_key)
                st.session_state.disrupted_keys.append(task_key)
                
                # 2. Run schedule recovery
                result = schedule_recovery.recover(
                    disrupted_tasks=[removed_task],
                    employees=list(schedule_data.employees.values())
                )
                
                # 3. Store result level
                level_record = {
                    "level": level_counter,
                    "seed": random_seed,
                    "task_key": task_key,
                    "original_employee": original_employee.employee_id,
                    "time_window": f"{removed_task.date} {removed_task.start_time}-{removed_task.end_time}",
                    "result": result,
                    "feasible": result.feasible if result else False,
                    "total_cost": getattr(result, "total_cost", 0.0) if result else 0.0,
                    "assignments": getattr(result, "assignments", []) if result else [],
                    "unassigned": getattr(result, "unassigned_tasks", []) if result else [],
                    "timestamp": time.strftime("%H:%M:%S")
                }
                
                # Verify roster update directly from schedule_data
                verification = []
                if result and result.assignments:
                    for assignment in result.assignments:
                        emp = assignment["employee"]
                        assigned_task = assignment["task"]
                        roster = emp.rosters[assigned_task.date]
                        is_stored = any(t.task_key == assigned_task.task_key for t in roster.tasks)
                        verification.append({
                            "employee_id": emp.employee_id,
                            "task_key": assigned_task.task_key,
                            "stored_in_roster": is_stored
                        })
                level_record["verification"] = verification
                st.session_state.recovery_history.append(level_record)
            
            elapsed = time.time() - start_time
            st.toast(f"Recovery finished in {elapsed:.2f} seconds!", icon="✅")
            st.rerun()


# ==========================================
# DASHBOARD BODY & TABS
# ==========================================
st.title("🛫 Airline Roster Disruption & Task Recovery Engine")

tab_roster, tab_levels, tab_diagnostics = st.tabs([
    "📅 Day-Wise Roster Assignments",
    "🔄 Recovery Progression & Minimized Levels",
    "📊 Gantt Timeline & Verification"
])

# ----------------------------------------------------
# TAB 1: DAY-WISE ROSTER ASSIGNMENTS
# ----------------------------------------------------
with tab_roster:
    st.subheader(f"Roster Matrix: Date `{selected_date}`")
    
    col1, col2, col3, col4 = st.columns(4)
    df_all_today = extract_roster_dataframe(schedule_data, target_date=selected_date)
    df_emp_today = extract_roster_dataframe(schedule_data, target_date=selected_date, target_employee=selected_emp_id)
    
    total_assigned = len(df_all_today)
    active_employees = df_all_today["Employee ID"].nunique() if not df_all_today.empty else 0
    emp_tasks = len(df_emp_today)
    
    col1.metric("Active Employees Today", active_employees)
    col2.metric("Total Assigned Tasks", total_assigned)
    col3.metric(f"Tasks for Employee {selected_emp_id}", emp_tasks)
    col4.metric("Disrupted Tasks Logged", len(st.session_state.disrupted_keys))
    
    st.markdown("#### Staff Roster Table")
    view_scope = st.radio("Display Scope", ["Current Target Employee Only", "All Employees Today"], horizontal=True)
    
    display_df = df_emp_today if view_scope == "Current Target Employee Only" else df_all_today
    
    if not display_df.empty:
        # Add visual highlighting for newly recovered tasks
        st.dataframe(
            display_df,
            use_container_width=True,
            height=380
        )
    else:
        st.info(f"No tasks scheduled for Employee {selected_emp_id} on {selected_date}.")

    # Quick download button
    csv_bytes = display_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        "📥 Download Current View as CSV",
        data=csv_bytes,
        file_name=f"roster_{selected_date}_{selected_emp_id}.csv",
        mime="text/csv"
    )

# ----------------------------------------------------
# TAB 2: RECOVERY LEVELS & MINIMIZED EXPANDERS
# ----------------------------------------------------
with tab_levels:
    st.subheader("Neighborhood Search & Hungarian Recovery Levels")
    
    history = st.session_state.recovery_history
    
    if not history:
        st.info("No disruptions run yet. Click '🚀 Run Disruption & Recovery' in the sidebar to observe the level steps.")
    else:
        latest_idx = len(history) - 1
        
        for idx, lvl in enumerate(history):
            is_latest = (idx == latest_idx)
            feasibility_badge = "✅ Feasible" if lvl["feasible"] else "❌ Infeasible"
            
            # Label with seed, task key, cost, and assignment status
            expander_title = (
                f"{'🟢 [LATEST ACTIVE]' if is_latest else '⚪'} Level {lvl['level']} — "
                f"Task: {lvl['task_key']} | From: {lvl['original_employee']} | Status: {feasibility_badge} | "
                f"Cost: {lvl['total_cost']:.2f} | Assignments: {len(lvl['assignments'])}"
            )
            
            # Older levels stay collapsed as minimized bars; latest level stays expanded
            with st.expander(expander_title, expanded=is_latest):
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Search Level", f"Level {lvl['level']}")
                m2.metric("Simulator Seed", lvl["seed"])
                m3.metric("Local Evaluated", len(lvl["assignments"]) + len(lvl["unassigned"]))
                m4.metric("Assigned Tasks", len(lvl["assignments"]))
                m5.metric("Unassigned Tasks", len(lvl["unassigned"]))
                
                c_left, c_right = st.columns([1, 1])
                
                with c_left:
                    st.write("**New Task Assignments:**")
                    if lvl["assignments"]:
                        assign_rows = []
                        for a in lvl["assignments"]:
                            assign_rows.append({
                                "Task Key": a["task"].task_key,
                                "Reassigned Employee": a["employee"].employee_id,
                                "Task Window": f"{a['task'].start_time} - {a['task'].end_time}",
                                "Assignment Cost": a["cost"]
                            })
                        st.dataframe(pd.DataFrame(assign_rows), use_container_width=True)
                    else:
                        st.warning("No assignments generated.")
                
                with c_right:
                    st.write("**Roster Integration Verification:**")
                    if lvl["verification"]:
                        v_df = pd.DataFrame(lvl["verification"])
                        st.dataframe(v_df, use_container_width=True)
                    else:
                        st.info("No verification records available.")

# ----------------------------------------------------
# TAB 3: GANTT TIMELINE & VERIFICATION
# ----------------------------------------------------
with tab_diagnostics:
    st.subheader(f"Task Schedule Gantt: {selected_date}")
    
    df_timeline = extract_roster_dataframe(schedule_data, target_date=selected_date)
    
    if not df_timeline.empty and "Start Time" in df_timeline.columns:
        try:
            # Parse datetime for timeline plotting
            df_timeline["Start_DT"] = pd.to_datetime(df_timeline["Date"] + " " + df_timeline["Start Time"])
            df_timeline["End_DT"] = pd.to_datetime(df_timeline["Date"] + " " + df_timeline["End Time"])
            
            fig = px.timeline(
                df_timeline,
                x_start="Start_DT",
                x_end="End_DT",
                y="Employee ID",
                color="Task Key",
                hover_data=["Task Name", "Shift Window", "Required Skill"],
                title=f"Employee Task Timeline for {selected_date}"
            )
            fig.update_yaxes(autorange="reversed")
            fig.update_layout(height=450, margin=dict(l=20, r=20, t=40, b=20))
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.warning(f"Could not render timeline: {e}")
            st.dataframe(df_timeline)
    else:
        st.info("No timeline data available.")