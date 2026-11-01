# Stage 1: Build virtual environment
FROM python:3.12-alpine AS builder

WORKDIR /build

RUN apk add --no-cache gcc musl-dev libffi-dev

COPY app/requirements.txt requirements.txt
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Stage 2: Hardened Runtime (PSS Restricted Compliant)
FROM python:3.12-alpine AS runner

WORKDIR /app

# Create unprivileged user with UID 101 (matching non-root PSS profile)
RUN addgroup -g 101 appgroup && \
    adduser -u 101 -G appgroup -s /sbin/nologin -D appuser

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy application code
COPY app /app/app

# Set non-root user permissions
USER 101:101

EXPOSE 8000

# Graceful SIGTERM handling with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
