# Single app image with THREE entrypoints (selected via command override):
#   init : python -m hr_core.seed                        (seed shared SQLite)
#   api  : uvicorn api.main:app --host 0.0.0.0 --port 8000 (web console + REST)
#   mcp  : python -m mcp_server                          (MCP streamable HTTP :8001)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HR_DB_PATH=/data/hr.db

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY hr_core/ ./hr_core/
COPY api/ ./api/
COPY mcp_server/ ./mcp_server/

# Shared ephemeral DB lives here (EmptyDir volume in ACA / named volume in compose).
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000 8001

# Default to the API/web surface; overridden per-container in compose / Bicep.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
