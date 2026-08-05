FROM python:3.12-slim

WORKDIR /app

# Dependencies live in pyproject.toml only — this file must not hardcode
# a second copy that can drift from it.
COPY pyproject.toml ./
COPY gregory_mcp ./gregory_mcp
COPY healthcheck.py ./
RUN pip install --no-cache-dir .

# Unauthenticated and internet-reachable via nginx /mcp/ — don't run as root.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser
USER appuser

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8001 \
    PYTHONUNBUFFERED=1

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python healthcheck.py

CMD ["python", "-m", "gregory_mcp"]
