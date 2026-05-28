---
description: Push to origin/main, wait for systemd restart, smoke-test the 4 fail-fast endpoints, dump logs on failure.
allowed-tools: Bash
---

# /ehds-deploy-and-verify

Verify a change is actually live and healthy on https://ehds.joshpriebe.com.
Use after any code change you want to ship. If running off-box (no
systemd access), the post-push verification still works — it hits the
public URL.

## Steps

1. **Sanity-check working tree.** Refuse to deploy if `git status --porcelain`
   has unstaged changes (the user can commit explicitly or pass
   `--allow-dirty` if they really mean it).
2. **Push.**
   ```bash
   cd /srv/ehds-api && git push origin main
   ```
3. **If on-box**, restart the service and wait for it to be ready:
   ```bash
   sudo systemctl restart ehds-api
   for i in $(seq 1 20); do
     curl -fsS http://127.0.0.1:8000/health >/dev/null && break
     sleep 0.5
   done
   ```
4. **Smoke-test the 4 endpoints that fail fast on most regressions.** Use
   the public URL so this works from anywhere:
   ```bash
   BASE=https://ehds.joshpriebe.com
   # a) discovery is alive (auth layer)
   curl -fsS $BASE/.well-known/smart-configuration | jq -e '.token_endpoint'
   # b) FHIR metadata advertises 4.0.1
   curl -fsS $BASE/metadata | jq -e '.fhirVersion == "4.0.1"'
   # c) store + slot resolution work
   curl -fsS "$BASE/Patient?identifier=urn:ehds-demo:slot|p-001" | jq -e '.total >= 1'
   # d) on-demand document compilation + reverse index — patient-summary for p-001
   PID=$(python3 -c 'from app.fhir.ids import patient_id; print(patient_id("p-001"))')
   BID=$(python3 -c 'from app.fhir.ids import bundle_id; print(bundle_id("p-001","patient-summary"))')
   curl -fsS "$BASE/Bundle/$BID" | jq -e '.type == "document"'
   ```
5. **If any step fails**, dump the last 50 systemd lines and the most
   recent 3 audit entries:
   ```bash
   sudo journalctl -u ehds-api -n 50 --no-pager
   tail -3 /srv/ehds-api/data/audit/audit-$(date -u +%F).jsonl | jq .
   ```
6. **Report**: one line per check (✅/❌), commit SHA deployed, time taken.

## Notes
- This skill assumes the on-box `deploy` user with passwordless sudo for
  `systemctl restart ehds-api`. If running as a different user, skip the
  restart and rely on whatever your remote-restart mechanism is.
- The 4 endpoints exercise: auth/discovery, FHIR conformance, identifier
  search + slot indirection, and document compilation + reverse index.
  A regression in any of the cross-file invariants in `CLAUDE.md` will
  trip at least one.
- `/health` is not gated; the 4 FHIR endpoints are read-only and work
  anonymously in dev mode (no Authorization header sent).
