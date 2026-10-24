#!/bin/sh
# Brings the database up to date before the app starts.
set -e

python manage.py migrate --noinput
python manage.py createcachetable

exec "$@"
