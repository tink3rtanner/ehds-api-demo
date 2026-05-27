#!/usr/bin/env bash
# dev server. for prod use the systemd unit in deploy/.
set -euo pipefail
cd "$(dirname "$0")"
: "${EHDS_BASE_URL:=http://localhost:8000}"
: "${EHDS_ISSUER:=$EHDS_BASE_URL}"
: "${EHDS_DATA_DIR:=$(pwd)/data}"
: "${EHDS_CLIENT_REGISTRY:=$(pwd)/config/clients.json}"
: "${EHDS_JWKS_PATH:=$(pwd)/config/server_keys}"
: "${EHDS_VALIDATOR_JAR:=$(pwd)/.cache/validator_cli.jar}"
: "${ENV:=dev}"
export EHDS_BASE_URL EHDS_ISSUER EHDS_DATA_DIR EHDS_CLIENT_REGISTRY EHDS_JWKS_PATH EHDS_VALIDATOR_JAR ENV
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
