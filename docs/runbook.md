# On-call runbook

Four ways the box can go down. Each entry: symptom, diagnosis,
mitigation, root-cause follow-up.

## 1. TLS cert expired / not provisioned

**Symptom**: browser warning, `curl https://...` returns
`SSL certificate problem`.

**Diagnosis**:
```bash
echo | openssl s_client -servername ehds.joshpriebe.com \
  -connect ehds.joshpriebe.com:443 2>/dev/null \
  | openssl x509 -noout -dates -issuer
```

**Mitigation**: Caddy auto-renews; if it stopped, force a renewal:
```bash
sudo systemctl restart caddy
sudo journalctl -u caddy -n 100 --no-pager | grep -i 'certificate\|acme\|error'
```

Common renewal blockers:
- **Port 80 blocked**: ACME HTTP-01 challenge needs `:80` open. Check
  `sudo ufw status` shows `80/tcp ALLOW`.
- **Rate limit**: Let's Encrypt rate limits to ~5 certs/week per
  hostname. Wait or switch to ZeroSSL (Caddy supports both).
- **DNS drift**: cert provisioning fails if the A record doesn't point
  here. `dig ehds.joshpriebe.com` should resolve to `46.225.121.57`.

**Root-cause follow-up**: certs are auto-renewed at ~30 days remaining.
If Caddy logs show repeated failures, paste the error into a journal
entry and investigate.

## 2. Data dir full

**Symptom**: 500 errors on `POST /` with `OperationOutcome` mentioning
"failed to write". `df -h /srv` shows >90% used.

**Diagnosis**:
```bash
df -h /srv
du -sh /srv/ehds-api/data/*/ | sort -h | tail -10
```

Typical culprits in order:
1. `data/inbox/` — every ITI-105 submission lives here forever (~30 KB each)
2. `data/audit/` — JSONL grows ~5 KB per request; rotation is daily
3. `data/<resourceType>/` — Epic-style submitted Patients/etc

**Mitigation (fast, safe)**: rotate audit logs:
```bash
cd /srv/ehds-api/data/audit
gzip audit-2026-05-*.jsonl   # adjust window
```

**Mitigation (inbox)**: inbox files are kept as evidence; archive rather
than delete:
```bash
sudo tar czf /var/backups/ehds-inbox-$(date -u +%F).tar.gz \
  -C /srv/ehds-api/data inbox
# only after verifying the tarball:
rm /srv/ehds-api/data/inbox/*.json
```

**Root-cause follow-up**: add a cron to gzip audit JSONL >7 days old and
roll inbox tarballs monthly. Document the cron in `deploy/`.

## 3. systemd unit fails to start

**Symptom**: `systemctl status ehds-api` shows `failed`; site returns
502.

**Diagnosis**: in order, the four most common causes:

```bash
sudo journalctl -u ehds-api -n 100 --no-pager
```

| journal pattern | Root cause | Fix |
|---|---|---|
| `203/EXEC` or `Permission denied: /home/deploy` | Venv shebang drift or `ProtectHome=true` without `HOME=` env | See `docs/TROUBLESHOOTING.md` § gunicorn 203/EXEC |
| `ModuleNotFoundError` | New dep added but `pip install -e .` not re-run | `cd /srv/ehds-api && source .venv/bin/activate && pip install -e .` |
| `Address already in use` | A stray gunicorn from a previous run is holding `:8000` | `sudo pkill -f 'gunicorn.*ehds-api'` then restart |
| `validator-unavailable` warnings + 500s | fhir.resources major bump silently changed APIs | See `docs/TROUBLESHOOTING.md` § validator silently passes |

**Mitigation**: restart after fixing root cause:
```bash
sudo systemctl daemon-reload  # if unit file changed
sudo systemctl restart ehds-api
sudo systemctl status ehds-api
```

**Root-cause follow-up**: add the new failure mode to
`docs/TROUBLESHOOTING.md` if it isn't there.

## 4. Caddy config syntax error

**Symptom**: `sudo systemctl reload caddy` returns non-zero; site stops
responding.

**Diagnosis**:
```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo journalctl -u caddy -n 50 --no-pager
```

**Mitigation**: revert the change:
```bash
# if you have a git-tracked Caddyfile somewhere
cp /var/backups/Caddyfile.last-known-good /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

If no backup exists, the minimal working config for this box is:

```
ehds.joshpriebe.com {
    reverse_proxy 127.0.0.1:8000
}
```

**Root-cause follow-up**: commit `Caddyfile` to a git repo (or copy it
to `/var/backups/Caddyfile.last-known-good` before every edit). Caddy's
own config-history feature can be enabled too.

## Diagnostic checklist for "site is down"

When you don't know which of the four it is:

```bash
# layer 1: DNS + L4
dig +short ehds.joshpriebe.com
nc -zv ehds.joshpriebe.com 443

# layer 2: TLS
echo | openssl s_client -servername ehds.joshpriebe.com -connect ehds.joshpriebe.com:443 2>/dev/null | grep 'Verify return'

# layer 3: Caddy
sudo systemctl status caddy
curl -fsS -o /dev/null -w '%{http_code}\n' https://ehds.joshpriebe.com/.well-known/smart-configuration

# layer 4: app
sudo systemctl status ehds-api
curl -fsS http://127.0.0.1:8000/health

# layer 5: disk + resources
df -h /srv; free -h; uptime
```

First non-200 / non-success tells you which layer to dig into.

## Where to find more

- `~/baseline-hardening.md` — UFW, SSH, fail2ban, journald baseline.
  **Read before changing security config.**
- `HANDOFF.md` — VPS bring-up runbook (rebuilding from scratch).
- `~/notes/ehds-api-demo/journal.md` — history of past incidents and
  fixes. `tail -200` it.
- `docs/TROUBLESHOOTING.md` — symptom-keyed root causes.
- `docs/audit-recipes.md` — jq snippets for investigating what happened.
