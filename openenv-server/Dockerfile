FROM python:3.11-slim

WORKDIR /app

# Install curl for health check
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY openenv-server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy openenv-server application
COPY openenv-server/ .

# Copy servers module from evaluation
COPY evaluation/servers /app/servers

# Set PYTHONPATH to find servers module
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8005

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8005/health || exit 1

# Run server
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8005"]
