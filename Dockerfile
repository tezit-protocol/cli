FROM python:3.12-slim

WORKDIR /app

# Create non-root user and install curl for health checks
RUN useradd --create-home --shell /bin/bash appuser \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Switch to non-root user
USER appuser

# Expose the application port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["python", "-m", "src.main"]
