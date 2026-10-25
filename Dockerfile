FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# gettext provides msgfmt, needed to compile the translations (compilemessages).
RUN apt-get update \
    && apt-get install --yes --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the dependencies first so that this layer stays in the build cache.
COPY pyproject.toml ./
RUN pip install .

COPY . .
RUN DJANGO_SECRET_KEY=build-only python manage.py compilemessages \
    && DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

# The app runs without root privileges; the code stays read-only for it.
RUN useradd --no-create-home --uid 10001 app
USER app

# Git commit the image was built from (set by the CI build), shown next to the version.
ARG APP_REVISION=""
ENV APP_REVISION=${APP_REVISION}

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-", "--no-control-socket"]
