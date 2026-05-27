# multi-stage build for ehds-api.
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --prefix=/install .[dev]==0.1.0 || pip install --prefix=/install .

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# java for the optional validator (~250MB; switch to debian:slim + openjdk-21-jre-headless if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jre-headless ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
WORKDIR /srv/ehds-api
COPY app ./app
COPY scripts ./scripts
RUN useradd --system --uid 1001 ehds && mkdir -p /data /etc/ehds-api && chown -R ehds /data
USER ehds
EXPOSE 8000
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "--workers", "2", \
     "--bind", "0.0.0.0:8000", "--access-logfile", "-", "app.main:app"]
