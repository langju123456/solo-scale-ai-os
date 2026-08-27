FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels '.[web,cloud]'

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BUILDLOG_ENV=production \
    BUILDLOG_DATABASE_URL=sqlite:////data/buildlog.db \
    BUILDLOG_RUNS_DIR=/data/runs \
    BUILDLOG_WEB_JOBS_DIR=/data/jobs \
    BUILDLOG_PROMPTS_DIR=/app/prompts

RUN groupadd --system buildlog \
    && useradd --system --gid buildlog --home-dir /home/buildlog --create-home buildlog \
    && mkdir -p /app /data/runs /data/jobs \
    && chown -R buildlog:buildlog /app /data /home/buildlog

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels buildlog \
    && rm -rf /wheels

WORKDIR /app
COPY --chown=buildlog:buildlog prompts ./prompts
COPY --chown=buildlog:buildlog alembic.ini ./alembic.ini
COPY --chown=buildlog:buildlog migrations ./migrations
USER buildlog

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)" || exit 1

CMD ["python", "-m", "buildlog.container_entrypoint"]
