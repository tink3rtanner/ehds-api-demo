# EHDS Demo · deployment handoff

This file is the bridge between the provisioning agent (Hetzner brings up the box) and the first-deploy agent (installs and starts the app). Keep both happy and the demo is online.

## Repo
- GitHub: <https://github.com/tink3rtanner/ehds-api-demo>
- License: Apache-2.0
- Branch to deploy: `main`

## Target box (per the provisioning brief)
- Hetzner Cloud · **CX33** (4 vCPU / 8 GB / 80 GB · ~€7/mo)
- Ubuntu 24.04 LTS · Nuremberg (or Helsinki)
- ufw + cloud firewall: inbound 22/80/443 only
- non-root `deploy` user · password ssh disabled · root ssh disabled
- caddy installed (TLS termination + auto-LE)
- openjdk-21-jre-headless installed (for the HL7 validator)
- docker.io + docker-compose-plugin installed (for the future expansion path; v0 uses systemd)
- domain pointing at the box (A + AAAA) — see [deploy/README.md](deploy/README.md)

## What this demo is (1-paragraph executive)

Open-source FHIR R4 server implementing the [EU Health Data API IG](https://build.fhir.org/ig/euridice-org/eu-health-data-api/en/) end-to-end. Synthetic data only. Ten EU-flavoured patients with full clinical compartments. Four priority-category documents compiled on demand (Patient Summary, Lab, Discharge, Imaging) as `Bundle.type=document` with the correct HL7 EU profile URLs. SMART Backend Services auth (JWT client assertion). PDQm full search + `$match`. ITI-67/68/105 transactions. 1087 passing tests. Also ships a pretty read-only viewer at `/ui` for connectathon demos — patient panel, document viewer, server stats, live curl snippets with a fresh dev bearer.

## First-deploy runbook (after the box is up + DNS resolves)

```bash
ssh deploy@<box-ip>

# 1. clone — target dir MUST be /srv/ehds-api (the systemd unit has that
#    path hard-coded in WorkingDirectory + ReadWritePaths)
sudo mkdir -p /srv && sudo chown deploy:deploy /srv
cd /srv
git clone https://github.com/tink3rtanner/ehds-api-demo.git ehds-api
cd ehds-api

# 2. python env + deps
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"

# 3. (optional but recommended) cache the HL7 validator jar (~70MB)
./fetch_validator.sh

# 4. seed data IS already in the repo. if you want a clean re-seed:
#    python -m scripts.seed --clean

# 5. environment
sudo mkdir -p /etc/ehds-api
sudo cp .env.example /etc/ehds-api/env
sudo nano /etc/ehds-api/env
# REQUIRED edits in env:
#   EHDS_BASE_URL=https://your.domain
#   EHDS_ISSUER=https://your.domain
#   ENV=prod        # disables /docs, /openapi.json, /ui (the viewer is dev-only)
# leave other defaults

# 6. install systemd unit
sudo install -m 644 deploy/ehds-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ehds-api
sudo systemctl status ehds-api --no-pager

# 7. caddy reverse proxy + auto-TLS
sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo sed -i 's/<your-domain>/your.actual.domain/g' /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy

# 8. smoke test
curl -sI https://your.actual.domain/healthz                 # → 200 ok
curl -s  https://your.actual.domain/metadata | jq .fhirVersion   # → "4.0.1"
curl -s  https://your.actual.domain/.well-known/smart-configuration | jq .token_endpoint
```

## Two demo modes

| mode      | how                                       | what the viewer at /ui does                 |
| --------- | ----------------------------------------- | ------------------------------------------- |
| **dev**   | `ENV=dev` in env file (default)           | `/ui` serves the demo viewer + endpoints page with live dev token |
| **prod**  | `ENV=prod` in env file                    | `/ui` returns 404 (only FHIR REST surface is public) |

For the connectathon, recommend **dev** mode behind a non-public domain (or basic-auth in front of caddy) so attendees can see the pretty UI. For a public conformance test target, use **prod** so only the FHIR endpoints are reachable.

## Register a client (so a peer FHIR server can authenticate)

```bash
# generate keypair on YOUR machine (not the server)
python -m app.tools.register_client --client-id my-app --generate --scope "system/*.read"
# this writes client-my-app.pub.pem locally + appends config/clients.json on the server.
# upload your client-my-app.pem (private) to the peer; never to the server.
```

Or, on the server, just register an inbound client by its public JWK:

```bash
cd /srv/ehds-api-demo
source .venv/bin/activate
python -m app.tools.register_client --client-id partner-a \
  --jwk-from-pem /tmp/partner-a-pubkey.pem \
  --scope "system/*.read" --scope "system/Bundle.write"
sudo systemctl restart ehds-api  # picks up the new registry
```

## Mint a token (client side)

```python
# minimal_client.py — copy onto a client box
import time, uuid, jwt, requests
from cryptography.hazmat.primitives import serialization

priv = serialization.load_pem_private_key(open("client-my-app.pem","rb").read(), password=None)
now = int(time.time())
assertion = jwt.encode({
    "iss": "my-app", "sub": "my-app",
    "aud": "https://your.actual.domain/token",
    "iat": now, "exp": now + 60, "jti": str(uuid.uuid4()),
}, priv, algorithm="RS256", headers={"kid": "my-app-key-1"})

r = requests.post("https://your.actual.domain/token", data={
    "grant_type": "client_credentials",
    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
    "client_assertion": assertion,
    "scope": "system/*.read",
}).json()
print(r["access_token"])
```

## Updating

```bash
ssh deploy@<box-ip>
cd /srv/ehds-api-demo
git pull --ff-only
source .venv/bin/activate
pip install -e .
sudo systemctl restart ehds-api
```

## Backups (data is gitignored — synthetic, can be re-seeded)

```bash
sudo tar czf /var/backups/ehds-$(date +%F).tgz \
  -C /srv/ehds-api-demo data config
```

Or just `python -m scripts.seed --clean` to regenerate the canonical 10-patient panel deterministically.

## Hardening checklist (final pass before sharing the URL)

- [ ] `ENV=prod` set (unless intentionally exposing /ui)
- [ ] `EHDS_RATE_LIMIT_PER_MIN` tuned (default 240)
- [ ] only ports 22/80/443 are reachable (`ufw status` + Hetzner firewall)
- [ ] root ssh disabled (`PermitRootLogin no`)
- [ ] password ssh disabled (`PasswordAuthentication no`)
- [ ] `fail2ban` + `unattended-upgrades` active
- [ ] caddy serving valid LE cert (check at <https://www.ssllabs.com/ssltest/>)
- [ ] no credentials/keys committed (only public JWKs for clients in `config/clients.json`)
- [ ] systemd unit has the hardening flags (already present in `deploy/ehds-api.service`)

## Sanity tests on the live box

```bash
# both should pass on the deployed instance:
curl -s https://your.actual.domain/metadata | jq -e '.fhirVersion=="4.0.1"'
curl -s https://your.actual.domain/.well-known/smart-configuration | jq -e '.token_endpoint | startswith("https://")'
```

## Known quirks

- **The HL7 java validator integration is opt-in** — if `openjdk-21-jre-headless` is not installed, profile-validation tests are skipped (other tests still pass). Install it for full conformance assertions.
- **Validator jar is ~70 MB** — it is NOT in the repo. `./fetch_validator.sh` downloads it from GitHub releases on first run.
- **HL7 EU IG packages** (eps / laboratory / hdr / imaging / eu-health-data-api / eu-core) are cloned by `./download_igs.sh` on demand. NOT committed.
- **EHDS_BASE_URL must match the public domain** or SMART config + token audience checks will fail.

## Contact

[Repo issues](https://github.com/tink3rtanner/ehds-api-demo/issues) · Apache-2.0
