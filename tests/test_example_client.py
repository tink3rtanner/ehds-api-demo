"""examples/ehds_client.py, the reference client, against a recording fake server.

The ITB client-side cases (DocumentConsumer, DocumentPublisher, ResourceConsumer)
are its acceptance tests; these pin the behaviour they rely on.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_spec = importlib.util.spec_from_file_location(
    "ehds_client", Path(__file__).resolve().parent.parent / "examples" / "ehds_client.py")
ehds_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ehds_client)


class Fake:
    """Answers from a queue of (status, body); records every request."""

    def __init__(self):
        self.requests: list[dict] = []
        self.replies: list[tuple[int, dict]] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def _serve(self):
                n = int(self.headers.get("content-length") or 0)
                fake.requests.append({"method": self.command, "path": self.path,
                                      "headers": dict(self.headers), "body": self.rfile.read(n).decode()})
                status, body = fake.replies.pop(0) if fake.replies else (500, {"error": "no reply queued"})
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/fhir+json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _serve

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/fhir"

    def token(self, value="tok-1"):
        self.replies.append((200, {"access_token": value, "token_type": "Bearer", "expires_in": 300}))


@pytest.fixture
def fake():
    f = Fake()
    yield f
    f.server.shutdown()


@pytest.fixture
def client(fake, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kp = tmp_path / "key.pem"
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    return ehds_client.Client(base=fake.base, token_endpoint=f"{fake.base}/token", client_id="c1",
                              key_path=kp, scope="system/*.rs", state_path=tmp_path / "state.json")


def _b64url_json(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_token_request_is_a_signed_client_assertion(fake, client):
    fake.token()
    assert client.token() == "tok-1"
    req = fake.requests[0]
    assert req["method"] == "POST" and req["path"] == "/fhir/token"
    form = urllib.parse.parse_qs(req["body"])
    assert form["grant_type"] == ["client_credentials"]
    assert form["client_assertion_type"] == ["urn:ietf:params:oauth:client-assertion-type:jwt-bearer"]
    assert form["scope"] == ["system/*.rs"]
    head, claims, sig = form["client_assertion"][0].split(".")
    assert sig
    assert _b64url_json(head)["alg"] in ("RS384", "RS256")
    c = _b64url_json(claims)
    assert c["iss"] == c["sub"] == "c1"
    assert c["aud"] == f"{fake.base}/token"
    assert 0 < c["exp"] - c["iat"] <= 300


def test_token_is_cached_across_runs(fake, client, tmp_path):
    fake.token()
    client.token()
    again = ehds_client.Client(base=client.base, token_endpoint=client.token_endpoint, client_id="c1",
                               key_path=client.key_path, scope="system/*.rs", state_path=tmp_path / "state.json")
    assert again.token() == "tok-1"
    assert len(fake.requests) == 1


def test_a_cached_token_for_another_scope_is_not_reused(fake, client, tmp_path):
    fake.token("read-token")
    client.token()
    publisher = ehds_client.Client(base=client.base, token_endpoint=client.token_endpoint, client_id="c1",
                                   key_path=client.key_path, scope="system/DocumentReference.c",
                                   state_path=tmp_path / "state.json")
    fake.token("publish-token")
    assert publisher.token() == "publish-token"


def test_search_sends_the_bearer_and_encodes_tokens(fake, client):
    fake.token()
    fake.replies.append((200, {"resourceType": "Bundle", "type": "searchset"}))
    status, body = client.search("Patient", {"identifier": "http://example.org/ids|OTB-1"})
    assert status == 200 and body["type"] == "searchset"
    req = fake.requests[1]
    assert req["method"] == "GET"
    assert req["headers"]["Authorization"] == "Bearer tok-1"
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(req["path"]).query) == {
        "identifier": ["http://example.org/ids|OTB-1"]}


def test_after_a_401_it_gets_a_fresh_token_and_retries_once(fake, client):
    fake.token("old")
    fake.replies.append((401, {"resourceType": "OperationOutcome"}))
    fake.token("fresh")
    fake.replies.append((200, {"resourceType": "Patient", "id": "p"}))
    status, _ = client.get("Patient/p")
    assert status == 200
    methods = [(r["method"], r["path"].split("?")[0]) for r in fake.requests]
    assert methods == [("POST", "/fhir/token"), ("GET", "/fhir/Patient/p"),
                       ("POST", "/fhir/token"), ("GET", "/fhir/Patient/p")]
    assert fake.requests[3]["headers"]["Authorization"] == "Bearer fresh"


def test_a_second_401_is_returned_not_retried_forever(fake, client):
    fake.token()
    fake.replies.append((401, {}))
    fake.token("fresh")
    fake.replies.append((401, {}))
    status, _ = client.get("Patient/p")
    assert status == 401
    assert len(fake.requests) == 4


EPS = {"resourceType": "Bundle", "type": "document", "timestamp": "2026-11-16T10:00:00Z",
       "identifier": {"system": "urn:ietf:rfc:3986", "value": "urn:uuid:22222222-2222-4222-8222-222222222222"},
       "entry": [{"fullUrl": "urn:uuid:33333333-3333-4333-8333-333333333333",
                  "resource": {"resourceType": "Composition", "status": "final", "title": "Patient Summary",
                               "date": "2026-11-16T10:00:00Z",
                               "type": {"coding": [{"system": "http://loinc.org", "code": "60591-5"}]}}}]}


def test_publish_builds_an_mhd_simplified_publish_document_reference(fake, client):
    raw = json.dumps(EPS, indent=1).encode()
    doc = ehds_client.simplified_publish(raw, "Patient/otb-pat-1")
    assert doc["resourceType"] == "DocumentReference"
    assert doc["status"] == "current"
    assert doc["subject"] == {"reference": "Patient/otb-pat-1"}
    # MHD UniqueIdIdentifier: system + value from the document, type fixed to uniqueId
    assert doc["masterIdentifier"] == {**EPS["identifier"], "type": {"coding": [{
        "system": "https://profiles.ihe.net/ITI/MHD/CodeSystem/IHE.MHD.MHDIdentifierType", "code": "uniqueId"}]}}
    assert doc["type"] == EPS["entry"][0]["resource"]["type"]
    assert "category" not in doc  # a Patient Summary has no document class (euridice-org PR #88)
    att = doc["content"][0]["attachment"]
    assert base64.b64decode(att["data"]) == raw
    assert att["contentType"] == "application/fhir+json"
    assert att["size"] == len(raw)
    assert att["hash"] == base64.b64encode(hashlib.sha1(raw).digest()).decode()  # noqa: S324
    assert att["creation"] == EPS["timestamp"]

    fake.token()
    fake.replies.append((201, {"resourceType": "OperationOutcome"}))
    status, _ = client.publish(raw, "Patient/otb-pat-1")
    assert status == 201
    req = fake.requests[1]
    assert req["method"] == "POST" and req["path"] == "/fhir/DocumentReference"
    assert req["headers"]["Content-Type"].startswith("application/fhir+json")
    assert json.loads(req["body"]) == doc | {"date": json.loads(req["body"])["date"]}


def test_cli_runs_one_action(fake, tmp_path, client, capsys):
    fake.token()
    fake.replies.append((200, {"resourceType": "Bundle", "type": "searchset", "total": 0}))
    code = ehds_client.main(["--base", fake.base, "--token-endpoint", f"{fake.base}/token", "--client-id", "c1",
                             "--key", str(client.key_path), "--state", str(tmp_path / "s.json"),
                             "search", "DocumentReference", "patient=otb-pat-1", "type=http://loinc.org|60591-5"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == 200 and out["body"]["type"] == "searchset"
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(fake.requests[1]["path"]).query)
    assert q == {"patient": ["otb-pat-1"], "type": ["http://loinc.org|60591-5"]}


def test_the_client_has_no_third_party_dependencies_but_cryptography():
    src = Path(ehds_client.__file__).read_text()
    imports = {line.split()[1].split(".")[0] for line in src.splitlines()
               if line.startswith(("import ", "from ")) and not line.startswith("from __future__")}
    assert imports - {"argparse", "base64", "hashlib", "json", "socket", "ssl", "sys", "time", "urllib", "uuid",
                      "pathlib", "datetime", "cryptography", "typing"} == set()


def test_category_classes_match_what_the_server_seeds():
    from scripts.seed import DOC_TYPES, DOCREF_CATEGORY_CLASS
    expected = {DOC_TYPES[cat]["code"]: cls for cat, cls in DOCREF_CATEGORY_CLASS.items()}
    assert ehds_client.CATEGORY_CLASS == expected


def test_publish_dry_run_prints_the_payload_and_sends_nothing(fake, client, tmp_path, capsys):
    doc_file = tmp_path / "eps.json"
    doc_file.write_bytes(json.dumps(EPS).encode())
    code = ehds_client.main(["--base", fake.base, "--token-endpoint", f"{fake.base}/token", "--client-id", "c1",
                             "--key", str(client.key_path), "--state", str(tmp_path / "s.json"),
                             "publish", "--patient", "Patient/otb-pat-1", "--file", str(doc_file), "--dry-run"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["resourceType"] == "DocumentReference"
    assert base64.b64decode(out["content"][0]["attachment"]["data"]) == doc_file.read_bytes()
    assert fake.requests == []


def _tls_server(tmp_path, *, min_version):
    """A local HTTPS server with a throwaway CA-signed cert; returns (url, ca_file, server)."""
    import datetime
    import ssl

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID

    def name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    now = datetime.datetime.now(datetime.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca = (x509.CertificateBuilder().subject_name(name("test ca")).issuer_name(name("test ca"))
          .public_key(ca_key.public_key()).serial_number(1).not_valid_before(now)
          .not_valid_after(now + datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (x509.CertificateBuilder().subject_name(name("localhost")).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(2).not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    (tmp_path / "ca.pem").write_bytes(ca.public_bytes(pem))
    (tmp_path / "srv.pem").write_bytes(cert.public_bytes(pem) + key.private_bytes(
        pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = min_version
    ctx.load_cert_chain(tmp_path / "srv.pem")
    fake = Fake()
    fake.server.socket = ctx.wrap_socket(fake.server.socket, server_side=True)
    return fake.base.replace("http://127.0.0.1", "https://localhost"), tmp_path / "ca.pem", fake


def test_tls_check_reports_the_version_the_client_negotiates(tmp_path, monkeypatch, capsys):
    import ssl
    url, ca, fake = _tls_server(tmp_path, min_version=ssl.TLSVersion.TLSv1_3)
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    try:
        info = ehds_client.tls_check(url)
        assert info["version"] == "TLSv1.3"
        assert info["ok"] is True
        assert info["host"] == "localhost" and info["cipher"]
        assert ehds_client.main(["--base", url, "--client-id", "c1", "--key", "unused",
                                 "--token-endpoint", "unused", "tls-check"]) == 0
        assert json.loads(capsys.readouterr().out)["version"] == "TLSv1.3"
    finally:
        fake.server.shutdown()


def test_tls_check_refuses_a_plain_http_base():
    with pytest.raises(ValueError, match="https"):
        ehds_client.tls_check("http://localhost:1/fhir")
