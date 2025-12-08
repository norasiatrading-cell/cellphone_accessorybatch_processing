# Streamlit Batch Data Processor UI

A user-friendly web interface for the batch data processing system using Streamlit.

## Features

- 📤 **File Upload**: Upload Excel (.xlsx, .xls) or CSV files directly through the web interface
- 🚀 **Background Processing**: Submit batch jobs that run in the background - you can close the browser tab
- 📊 **Real-time Monitoring**: View live progress updates and statistics
- 📜 **Live Logs**: Monitor processing logs in real-time
- 📁 **File Management**: View uploaded files and download processed results
- ⏸️ **Process Control**: Start and stop batch processing jobs

## Installation

1. Install required dependencies:
```bash
pip install -r requirements.txt
```

2. Ensure you have your `.env` file with your Groq API key:
```
GROQ_API_KEY=your_api_key_here
```

## Usage

### Starting the Streamlit App

Run the Streamlit app with:
```bash
streamlit run streamlit_app.py
```

The app will open in your default browser at `http://localhost:8501`

### Using the Interface

#### 1. Upload File
- Click on "Choose an Excel or CSV file" in the sidebar
- Select your data file (must have the required columns)
- Preview the data to verify it loaded correctly

#### 2. Configure Processing
- Optionally set "Max Rows" to limit the number of rows processed
- Leave empty to process all rows

#### 3. Start Processing
- Click "🚀 Start Processing" button
- The batch job will be submitted and start running in the background
- You can safely close the browser tab - processing will continue

#### 4. Monitor Progress
- **Status Tab**: View real-time statistics including:
  - Processing status
  - Total rows and progress percentage
  - Success/failure counts
  - Elapsed time
- Enable "Auto-refresh" to automatically update every 3 seconds

#### 5. View Logs
- **Logs Tab**: Monitor detailed processing logs
- Adjust the number of lines to display
- Click "🔄 Refresh Logs" to update

#### 6. Download Results
- **Files Tab**: 
  - View uploaded files in the "Uploaded Files" section
  - Download processed results from the "Output Files" section
  - Click "⬇️ Download" to get your processed file

### Process Control

- **Stop Processing**: Click "🛑 Stop Processing" to terminate the running batch job
- **Status Indicator**: Shows whether a process is currently running
- **PID Tracking**: Displays the process ID for monitoring

## Background Processing

The app uses a clever background processing mechanism:

1. When you click "Start Processing", the app spawns a new Python process running `batch_main.py`
2. The process ID is saved to `batch_process.pid`
3. The main process updates `batch_results.json` with real-time progress
4. The Streamlit app reads this file to display updates
5. You can close the browser - the Python process continues independently
6. When you return, the app reconnects to the running process

## File Structure

```
streamlit_app.py          # Main Streamlit application
batch_main.py             # Background processing script
batch_config.json         # Configuration (input file, max rows)
batch_results.json        # Real-time progress updates
batch_process.pid         # Running process ID
batch_processing.log      # Detailed processing logs
uploads/                  # Uploaded files directory
output/                   # Processed results directory
```

## Required File Headers

Your input file must contain these columns:
- `Product_title`
- `Product_description`
- `sales_attribute` (optional)
- Other columns as needed by your processing logic

## Tips

1. **Auto-refresh**: Enable auto-refresh on the Status tab to see live updates
2. **Log Monitoring**: Check the Logs tab if you encounter any issues
3. **Background Processing**: Feel free to close the browser - processing continues
4. **Multiple Jobs**: Only one batch job can run at a time
5. **File Management**: Old files remain in uploads/ and output/ - clean manually if needed

## Troubleshooting

### Process Not Starting
- Check that `batch_main.py` is in the same directory
- Verify your `.env` file contains `GROQ_API_KEY`
- Check `batch_processing.log` for errors

### Process Not Stopping
- The app tries SIGTERM first, then SIGKILL
- Check if the PID exists: `ps -p <PID>`
- Manually kill if needed: `kill -9 <PID>`

### Progress Not Updating
- Ensure `batch_results.json` is being written
- Check file permissions
- Verify the process is actually running

### File Upload Errors
- Verify file format (.xlsx, .xls, or .csv)
- Check file has required columns
- Ensure file is not corrupted

## Advanced Usage

### Custom Port
Run on a different port:
```bash
streamlit run streamlit_app.py --server.port 8502
```

### Remote Access
Allow access from other machines:
```bash
streamlit run streamlit_app.py --server.address 0.0.0.0
```

### Production Deployment
For production use, consider:
- Using a process manager like `supervisor` or `systemd`
- Setting up authentication
- Using a reverse proxy (nginx)
- Implementing proper logging and monitoring

## Notes

- The batch processing uses Groq's Batch API which has a ~50% cost savings
- Batch jobs typically complete in 5-30 minutes depending on queue
- Progress updates are approximate until batch completion
- You cannot cancel a submitted batch job on Groq's servers

## Support

For issues or questions, check:
1. `batch_processing.log` for detailed error messages
2. Console output when running Streamlit
3. Groq API documentation for API-specific issues
