from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
import json
import os
import subprocess
import time
from pathlib import Path
from datetime import datetime
import signal
import psutil
import pandas as pd
from typing import Optional
import shutil

# Initialize FastAPI app with increased max file size
# Set max upload size to 500MB for large datasets
app = FastAPI(
    title="Batch Data Processor",
    version="1.0.0",
    max_request_size=500 * 1024 * 1024  # 500 in megabytes
)

# Directories
UPLOADS_DIR = Path("./uploads")
OUTPUT_DIR = Path("./output")
STATIC_DIR = Path("./static")
TEMPLATES_DIR = Path("./templates")
BATCH_CONFIG_FILE = "batch_config.json"
BATCH_RESULTS_FILE = "batch_results.json"
LOG_FILE = "batch_processing.log"
PID_FILE = "batch_process.pid"

# Ensure directories exist
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Helper functions
def update_batch_config(input_file: str, max_rows: Optional[int] = None):
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
        
        if psutil.pid_exists(pid):
            proc = psutil.Process(pid)
            if "python" in proc.name().lower() and "batch_main.py" in " ".join(proc.cmdline()):
                return True, pid
        
        os.remove(PID_FILE)
        return False, None
    except:
        return False, None

def start_batch_processing():
    """Start batch processing in background"""
    is_running, pid = is_process_running()
    if is_running:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(2)
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except:
            pass
    
    process = subprocess.Popen(
        ["python", "batch_main.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    
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
            raise HTTPException(status_code=500, detail=f"Error stopping process: {e}")
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
            "elapsed_time": None,
            "last_updated": None
        }
    
    total_rows = results.get("total_rows", 0)
    successful = results.get("successful_rows", 0)
    failed = results.get("failed_rows", 0)
    processed = successful + failed
    
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
        "elapsed_time": elapsed,
        "last_updated": results.get("last_updated"),
        "output_file": results.get("output_file")
    }

def format_time(seconds):
    """Format seconds into readable time"""
    if seconds is None:
        return "N/A"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

# Routes
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the main dashboard"""
    return templates.TemplateResponse(request, "dashboard.html")
@app.get("/api/status")
async def get_status():
    """Get current processing status"""
    is_running, pid = is_process_running()
    stats = get_processing_stats()
    
    return {
        "is_running": is_running,
        "pid": pid,
        "stats": stats
    }

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), max_rows: Optional[int] = Form(None)):
    """Upload a file for processing"""
    try:
        # Validate file type
        if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
            raise HTTPException(status_code=400, detail="Invalid file type. Only Excel and CSV files are supported.")
        
        # Save file
        file_path = UPLOADS_DIR / file.filename
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        
        # Get file info
        try:
            if file.filename.endswith('.csv'):
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)
            
            rows = len(df)
            columns = len(df.columns)
            
            # Convert to JSON-safe format
            preview_df = df.head(10).copy()
            
            # Replace inf and -inf with None
            preview_df = preview_df.replace([float('inf'), float('-inf')], None)
            
            # Replace NaN with None
            preview_df = preview_df.where(pd.notna(preview_df), None)
            
            # Convert to dict and ensure all values are JSON-serializable
            preview = []
            for record in preview_df.to_dict('records'):
                clean_record = {}
                for key, value in record.items():
                    if pd.isna(value) if isinstance(value, (int, float)) else False:
                        clean_record[key] = None
                    elif isinstance(value, (int, float)):
                        # Check if it's inf or -inf
                        if value == float('inf') or value == float('-inf'):
                            clean_record[key] = None
                        else:
                            clean_record[key] = value
                    elif isinstance(value, pd.Timestamp):
                        clean_record[key] = value.isoformat()
                    else:
                        clean_record[key] = value
                preview.append(clean_record)
                
        except Exception as e:
            rows = 0
            columns = 0
            preview = []
        
        return {
            "success": True,
            "filename": file.filename,
            "path": str(file_path),
            "rows": rows,
            "columns": columns,
            "preview": preview
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/start")
async def start_processing(input_file: str = Form(...), max_rows: Optional[int] = Form(None)):
    """Start batch processing"""
    try:
        # Check if file exists
        if not os.path.exists(input_file):
            raise HTTPException(status_code=404, detail="Input file not found")
        
        # Update config
        update_batch_config(input_file, max_rows)
        
        # Start processing
        pid = start_batch_processing()
        
        return {
            "success": True,
            "message": "Processing started",
            "pid": pid
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/stop")
async def stop_processing():
    """Stop batch processing"""
    try:
        success = stop_batch_processing()
        return {
            "success": success,
            "message": "Processing stopped" if success else "No process running"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/logs")
async def get_logs(lines: int = 50):
    """Get processing logs"""
    logs = get_log_tail(lines)
    return {
        "logs": "".join(logs)
    }

@app.get("/api/files/uploads")
async def list_uploads():
    """List uploaded files"""
    files = []
    if UPLOADS_DIR.exists():
        for file in sorted(UPLOADS_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            files.append({
                "name": file.name,
                "size": file.stat().st_size,
                "modified": datetime.fromtimestamp(file.stat().st_mtime).isoformat()
            })
    return {"files": files}

@app.get("/api/files/outputs")
async def list_outputs():
    """List output files"""
    files = []
    if OUTPUT_DIR.exists():
        for file in sorted(OUTPUT_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            files.append({
                "name": file.name,
                "size": file.stat().st_size,
                "modified": datetime.fromtimestamp(file.stat().st_mtime).isoformat()
            })
    return {"files": files}

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    """Download an output file"""
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type='application/octet-stream'
    )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
