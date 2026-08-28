# syntax=docker/dockerfile:1

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RESEARCHTWIN_HOST=0.0.0.0 \
    RESEARCHTWIN_PORT=8000 \
    RESEARCHTWIN_DATA_DIR=/app/runtime_data \
    RESEARCHTWIN_LOG_LEVEL=INFO

WORKDIR /app

RUN groupadd --system --gid 10001 researchtwin \
    && useradd --system --uid 10001 --gid researchtwin --home-dir /app --shell /usr/sbin/nologin researchtwin

COPY pyproject.toml README.md ./
COPY src ./src/
COPY server.py ./server.py
COPY scripts/deployment_check.py ./scripts/deployment_check.py

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir . \
    && mkdir -p /app/runtime_data \
    && chown -R researchtwin:researchtwin /app

USER researchtwin

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python scripts/deployment_check.py --probe-url http://127.0.0.1:8000/mcp || exit 1

# Dependencies are installed above during image build. Runtime starts the
# persistent official SDK transport directly and never invokes uvx.
CMD ["python", "-m", "researchtwin_mcp.remote_entry"]
