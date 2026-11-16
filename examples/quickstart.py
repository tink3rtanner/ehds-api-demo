#!/usr/bin/env python3
"""Quickstart for the EHDS demo server (HL7 Europe Health Data API). Read-only.

One file. Needs Python 3.9+ and the `cryptography` package:

    pip install cryptography
    python quickstart.py

It gets you a SMART Backend Services token and proves it works with a few
reads: find a patient, list their documents, read one. It changes nothing on
the server except registering your client once. The key and client id are saved
next to this script (ehds-quickstart-key.pem, ehds-quickstart.json) and reused
on later runs.

At the end it prints the token and ready-made requests to paste into Bruno,
Postman, curl or anything else.

    python quickstart.py --name "Jane Doe, Acme"   # label your client (else it asks)
    python quickstart.py --publish-scope   # token may also publish (system/DocumentReference.c)
    python quickstart.py --token-only      # just print a fresh token
"""
from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

BASE = "https://ehds.joshpriebe.com"
HERE = Path(__file__).resolve().parent
KEY_FILE = HERE / "ehds-quickstart-key.pem"
STATE_FILE = HERE / "ehds-quickstart.json"
READ_SCOPE = "system/*.rs"
PUBLISH_SCOPE = "system/DocumentReference.c"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def http(method: str, url: str, *, token: str | None = None, body: bytes | None = None,
         ctype: str | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/fhir+json, application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def must(status: int, raw: bytes) -> dict:
    if status != 200:
        sys.exit(f"HTTP {status}: {raw.decode(errors='replace')[:800]}")
    return json.loads(raw)


def load_key() -> rsa.RSAPrivateKey:
    if KEY_FILE.exists():
        return serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    KEY_FILE.chmod(0o600)
    return key


def sign_rs256(key: rsa.RSAPrivateKey, header: dict, claims: dict) -> str:
    signing_input = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(claims).encode())}"
    sig = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{b64url(sig)}"


def ask_name(given: str | None) -> str:
    """Who is this? Shown in the server's audit log next to your requests."""
    if given:
        return given
    if sys.stdin.isatty():
        name = input("Your name or organisation (shown in the server's audit log): ").strip()
        if name:
            return name
    return "quickstart user"


def register(base: str, smart: dict, key: rsa.RSAPrivateKey, scopes: list[str], name_arg: str | None) -> dict:
    """Register a client once; reuse it on later runs against the same server."""
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    if state.get("base") == base and set(scopes) <= set(state.get("scopes", [])):
        return state
    name = ask_name(name_arg)
    slug = "-".join("".join(ch if ch.isalnum() else " " for ch in name.lower()).split())[:30] or "user"
    client_id = f"quickstart-{slug}-{secrets.token_hex(2)}"
    pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    st, raw = http("POST", smart["registration_endpoint"], ctype="application/json",
                   body=json.dumps({"client_id": client_id, "client_name": f"{name} (quickstart.py)",
                                    "scopes": scopes, "public_key_pem": pem}).encode())
    reg = must(st, raw)
    state = {"base": base, "client_id": client_id, "kid": reg["next_steps"]["client_assertion_kid"],
             "scopes": scopes}
    STATE_FILE.write_text(json.dumps(state, indent=2))
    return state


def get_token(smart: dict, key: rsa.RSAPrivateKey, client: dict, scopes: list[str]) -> tuple[int, bytes]:
    now = int(time.time())
    assertion = sign_rs256(key, {"alg": "RS256", "typ": "JWT", "kid": client["kid"]},
                           {"iss": client["client_id"], "sub": client["client_id"], "aud": smart["token_endpoint"],
                            "iat": now, "exp": now + 60, "jti": str(uuid.uuid4())})
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion, "scope": " ".join(scopes)}).encode()
    return http("POST", smart["token_endpoint"], body=form, ctype="application/x-www-form-urlencoded")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--name", help="who you are, e.g. 'Jane Doe, Acme' (labels your client on the server)")
    ap.add_argument("--publish-scope", action="store_true",
                    help=f"also ask for {PUBLISH_SCOPE}, so the token can publish from Bruno etc.")
    ap.add_argument("--token-only", action="store_true", help="print a token and stop")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    scopes = [READ_SCOPE] + ([PUBLISH_SCOPE] if a.publish_scope else [])
    key = load_key()

    smart = must(*http("GET", f"{base}/.well-known/smart-configuration"))
    client = register(base, smart, key, scopes, a.name)
    st, raw = get_token(smart, key, client, scopes)
    if st == 401 and STATE_FILE.exists():   # server forgot the client (reset): register again
        STATE_FILE.unlink()
        client = register(base, smart, key, scopes, a.name)
        st, raw = get_token(smart, key, client, scopes)
    tok = must(st, raw)
    token = tok["access_token"]
    if a.token_only:
        print(token)
        return
    print(f"1. token for client {client['client_id']} (scope: {tok['scope']}, {tok['expires_in'] // 60} min)")

    # find a patient (IHE PDQm)
    st, raw = http("GET", f"{base}/Patient?identifier=urn:ehds-demo:slot%7Cp-001", token=token)
    p = must(st, raw)["entry"][0]["resource"]
    print(f"2. found Patient/{p['id']}: {p['name'][0]['given'][0]} {p['name'][0]['family']}")

    # their documents (IHE MHD ITI-67)
    st, raw = http("GET", f"{base}/DocumentReference?patient={p['id']}", token=token)
    docs = [e["resource"] for e in must(st, raw).get("entry", [])]
    print(f"3. {len(docs)} documents:")
    for d in docs:
        coding = d["type"]["coding"][0]
        print(f"     {coding.get('display', coding['code']):<28} {d['content'][0]['attachment']['url']}")

    # read one (IHE MHD ITI-68): the Patient Summary if there is one
    ps = [d for d in docs if any(c.get("code") == "60591-5" for c in d["type"]["coding"])]
    url = (ps or docs)[0]["content"][0]["attachment"]["url"]
    doc_url = url if url.startswith("http") else f"{base}/{url}"
    st, raw = http("GET", doc_url, token=token)
    b = must(st, raw)
    print(f"4. read {url}: Bundle type={b['type']}, {len(b['entry'])} entries")

    expires = time.strftime("%H:%M UTC", time.gmtime(time.time() + tok["expires_in"]))
    print(f"""
--- use it in Bruno / Postman / curl (token valid until {expires}; re-run for a new one) ---
Base URL:      {base}
Header:        Authorization: Bearer {token}

GET {base}/Patient?identifier=urn:ehds-demo:slot|p-001
GET {base}/DocumentReference?patient={p['id']}
GET {doc_url}
GET {base}/MedicationRequest?patient={p['id']}
GET {base}/MedicationDispense?patient={p['id']}

Everything else: {base}/llms.txt
""")


if __name__ == "__main__":
    main()
