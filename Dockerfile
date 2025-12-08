# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Environment settings
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

# Copy application files (Including the new wrapper)
COPY batch_main.py .
COPY streamlit_app.py .
COPY batch_config.json .
COPY run_app.py .

# Create non-root user and fix permissions
RUN useradd --create-home --shell /bin/bash appuser \
 && chown -R appuser:appuser /app
USER appuser

# --- FINAL FIX ---
# Instead of a complex shell command, we run the Python wrapper.
# This handles the PORT variable safely inside Python.
CMD ["python", "run_app.py"]
