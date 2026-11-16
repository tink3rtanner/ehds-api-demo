"""Submit a compiled document Bundle to a running ehds-api server as a SMART
backend-services client (default: vps_bundler).

Mints a private_key_jwt client assertion, exchanges it for a bearer at
{base}/token, then publishes a Bundle.type=document with ITI-105 Simplified
Publish (POST {base}/DocumentReference, the file's exact bytes inline as base64)
and reads the document back, checking the bytes are unchanged.

    python -m scripts.submit_bundle --bundle /tmp/eps-camila.json \
        --client-id vps_bundler \
        --key /home/deploy/epic-fhir/client-vps_bundler.pem \
        --base-url http://localhost:8000 --epic-sandbox

--epic-sandbox tags the DocumentReference as derived from the public
fhir.epic.com sandbox. --legacy uses the old POST / Bundle upload instead
(scope system/Bundle.write), which makes no DocumentReference.
"""
from __future__ import annotations

import argparse
import base64
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
    ap.add_argument("--scope", default=None)
    ap.add_argument("--epic-sandbox", action="store_true", help="tag as derived from the fhir.epic.com sandbox")
    ap.add_argument("--legacy", action="store_true", help="POST / Bundle upload instead of ITI-105")
    args = ap.parse_args()
    if args.scope is None:
        args.scope = "system/Bundle.write system/*.rs" if args.legacy else "system/DocumentReference.c system/*.rs"

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

        raw = open(args.bundle, "rb").read()
        if not args.legacy:
            return _publish(h, base, bearer, raw, epic=args.epic_sandbox)
        bundle = json.loads(raw)
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


def _publish(h: httpx.Client, base: str, bearer: str, raw: bytes, *, epic: bool) -> int:
    bundle = json.loads(raw)
    comp = next((e["resource"] for e in bundle.get("entry", [])
                 if e.get("resource", {}).get("resourceType") == "Composition"), {})
    docref = {
        "resourceType": "DocumentReference",
        "status": "current",
        "type": comp.get("type"),
        "content": [{"attachment": {"contentType": "application/fhir+json",
                                    "data": base64.b64encode(raw).decode()}}],
    }
    if epic:
        docref["meta"] = {"tag": [{"system": "urn:ehds-demo:derived-from", "code": "fhir.epic.com-sandbox",
                                   "display": "Derived from the public fhir.epic.com sandbox"}]}
    auth = {"Authorization": f"Bearer {bearer}"}
    r = h.post(f"{base}/DocumentReference", content=json.dumps(docref),
               headers={**auth, "Content-Type": "application/fhir+json"})
    print(f"publish -> {r.status_code}", file=sys.stderr)
    print(json.dumps(r.json(), indent=2))
    if r.status_code != 201:
        return 1
    print(f"Location: {r.headers.get('Location')}", file=sys.stderr)
    url = r.json()["content"][0]["attachment"]["url"]
    back = h.get(url if url.startswith("http") else f"{base}/{url}", headers=auth)
    print(f"retrieve {url} -> {back.status_code}, bytes identical: {back.content == raw}", file=sys.stderr)
    return 0 if back.content == raw else 1


if __name__ == "__main__":
    sys.exit(main())
