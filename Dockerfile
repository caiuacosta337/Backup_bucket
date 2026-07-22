# Base image with a slim Python runtime for the backup service.
FROM python:3.12-slim

# Avoid bytecode files and keep logs visible in container output.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# All files are copied and executed from /app.
WORKDIR /app

# Install only the runtime dependencies needed by the backup process.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the service code and provide a default config path inside the image.
COPY backup.py ./
COPY config.example.yml ./
COPY config.example.yml ./config.yml

# Prepare writable folders for bind-mounted state and source data.
RUN mkdir -p /app/state /data/source

# Default command can be overridden at docker run time.
CMD ["python", "backup.py", "--config", "/app/config.yml"]