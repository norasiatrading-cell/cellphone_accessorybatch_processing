# 🌐 Data Processing Web Interface

Perfect solution for VPS deployment without SSH access! Control your data processing through a beautiful web interface.

## ✨ Features:

### 🖥️ **Modern Web Interface:**
- **Drag & Drop Upload** - Simply drag Excel files to start processing
- **Real-time Dashboard** - Live progress, statistics, and status updates
- **Processing Logs** - Watch the processing happen in real-time
- **File Management** - Download results directly from browser
- **Responsive Design** - Works on desktop, tablet, and mobile

### 🛡️ **Robust Processing:**
- **Background Processing** - Upload and close browser, processing continues
- **Graceful Shutdown** - Stop processing anytime with data preservation
- **Error Handling** - Network issues and crashes handled gracefully
- **Progress Persistence** - Resume monitoring from any device

## 🚀 Quick Start:

### 1. Create `.env` file:
```bash
echo "GROQ_API_KEY=your_actual_groq_api_key_here" > .env
```

### 2. Start Web Interface:
```bash
# Make script executable
chmod +x docker-helper.sh

# Start web interface (builds automatically)
./docker-helper.sh start
```

### 3. Access Web Interface:
```
🌐 Open browser: http://localhost:8080
📱 Or from another device: http://your-vps-ip:8080
```

### 4. Use the Interface:
1. **Upload Excel File** - Drag & drop or click to browse
2. **Monitor Progress** - Watch real-time processing status
3. **View Logs** - See detailed processing information
4. **Download Results** - Get processed files when complete

## 📊 Web Interface Screenshots:

```
┌─────────────────────────────────────────────────┐
│  🚀 Data Processing Interface                   │
├─────────────────────────────────────────────────┤
│  Status: Processing  │  Success: 87%  │ ETA: 2h │
│  Progress: [████████████░░░] 85%                │
├─────────────────────────────────────────────────┤
│  📁 Upload Area (Drag & Drop)                   │
│  📊 Live Logs        │  📥 Download Files       │
└─────────────────────────────────────────────────┘
```

## 🐳 Docker Commands:

### **Easy Method (Recommended):**
```bash
# Start web interface
./docker-helper.sh start

# View logs
./docker-helper.sh logs

# Check status
./docker-helper.sh status

# Stop interface
./docker-helper.sh stop
```

### **Manual Docker Commands:**
```bash
# Build and start
docker build -t data-processor-web .
docker run -d --name data-processing-web \
  -p 8080:8080 \
  -v $(pwd)/output:/app/output \
  -v $(pwd)/uploads:/app/uploads \
  data-processor-web

# Access at http://localhost:8080
```

## 🌍 VPS Deployment:

### **1. Transfer Files to VPS:**
```bash
# From your local machine
scp -r ./ user@your-vps-ip:~/data-processing/
```

### **2. On VPS:**
```bash
cd ~/data-processing

# Create API key file
echo "GROQ_API_KEY=your_key_here" > .env

# Start web interface
./docker-helper.sh start

# Access from browser
# http://your-vps-ip:8080
```

## 🎯 Perfect for Non-SSH VPS:

### **Why This Solution is Ideal:**
- ✅ **No SSH Required** - Everything through web browser
- ✅ **Any Device Access** - Phone, tablet, laptop
- ✅ **Remote Monitoring** - Check progress from anywhere
- ✅ **File Management** - Upload/download through browser
- ✅ **User Friendly** - No command line knowledge needed

## 📱 API Endpoints:

The web interface also provides REST API endpoints:

```bash
# Upload file
POST /upload

# Get status
GET /status

# Get logs
GET /logs

# Stop processing
POST /stop

# Download file
GET /download/{filename}

# Health check
GET /health
```

## 🔧 Configuration:

Web interface loads settings from `config.json`:
```json
{
    "BATCH_SIZE": 100,
    "MAX_BATCHES": null,
    "MAX_CONCURRENT_REQUESTS": 10,
    "REQUESTS_PER_MINUTE": 1000,
    "INCREMENTAL_SAVE_INTERVAL": 15
}
```

## 📂 Directory Structure:

```
/app/                 # Container working directory
├── web_interface.py  # FastAPI web server
├── main.py          # Data processing script
├── templates/       # Web UI templates
├── uploads/         # Uploaded files (mounted)
├── output/          # Processed files (mounted)
├── backups/         # Backup files (mounted)
└── logs/            # Log files (mounted)
```

## 🛠️ Troubleshooting:

### **Web Interface Not Loading:**
```bash
# Check if container is running
docker ps

# Check logs
./docker-helper.sh logs

# Restart interface
./docker-helper.sh stop
./docker-helper.sh start
```

### **Upload Issues:**
- Check file size limits (default: reasonable for Excel files)
- Ensure API key is set in `.env` file
- Check container logs for errors

### **Processing Stuck:**
- Use web interface "Stop" button
- Check logs for error messages
- Restart container if needed

## 🚨 Important Notes:

### **For Production VPS:**
1. **Firewall**: Open port 8080 on your VPS
2. **Security**: Consider adding authentication for public access
3. **SSL**: Use reverse proxy (nginx) for HTTPS in production
4. **Resources**: Monitor VPS memory/CPU usage

### **File Size Limits:**
- Default upload limit: Reasonable for typical Excel files
- Increase if needed by modifying FastAPI settings
- Monitor disk space on VPS

This web interface makes your data processing script accessible from any browser without needing SSH access to your VPS!