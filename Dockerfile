FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
COPY garmin_mcp ./garmin_mcp
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install . && useradd --create-home appuser
USER appuser

EXPOSE 8000
# Migrations run on start (single replica); proxy headers so OAuth redirects use https behind Azure ingress
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
