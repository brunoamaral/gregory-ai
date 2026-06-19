#!/bin/sh
set -e

wait_for_db() {
    echo "Waiting for database..."
    until python manage.py check --database default >/dev/null 2>&1; do
        sleep 2
    done
}

# Only run init tasks when starting the web server, not for one-off commands
# (e.g. docker compose run gregory sh, or manage.py shell)
if [ "$1" = "gunicorn" ] || echo "$DJANGO_DEBUG" | grep -qiE "^(true|1|yes)$"; then
    wait_for_db
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
fi

if echo "$DJANGO_DEBUG" | grep -qiE "^(true|1|yes)$"; then
    exec python manage.py runserver 0.0.0.0:8000
else
    exec "$@"
fi
