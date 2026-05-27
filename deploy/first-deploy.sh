#!/usr/bin/env bash
# one-shot post-provisioning installer.
# run as the `deploy` user on a fresh Hetzner box that already has python3.12,
# openjdk-21-jre-headless, git, caddy, ufw, fail2ban installed (per the
# provisioning brief).
#
# usage:  cd /srv && git clone https://github.com/tink3rtanner/ehds-api-demo && cd ehds-api-demo && bash deploy/first-deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> python venv + deps"
python3.12 -m venv .venv
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]"

echo "==> caching HL7 validator jar (skip if already present)"
./fetch_validator.sh

echo "==> running tests (~30s, skips java validator tests)"
pytest -q --maxfail=5

echo "==> installing systemd service"
sudo mkdir -p /etc/ehds-api
[[ -f /etc/ehds-api/env ]] || sudo cp .env.example /etc/ehds-api/env
sudo install -m 644 deploy/ehds-api.service /etc/systemd/system/
sudo systemctl daemon-reload

cat <<'TODO'

==========================================
  manual finishing steps:
==========================================

1. edit /etc/ehds-api/env — set EHDS_BASE_URL + EHDS_ISSUER to your https://...
   and choose ENV=prod (public-facing) or ENV=dev (viewer at /ui enabled).

2. start the service:
       sudo systemctl enable --now ehds-api
       sudo systemctl status ehds-api --no-pager

3. set up caddy:
       sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
       sudo sed -i 's/<your-domain>/your.actual.domain/g' /etc/caddy/Caddyfile
       sudo caddy validate --config /etc/caddy/Caddyfile
       sudo systemctl reload caddy

4. smoke test:
       curl -sI https://your.actual.domain/healthz
       curl -s  https://your.actual.domain/metadata | jq .fhirVersion

   see HANDOFF.md for the full runbook.
TODO
