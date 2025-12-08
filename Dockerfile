# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Environment settings
# Pythondontwritebytecode: Prevents Python from writing .pyc files
# Pythonunbuffered: Forces stdout/stderr to be flushed immediately (vital for Railway logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create folders
RUN mkdir -p /app/output /app/batches /app/backups /app/logs /app/uploads

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY batch_main.py .
COPY streamlit_app.py .
COPY batch_config.json .

# Create non-root user and fix permissions
RUN useradd --create-home --shell /bin/bash appuser \
 && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# --- CHANGE 1: REMOVED INTERNAL HEALTHCHECK ---
# Railway handles health checks externally. 
# An internal check that fails (e.g., due to slow startup) can cause Railway to kill a valid app.

# --- CHANGE 2: OPTIMIZED CMD FOR RAILWAY ---
# Added:
# --server.fileWatcherType=none: Prevents CPU spikes on Linux containers
# --server.enableCORS=false: Prevents timeouts behind Railway's load balancer
# --server.enableXsrfProtection=false: Prevents 403/Connection issues on some setups
CMD ["sh", "-c", "streamlit run streamlit_app.py \
    --server.port=$PORT \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.fileWatcherType=none \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false"]
