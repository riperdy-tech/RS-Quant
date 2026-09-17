# QuantDesk Production Dockerfile (§16.3)
# Multi-stage build with non-root user, persistent volumes, and health checks.

FROM node:22-bookworm-slim AS web-builder
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runner
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

# Install runtime dependencies (OpenMP for LightGBM, libgomp1, curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root system user
RUN groupadd -g 10001 quantdesk && \
    useradd -u 10001 -g quantdesk -m -s /bin/bash quantdesk

WORKDIR /app

# Copy application source and configurations
COPY pyproject.toml ./
COPY configs/ ./configs/
COPY fixtures/ ./fixtures/
COPY src/ ./src/
COPY --from=web-builder /app/web/dist ./web/dist

# Install Python dependencies
RUN pip install --no-cache-dir .

# Create persistent storage directories owned by quantdesk
RUN mkdir -p /data/accounts /data/logs /data/backups && \
    chown -R quantdesk:quantdesk /data /app

USER quantdesk

EXPOSE 8000

# Private loopback/internal health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/api/v1/health || exit 1

VOLUME ["/data"]

ENTRYPOINT ["python", "-m", "uvicorn", "quantdesk.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
