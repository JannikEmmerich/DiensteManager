#!/bin/sh
# Applies the retention periods of the data lifecycle once a day (see README). The web
# container runs the migrations; this one only starts once the web container is healthy.

while true; do
    python manage.py apply_retention || echo "apply_retention failed; trying again tomorrow" >&2
    sleep 86400
done
