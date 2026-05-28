# Audit log recipes

`jq` snippets against
`/srv/ehds-api/data/audit/audit-YYYY-MM-DD.jsonl` for the questions you
actually ask in incident response. Each file is one JSON line per
request, written by `StructuredLogMiddleware` in `app/security.py`.

The same data is browsable at https://ehds.joshpriebe.com/ui/#/logs.

## Common entry shape

```json
{
  "ts": "2026-05-28T19:22:35Z",
  "method": "POST",
  "path": "/",
  "status": 201,
  "duration_ms": 152,
  "client_id": "api-demo-tester",
  "ip": "146.198.105.60",
  "req_bytes": 31687,
  "resp_bytes": 412,
  "error": null
}
```

`client_id` is parsed from the JWT `sub` claim **without signature
verification** — so a 401 still shows who *claimed* to call.

## Common questions

### "What did client X do today?"

```bash
day=$(date -u +%F)
jq -c 'select(.client_id == "api-demo-tester") | {ts, method, path, status, dur: .duration_ms}' \
  /srv/ehds-api/data/audit/audit-$day.jsonl
```

### "All 4xx in the last hour"

```bash
day=$(date -u +%F)
since=$(date -u -d '1 hour ago' +%FT%TZ)
jq -c --arg s "$since" 'select(.ts >= $s and .status >= 400 and .status < 500)' \
  /srv/ehds-api/data/audit/audit-$day.jsonl
```

### "Slowest 10 requests today"

```bash
day=$(date -u +%F)
jq -c '{ts, path, status, dur: .duration_ms, client: .client_id}' \
  /srv/ehds-api/data/audit/audit-$day.jsonl \
  | jq -s 'sort_by(-.dur) | .[:10]'
```

### "ITI-105 submissions by Composition.type code"

(Requires cross-referencing with the inbox file — submit log doesn't
include payload structure.)

```bash
cd /srv/ehds-api/data/inbox
for f in *.json; do
  code=$(jq -r '.entry[0].resource.type.coding[0].code // "?"' "$f")
  display=$(jq -r '.entry[0].resource.type.coding[0].display // "?"' "$f")
  echo "$code  $display  $f"
done | sort | uniq -c -w 12 | sort -rn
```

### "What's the breakdown of status codes today?"

```bash
day=$(date -u +%F)
jq -r '.status' /srv/ehds-api/data/audit/audit-$day.jsonl \
  | sort | uniq -c | sort -rn
```

### "Show all token-mint failures (status 4xx on /token)"

```bash
day=$(date -u +%F)
jq -c 'select(.path == "/token" and .status >= 400) | {ts, status, client: .client_id, ip, error}' \
  /srv/ehds-api/data/audit/audit-$day.jsonl
```

### "Who's hitting which patient compartment?"

```bash
day=$(date -u +%F)
jq -c 'select(.path | test("/Patient/[0-9a-f-]+")) | {ts, client: .client_id, path, status}' \
  /srv/ehds-api/data/audit/audit-$day.jsonl \
  | head -50
```

### "Per-client request count today"

```bash
day=$(date -u +%F)
jq -r '.client_id // "-"' /srv/ehds-api/data/audit/audit-$day.jsonl \
  | sort | uniq -c | sort -rn
```

### "All scanner / 404 noise (filter it out of an analysis)"

`/.git/HEAD`, `/.env`, `/credentials.json`, `/swagger`, etc. are
internet background noise, not real traffic. Filter them out:

```bash
day=$(date -u +%F)
jq -c 'select((.path | test("\\.git|\\.env|credentials|firebase|service-account|secrets|swagger|robots|sitemap")) | not)' \
  /srv/ehds-api/data/audit/audit-$day.jsonl
```

### "Reconstruct a single client's session"

Given a `client_id`, walk through every request chronologically:

```bash
day=$(date -u +%F)
client="api-demo-tester"
jq -c --arg c "$client" \
  'select(.client_id == $c) | {ts, method, path: (.path[:80]), status, dur: .duration_ms}' \
  /srv/ehds-api/data/audit/audit-$day.jsonl
```

### "Cross-day window"

If the question spans multiple days:

```bash
cat /srv/ehds-api/data/audit/audit-*.jsonl | jq -c '...'
```

(Audit files are gzipped after `audit_retention_days` — default 30 — by
a separate job; recent files are plain JSONL.)

## Programmatic API

The UI viewer reads two endpoints:
- `GET /ui/api/audit?from=…&to=…&client_id=…` — paginated entries
- `GET /ui/api/audit/stats?date=…` — pre-aggregated counts

These accept the same parameters as the jq filters above.

## Adding new fields

If you start emitting a new field from `StructuredLogMiddleware`:
1. Add the field to the log dict construction
2. Add a recipe here that uses it
3. (Optional) Surface it in the UI viewer

## Secrets are scrubbed at write time

`StructuredLogMiddleware` scrubs `Authorization`, `Cookie`, and any
field matching common secret patterns before write. Don't add a recipe
that assumes raw bearer tokens are in the log — they aren't.
