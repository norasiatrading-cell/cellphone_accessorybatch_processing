FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Install minimal dependencies
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Copy only the debug script
COPY debug_run.py .

# --- TEMPORARILY RUN AS ROOT ---
# This rules out any permission issues with 'appuser'
# USER appuser 

# --- RUN DEBUG SCRIPT ---
# This bypasses shell variable expansion issues entirely
CMD ["python", "debug_run.py"]
