# ==========================================
# Multi-stage Dockerfile for FastAPI Backend
# ==========================================

ARG PYTHON_VERSION=3.11

# ==================== STAGE 1: Builder ====================
FROM python:${PYTHON_VERSION}-slim as builder

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ==================== STAGE 2: Runtime ====================
FROM python:${PYTHON_VERSION}-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install runtime dependencies only (including Docker CLI for log viewing)
RUN apt-get update && apt-get install -y \
    curl \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user and add to docker group
RUN groupadd -r appuser && useradd -r -g appuser appuser && \
    usermod -aG docker appuser

# Set working directory
WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY ./app /app/app

# Create necessary directories
RUN mkdir -p /app/tokens /app/logs

# Note: Running as root to allow Docker socket access for log viewing
# In production, use a separate log aggregator service

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
