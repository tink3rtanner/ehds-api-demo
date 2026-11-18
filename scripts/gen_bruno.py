"""Generate the Bruno collection in examples/bruno/ (60 requests, folders 00-07).

Edit this file, not the .bru files, then: python -m scripts.gen_bruno
Run it:  cd examples/bruno && bru run --env ehds.joshpriebe.com --sandbox developer

The private key is the user's own (examples/bruno/private-key.pem, gitignored).
The client id is derived from that key unless the environment sets clientId.
"""
from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "examples" / "bruno"


def indent(s: str, n: int = 2) -> str:
    s = textwrap.dedent(s).strip("\n")
    return "\n".join((" " * n + line) if line.strip() else "" for line in s.splitlines())


def block(name: str, body: str) -> str:
    return f"{name} {{\n{indent(body)}\n}}\n"


def request(folder: str, seq: int, name: str, method: str, url: str, *, auth: str = "inherit",
            headers: dict | None = None, body_kind: str = "none", body: str | None = None,
            pre: str | None = None, post: str | None = None, tests: str | None = None,
            docs: str | None = None):
    parts = [block("meta", f"name: {name}\ntype: http\nseq: {seq}")]
    parts.append(block(method.lower(), f"url: {url}\nbody: {body_kind}\nauth: {auth}"))
    hdrs = {"Accept": "application/fhir+json"}
    hdrs.update(headers or {})
    parts.append(block("headers", "\n".join(f"{k}: {v}" for k, v in hdrs.items())))
    if body is not None:
        tag = {"json": "body:json", "formUrlEncoded": "body:form-urlencoded", "text": "body:text"}[body_kind]
        parts.append(block(tag, body))
    if pre:
        parts.append(block("script:pre-request", pre))
    if post:
        parts.append(block("script:post-response", post))
    if tests:
        parts.append(block("tests", tests))
    if docs:
        parts.append(block("docs", docs))
    d = OUT / folder
    d.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in name if ch not in '/\\|$:?*"<>')
    (d / f"{seq:02d} {safe}.bru").write_text("\n".join(parts), encoding="utf-8")


def folder_meta(folder: str, seq: int, docs: str, auth_none: bool = False):
    d = OUT / folder
    d.mkdir(parents=True, exist_ok=True)
    parts = [block("meta", f"name: {folder}\nseq: {seq}")]
    if auth_none:
        parts.append(block("auth", "mode: none"))
    parts.append(block("docs", docs))
    (d / "folder.bru").write_text("\n".join(parts), encoding="utf-8")


OK200 = "test('200 OK', function() { expect(res.getStatus()).to.equal(200); });"
SEARCHSET = """
test('200 searchset', function() {
  expect(res.getStatus()).to.equal(200);
  expect(res.getBody().resourceType).to.equal('Bundle');
  expect(res.getBody().type).to.equal('searchset');
});
"""


def searchset_nonempty(rtype: str) -> str:
    return SEARCHSET + f"""
test('returns {rtype}', function() {{
  const e = (res.getBody().entry || []).filter(x => (x.search || {{}}).mode !== 'include' && (x.search || {{}}).mode !== 'outcome');
  expect(e.length).to.be.above(0);
  e.forEach(x => expect(x.resource.resourceType).to.equal('{rtype}'));
}});
"""


DOC_BUNDLE_TESTS = """
test('200 document Bundle', function() {
  expect(res.getStatus()).to.equal(200);
  const b = res.getBody();
  expect(b.resourceType).to.equal('Bundle');
  expect(b.type).to.equal('document');
});
test('Composition is first entry', function() {
  expect(res.getBody().entry[0].resource.resourceType).to.equal('Composition');
});
test('Bundle contains the Patient', function() {
  expect(res.getBody().entry.some(e => e.resource.resourceType === 'Patient')).to.equal(true);
});
"""

# Shared JS: sign a SMART Backend Services client assertion (RS256) with the local key.
SIGN_JS = r"""
function ehdsAssertion() {
  const crypto = require('crypto');
  const fs = require('fs');
  const path = require('path');
  const base = bru.getEnvVar('baseUrl');
  const pem = fs.readFileSync(path.resolve(bru.cwd(), bru.getEnvVar('privateKeyPath')), 'utf8');
  const cid = (bru.getEnvVar('clientId') || ('bruno-' + crypto.createHash('sha256').update(crypto.createPublicKey(pem).export({ type: 'spki', format: 'der' })).digest('hex').slice(0, 12)));
  const b64u = s => Buffer.from(s).toString('base64url');
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: 'RS256', typ: 'JWT', kid: cid + '-key-1' };
  const claims = { iss: cid, sub: cid, aud: base + '/token', exp: now + 60, jti: crypto.randomUUID() };
  const input = b64u(JSON.stringify(header)) + '.' + b64u(JSON.stringify(claims));
  return input + '.' + crypto.sign('RSA-SHA256', Buffer.from(input), pem).toString('base64url');
}
"""

