from fastapi import FastAPI, File, UploadFile, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
import asyncio
import json
import os
import shutil
from pathlib import Path
from datetime import datetime
import logging
from typing import Optional, Dict, List
import pandas as pd
from main import DataProcessor
import threading
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Data Processing Web Interface",
    description="Web interface for processing Excel files with AI-powered data enhancement",
    version="1.0.0"
)

# Create directories
os.makedirs("uploads", exist_ok=True)
os.makedirs("output", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Global variables to track processing
processing_status = {
    "is_running": False,
    "current_file": None,
    "progress": 0,
    "total_rows": 0,
    "successful_rows": 0,
    "failed_rows": 0,
    "start_time": None,
    "estimated_completion": None,
    "current_batch": 0,
    "total_batches": 0,
    "logs": []
}

# Store processor separately to avoid serialization issues
current_processor = None
progress_task = None  # Track the progress update task
processing_lock = asyncio.Lock()  # Prevent concurrent processing

def add_log(message: str, level: str = "info"):
    """Add log message to processing status"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = {
        "timestamp": timestamp,
        "level": level,
        "message": message
    }
    processing_status["logs"].append(log_entry)
    # Keep only last 100 log entries
    if len(processing_status["logs"]) > 100:
        processing_status["logs"] = processing_status["logs"][-100:]

async def update_progress_async():
    """Async progress tracking task"""
    global current_processor
    
    while processing_status["is_running"] and current_processor:
        try:
            if current_processor:
                processing_status["successful_rows"] = current_processor.successful_rows
                processing_status["failed_rows"] = current_processor.failed_rows
                processing_status["total_rows"] = current_processor.total_rows
                if current_processor.total_rows > 0:
                    processing_status["progress"] = (current_processor.successful_rows / current_processor.total_rows) * 100
        except Exception as e:
            logger.error(f"Error in progress tracking: {e}")
            break
        
        await asyncio.sleep(2)  # Use async sleep
    
    logger.info("Progress tracking task ended")

async def cleanup_processing():
    """Clean up processing resources"""
    global current_processor, progress_task
    
    logger.info("Starting cleanup process")
    
    # Stop progress tracking
    if progress_task and not progress_task.done():
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass
    
    # Clean up processor
    if current_processor:
        try:
            current_processor._finalize_incremental_save()
        except Exception as e:
            logger.error(f"Error during processor cleanup: {e}")
    
    # Reset global state
    processing_status["is_running"] = False
    current_processor = None
    progress_task = None
    
    logger.info("Cleanup completed")

async def run_data_processing(input_file: str, config: dict):
    """Background task to run data processing"""
    global current_processor, progress_task
    
    try:
        # Acquire lock only for initial setup
        async with processing_lock:
            # Ensure clean state
            await cleanup_processing()
            
            # Reset status completely for new processing
            processing_status["is_running"] = True
            processing_status["current_file"] = os.path.basename(input_file)  # Show just filename, not full path
            processing_status["start_time"] = datetime.now()
            processing_status["progress"] = 0
            processing_status["total_rows"] = 0
            processing_status["successful_rows"] = 0
            processing_status["failed_rows"] = 0
            processing_status["logs"] = []
            
            add_log(f"Starting processing of {input_file}")
            logger.info(f"Background task received file path: {input_file}")
            
            # Verify file exists before processing
            if not os.path.exists(input_file):
                raise Exception(f"Input file does not exist: {input_file}")
            
            file_size = os.path.getsize(input_file)
            logger.info(f"Processing file: {input_file} (size: {file_size} bytes)")
            
            # Get API key from environment
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                raise Exception("GROQ_API_KEY not found in environment variables")
            
            # Create processor
            processor = DataProcessor(
                api_key=api_key,
                batch_size=config.get("BATCH_SIZE", 100),
                max_batches=config.get("MAX_BATCHES"),
                max_concurrent_requests=config.get("MAX_CONCURRENT_REQUESTS", 10),
                requests_per_minute=config.get("REQUESTS_PER_MINUTE", 1000),
                incremental_save=True,
                incremental_save_interval=config.get("INCREMENTAL_SAVE_INTERVAL", 15)
            )
            
            # Store processor in global variable
            current_processor = processor
            
            # Generate output filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = f"processed_data_{timestamp}.xlsx"
            
            # Start async progress tracking
            progress_task = asyncio.create_task(update_progress_async())
            
            add_log("Processing started successfully")
        
        # Release lock before long-running processing
        # Run the processing (outside of lock)
        await processor.process_file(input_file, output_file)
        
        add_log("Processing completed successfully!", "success")
        
    except Exception as e:
        add_log(f"Processing error: {str(e)}", "error")
        logger.error(f"Processing error: {e}")
        
    finally:
        # Always clean up (this will make system ready for next upload)
        await cleanup_processing()
        
        # Clean up the uploaded file after processing (optional - saves disk space)
        try:
            if os.path.exists(input_file):
                os.remove(input_file)
                logger.info(f"Cleaned up uploaded file: {input_file}")
        except Exception as cleanup_error:
            logger.warning(f"Could not clean up uploaded file {input_file}: {cleanup_error}")
        
        add_log("Processing cleanup completed - system ready for next upload")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Main page"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload")
async def upload_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Upload Excel file for processing"""
    global current_processor, progress_task
    
    try:
        logger.info(f"Upload request received. Current status - is_running: {processing_status['is_running']}, current_processor: {current_processor is not None}")
        
        # Simple check - if status says running, reject
        if processing_status["is_running"]:
            logger.warning("Upload rejected - processing already running")
            raise HTTPException(status_code=400, detail="Processing is already running")
        
        # Force cleanup if there are lingering resources (this should make uploads work again)
        if current_processor is not None or progress_task is not None:
            logger.warning("Upload detected lingering resources, forcing cleanup")
            await cleanup_processing()
            # Give a moment for cleanup to complete
            await asyncio.sleep(0.1)
        
        # Validate file type
        if not file.filename.endswith(('.xlsx', '.xls')):
            raise HTTPException(status_code=400, detail="Only Excel files (.xlsx, .xls) are allowed")
        
        # Ensure uploads directory exists
        os.makedirs("uploads", exist_ok=True)
        
        # Create unique filename to avoid conflicts between uploads
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]  # Include milliseconds
        original_name = file.filename
        name_without_ext = os.path.splitext(original_name)[0]
        extension = os.path.splitext(original_name)[1]
        unique_filename = f"{name_without_ext}_{timestamp}{extension}"
        file_path = f"uploads/{unique_filename}"
        
        logger.info(f"Saving file '{original_name}' to unique path: {file_path}")
        
        # Read file content into memory first to avoid file handle issues
        file_content = await file.read()
        
        # Save uploaded file with unique name
        with open(file_path, "wb") as buffer:
            buffer.write(file_content)
        
        # Verify file was saved
        if not os.path.exists(file_path):
            raise Exception(f"Failed to save uploaded file to {file_path}")
        
        file_size = os.path.getsize(file_path)
        logger.info(f"File saved successfully. Original: '{original_name}', Unique path: '{file_path}', Size: {file_size} bytes")
        
        add_log(f"File uploaded: {original_name} → {unique_filename} ({file_size} bytes)")
        
        # Load default config
        try:
            with open('config.json', 'r') as f:
                config = json.load(f)
                logger.info("Loaded config from config.json")
        except Exception as config_error:
            logger.warning(f"Could not load config.json: {config_error}. Using defaults.")
            config = {
                "BATCH_SIZE": 100,
                "MAX_BATCHES": None,
                "MAX_CONCURRENT_REQUESTS": 10,
                "REQUESTS_PER_MINUTE": 1000,
                "INCREMENTAL_SAVE_INTERVAL": 15
            }
        
        # Start processing in background
        logger.info(f"Starting background processing task for file: {file_path}")
        background_tasks.add_task(run_data_processing, file_path, config)
        
        add_log(f"Background processing task queued for: {original_name}")  
        
        return {
            "message": "File uploaded and processing started", 
            "original_filename": original_name,
            "unique_filename": unique_filename,
            "file_path": file_path
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Upload error: {e}")
        add_log(f"Upload error: {str(e)}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
async def get_status():
    """Get current processing status"""
    global current_processor, progress_task
    
    # Return a clean copy of status without the processor object
    status_copy = processing_status.copy()
    # Convert datetime to string for JSON serialization
    if status_copy.get("start_time"):
        status_copy["start_time"] = status_copy["start_time"].isoformat()
    
    # Add processor status for debugging
    status_copy["processor_exists"] = current_processor is not None
    status_copy["progress_task_active"] = progress_task is not None and not progress_task.done()
    status_copy["processing_lock_active"] = processing_lock.locked()
    
    return status_copy

@app.get("/logs")
async def get_logs():
    """Get processing logs"""
    return {"logs": processing_status["logs"]}

@app.post("/stop")
async def stop_processing():
    """Stop current processing"""
    global current_processor, progress_task
    
    logger.info(f"Stop request received. Current status - is_running: {processing_status['is_running']}, current_processor: {current_processor is not None}")
    
    if processing_status["is_running"] or current_processor or progress_task:
        try:
            add_log("Processing stop requested by user", "warning")
            await cleanup_processing()
            add_log("Processing stopped by user", "warning")
            logger.info("Processing stopped successfully")
            return {"message": "Processing stopped successfully"}
            
        except Exception as e:
            logger.error(f"Error stopping processing: {e}")
            # Force cleanup even if there was an error
            await cleanup_processing()
            add_log(f"Error stopping processing: {str(e)}, but forced cleanup completed", "error")
            return {"message": f"Processing stopped with errors: {str(e)}"}
    else:
        return {"message": "No processing is currently running"}

@app.get("/download/{filename}")
async def download_file(filename: str):
    """Download processed file"""
    file_path = f"output/{filename}"
    if os.path.exists(file_path):
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    else:
        raise HTTPException(status_code=404, detail="File not found")

@app.get("/files")
async def list_files():
    """List all output files"""
    try:
        output_files = []
        if os.path.exists("output"):
            for file in os.listdir("output"):
                if file.endswith(('.xlsx', '.csv')):
                    file_path = os.path.join("output", file)
                    stat = os.stat(file_path)
                    output_files.append({
                        "name": file,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    })
        
        return {"files": output_files}
    except Exception as e:
        logger.error(f"Error listing files: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/config")
async def get_config():
    """Get current configuration"""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        return config
    except:
        return {
            "BATCH_SIZE": 100,
            "MAX_BATCHES": None,
            "MAX_CONCURRENT_REQUESTS": 10,
            "REQUESTS_PER_MINUTE": 1000,
            "INCREMENTAL_SAVE_INTERVAL": 15
        }

@app.post("/config")
async def update_config(config: dict):
    """Update configuration"""
    try:
        if processing_status["is_running"]:
            raise HTTPException(status_code=400, detail="Cannot update config while processing is running")
        
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=2)
        
        add_log("Configuration updated")
        return {"message": "Configuration updated successfully"}
        
    except Exception as e:
        logger.error(f"Error updating config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ready")
async def check_ready():
    """Check if system is ready for new uploads"""
    global current_processor, progress_task
    
    is_ready = (
        not processing_status["is_running"] 
        and current_processor is None 
        and (progress_task is None or progress_task.done())
        and not processing_lock.locked()
    )
    
    return {
        "ready": is_ready,
        "is_running": processing_status["is_running"],
        "processor_exists": current_processor is not None,
        "progress_task_active": progress_task is not None and not progress_task.done(),
        "lock_active": processing_lock.locked()
    }

@app.post("/reset")
async def reset_system():
    """Reset system state (for debugging)"""
    global current_processor, progress_task
    
    logger.info("System reset requested")
    
    try:
        await cleanup_processing()
        add_log("System reset completed", "info")
        return {"message": "System reset successfully"}
    except Exception as e:
        logger.error(f"Error during system reset: {e}")
        return {"message": f"System reset completed with errors: {str(e)}"}

@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "processing_active": processing_status["is_running"]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)