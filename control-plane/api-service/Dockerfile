# control-plane API image. Build context: control-plane/api-service (see `make build-api-image`).
# Stage 1: virtualenv with runtime dependencies only (no test tooling).
FROM python:3.12-alpine AS builder
WORKDIR /build
RUN apk add --no-cache gcc musl-dev libffi-dev
COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Stage 2: hardened runtime (PSS restricted: non-root, no writable paths needed, no shell login)
FROM python:3.12-alpine AS runner
RUN addgroup -g 101 appgroup && \
    adduser -u 101 -G appgroup -s /sbin/nologin -D appuser
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY src /app/src
USER 101:101
EXPOSE 8000
# main.py handles SIGTERM: readiness flips to 503, in-flight requests finish, then the process exits (H7).
CMD ["python", "-m", "api_service.main"]