COLLECTION_PRE = SIGN_JS + r"""
// Auto-token: when no bearer is cached (or it expires within 60 s), fetch one.
// Skipped for discovery, registration and the token request itself.
const url = req.getUrl();
const base = bru.getEnvVar('baseUrl');
const open = /\/(token|register-client)(\/[^/]*)?$/.test(url) || /\/(metadata|\.well-known\/[^/]+)$/.test(url)
  || url.replace(/\/$/, '') === base;
const exp = Number(bru.getVar('tokenExpiresAt') || 0);
if (!open && (!bru.getVar('accessToken') || Date.now() / 1000 > exp - 60)) {
  const axios = require('axios');
  // plain encoding: the CLI sandbox has no URLSearchParams
  const form = Object.entries({
    grant_type: 'client_credentials',
    client_assertion_type: 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
    client_assertion: ehdsAssertion(),
    scope: bru.getEnvVar('scope'),
  }).map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');
  const r = await axios.post(base + '/token', form,
    { headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, validateStatus: () => true });
  if (r.status === 200 && r.data.access_token) {
    bru.setVar('accessToken', r.data.access_token);
    bru.setVar('tokenExpiresAt', Math.floor(Date.now() / 1000) + Number(r.data.expires_in || 3600));
  } else {
    console.log('auto-token failed', r.status, JSON.stringify(r.data).slice(0, 300));
  }
}
"""

# Minimal patient summary for ITI-105. The Patient carries a fixed identifier so
# repeat runs file under the same server Patient instead of creating new ones.
PUBLISH_PRE = r"""
const crypto = require('crypto');
const now = new Date().toISOString();
const runId = crypto.randomUUID();
const bundle = {
  resourceType: 'Bundle',
  identifier: { system: 'urn:ietf:rfc:3986', value: 'urn:uuid:' + runId },
  type: 'document',
  timestamp: now,
  entry: [
    { fullUrl: 'urn:uuid:' + runId + '-comp', resource: {
      resourceType: 'Composition', status: 'final',
      type: { coding: [{ system: 'http://loinc.org', code: '60591-5', display: 'Patient summary Document' }] },
      subject: { reference: 'urn:uuid:' + runId + '-pat' },
      date: now, author: [{ display: 'Bruno collection' }],
      title: 'Patient Summary (Bruno ITI-105 test ' + now + ')',
      section: [{ title: 'Allergies and intolerances',
        code: { coding: [{ system: 'http://loinc.org', code: '48765-2' }] },
        text: { status: 'generated', div: '<div xmlns="http://www.w3.org/1999/xhtml">No known allergies</div>' } }] } },
    { fullUrl: 'urn:uuid:' + runId + '-pat', resource: {
      resourceType: 'Patient', id: 'bruno-ps-patient',
      identifier: [{ system: bru.getEnvVar('publishPatientSystem'), value: bru.getEnvVar('publishPatientValue') }],
      name: [{ family: 'Bruno', given: ['Testa'] }], gender: 'female', birthDate: '1990-01-01' } },
  ],
};
const bytes = Buffer.from(JSON.stringify(bundle), 'utf8');
bru.setVar('publishBundleJson', bytes.toString('utf8'));
bru.setVar('publishData', bytes.toString('base64'));
bru.setVar('publishSize', bytes.length);
bru.setVar('publishHash', crypto.createHash('sha1').update(bytes).digest('base64'));
"""



