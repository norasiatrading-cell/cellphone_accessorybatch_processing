# Use Python 3.11 slim image for better performance
FROM python:3.11-slim

# Set working directory in container
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

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser \
 && chown -R appuser:appuser /app
USER appuser

# Healthcheck using dynamic Railway port
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:$PORT/_stcore/health || exit 1

# Start Streamlit using Railway-assigned port
CMD ["sh", "-c", "streamlit run streamlit_app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true"]
