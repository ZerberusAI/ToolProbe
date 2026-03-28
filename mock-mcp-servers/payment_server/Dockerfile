FROM python:3.11-slim

WORKDIR /app

# Install curl for health checks and dependencies
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir \
    fastapi>=0.109.0 \
    uvicorn>=0.27.0 \
    pyyaml>=6.0.0 \
    pydantic>=2.0.0

# Copy base module
COPY base/ /app/base/

# Copy payment server
COPY payment_server/ /app/payment_server/

# Set Python path
ENV PYTHONPATH=/app

# Default port
ENV PORT=8010
ENV HOST=0.0.0.0

# Expose port
EXPOSE 8010

# Run the server
CMD ["python", "-m", "payment_server.server"]
