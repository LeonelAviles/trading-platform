# Backend image: FastAPI + NautilusTrader + DuckDB. Python 3.12 per
# PLATFORM-SPEC.md §5 Phase 0 (bare-metal dev may use a newer interpreter).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/ /app/backend/

# Derived data, raw data and job folders are bind-mounted by docker-compose.
EXPOSE 8123
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8123"]
