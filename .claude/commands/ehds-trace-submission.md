---
description: Given an ITI-105 submission id (or substring), dump its structure, dangling refs, and audit-log entries.
allowed-tools: Bash
---

# /ehds-trace-submission

Investigate a single Epic-style submission stored in `data/inbox/`. The
submission id is the bundle's filename uuid; the user may give a full
uuid, a substring, or a relative path.

## Usage

```
/ehds-trace-submission <id-or-substring>
```

If the substring matches more than one file, list them and stop.

## Steps

1. **Resolve the bundle file.**
   ```bash
   ARG="$1"
   cd /srv/ehds-api/data/inbox
   MATCHES=$(ls | grep -F "$ARG")
   case $(echo "$MATCHES" | wc -l) in
     0) echo "no inbox file matches '$ARG'"; exit 1 ;;
     1) FILE="$MATCHES" ;;
     *) echo "ambiguous: $MATCHES"; exit 1 ;;
   esac
   echo "→ /srv/ehds-api/data/inbox/$FILE"
   ```
2. **Header.** Bundle type, total entries, leading resourceType, Composition
   type code+display, Composition.subject reference, Composition.author refs.
   ```bash
   jq '{type, total, leading: .entry[0].resource.resourceType,
        comp_type: .entry[0].resource.type.coding[0],
        comp_title: .entry[0].resource.title,
        comp_subject: .entry[0].resource.subject,
        comp_author: .entry[0].resource.author}' "$FILE"
   ```
3. **Resource inventory.** Counts per resourceType.
   ```bash
   jq -r '.entry[].resource.resourceType' "$FILE" | sort | uniq -c | sort -rn
   ```
4. **Dangling references.** Compute the set of `entry[].fullUrl` and the set
   of `reference` values referenced anywhere in the bundle; report
   referenced-but-not-bundled URLs. This is where Epic-side bugs show up
   (urn:uuid subject pointing at a random uuid that's not in fullUrl set;
   author pointing at `Organization/epic-source` that's never bundled).
   ```bash
   jq -r '
     ([.entry[].fullUrl] | map(select(.))) as $urls |
     [.. | .reference? | select(.)] as $refs |
     ($refs - $urls) | unique[]
   ' "$FILE"
   ```
5. **Mirrored type-folder hits.** Show which entries our submit handler
   mirrored into the typed store (it writes anything in
   `app.fhir.store.SUPPORTED_TYPES`):
   ```bash
   jq -r '.entry[] | "\(.resource.resourceType)/\(.resource.id)"' "$FILE" \
     | while read -r ref; do
         t="${ref%%/*}"; id="${ref##*/}"
         path="/srv/ehds-api/data/$t/$id.json"
         [ -f "$path" ] && echo "✅ $ref" || echo "❌ $ref (not mirrored — type not in SUPPORTED_TYPES)"
       done
   ```
6. **Audit-log correlation.** Find the POST audit entry whose request body
   produced this file (correlate by approximate timestamp from file mtime
   ± 60s).
   ```bash
   mtime=$(stat -c %Y "$FILE")
   start=$(date -u -d "@$((mtime-60))" +%FT%TZ)
   end=$(date -u -d "@$((mtime+60))" +%FT%TZ)
   day=$(date -u -d "@$mtime" +%F)
   jq -c --arg s "$start" --arg e "$end" '
     select(.ts >= $s and .ts <= $e and .method == "POST" and (.path == "/" or .path == ""))
   ' /srv/ehds-api/data/audit/audit-$day.jsonl
   ```
7. **Verdict.** Print one of:
   - ✅ structurally clean (no dangling refs, all resource types mirrored)
   - ⚠ dangling refs present (list them) — usually a sender-side bug
   - ⚠ unmirrored resource types (list them) — extend `SUPPORTED_TYPES` if expected

## Notes
- Inbox files are bundles **as received** — we do not rewrite. So this
  skill diagnoses what the sender actually sent, not what we stored.
- For the inverse — "what does the server now believe about Patient X"
  — use `GET /Patient/{id}` and `GET /Patient/{id}/$everything` (if
  enabled) instead.
- See `docs/conformance-deviations.md` for why dangling refs aren't
  rejected at submit time.
