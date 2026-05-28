"""Submit a compiled document Bundle to a running ehds-api server as a SMART
backend-services client (default: vps_bundler).

Mints a private_key_jwt client assertion, exchanges it for a bearer at
{base}/token, then POSTs the bundle to {base}/ (ITI-105 submit) and reads it
back at the Location header.

    python -m scripts.submit_bundle --bundle /tmp/eps-camila.json \
        --client-id vps_bundler \
        --key /home/deploy/epic-fhir/client-vps_bundler.pem \
        --base-url http://localhost:8000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

import httpx
import jwt


def mint(client_id: str, key_pem: str, audience: str, kid: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": client_id,
            "sub": client_id,
            "aud": audience,
            "iat": now,
            "exp": now + 60,
            "jti": str(uuid.uuid4()),
        },
        open(key_pem).read(),
        algorithm="RS256",
        headers={"kid": kid},
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--client-id", default="vps_bundler")
    ap.add_argument("--key", required=True, help="client private key PEM")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--scope", default="system/Bundle.write system/*.read")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    token_url = f"{base}/token"
    assertion = mint(args.client_id, args.key, token_url, f"{args.client_id}-key-1")

    with httpx.Client(timeout=30) as h:
        tok = h.post(token_url, data={
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
            "scope": args.scope,
        })
        if tok.status_code != 200:
            print(f"token {tok.status_code}: {tok.text}", file=sys.stderr)
            return 1
        bearer = tok.json()["access_token"]
        print(f"got bearer ({len(bearer)} chars)", file=sys.stderr)

        bundle = json.load(open(args.bundle))
        r = h.post(
            f"{base}/",
            content=json.dumps(bundle),
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/fhir+json",
            },
        )
        print(f"submit -> {r.status_code}", file=sys.stderr)
        print(json.dumps(r.json(), indent=2))
        loc = r.headers.get("Location")
        if loc:
            print(f"\nLocation: {loc}", file=sys.stderr)
            rb = h.get(loc, headers={"Authorization": f"Bearer {bearer}"})
            print(f"readback -> {rb.status_code} ({len(rb.content)} bytes)", file=sys.stderr)
        return 0 if r.status_code < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
