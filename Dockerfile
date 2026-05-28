# Minimal Python image for an aiogram long-polling bot (no HTTP server).
FROM python:3.12-slim

# Avoid .pyc files and ensure logs are flushed immediately to the container log.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so the layer is cached across code-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code and bundled assets (images used in messages).
COPY bot ./bot
COPY assets ./assets

# Runtime state (events.json / users.json) is written here. Mounted as a volume
# in production so signups survive container recreation.
RUN mkdir -p /app/data

CMD ["python", "-m", "bot"]
