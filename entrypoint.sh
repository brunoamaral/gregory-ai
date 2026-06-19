#!/bin/sh
set -e
python manage.py migrate --noinput
python manage.py collectstatic --noinput

if echo "$DJANGO_DEBUG" | grep -qiE "^(true|1|yes)$"; then
    exec python manage.py runserver 0.0.0.0:8000
else
    exec "$@"
fi
