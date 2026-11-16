#!/usr/bin/env python3
"""Reference client for the HL7 Europe Health Data API: one action per run.

Document Consumer, Document Publisher and Resource Consumer in one small file.
Needs Python 3.9+ and the `cryptography` package. Unlike quickstart.py (which
registers and walks a fixed tour), this does exactly the one thing you ask, so
it can be pointed at any server, including a test bed that checks each request.

    ehds_client.py [connection options] token
    ehds_client.py [connection options] get Patient/123
    ehds_client.py [connection options] search DocumentReference patient=123 type=http://loinc.org|60591-5
    ehds_client.py [connection options] publish --patient Patient/123 --file summary.json [--dry-run]
    ehds_client.py [connection options] tls-check     # the TLS version this client negotiates

Connection options: --base (FHIR base URL), --token-endpoint (default: read from
<base>/.well-known/smart-configuration), --client-id and --key (a registered
SMART Backend Services client and its RSA private key, PEM), --scope, --state
(where the token is cached between runs).

Behaviour the EU API relies on:
- SMART Backend Services: client_credentials with an RS384-signed JWT client
  assertion (RFC 7523), exp at most 5 minutes after iat.
- The token is cached per (token endpoint, client, scope) until shortly before
  it expires, so consecutive runs reuse it.
- A 401 on a FHIR call drops the token, gets a fresh one and retries once.
- publish is ITI-105 Simplified Publish: a DocumentReference with the document
  inline (base64), plus the metadata MHD Minimal asks for, taken from the
  document itself: masterIdentifier, type, category (the LOINC document class),
  date, size, SHA-1 hash, creation.

Output is JSON: {"status": <http status>, "body": <response body>}. Exit code 0
for a 2xx answer, 1 otherwise.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

FHIR_JSON = "application/fhir+json"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
UNIQUE_ID_TYPE = {"coding": [{"system": "https://profiles.ihe.net/ITI/MHD/CodeSystem/IHE.MHD.MHDIdentifierType",
                              "code": "uniqueId"}]}

# DocumentReference.category: the coarse LOINC document class per document type,
# as the demo server seeds it (euridice-org PR #88). A Patient Summary has none.
CATEGORY_CLASS: dict[str, dict[str, str] | None] = {
    "60591-5": None,
    "11502-2": {"system": "http://loinc.org", "code": "26436-6", "display": "Laboratory Studies (set)"},
    "34105-7": {"system": "http://loinc.org", "code": "18842-5", "display": "Discharge summary"},
    "18748-4": {"system": "http://loinc.org", "code": "18748-4", "display": "Diagnostic imaging study"},
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")  # noqa: UP017


class Client:
    def __init__(self, *, base: str, token_endpoint: str, client_id: str, key_path: Path | str,
                 scope: str, state_path: Path | str):
        self.base = base.rstrip("/")
        self.token_endpoint = token_endpoint
        self.client_id = client_id
        self.key_path = Path(key_path)
        self.scope = scope
        self.state_path = Path(state_path)

    # ---------- HTTP ----------

    def _http(self, method: str, url: str, *, body: bytes | None = None, headers: dict[str, str] | None = None
              ) -> tuple[int, Any]:
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - the URL is the user's server
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, raw.decode(errors="replace")

    # ---------- token ----------

    def _cache_key(self) -> str:
        return f"{self.token_endpoint} {self.client_id} {self.scope}"

    def _load_cache(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_cache(self, cache: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(cache, indent=1))
        self.state_path.chmod(0o600)

    def _assertion(self) -> str:
        key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        now = int(time.time())
        header = {"alg": "RS384", "typ": "JWT", "kid": f"{self.client_id}-key-1"}
        claims = {"iss": self.client_id, "sub": self.client_id, "aud": self.token_endpoint,
                  "jti": str(uuid.uuid4()), "iat": now, "exp": now + 240}
        signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
        sig = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA384())
        return f"{signing_input}.{_b64url(sig)}"

    def token(self, *, fresh: bool = False) -> str:
        cache = self._load_cache()
        hit = cache.get(self._cache_key())
        if hit and not fresh and hit["expires_at"] > time.time() + 30:
            return hit["access_token"]
        form = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": self.scope,
                                       "client_assertion_type": ASSERTION_TYPE,
                                       "client_assertion": self._assertion()}).encode()
        status, body = self._http("POST", self.token_endpoint, body=form,
                                  headers={"Content-Type": "application/x-www-form-urlencoded",
                                           "Accept": "application/json"})
        if status != 200 or not isinstance(body, dict) or "access_token" not in body:
            raise RuntimeError(f"token request failed: {status} {body}")
        cache[self._cache_key()] = {"access_token": body["access_token"],
                                    "expires_at": time.time() + int(body.get("expires_in") or 300)}
        self._save_cache(cache)
        return body["access_token"]

    # ---------- FHIR ----------

    def _fhir(self, method: str, path: str, body: bytes | None = None,
              accept: str = FHIR_JSON) -> tuple[int, Any]:
        url = path if path.startswith(("http://", "https://")) else f"{self.base}/{path.lstrip('/')}"
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self.token(fresh=attempt == 2)}", "Accept": accept}
            if body is not None:
                headers["Content-Type"] = FHIR_JSON
            status, out = self._http(method, url, body=body, headers=headers)
            if status != 401:
                break
        return status, out

    def get(self, path: str, accept: str = FHIR_JSON) -> tuple[int, Any]:
        return self._fhir("GET", path, accept=accept)

    def search(self, rtype: str, params: dict[str, str]) -> tuple[int, Any]:
        return self._fhir("GET", f"{rtype}?{urllib.parse.urlencode(params)}")

    def publish(self, document: bytes, patient_ref: str) -> tuple[int, Any]:
        return self._fhir("POST", "DocumentReference",
                          json.dumps(simplified_publish(document, patient_ref)).encode())


def simplified_publish(document: bytes, patient_ref: str) -> dict[str, Any]:
    """ITI-105 DocumentReference for a FHIR document, its metadata read from the document."""
    bundle = json.loads(document)
    comp = next((e["resource"] for e in bundle.get("entry") or []
                 if (e.get("resource") or {}).get("resourceType") == "Composition"), {})
    doc: dict[str, Any] = {"resourceType": "DocumentReference", "status": "current"}
    if isinstance(bundle.get("identifier"), dict):
        # MHD UniqueIdIdentifier: the document's own identifier, typed as its uniqueId
        doc["masterIdentifier"] = {**bundle["identifier"], "type": UNIQUE_ID_TYPE}
    if comp.get("type"):
        doc["type"] = comp["type"]
        code = next((c.get("code") for c in comp["type"].get("coding") or []
                     if c.get("system") == "http://loinc.org"), None)
        if CATEGORY_CLASS.get(code):
            doc["category"] = [{"coding": [CATEGORY_CLASS[code]]}]
    doc["subject"] = {"reference": patient_ref}
    doc["date"] = _now()
    attachment = {"contentType": FHIR_JSON, "data": base64.b64encode(document).decode(),
                  "size": len(document),
                  "hash": base64.b64encode(hashlib.sha1(document).digest()).decode()}  # noqa: S324 - MHD uses SHA-1
    if comp.get("title"):
        attachment["title"] = comp["title"]
    if bundle.get("timestamp") or comp.get("date"):
        attachment["creation"] = bundle.get("timestamp") or comp.get("date")
    doc["content"] = [{"attachment": attachment}]
    return doc


def tls_check(base: str) -> dict[str, Any]:
    """The TLS version and cipher this client negotiates with the server at ``base``.

    Uses ssl.create_default_context(), the same context urllib uses for every
    request this client makes, so the answer is the client's own, not a probe's.
    ok is True for TLS 1.2 or higher (EU Health Data API: TLS >= 1.2)."""
    url = urllib.parse.urlsplit(base)
    if url.scheme != "https":
        raise ValueError(f"tls-check needs an https base URL, not {url.scheme}://")
    host, port = url.hostname, url.port or 443
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=30) as raw, \
            ctx.wrap_socket(raw, server_hostname=host) as tls:
        version, cipher = tls.version(), tls.cipher()[0]
    return {"host": host, "port": port, "version": version, "cipher": cipher,
            "ok": version in ("TLSv1.2", "TLSv1.3"), "checked_at": _now()}


def _discover_token_endpoint(base: str) -> str:
    with urllib.request.urlopen(f"{base.rstrip('/')}/.well-known/smart-configuration", timeout=60) as r:  # noqa: S310
        return json.load(r)["token_endpoint"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, help="FHIR base URL")
    ap.add_argument("--token-endpoint", help="default: from <base>/.well-known/smart-configuration")
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--key", required=True, help="RSA private key (PEM) registered for the client")
    ap.add_argument("--scope", default="system/*.rs")
    ap.add_argument("--state", default=str(Path.home() / ".ehds-client-tokens.json"))
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("token", help="get (or reuse) an access token")
    g = sub.add_parser("get", help="GET a path relative to the base, e.g. Patient/123 or Binary/abc")
    g.add_argument("path")
    g.add_argument("--accept", default=FHIR_JSON)
    s = sub.add_parser("search", help="search a resource type: search Condition patient=123")
    s.add_argument("type")
    s.add_argument("params", nargs="*", help="name=value pairs")
    p = sub.add_parser("publish", help="ITI-105 Simplified Publish of a FHIR document")
    p.add_argument("--patient", required=True, help="the subject, e.g. Patient/123")
    p.add_argument("--file", required=True, help="the document Bundle (JSON); its bytes are sent unchanged")
    p.add_argument("--dry-run", action="store_true", help="print the DocumentReference and send nothing")
    sub.add_parser("tls-check", help="report the TLS version this client negotiates with the base URL")
    a = ap.parse_args(argv)
    if a.action == "tls-check":
        info = tls_check(a.base)
        print(json.dumps(info, indent=1))
        return 0 if info["ok"] else 1
    if a.action == "publish" and a.dry_run:
        print(json.dumps(simplified_publish(Path(a.file).read_bytes(), a.patient), indent=1))
        return 0

    client = Client(base=a.base, token_endpoint=a.token_endpoint or _discover_token_endpoint(a.base),
                    client_id=a.client_id, key_path=a.key, scope=a.scope, state_path=a.state)
    if a.action == "token":
        status, body = 200, {"access_token": client.token()}
    elif a.action == "get":
        status, body = client.get(a.path, accept=a.accept)
    elif a.action == "search":
        params = dict(kv.split("=", 1) for kv in a.params)
        status, body = client.search(a.type, params)
    else:
        status, body = client.publish(Path(a.file).read_bytes(), a.patient)
    print(json.dumps({"status": status, "body": body}, indent=1))
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
