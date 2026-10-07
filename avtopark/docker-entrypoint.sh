#!/bin/sh
# Prepares the database before the web server starts.
set -e
if [ "${LOAD_DEMO_DATA:-0}" = "1" ] && [ ! -f /app/instance/avtopark.db ]; then
    flask --app avtopark seed-demo --yes
else
    # Creates missing tables and the first administrator (ADMIN_PASSWORD is required on first start).
    flask --app avtopark init-db
fi
exec "$@"
