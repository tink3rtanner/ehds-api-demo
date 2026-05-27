# ehds-api — VPS bring-up runbook

Hetzner Cloud CX33 (4 vCPU / 8 GB / 80 GB) + Ubuntu 24.04 + Caddy + (systemd OR
docker-compose). Targets a hobby-scale connectathon demo. Synthetic data only.

## Prerequisites

- domain pointing at the box (both A and AAAA)
- `deploy` user already created during initial provisioning (see provisioning brief)
- ufw enabled with 22/80/443
- `caddy`, `python3.12`, `python3.12-venv`, `openjdk-21-jre-headless`, `git` installed

## Path A — systemd (recommended for the first deploy)

```bash
# as deploy user
cd /srv && sudo mkdir -p ehds-api && sudo chown deploy:deploy ehds-api && cd ehds-api
git clone <repo-url> .
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
./fetch_validator.sh    # caches validator_cli.jar
python -m scripts.seed  # writes data/
```

Create env file:

```bash
sudo mkdir -p /etc/ehds-api
sudo cp .env.example /etc/ehds-api/env
sudo nano /etc/ehds-api/env  # edit EHDS_BASE_URL etc.
```

Register the first client:

```bash
# generate keypair for the client (you can do this on a laptop)
openssl genrsa -out client.pem 2048
openssl rsa -in client.pem -pubout -out client.pub

# convert to JWK and register
python -m app.tools.register_client --client-id my-app --jwk-from-pem client.pub --scope system/*.read
```

Install service + reload:

```bash
sudo install -m 644 deploy/ehds-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ehds-api
sudo systemctl status ehds-api
```

Configure caddy:

```bash
sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo sed -i 's/<your-domain>/your.actual.domain/g' /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Smoke test:

```bash
curl -i https://your.actual.domain/healthz       # 200 ok
curl -i https://your.actual.domain/metadata      # CapabilityStatement
curl -i https://your.actual.domain/.well-known/smart-configuration
```

## Path B — docker-compose (preferred once you add postgres/hapi)

```bash
cd /srv/ehds-api
cp ../.env.example .env  # edit
cp deploy/Caddyfile.example deploy/Caddyfile  # edit
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml logs -f ehds-api
```

## Updating

```bash
cd /srv/ehds-api
git pull --ff-only
source .venv/bin/activate
pip install -e .
sudo systemctl restart ehds-api
```

## Backups

The `data/` directory is the entire persistent store. snapshot it nightly:

```bash
sudo tar czf /var/backups/ehds-$(date +%F).tgz -C /srv/ehds-api data config
```

(Hetzner snapshots are fine for hobby scale; enable them in the cloud console.)

## Rollback

```bash
sudo systemctl stop ehds-api
sudo tar xzf /var/backups/ehds-<date>.tgz -C /srv/ehds-api
sudo systemctl start ehds-api
```

## Observability

Logs go to systemd journal:

```bash
journalctl -u ehds-api -f --output=json | jq .
```

Each request emits one structured JSON line. No Authorization headers ever
land in the log (verified by `tests/test_security.py::test_logs_do_not_leak_authorization`).

## Hardening checklist (final pass before posting the URL)

- [ ] `ENV=prod` in `/etc/ehds-api/env` — disables `/docs`, `/openapi.json`
- [ ] `EHDS_RATE_LIMIT_PER_MIN` tuned (default 240; lower for very small VPS)
- [ ] `ufw status` shows only 22/80/443
- [ ] Hetzner cloud firewall mirrors ufw
- [ ] root SSH login disabled (`PermitRootLogin no` in `/etc/ssh/sshd_config`)
- [ ] `fail2ban` active
- [ ] `unattended-upgrades` active
- [ ] caddy serving valid LE cert (check at https://www.ssllabs.com/ssltest/)
- [ ] no credentials in any file in `/srv/ehds-api` (only public JWKs for clients)
