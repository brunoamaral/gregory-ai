# syntax=docker/dockerfile:1

# === Stage 1: Builder ===
# Install Python dependencies using pip wheel for faster deployment
FROM python:3.12-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
	build-essential \
	gcc \
	libpq-dev \
	python3-dev \
	libffi-dev \
	libxml2-dev \
	libxslt1-dev \
	zlib1g-dev \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy requirements and create wheels
COPY django/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
	&& pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# === Stage 2: Runtime ===
# Final slim image with only runtime dependencies
FROM python:3.12-slim

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
	libpq5 \
	libgomp1 \
	&& rm -rf /var/lib/apt/lists/*

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PYTHONPATH="/code"

WORKDIR /code

# Install wheels from builder via bind-mount (never copies them into the image)
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
	--mount=type=bind,source=django/requirements.txt,target=/tmp/requirements.txt \
	pip install --no-cache-dir --no-compile --no-index --find-links /wheels -r /tmp/requirements.txt

# Copy application code
COPY django/ /code/

# Copy and register entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Expose port
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
# --log-level defaults to info; override per-deployment without a rebuild by
# setting GUNICORN_CMD_ARGS="--log-level debug" (gunicorn reads that env var
# and its values take precedence over the CMD below).
CMD ["gunicorn", "--workers", "4", "--threads", "2", "--timeout", "300", "--log-level", "info", "-b", "0.0.0.0:8000", "admin.wsgi"]
