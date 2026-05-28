---
description: Walk through the likely root causes of a 401 from /token or any gated FHIR endpoint, in priority order.
allowed-tools: Bash, Read
---

# /ehds-explain-401

Decision tree for "I got a 401, why?" — keyed on the exact
`error_description` the server returns (see `app/auth/smart.py` and
`app/auth/verify.py`).

## Usage

```
/ehds-explain-401 [<audit-log-line-or-error-description>]
```

If no argument, tail the most recent 401s from today's audit log:

```bash
jq -c 'select(.status == 401)' \
  /srv/ehds-api/data/audit/audit-$(date -u +%F).jsonl | tail -5
```

## Decision tree (check in order)

### 1. At `/token` — the assertion didn't verify

`error_description` will start with `invalid client_assertion`, `bad
signature`, or `alg/kty mismatch`.

| Sub-message | Root cause | Fix |
|---|---|---|
| `unknown client_id` | `iss`/`sub` doesn't match any registered client | Re-register: `/ehds-register-and-mint`. Check `cat /srv/ehds-api/data/clients.json` |
| `no matching jwk for kid` | `kid` header doesn't match any key in registered jwks | Re-fetch the registered jwks via `GET /register-client/{id}`; ensure your local key file's `kid` matches |
| `alg/kty mismatch` | e.g. `alg=ES256` but the registered jwk is `kty=RSA` | Re-mint with the right algorithm. RSA → RS256/PS256; EC P-256 → ES256 |
| `bad signature` | Key actually differs from registered public key — usually after key rotation | Re-register (PATCH) with the new jwks |
| `bad aud` | `aud` claim != `<base_url>/token` | Set `aud` exactly to the issuer's `/token` endpoint; check `GET /.well-known/smart-configuration .token_endpoint` |
| `expired` / `iat in future` | Clock skew between client and server | Sync clocks. Server allows ±60s leeway |
| `assertion replayed` | Same `jti` used twice within TTL | Generate a fresh `jti` per assertion (uuid4) |

### 2. At `/token` — invalid scope

`error_description: requested scope X not in registered scopes`.

- Print the registered scopes: `curl -fsS https://ehds.joshpriebe.com/register-client/<id> | jq .scopes`
- If the scope you want isn't there, PATCH the registration:
  ```bash
  curl -fsS -X PATCH https://ehds.joshpriebe.com/register-client/<id> \
    -H 'content-type: application/json' \
    -d '{"scopes": ["system/*.read", "system/Bundle.write"]}'
  ```
- `_ALLOWED_REG_SCOPES` in `app/auth/smart.py` is the union of scopes the
  server is willing to register; if your scope isn't there, the
  registration itself will 400, not 401.

### 3. At a FHIR endpoint — bearer rejected

`error_description: bearer invalid` / `bearer expired`.

- Token TTL is 900s. Expired? Re-mint.
- Token signed by a different issuer? In dev `iss` defaults to base_url;
  in prod ensure the token came from this server's `/token`, not a stale
  cached one from a previous deploy.

### 4. At a FHIR endpoint — scope insufficient

`error_description: scope X required, you have Y`.

- Compare token scope (`jwt decode $TOKEN` payload `.scope`) against what
  the endpoint advertises. CapabilityStatement gates are coarse —
  read endpoints need `system/<type>.read`; ITI-105 needs
  `system/Bundle.write` (or per-type `<type>.write`).

### 5. Dev anon-read fallback wasn't triggered

In `ENV=dev`, **anonymous GET** is allowed only when **no `Authorization`
header is sent at all** (`app/auth/verify.py`). Sending an invalid or
malformed bearer disables the fallback and goes through strict checks —
which 401.

Symptom: `curl https://ehds.joshpriebe.com/Patient` (no header) → 200,
but `curl -H "Authorization: Bearer junk" ...` → 401.

If a tool is auto-injecting a stale header, strip it.

### 6. Production strict mode

`ENV=prod` requires a bearer always. Anon-read shortcut is dev-only.
Check `cat /etc/ehds-api/env | grep -E '^ENV='`.

## Sanity checks the skill should run

```bash
# 1. Server alive + advertising the right issuer
curl -fsS https://ehds.joshpriebe.com/.well-known/smart-configuration | jq '{token_endpoint, issuer, scopes_supported}'

# 2. The 401 is recent and matches the request you think you made
tail -10 /srv/ehds-api/data/audit/audit-$(date -u +%F).jsonl | jq 'select(.status == 401) | {ts, path, client_id, error}'
```

## Related
- `docs/identity-cheatsheet.md` — slot vs uuid (sometimes a 401 is
  actually a 404 misread because the client typed the wrong patient id)
- `/ehds-register-and-mint` — clean-slate restart
