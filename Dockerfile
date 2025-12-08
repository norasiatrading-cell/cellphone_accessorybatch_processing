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

# Copy application files
COPY batch_main.py .
COPY streamlit_app.py .
COPY batch_config.json .

# Create non-root user and fix permissions
RUN useradd --create-home --shell /bin/bash appuser \
 && chown -R appuser:appuser /app
USER appuser

# --- FIXED CMD (Shell Form) ---
# Removing the brackets [] tells Docker to run this in a shell.
# This ensures $PORT is correctly converted to a number (e.g., 8080).
# CMD streamlit run streamlit_app.py \
CMD python -m http.server $PORT
    --server.port=$PORT \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false
