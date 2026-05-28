---
description: Register a new SMART Backend Services client and immediately mint a bearer token. Prints both for copy-paste.
allowed-tools: Bash
---

# /ehds-register-and-mint

End-to-end onboarding for a fresh client. Chains
`POST /register-client` (RFC 7591) → `POST /token` (JWT client_assertion).

Useful when:
- Bringing up a new bridge / test client and you want to validate the
  full handshake works before handing creds to whoever owns the client.
- Reproducing an Epic-side problem locally.

## Usage

```
/ehds-register-and-mint [<client-id>] [<scope>...]
```

Defaults:
- `client-id`: `tester-$(date +%s)`
- `scope`: `system/*.read system/Bundle.write` (covers ITI-67/68 + ITI-105)
- Base URL: `https://ehds.joshpriebe.com` (override with `EHDS_BASE_URL` env var)

## Steps

1. **Generate keypair and register.** The CLI tool already does this in one
   call against a remote server with `--out json`:
   ```bash
   BASE="${EHDS_BASE_URL:-https://ehds.joshpriebe.com}"
   CID="${1:-tester-$(date +%s)}"
   shift 2>/dev/null
   SCOPES="${*:-system/*.read system/Bundle.write}"
   cd /srv/ehds-api && source .venv/bin/activate

   SCOPE_ARGS=""
   for s in $SCOPES; do SCOPE_ARGS="$SCOPE_ARGS --scope $s"; done

   OUT=$(python -m app.tools.register_client \
     --client-id "$CID" --generate $SCOPE_ARGS \
     --base-url "$BASE" --out json)
   echo "$OUT" | jq .
   ```
   The tool writes the private key to `data/keys/$CID.private.json` (or
   wherever `EHDS_KEY_OUT` points). It also returns the
   `registration_client_uri` and the `next_steps` block.
2. **Mint a token using the just-generated key.** The CLI tool emits a
   convenience block under `.next_steps.mint_token` — but doing it
   explicitly here gives the user a copy-pasteable token:
   ```bash
   KEY_PATH=$(echo "$OUT" | jq -r '.next_steps.private_key_path // empty')
   [ -z "$KEY_PATH" ] && KEY_PATH="/srv/ehds-api/data/keys/$CID.private.json"

   TOKEN=$(python -m app.tools.mint_token \
     --client-id "$CID" --key "$KEY_PATH" \
     --aud "$BASE/token" --scope "$SCOPES" 2>/dev/null)
   # if mint_token doesn't exist, fall back to inlining the assertion:
   if [ -z "$TOKEN" ]; then
     ASSERT=$(python - <<PY
import json, time, uuid, jwt
from cryptography.hazmat.primitives import serialization
priv = json.load(open("$KEY_PATH"))
# private jwk → PEM
from jwt.algorithms import RSAAlgorithm, ECAlgorithm
alg = RSAAlgorithm if priv["kty"] == "RSA" else ECAlgorithm
key = alg.from_jwk(json.dumps(priv))
pem = key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption())
now = int(time.time())
payload = {"iss": "$CID", "sub": "$CID", "aud": "$BASE/token",
           "iat": now, "exp": now+60, "jti": str(uuid.uuid4())}
print(jwt.encode(payload, pem, algorithm="RS256" if priv["kty"]=="RSA" else "ES256",
                 headers={"kid": priv.get("kid","key-1")}))
PY
)
     TOKEN=$(curl -fsS "$BASE/token" \
       -d "grant_type=client_credentials" \
       -d "client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer" \
       -d "client_assertion=$ASSERT" \
       -d "scope=$SCOPES" | jq -r '.access_token')
   fi
   echo "ACCESS_TOKEN=$TOKEN"
   ```
3. **Smoke-test the token against a gated endpoint** so we know the scope
   landed:
   ```bash
   curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE/Patient?_count=1" | jq '.total'
   ```
4. **Print copy-paste recipe** for the user: `export BASE=... TOKEN=...` plus
   a one-liner `curl -H "Authorization: Bearer $TOKEN" $BASE/...`.

## Notes
- Scope authorisation is enforced against the registered scope set; you
  cannot mint a broader scope than you registered. See
  `app/auth/smart.py` `_ALLOWED_REG_SCOPES`.
- The token TTL is 900s (bumped from 300s; see CLAUDE.md).
- If the server rejects with 401 at mint time, follow `/ehds-explain-401`.
- Private keys are not gitignored individually — they live under
  `data/keys/` which is gitignored as a whole.
