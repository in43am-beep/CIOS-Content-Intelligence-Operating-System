# CIOS — $0 deploy (Render free web service, Docker runtime).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Single worker REQUIRED: SQLite + in-process quota/throttle state are only
# correct with one worker. Auth + JWT on (AUTH_REQUIRED=true) in production.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