DOCS = """
## EU Health Data API demo

Every transaction the demo server supports, in order: discovery, SMART Backend
Services auth, PDQm patient search (ITI-78), find documents (ITI-67), retrieve
them (ITI-68), publish (ITI-105) and single-resource access. Each request has tests.
Synthetic data only.

### Setup

1. Make a key next to this collection (it never leaves your machine):

   ```
   openssl genrsa -out private-key.pem 2048
   ```

2. Open the collection in Bruno, pick an environment (`ehds.joshpriebe.com` or
   `local`) and turn on **Developer mode** (the scripts sign JWTs with your key).
3. Run the collection. 01 Auth registers your public key under a client id
   derived from it, so nothing else needs to be set. To use a client you already
   registered, add `clientId` to the environment.

CLI: `bru run --env ehds.joshpriebe.com --sandbox developer`

### Folders

```
00 Discovery   /, smart-configuration, metadata, jwks          (no token)
01 Auth        register-client, then token                     (private_key_jwt)
02 PDQm        Patient?identifier=slot|p-001 sets patientId
03 ITI-67      DocumentReference?patient=...
04 ITI-68      GET each document's attachment.url
05 ITI-105     publish a document, then read, find and retrieve it
06 Resources   MedicationRequest, Observation, ... $summary, $everything
07 Errors      401, 404, empty searchset
```

Requests after 01 fetch a token themselves when needed. Running 05 publishes a
small synthetic Patient Summary to the server.

Generated by `scripts/gen_bruno.py` in the server repo: edit that, not the .bru files.
"""

