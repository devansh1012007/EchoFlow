FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd -g 1000 appgroup && \
    useradd -u 1000 -g appgroup -s /bin/bash -m appuser && \
    apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        libpq-dev \
        gcc \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

# Remove compiled bytecode from all Python versions to avoid cross-version contamination
RUN find /app -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

RUN chown -R appuser:appgroup /app /root/.cache

USER appuser
