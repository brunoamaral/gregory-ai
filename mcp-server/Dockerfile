FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir mcp==2.0.0 "httpx2>=2.9,<3"

COPY gregory_mcp ./gregory_mcp

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8001 \
    PYTHONUNBUFFERED=1

EXPOSE 8001

CMD ["python", "-m", "gregory_mcp"]
