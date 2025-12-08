import streamlit as st
import pandas as pd
import json
import os
import subprocess
import time
from pathlib import Path
from datetime import datetime
import signal
import psutil

# Configure Streamlit page
st.set_page_config(
    page_title="Batch Data Processor",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Directories
UPLOADS_DIR = Path("./uploads")
OUTPUT_DIR = Path("./output")
BATCH_CONFIG_FILE = "batch_config.json"
BATCH_RESULTS_FILE = "batch_results.json"
LOG_FILE = "batch_processing.log"
PID_FILE = "batch_process.pid"

# Ensure directories exist
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

def save_uploaded_file(uploaded_file) -> str:
    """Save uploaded file and return the path"""
    file_path = UPLOADS_DIR / uploaded_file.name
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return str(file_path)

def update_batch_config(input_file: str, max_rows: int = None):
    """Update batch configuration file"""
    config = {
        "INPUT_FILE": input_file,
        "MAX_ROWS": max_rows
    }
    with open(BATCH_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

def load_batch_config() -> dict:
    """Load batch configuration"""
    if os.path.exists(BATCH_CONFIG_FILE):
        with open(BATCH_CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def load_batch_results() -> dict:
    """Load batch processing results"""
    if os.path.exists(BATCH_RESULTS_FILE):
        try:
            with open(BATCH_RESULTS_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def get_log_tail(n_lines: int = 50) -> list:
    """Get last n lines from log file"""
    if not os.path.exists(LOG_FILE):
        return []
    
    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
            return lines[-n_lines:]
    except:
        return []

def is_process_running() -> tuple:
    """Check if batch processing is running"""
    if not os.path.exists(PID_FILE):
        return False, None
    
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        
        # Check if process exists
        if psutil.pid_exists(pid):
            proc = psutil.Process(pid)
            # Check if it's actually our python process
            if "python" in proc.name().lower() and "batch_main.py" in " ".join(proc.cmdline()):
                return True, pid
        
        # Process not running, clean up PID file
        os.remove(PID_FILE)
        return False, None
    except:
        return False, None

def start_batch_processing():
    """Start batch processing in background"""
    # Kill any existing process first
    is_running, pid = is_process_running()
    if is_running:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(2)
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except:
            pass
    
    # Start new process
    process = subprocess.Popen(
        ["python", "batch_main.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    
    # Save PID
    with open(PID_FILE, "w") as f:
        f.write(str(process.pid))
    
    return process.pid

def stop_batch_processing():
    """Stop batch processing"""
    is_running, pid = is_process_running()
    if is_running:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            if psutil.pid_exists(pid):
                os.kill(pid, signal.SIGKILL)
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
            return True
        except Exception as e:
            st.error(f"Error stopping process: {e}")
            return False
    return False

def get_processing_stats() -> dict:
    """Get current processing statistics"""
    results = load_batch_results()
    if not results:
        return {
            "status": "Not Started",
            "total_rows": 0,
            "processed_rows": 0,
            "successful_rows": 0,
            "failed_rows": 0,
            "progress_percent": 0,
            "start_time": None,
            "elapsed_time": None
        }
    
    total_rows = results.get("total_rows", 0)
    successful = results.get("successful_rows", 0)
    failed = results.get("failed_rows", 0)
    
    # During batch processing, successful_rows contains estimated progress
    # After completion, it's the actual count
    # Use successful as processed during batch waiting
    processed = successful + failed
    
    # Ensure progress never exceeds 100%
    progress_percent = min(100.0, (processed / total_rows * 100)) if total_rows > 0 else 0
    
    start_time = results.get("start_time")
    elapsed = None
    if start_time:
        try:
            start_dt = datetime.fromisoformat(start_time)
            elapsed = (datetime.now() - start_dt).total_seconds()
        except:
            pass
    
    return {
        "status": results.get("status", "Unknown"),
        "total_rows": total_rows,
        "processed_rows": processed,
        "successful_rows": successful,
        "failed_rows": failed,
        "progress_percent": progress_percent,
        "start_time": start_time,
        "elapsed_time": elapsed
    }

def format_time(seconds):
    """Format seconds into readable time"""
    if seconds is None:
        return "N/A"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

# Main App
st.title("📊 Batch Data Processor")
st.markdown("Upload your Excel/CSV file and process it with AI-powered batch operations")

# Sidebar for configuration
with st.sidebar:
    st.header("⚙️ Configuration")
    
    # Check if process is running
    is_running, current_pid = is_process_running()
    
    if is_running:
        st.success(f"✅ Process Running (PID: {current_pid})")
        if st.button("🛑 Stop Processing", type="secondary"):
            if stop_batch_processing():
                st.success("Process stopped successfully")
                st.rerun()
            else:
                st.error("Failed to stop process")
    else:
        st.info("⏸️ No Active Process")
    
    st.divider()
    
    # File Upload
    st.subheader("📁 Upload File")
    uploaded_file = st.file_uploader(
        "Choose an Excel or CSV file",
        type=["xlsx", "xls", "csv"],
        help="Upload your data file with the required columns"
    )
    
    max_rows = st.number_input(
        "Max Rows (optional)",
        min_value=1,
        value=None,
        help="Leave empty to process all rows"
    )
    
    if uploaded_file is not None:
        st.success(f"File uploaded: {uploaded_file.name}")
        
        # Preview data
        try:
            if uploaded_file.name.endswith('.csv'):
                df_preview = pd.read_csv(uploaded_file)
            else:
                df_preview = pd.read_excel(uploaded_file)
            
            st.info(f"📊 {len(df_preview)} rows, {len(df_preview.columns)} columns")
            
            with st.expander("Preview Data"):
                st.dataframe(df_preview.head(10))
            
            # Reset file pointer
            uploaded_file.seek(0)
        except Exception as e:
            st.error(f"Error previewing file: {e}")
    
    st.divider()
    
    # Start Processing Button
    if st.button("🚀 Start Processing", type="primary", disabled=is_running):
        if uploaded_file is None:
            st.error("Please upload a file first!")
        else:
            # Save file
            file_path = save_uploaded_file(uploaded_file)
            st.success(f"File saved to: {file_path}")
            
            # Update config
            update_batch_config(file_path, max_rows)
            st.success("Configuration updated")
            
            # Start processing
            pid = start_batch_processing()
            st.success(f"Processing started! (PID: {pid})")
            st.info("You can now close this browser tab. Processing will continue in the background.")
            time.sleep(2)
            st.rerun()

# Main Content Area
tab1, tab2, tab3 = st.tabs(["📈 Status", "📜 Logs", "📁 Files"])

with tab1:
    st.header("Processing Status")
    
    # Auto-refresh toggle
    col1, col2 = st.columns([3, 1])
    with col1:
        st.subheader("Real-time Updates")
    with col2:
        auto_refresh = st.checkbox("Auto-refresh", value=True)
    
    if auto_refresh:
        st.info("Auto-refreshing every 3 seconds...")
    
    # Get stats
    stats = get_processing_stats()
    
    # Display metrics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Status", stats["status"])
    
    with col2:
        st.metric("Total Rows", stats["total_rows"])
    
    with col3:
        st.metric("Processed", stats["processed_rows"])
    
    with col4:
        st.metric("Success Rate", f"{(stats['successful_rows'] / stats['processed_rows'] * 100) if stats['processed_rows'] > 0 else 0:.1f}%")
    
    # Progress bar (ensure value is between 0 and 1)
    progress_value = max(0.0, min(1.0, stats["progress_percent"] / 100))
    st.progress(progress_value)
    st.caption(f"Progress: {stats['progress_percent']:.1f}% ({stats['processed_rows']}/{stats['total_rows']})")
    
    # Detailed stats
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("✅ Successful", stats["successful_rows"])
    
    with col2:
        st.metric("❌ Failed", stats["failed_rows"])
    
    with col3:
        st.metric("⏱️ Elapsed Time", format_time(stats["elapsed_time"]))
    
    # Start time
    if stats["start_time"]:
        st.caption(f"Started at: {stats['start_time']}")
    
    # Show last update time from results file
    results = load_batch_results()
    if results and "last_updated" in results:
        try:
            last_update = datetime.fromisoformat(results["last_updated"])
            seconds_ago = (datetime.now() - last_update).total_seconds()
            st.caption(f"📡 Last updated: {seconds_ago:.0f} seconds ago")
        except:
            pass
    
    # Results summary
    if stats["status"] == "Completed":
        st.success("✅ Processing completed successfully!")
        results = load_batch_results()
        
        if "output_file" in results:
            st.info(f"📄 Output file: {results['output_file']}")
        
        # Show final summary
        with st.expander("📊 Final Summary"):
            st.json(results)

with tab2:
    st.header("Processing Logs")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        n_lines = st.slider("Number of lines to show", 10, 200, 50)
    with col2:
        if st.button("🔄 Refresh Logs"):
            st.rerun()
    
    logs = get_log_tail(n_lines)
    
    if logs:
        log_text = "".join(logs)
        st.text_area("Recent Logs", log_text, height=500)
    else:
        st.info("No logs available yet")

with tab3:
    st.header("Files")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📤 Uploaded Files")
        if UPLOADS_DIR.exists():
            files = list(UPLOADS_DIR.glob("*"))
            if files:
                for file in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
                    st.text(f"📄 {file.name}")
                    st.caption(f"Size: {file.stat().st_size / 1024:.2f} KB | Modified: {datetime.fromtimestamp(file.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                st.info("No uploaded files")
    
    with col2:
        st.subheader("📥 Output Files")
        if OUTPUT_DIR.exists():
            files = list(OUTPUT_DIR.glob("*"))
            if files:
                for file in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
                    col_a, col_b = st.columns([3, 1])
                    with col_a:
                        st.text(f"📄 {file.name}")
                        st.caption(f"Size: {file.stat().st_size / 1024:.2f} KB | Modified: {datetime.fromtimestamp(file.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
                    with col_b:
                        with open(file, "rb") as f:
                            st.download_button(
                                "⬇️ Download",
                                f,
                                file_name=file.name,
                                key=f"download_{file.name}"
                            )
            else:
                st.info("No output files yet")

# Auto-refresh logic
if auto_refresh and is_running:
    time.sleep(3)
    st.rerun()

# Footer
st.divider()
st.caption("💡 Tip: You can safely close this browser tab while processing continues in the background. Come back anytime to check the status!")
