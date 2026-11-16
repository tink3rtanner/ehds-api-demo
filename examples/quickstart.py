#!/usr/bin/env python3
"""Quickstart for the EHDS demo server (HL7 Europe Health Data API).

One file. Needs Python 3.9+ and the `cryptography` package:

    pip install cryptography
    python quickstart.py                 # register, token, find, retrieve
    python quickstart.py --publish       # ...then publish a document and read it back

It makes an RSA key (saved next to this script as ehds-quickstart-key.pem and
reused on later runs), registers a client, gets a SMART Backend Services token,
finds a patient, lists their documents and retrieves one. With --publish it also
publishes a small Patient Summary with ITI-105 and checks the server returns the
exact bytes.
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
KEY_FILE = Path(__file__).with_name("ehds-quickstart-key.pem")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def http(method: str, url: str, *, token: str | None = None, body: bytes | None = None,
         ctype: str | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/fhir+json, application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def must(status: int, raw: bytes, ok: tuple[int, ...] = (200,)) -> dict:
    if status not in ok:
        sys.exit(f"HTTP {status}: {raw.decode(errors='replace')[:800]}")
    return json.loads(raw)


def load_key() -> rsa.RSAPrivateKey:
    if KEY_FILE.exists():
        return serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return key


def sign_rs256(key: rsa.RSAPrivateKey, header: dict, claims: dict) -> str:
    signing_input = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(claims).encode())}"
    sig = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{b64url(sig)}"


def tiny_patient_summary() -> dict:
    """A minimal Patient Summary document for a new synthetic patient."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    pat, comp, allergy = (f"urn:uuid:{uuid.uuid4()}" for _ in range(3))
    tag = secrets.token_hex(3)
    return {
        "resourceType": "Bundle",
        "identifier": {"system": "urn:ietf:rfc:3986", "value": f"urn:uuid:{uuid.uuid4()}"},
        "type": "document",
        "timestamp": now,
        "entry": [
            {"fullUrl": comp, "resource": {
                "resourceType": "Composition", "status": "final",
                "type": {"coding": [{"system": "http://loinc.org", "code": "60591-5",
                                     "display": "Patient summary Document"}]},
                "subject": {"reference": pat}, "date": now, "title": "Patient Summary",
                "author": [{"display": "Quickstart script"}],
                "section": [{"title": "Allergies", "code": {"coding": [{"system": "http://loinc.org", "code": "48765-2"}]},
                             "text": {"status": "generated",
                                      "div": '<div xmlns="http://www.w3.org/1999/xhtml">Penicillin allergy</div>'},
                             "entry": [{"reference": allergy}]}]}},
            {"fullUrl": pat, "resource": {
                "resourceType": "Patient",
                "identifier": [{"system": "urn:ehds-demo:quickstart", "value": f"qs-{tag}"}],
                "name": [{"family": "Quickstart", "given": [f"Demo-{tag}"]}],
                "gender": "unknown", "birthDate": "1990-01-01", "address": [{"country": "BE"}]}},
            {"fullUrl": allergy, "resource": {
                "resourceType": "AllergyIntolerance",
                "code": {"coding": [{"system": "http://snomed.info/sct", "code": "764146007", "display": "Penicillin"}]},
                "patient": {"reference": pat}}},
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--client-id", default=f"quickstart-{secrets.token_hex(3)}")
    ap.add_argument("--publish", action="store_true", help="also publish a document (ITI-105)")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    key = load_key()

    # 0. discovery
    smart = must(*http("GET", f"{base}/.well-known/smart-configuration")[::2])
    token_url = smart["token_endpoint"]

    # 1. register (open to anyone)
    pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    scopes = ["system/*.rs", "system/DocumentReference.c"]
    st, _, raw = http("POST", smart["registration_endpoint"], ctype="application/json",
                      body=json.dumps({"client_id": a.client_id, "scopes": scopes, "public_key_pem": pem}).encode())
    reg = must(st, raw)
    kid = reg["next_steps"]["client_assertion_kid"]
    print(f"1. registered client {a.client_id} (kid {kid})")

    # 2. token
    now = int(time.time())
    assertion = sign_rs256(key, {"alg": "RS256", "typ": "JWT", "kid": kid},
                           {"iss": a.client_id, "sub": a.client_id, "aud": token_url,
                            "iat": now, "exp": now + 60, "jti": str(uuid.uuid4())})
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion, "scope": " ".join(scopes)}).encode()
    st, _, raw = http("POST", token_url, body=form, ctype="application/x-www-form-urlencoded")
    tok = must(st, raw)["access_token"]
    print(f"2. got a token (scope: {json.loads(raw)['scope']})")

    # 3. find a patient (PDQm)
    st, _, raw = http("GET", f"{base}/Patient?identifier=urn:ehds-demo:slot|p-001", token=tok)
    p = must(st, raw)["entry"][0]["resource"]
    print(f"3. found Patient/{p['id']}: {p['name'][0]['given'][0]} {p['name'][0]['family']}")

    # 4. find documents (ITI-67)
    st, _, raw = http("GET", f"{base}/DocumentReference?patient={p['id']}", token=tok)
    docs = [e["resource"] for e in must(st, raw).get("entry", [])]
    for d in docs:
        print(f"4. document: {d['type']['coding'][0]['display']:<28} -> {d['content'][0]['attachment']['url']}")

    # 5. retrieve one (ITI-68)
    url = docs[0]["content"][0]["attachment"]["url"]
    st, _, raw = http("GET", url if url.startswith("http") else f"{base}/{url}", token=tok)
    b = must(st, raw)
    print(f"5. retrieved Bundle type={b['type']} with {len(b['entry'])} entries")

    if not a.publish:
        print("done. run with --publish to publish a document too.")
        return

    # 6. publish (ITI-105 Simplified Publish)
    doc_bytes = json.dumps(tiny_patient_summary(), indent=2).encode()
    docref = {"resourceType": "DocumentReference", "status": "current",
              "type": {"coding": [{"system": "http://loinc.org", "code": "60591-5"}]},
              "subject": {"reference": "Patient/not-on-the-server-yet"},
              "content": [{"attachment": {"contentType": "application/fhir+json",
                                          "data": base64.b64encode(doc_bytes).decode()}}]}
    st, hdrs, raw = http("POST", f"{base}/DocumentReference", token=tok, ctype="application/fhir+json",
                         body=json.dumps(docref).encode())
    out = must(st, raw, ok=(201,))
    loc = hdrs.get("Location") or hdrs.get("location")
    print(f"6. published: 201 Location {loc}, subject {out['subject']['reference']}")

    # read it back: search, then retrieve the exact bytes
    pid = out["subject"]["reference"].split("/")[1]
    st, _, raw = http("GET", f"{base}/DocumentReference?patient={pid}", token=tok)
    found = [e["resource"]["id"] for e in must(st, raw).get("entry", [])]
    st, _, back = http("GET", f"{base}/{out['content'][0]['attachment']['url']}", token=tok)
    print(f"   found by ITI-67: {out['id'] in found}; bytes identical on ITI-68: {back == doc_bytes}")


if __name__ == "__main__":
    main()
