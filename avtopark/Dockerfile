# Production image: gunicorn + Flask, runs as an unprivileged user.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=avtopark

WORKDIR /app
RUN useradd --create-home --uid 1000 avtopark

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY avtopark ./avtopark
COPY wsgi.py docker-entrypoint.sh ./
RUN mkdir -p /app/instance /app/backups && chown -R avtopark:avtopark /app/instance /app/backups

USER avtopark
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-", "wsgi:app"]
