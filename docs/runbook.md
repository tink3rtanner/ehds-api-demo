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

## 5. Runaway process pegging the CPU

**Symptom**: `uptime` load average sits near (or above) the core count
(4 on this box) for a sustained period; the demo feels sluggish but
`systemctl status ehds-api` is healthy. The app workers are NOT the
culprit — they idle at ~0% between requests.

**Diagnosis**:
```bash
ps -eo pid,ppid,pcpu,etimes,args --sort=-pcpu | head -8
# inspect the top offender's lineage + working dir:
ps -o pid,ppid,etime,args -p <pid>
ls -l /proc/<pid>/cwd /proc/<pid>/exe
```

Known real incident (2026-05-31): an orphaned
`ugrep -r 'bundle-eu-eps|composition-eu-eps' /` left behind by a dead
remote-agent session pegged all 4 cores for 2.5 days. Its parent was a
stale `bash -c` wrapper from a session that had exited. Tell-tale signs
of this class: a `grep`/`find`/`ugrep` rooted at `/`, an `exe` symlink
under `~/.claude/remote/...`, and a multi-day `etimes`.

**Mitigation**: confirm it's safe to kill (orphaned search/loop, parent
is a dead shell), then:
```bash
kill -TERM <pid>     # then SIGKILL if it ignores TERM
```

**Detection (already wired up)**: `ehds-cpu-watchdog.timer` runs
`deploy/cpu-watchdog.sh` every 10 minutes. It flags any process holding
a high lifetime-average %CPU (default ≥80) for longer than a floor age
(default ≥1800 s) and logs a WARNING to the journal:
```bash
# did the watchdog ever fire?
sudo journalctl -t ehds-cpu-watchdog --no-pager | tail -20
# the timer itself:
systemctl list-timers ehds-cpu-watchdog.timer
sudo systemctl start ehds-cpu-watchdog.service   # run on demand
```
Thresholds are env-tunable (`EHDS_WATCHDOG_CPU`, `EHDS_WATCHDOG_MIN_ETIME`).
It is detection-only by default; set `EHDS_WATCHDOG_KILL=1` (e.g. via a
`systemctl edit` drop-in) to have it SIGTERM offenders automatically.

**Root-cause follow-up**: orphans from remote-agent sessions are the
usual source. If they recur, the watchdog log gives the cmdline to trace
back to the originating session.

## Note: submitted data and the multi-worker index

The server runs gunicorn with 2 workers, each with its own in-process
index cache (`app/fhir/store.py`). A historical bug made freshly
submitted (ITI-105) data appear *intermittently* — a search would
include or omit it depending on which worker answered, because a write
only invalidated the handling worker's cache. This is **fixed**: each
cache entry is now tagged with a cheap signature of the on-disk type-dir
(file count + newest mtime) and re-validated on every read, so any worker
notices another worker's writes. If you ever see submitted data flicker
in/out again, suspect this mechanism — `tests/test_store_cache.py` is the
regression guard, and a `systemctl restart ehds-api` is the immediate
workaround.

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
curl -fsS http://127.0.0.1:8000/healthz

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
