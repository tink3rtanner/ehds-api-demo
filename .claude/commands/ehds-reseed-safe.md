---
description: Reseed the synthetic panel deterministically while preserving inbox/ submissions and audit logs.
allowed-tools: Bash
---

# /ehds-reseed-safe

Wraps `python -m scripts.seed --clean` with two safety checks:
1. **Preserves** `data/inbox/`, `data/audit/`, `data/clients.json`,
   `data/keys/` — none of these are seed data.
2. **Verifies** the 10-patient panel is back after seeding by counting
   slot-identified Patients.

Useful when:
- You suspect the on-disk seed has drifted (an Epic submission overwrote
  a uuid that collides with a seeded uuid — rare but possible)
- You just merged a change to `scripts/seed.py` and want to land it live
- The UI is showing stale data and a cache-clearing restart should
  re-derive everything

## Steps

```bash
cd /srv/ehds-api && source .venv/bin/activate

# 1. snapshot what we're about to NOT touch (defense in depth)
mkdir -p /tmp/ehds-reseed-backup-$$
cp -r data/inbox data/audit data/clients.json data/keys /tmp/ehds-reseed-backup-$$/ 2>/dev/null
echo "→ backup at /tmp/ehds-reseed-backup-$$"

# 2. reseed
python -m scripts.seed --clean
echo "→ seed complete"

# 3. restart service to bust in-process caches
sudo systemctl restart ehds-api
for i in $(seq 1 20); do
  curl -fsS http://127.0.0.1:8000/health >/dev/null && break
  sleep 0.5
done

# 4. verify panel restored
COUNT=$(curl -fsS "http://127.0.0.1:8000/Patient?identifier=urn:ehds-demo:slot|" | jq '.total')
if [ "$COUNT" -eq 10 ]; then
  echo "✅ panel restored — 10 slot-identified Patients"
else
  echo "❌ expected 10 panel patients, found $COUNT"
  echo "   restore via: cp -r /tmp/ehds-reseed-backup-$$/* /srv/ehds-api/data/"
  exit 1
fi

# 5. spot-check a known bundle id round-trips
PID=$(python3 -c 'from app.fhir.ids import patient_id; print(patient_id("p-001"))')
BID=$(python3 -c 'from app.fhir.ids import bundle_id; print(bundle_id("p-001","patient-summary"))')
curl -fsS "http://127.0.0.1:8000/Bundle/$BID" \
  | jq -e --arg pid "$PID" '.entry[].resource | select(.resourceType == "Composition").subject.reference | endswith($pid)' \
  >/dev/null && echo "✅ patient-summary for p-001 compiles + binds to canonical uuid" \
              || { echo "❌ Bundle/$BID does not compile cleanly"; exit 1; }

# 6. confirm inbox preserved
INBOX_COUNT=$(ls /srv/ehds-api/data/inbox/ 2>/dev/null | wc -l)
echo "→ inbox preserved: $INBOX_COUNT files"
```

## Notes
- `scripts/seed.py` mints **only** seed files (uuid-named) into the type
  folders. It does not touch `inbox/`, `audit/`, `clients.json`, or
  `keys/`. The backup at step 1 is defense in depth; if seed ever grows
  a `--clean-all` flag this skill must not pass it.
- After a successful run, you can delete the backup:
  `rm -rf /tmp/ehds-reseed-backup-$$`. It's left in place automatically
  so a panicked rollback is one `cp` away.
- The 10-Patient count assumes the current panel size. If `seed.py`
  grows beyond 10 slots, update this skill.

## Related
- `tests/conftest.py` reseeds into a temp dir via the same
  `scripts.seed.seed()` entrypoint — see the "Tests reseed; never copy
  live data/" section in CLAUDE.md for why.
- `/ehds-deploy-and-verify` runs a subset of these post-deploy.