def build():
    # clear the previous output but keep the user's key (*.pem)
    if OUT.exists():
        for p in OUT.iterdir():
            if p.suffix != ".pem":
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / ".gitignore").write_text("*.pem\n", encoding="utf-8")
    (OUT / "bruno.json").write_text(json.dumps({
        "version": "1",
        "name": "EU Health Data API demo",
        "type": "collection",
        "ignore": ["node_modules", ".git"],
        "scripts": {"moduleWhitelist": ["crypto", "fs", "path", "axios"],
                    "filesystemAccess": {"allow": True}},
    }, indent=2) + "\n", encoding="utf-8")

    (OUT / "collection.bru").write_text(
        block("auth", "mode: bearer") + "\n" +
        block("auth:bearer", "token: {{accessToken}}") + "\n" +
        block("script:pre-request", COLLECTION_PRE) + "\n" +
        block("docs", DOCS),
        encoding="utf-8")

    env = OUT / "environments"
    env.mkdir()
    for name, base in (("ehds.joshpriebe.com", "https://ehds.joshpriebe.com"), ("local", "http://localhost:8000")):
        (env / f"{name}.bru").write_text(block("vars", f"""
            baseUrl: {base}
            clientName: Bruno collection
            privateKeyPath: private-key.pem
            scope: system/*.rs system/DocumentReference.c
            slotSystem: urn:ehds-demo:slot
            slot: p-001
            loinc: http://loinc.org
            publishPatientSystem: urn:ehds-demo:bruno
            publishPatientValue: bruno-ps-patient-1
        """), encoding="utf-8")

    # ---------------- 00 Discovery ----------------
    f = "00 Discovery"
    folder_meta(f, 1, "Unauthenticated discovery: discovery document, SMART configuration, CapabilityStatement, server JWKS.", auth_none=True)
    request(f, 1, "Discovery document", "GET", "{{baseUrl}}/", auth="none",
            headers={"Accept": "application/json"},
            tests=OK200 + "\ntest('lists steps', function() { expect(res.getBody().steps).to.be.an('array').that.is.not.empty; });")
    request(f, 2, "SMART configuration", "GET", "{{baseUrl}}/.well-known/smart-configuration", auth="none",
            headers={"Accept": "application/json"},
            tests=OK200 + """
test('backend services advertised', function() {
  const b = res.getBody();
  expect(b.token_endpoint).to.equal(bru.getEnvVar('baseUrl') + '/token');
  expect(b.grant_types_supported).to.include('client_credentials');
  expect(b.token_endpoint_auth_methods_supported).to.include('private_key_jwt');
  expect(b.scopes_supported).to.include('system/DocumentReference.c');
});""",
            docs="SMART App Launch 2 discovery for Backend Services (client_credentials + private_key_jwt).")
    request(f, 3, "CapabilityStatement", "GET", "{{baseUrl}}/metadata", auth="none",
            tests=OK200 + """
test('FHIR R4 CapabilityStatement with DocumentReference read/search/create', function() {
  const b = res.getBody();
  expect(b.resourceType).to.equal('CapabilityStatement');
  expect(b.fhirVersion).to.equal('4.0.1');
  const dr = b.rest[0].resource.find(r => r.type === 'DocumentReference');
  expect(dr.interaction.map(i => i.code)).to.include.members(['read', 'search-type', 'create']);
});""")
    request(f, 4, "Server JWKS", "GET", "{{baseUrl}}/.well-known/jwks.json", auth="none",
            headers={"Accept": "application/json"},
            tests=OK200 + "\ntest('has keys', function() { expect(res.getBody().keys).to.be.an('array').that.is.not.empty; });")

    # ---------------- 01 Auth ----------------
    f = "01 Auth"
    folder_meta(f, 2, "Register this collection as a SMART backend client (idempotent: 409 = already registered), then get a token. "
                      "The private key is read from `privateKeyPath` (relative to the collection folder) and never leaves the machine. "
                      "The collection pre-request script also fetches a token automatically when one is missing or expiring.", auth_none=True)
    request(f, 1, "Register client", "POST", "{{baseUrl}}/register-client", auth="none",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            body_kind="json", body="{{registerBody}}",
            pre=r"""
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const pem = fs.readFileSync(path.resolve(bru.cwd(), bru.getEnvVar('privateKeyPath')), 'utf8');
const jwk = crypto.createPublicKey(pem).export({ format: 'jwk' });
jwk.alg = 'RS256'; jwk.use = 'sig';
bru.setVar('registerBody', JSON.stringify({
  client_id: (bru.getEnvVar('clientId') || ('bruno-' + crypto.createHash('sha256').update(crypto.createPublicKey(pem).export({ type: 'spki', format: 'der' })).digest('hex').slice(0, 12))),
  client_name: bru.getEnvVar('clientName'),
  scopes: bru.getEnvVar('scope').split(' '),
  jwk: jwk,
}));
""",
            tests="""
test('registered (200) or already registered (409)', function() {
  expect([200, 201, 409]).to.include(res.getStatus());
});""",
            docs="Open dynamic client registration. The public JWK is derived from the local private key in the pre-request script.")
    request(f, 2, "Get token (client_credentials + private_key_jwt)", "POST", "{{baseUrl}}/token", auth="none",
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            body_kind="formUrlEncoded", body="""
grant_type: client_credentials
client_assertion_type: urn:ietf:params:oauth:client-assertion-type:jwt-bearer
client_assertion: {{clientAssertion}}
scope: {{scope}}
""",
            pre=SIGN_JS + "\nbru.setVar('clientAssertion', ehdsAssertion());",
            post="""
const b = res.getBody();
if (res.getStatus() === 200 && b.access_token) {
  bru.setVar('accessToken', b.access_token);
  bru.setVar('tokenExpiresAt', Math.floor(Date.now() / 1000) + Number(b.expires_in || 3600));
}""",
            tests=OK200 + """
test('bearer token with read + publish scope', function() {
  const b = res.getBody();
  expect(b.token_type.toLowerCase()).to.equal('bearer');
  expect(b.access_token).to.be.a('string').and.not.empty;
  expect(b.scope).to.include('system/*.rs');
  expect(b.scope).to.include('system/DocumentReference.c');
});""",
            docs="SMART Backend Services token request. The client assertion (RS256, kid <clientId>-key-1, aud = token endpoint) is signed in the pre-request script.")

    # ---------------- 02 PDQm ----------------
    f = "02 PDQm ITI-78 Patient"
    folder_meta(f, 3, "IHE PDQm ITI-78 Mobile Patient Demographics Query, plus $match.")
    request(f, 1, "Patient by slot identifier", "GET", "{{baseUrl}}/Patient?identifier={{slotSystem}}|{{slot}}",
            post="""
const e = (res.getBody().entry || [])[0];
if (e) {
  const p = e.resource;
  bru.setVar('patientId', p.id);
  bru.setVar('patientFamily', p.name[0].family);
  bru.setVar('patientGiven', p.name[0].given[0]);
  bru.setVar('patientBirthDate', p.birthDate);
  bru.setVar('patientGender', p.gender);
}""",
            tests=searchset_nonempty("Patient") + """
test('exactly one match carrying the slot identifier', function() {
  const e = res.getBody().entry;
  expect(e.length).to.equal(1);
  expect(e[0].resource.identifier.some(i => i.value === bru.getEnvVar('slot'))).to.equal(true);
});""",
            docs="Captures patientId and demographics for later requests. Change `slot` (p-001 … p-010) to use another seeded patient.")
    request(f, 2, "Patient read", "GET", "{{baseUrl}}/Patient/{{patientId}}",
            tests=OK200 + "\ntest('same patient', function() { expect(res.getBody().id).to.equal(bru.getVar('patientId')); });")
    request(f, 3, "Patient by family + birthdate", "GET", "{{baseUrl}}/Patient?family={{patientFamily}}&birthdate={{patientBirthDate}}",
            tests=searchset_nonempty("Patient") + """
test('finds the patient', function() {
  expect(res.getBody().entry.map(e => e.resource.id)).to.include(bru.getVar('patientId'));
});""")
    request(f, 4, "Patient by given + gender", "GET", "{{baseUrl}}/Patient?given={{patientGiven}}&gender={{patientGender}}",
            tests=searchset_nonempty("Patient") + """
test('finds the patient', function() {
  expect(res.getBody().entry.map(e => e.resource.id)).to.include(bru.getVar('patientId'));
});""")
    request(f, 5, "Patient $match", "POST", "{{baseUrl}}/Patient/$match",
            headers={"Content-Type": "application/fhir+json"}, body_kind="json", body="""
{
  "resourceType": "Parameters",
  "parameter": [
    {"name": "resource", "resource": {
      "resourceType": "Patient",
      "name": [{"family": "{{patientFamily}}", "given": ["{{patientGiven}}"]}],
      "birthDate": "{{patientBirthDate}}",
      "gender": "{{patientGender}}"
    }}
  ]
}""",
            tests=SEARCHSET + """
test('best match is the patient, with a score', function() {
  const e = res.getBody().entry.filter(x => x.resource.resourceType === 'Patient');
  expect(e[0].resource.id).to.equal(bru.getVar('patientId'));
  expect(e[0].search.score).to.be.a('number');
});""")

    # ---------------- 03 ITI-67 ----------------
    f = "03 ITI-67 Find Documents"
    folder_meta(f, 4, "IHE MHD ITI-67 Find Document References. Request 1 captures each category's attachment.url for 04 ITI-68.")
    request(f, 1, "All documents for patient", "GET", "{{baseUrl}}/DocumentReference?patient={{patientId}}",
            post="""
const map = { '60591-5': 'psUrl', '11502-2': 'labUrl', '18842-5': 'hdrUrl', '34105-7': 'hdrUrl', '85430-7': 'imgUrl', '18748-4': 'imgUrl' };
const entries = res.getBody().entry || [];
entries.forEach(e => {
  const d = e.resource;
  d.type.coding.forEach(c => { if (map[c.code]) bru.setVar(map[c.code], d.content[0].attachment.url); });
});
if (entries[0]) bru.setVar('docRefId', entries[0].resource.id);""",
            tests=searchset_nonempty("DocumentReference") + """
test('all four document categories present', function() {
  const codes = res.getBody().entry.flatMap(e => e.resource.type.coding.map(c => c.code));
  ['60591-5', '11502-2', '85430-7', '18842-5'].forEach(c => expect(codes).to.include(c));
});
test('attachments carry url and contentType', function() {
  res.getBody().entry.forEach(e => {
    const a = e.resource.content[0].attachment;
    expect(a.url).to.be.a('string');
    expect(a.contentType).to.be.a('string');
  });
});""")
    for seq, (label, code) in enumerate([("Patient Summary", "60591-5"), ("Laboratory Report", "11502-2"),
                                         ("Hospital Discharge Report", "18842-5"), ("Imaging Report", "85430-7")], start=2):
        request(f, seq, f"by type {label} ({code})", "GET",
                f"{{{{baseUrl}}}}/DocumentReference?patient={{{{patientId}}}}&type={{{{loinc}}}}|{code}",
                tests=searchset_nonempty("DocumentReference") + f"""
test('every result has type {code}', function() {{
  res.getBody().entry.forEach(e => expect(e.resource.type.coding.map(c => c.code)).to.include('{code}'));
}});""")
    request(f, 6, "by patient.identifier (chained)", "GET",
            "{{baseUrl}}/DocumentReference?patient.identifier={{slotSystem}}|{{slot}}",
            tests=searchset_nonempty("DocumentReference") + """
test('all for the slot patient', function() {
  res.getBody().entry.forEach(e => expect(e.resource.subject.reference).to.equal('Patient/' + bru.getVar('patientId')));
});""")
    request(f, 7, "by category (Laboratory 26436-6)", "GET",
            "{{baseUrl}}/DocumentReference?patient={{patientId}}&category={{loinc}}|26436-6",
            tests=searchset_nonempty("DocumentReference") + """
test('lab report found', function() {
  expect(res.getBody().entry.flatMap(e => e.resource.type.coding.map(c => c.code))).to.include('11502-2');
});""")
    request(f, 8, "status=current", "GET", "{{baseUrl}}/DocumentReference?patient={{patientId}}&status=current",
            tests=searchset_nonempty("DocumentReference") + """
test('all current', function() { res.getBody().entry.forEach(e => expect(e.resource.status).to.equal('current')); });""")
    request(f, 9, "by _id", "GET", "{{baseUrl}}/DocumentReference?_id={{docRefId}}",
            tests=searchset_nonempty("DocumentReference") + """
test('one result, the requested id', function() {
  expect(res.getBody().entry.length).to.equal(1);
  expect(res.getBody().entry[0].resource.id).to.equal(bru.getVar('docRefId'));
});""")
    request(f, 10, "DocumentReference read", "GET", "{{baseUrl}}/DocumentReference/{{docRefId}}",
            tests=OK200 + "\ntest('DocumentReference', function() { expect(res.getBody().resourceType).to.equal('DocumentReference'); });")

    # ---------------- 04 ITI-68 ----------------
    f = "04 ITI-68 Retrieve Document"
    folder_meta(f, 5, "IHE MHD ITI-68 Retrieve Document: GET the attachment.url captured by 03 ITI-67 (relative to baseUrl).")
    for seq, (label, var) in enumerate([("Patient Summary", "psUrl"), ("Laboratory Report", "labUrl"),
                                        ("Hospital Discharge Report", "hdrUrl"), ("Imaging Report", "imgUrl")], start=1):
        request(f, seq, f"Retrieve {label}", "GET", f"{{{{baseUrl}}}}/{{{{{var}}}}}", tests=DOC_BUNDLE_TESTS)

    # ---------------- 05 ITI-105 ----------------
    f = "05 ITI-105 Publish"
    folder_meta(f, 6, "IHE MHD ITI-105 Simplified Publish, run in order. 1 publishes a small patient summary whose Patient carries a fixed "
                      "identifier (publishPatientSystem|publishPatientValue), so repeat runs reuse one server Patient. 2-6 read, find, "
                      "retrieve byte-for-byte and check validation status. 7-8 publish/retrieve non-FHIR content. 9-10 are negative tests.")
    request(f, 1, "Publish FHIR document (base64 Bundle)", "POST", "{{baseUrl}}/DocumentReference",
            headers={"Content-Type": "application/fhir+json"}, body_kind="json", body="""
{
  "resourceType": "DocumentReference",
  "status": "current",
  "type": {"coding": [{"system": "http://loinc.org", "code": "60591-5", "display": "Patient summary Document"}]},
  "subject": {"reference": "Patient/bruno-ps-patient"},
  "content": [{"attachment": {
    "contentType": "application/fhir+json",
    "data": "{{publishData}}",
    "size": {{publishSize}},
    "hash": "{{publishHash}}"
  }}]
}""",
            pre=PUBLISH_PRE,
            post="""
const b = res.getBody();
if (res.getStatus() === 201) {
  bru.setVar('publishedDocRefId', b.id);
  bru.setVar('publishedUrl', b.content[0].attachment.url);
  bru.setVar('publishedPatientRef', b.subject.reference);
  bru.setVar('publishedPatientId', b.subject.reference.split('/').pop());
}""",
            tests="""
test('201 Created with Location', function() {
  expect(res.getStatus()).to.equal(201);
  expect(res.getHeader('location')).to.include('/DocumentReference/');
});
test('stored DocumentReference has url, size, hash and no data', function() {
  const a = res.getBody().content[0].attachment;
  expect(a.url).to.match(/^Bundle\\//);
  expect(a.size).to.equal(Number(bru.getVar('publishSize')));
  expect(a.hash).to.equal(bru.getVar('publishHash'));
  expect(a.data).to.be.undefined;
});
test('filed under a server Patient', function() {
  expect(res.getBody().subject.reference).to.match(/^Patient\\/[0-9a-f-]{36}$/);
});""")
    request(f, 2, "Read published DocumentReference", "GET", "{{baseUrl}}/DocumentReference/{{publishedDocRefId}}",
            tests=OK200 + "\ntest('same id', function() { expect(res.getBody().id).to.equal(bru.getVar('publishedDocRefId')); });")
    request(f, 3, "Find published document (ITI-67)", "GET",
            "{{baseUrl}}/DocumentReference?patient={{publishedPatientId}}&type={{loinc}}|60591-5",
            tests=searchset_nonempty("DocumentReference") + """
test('includes the published DocumentReference', function() {
  expect(res.getBody().entry.map(e => e.resource.id)).to.include(bru.getVar('publishedDocRefId'));
});""")
    request(f, 4, "Find published Patient by identifier", "GET",
            "{{baseUrl}}/Patient?identifier={{publishPatientSystem}}|{{publishPatientValue}}",
            tests=searchset_nonempty("Patient") + """
test('single Patient, the one the document was filed under', function() {
  expect(res.getBody().entry.length).to.equal(1);
  expect(res.getBody().entry[0].resource.id).to.equal(bru.getVar('publishedPatientId'));
});""",
            docs="Re-running the collection must not create duplicate Patients: the identifier match should reuse one.")
    request(f, 5, "Retrieve published document (ITI-68, exact content)", "GET", "{{baseUrl}}/{{publishedUrl}}",
            tests="""
test('200 document Bundle', function() {
  expect(res.getStatus()).to.equal(200);
  expect(res.getBody().type).to.equal('document');
});
test('identical to what was published', function() {
  expect(res.getBody()).to.eql(JSON.parse(bru.getVar('publishBundleJson')));
});""")
    request(f, 6, "Validation status of published document", "GET", "{{baseUrl}}/ui/api/validation/{{publishedDocRefId}}",
            headers={"Accept": "application/json"},
            tests=OK200 + """
test('has a validation state', function() {
  expect(['pending', 'validated', 'failed', 'unavailable']).to.include(res.getBody().state);
});""",
            docs="Server convenience (not IG). Post-publish profile validation is informational; this minimal fixture is not expected to pass the EPS profile.")
    request(f, 7, "Publish non-FHIR document (text-plain)", "POST", "{{baseUrl}}/DocumentReference",
            headers={"Content-Type": "application/fhir+json"}, body_kind="json", body="""
{
  "resourceType": "DocumentReference",
  "status": "current",
  "type": {"coding": [{"system": "http://loinc.org", "code": "11506-3", "display": "Progress note"}]},
  "subject": {"reference": "{{publishedPatientRef}}"},
  "content": [{"attachment": {"contentType": "text/plain", "data": "{{textData}}"}}]
}""",
            pre="""
const txt = 'Bruno ITI-105 plain-text test note ' + new Date().toISOString();
bru.setVar('textPlain', txt);
bru.setVar('textData', Buffer.from(txt, 'utf8').toString('base64'));""",
            post="if (res.getStatus() === 201) bru.setVar('binaryUrl', res.getBody().content[0].attachment.url);",
            tests="""
test('201 Created', function() { expect(res.getStatus()).to.equal(201); });
test('attachment.url is Binary/<id>', function() { expect(res.getBody().content[0].attachment.url).to.match(/^Binary\\//); });""")
    request(f, 8, "Retrieve non-FHIR document (raw Binary)", "GET", "{{baseUrl}}/{{binaryUrl}}",
            headers={"Accept": "text/plain"},
            tests="""
test('200 with original text', function() {
  expect(res.getStatus()).to.equal(200);
  expect(String(res.getBody())).to.equal(bru.getVar('textPlain'));
});""")
    request(f, 9, "Reject hash mismatch", "POST", "{{baseUrl}}/DocumentReference",
            headers={"Content-Type": "application/fhir+json"}, body_kind="json", body="""
{
  "resourceType": "DocumentReference",
  "status": "current",
  "type": {"coding": [{"system": "http://loinc.org", "code": "60591-5"}]},
  "subject": {"reference": "Patient/bruno-ps-patient"},
  "content": [{"attachment": {"contentType": "application/fhir+json", "data": "{{publishData}}", "hash": "AAAAAAAAAAAAAAAAAAAAAAAAAAA="}}]
}""",
            tests="""
test('4xx OperationOutcome', function() {
  expect(res.getStatus()).to.be.within(400, 499);
  expect(res.getBody().resourceType).to.equal('OperationOutcome');
});""")
    request(f, 10, "Reject Bundle that is not a document", "POST", "{{baseUrl}}/DocumentReference",
            headers={"Content-Type": "application/fhir+json"}, body_kind="json", body="""
{
  "resourceType": "DocumentReference",
  "status": "current",
  "type": {"coding": [{"system": "http://loinc.org", "code": "60591-5"}]},
  "subject": {"reference": "Patient/bruno-ps-patient"},
  "content": [{"attachment": {"contentType": "application/fhir+json", "data": "{{badData}}"}}]
}""",
            pre="bru.setVar('badData', Buffer.from(JSON.stringify({resourceType: 'Bundle', type: 'collection', entry: []})).toString('base64'));",
            tests="""
test('4xx OperationOutcome', function() {
  expect(res.getStatus()).to.be.within(400, 499);
  expect(res.getBody().resourceType).to.equal('OperationOutcome');
});""")

    # ---------------- 06 Resource access ----------------
    f = "06 Resource Access"
    folder_meta(f, 7, "RESTful FHIR resource access for the seeded patient, incl. ePrescription (MedicationRequest) and eDispensation (MedicationDispense).")
    seq = 1
    for rtype in ["MedicationRequest", "MedicationDispense", "MedicationStatement", "AllergyIntolerance", "Condition",
                  "Observation", "Immunization", "Procedure", "DiagnosticReport", "ImagingStudy", "Encounter",
                  "Specimen"]:
        var = rtype[0].lower() + rtype[1:] + "Id"
        cap = f"bru.setVar('{var}', ((res.getBody().entry || [])[0] || {{resource: {{}}}}).resource.id || '');"
        request(f, seq, f"{rtype} by patient", "GET", f"{{{{baseUrl}}}}/{rtype}?patient={{{{patientId}}}}",
                post=cap, tests=searchset_nonempty(rtype))
        seq += 1
    extra = [
        ("Composition for published patient", "{{baseUrl}}/Composition?patient={{publishedPatientId}}",
         searchset_nonempty("Composition"),
         "Seeded documents are compiled on retrieve, so only published documents have stored Compositions."),
        ("Observation laboratory category", "{{baseUrl}}/Observation?patient={{patientId}}&category=laboratory",
         searchset_nonempty("Observation"), None),
        ("MedicationRequest read", "{{baseUrl}}/MedicationRequest/{{medicationRequestId}}",
         OK200 + "\ntest('MedicationRequest', function() { expect(res.getBody().resourceType).to.equal('MedicationRequest'); });", None),
        ("Observation read", "{{baseUrl}}/Observation/{{observationId}}",
         OK200 + "\ntest('Observation', function() { expect(res.getBody().resourceType).to.equal('Observation'); });", None),
        ("Practitioner search", "{{baseUrl}}/Practitioner", searchset_nonempty("Practitioner"), None),
        ("Organization search", "{{baseUrl}}/Organization", searchset_nonempty("Organization"), None),
        ("Patient $summary (IPS)", "{{baseUrl}}/Patient/{{patientId}}/$summary", DOC_BUNDLE_TESTS,
         "IPS $summary - server convenience, not part of the EU IG."),
        ("Patient $everything", "{{baseUrl}}/Patient/{{patientId}}/$everything",
         OK200 + "\ntest('Bundle with entries', function() { expect(res.getBody().resourceType).to.equal('Bundle'); expect(res.getBody().entry.length).to.be.above(1); });",
         None),
    ]
    for name, url, tests, docs in extra:
        request(f, seq, name, "GET", url, tests=tests, docs=docs)
        seq += 1

    # ---------------- 07 Errors ----------------
    f = "07 Errors"
    folder_meta(f, 8, "Negative tests: missing/invalid token, unknown resources, unknown patient.")
    request(f, 1, "No token (401)", "GET", "{{baseUrl}}/Patient/{{patientId}}", auth="none",
            tests="test('401', function() { expect(res.getStatus()).to.equal(401); });")
    request(f, 2, "Invalid token (401)", "GET", "{{baseUrl}}/Patient/{{patientId}}", auth="none",
            headers={"Authorization": "Bearer not-a-real-token"},
            tests="test('401', function() { expect(res.getStatus()).to.equal(401); });")
    request(f, 3, "Unknown DocumentReference (404)", "GET", "{{baseUrl}}/DocumentReference/00000000-0000-0000-0000-000000000000",
            tests="test('404 OperationOutcome', function() { expect(res.getStatus()).to.equal(404); expect(res.getBody().resourceType).to.equal('OperationOutcome'); });")
    request(f, 4, "Unknown Bundle (404)", "GET", "{{baseUrl}}/Bundle/00000000-0000-0000-0000-000000000000",
            tests="test('404', function() { expect(res.getStatus()).to.equal(404); });")
    request(f, 5, "Unknown patient search (empty searchset)", "GET",
            "{{baseUrl}}/DocumentReference?patient=00000000-0000-0000-0000-000000000000",
            tests=SEARCHSET + "\ntest('no entries', function() { expect((res.getBody().entry || []).length).to.equal(0); });")


if __name__ == "__main__":
    build()
    print(f"wrote {sum(1 for _ in OUT.rglob('*.bru'))} .bru files to {OUT}")
