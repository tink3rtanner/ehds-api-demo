#!/usr/bin/env bash
# wrapper that lets the preview MCP launch uvicorn without touching .venv/pyvenv.cfg
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH="$(pwd)/.venv/lib/python3.11/site-packages:$(pwd)"
export EHDS_BASE_URL=http://127.0.0.1:8088
export EHDS_ISSUER=http://127.0.0.1:8088
export EHDS_DATA_DIR="$(pwd)/data"
export EHDS_CLIENT_REGISTRY="$(pwd)/config/clients.json"
export EHDS_JWKS_PATH="$(pwd)/config/server_keys"
export ENV=dev
exec /usr/local/bin/python3.11 -m uvicorn app.main:app --host 127.0.0.1 --port 8088
