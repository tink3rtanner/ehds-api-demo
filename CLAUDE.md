# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Minimum-viable FHIR R4 server implementing the EU Health Data API (EHDS) IG.
Synthetic data only. File-backed storage. SMART Backend Services auth (JWT
client assertion). Five EHDS priority-category documents compiled on demand
as `Bundle.type=document` (patient-summary, laboratory-report, discharge-report,
imaging-report, prescription). Ships a read-only viewer at `/ui` for
connectathon demos.

`README.md` has the IG-actor matrix and pretty-stack quickstart.
`HANDOFF.md` has the full VPS bring-up runbook.

## Skills + docs for future agents

Project-scoped, both under `/srv/ehds-api/`. Future Claude sessions
should run from this directory.

**Skills** (`.claude/commands/`, invoked as `/<name>`):

- `/ehds-deploy-and-verify` — push, restart, smoke-test 4 fail-fast
  endpoints, dump logs on failure
- `/ehds-trace-submission <id>` — diagnose a single ITI-105 submission
  in `data/inbox/` (structure, dangling refs, audit correlation)
- `/ehds-register-and-mint [client-id] [scope…]` — register a SMART
  client and immediately mint a token, copy-paste ready
- `/ehds-explain-401 [audit-line]` — decision tree for "why was this
  rejected" against the actual messages in `app/auth/`
- `/ehds-reseed-safe` — reseed panel preserving `inbox/`, `audit/`,
  `clients.json`, `keys/`, then verify
- `/ehds-add-document-category <slug> <loinc> <display>` — scaffold a
  new on-demand document category end-to-end

**Docs** (`docs/`):

- `docs/TROUBLESHOOTING.md` — symptom-keyed root causes (validator
  silently passing, scope errors, 203/EXEC, etc.)
- `docs/conformance-deviations.md` — intentional spec deviations
  (permissive submit, dev anon read, prescription-isn't-a-doc, etc.)
- `docs/identity-cheatsheet.md` — slot↔uuid table, derivation chain,
  the 4 patient-reference search forms
- `docs/audit-recipes.md` — jq snippets against the JSONL audit log
- `docs/runbook.md` — on-call for TLS expiry, disk full, systemd fail,
  Caddy syntax

## Commands

```bash
# dev server (auto-reload, dev env defaults from run.sh)
./run.sh                      # :8000, ENV=dev
PORT=8088 ./run.sh            # custom port

# tests (~50s without java, ~7min including profile validation)
pytest -q                                    # everything; profile-validation skips if no JRE
pytest -q --ignore=tests/test_profile_validation.py     # fast layers only
pytest -q tests/test_profile_validation.py              # heavy java-validator only
pytest -q tests/test_patient_pdqm.py::test_read_patient # single test
pytest -q -k 'match'                                    # by keyword

# lint (CI fails on any error)
ruff check app tests scripts
ruff check --fix app tests scripts

# seed deterministically (data/ accumulates ITI-105 submissions; reseed wipes it)
python -m scripts.seed --clean

# register a SMART client (local registry write)
python -m app.tools.register_client --client-id my-app --generate --scope "system/*.read"

# register against a running server (REST mode, JSON output)
python -m app.tools.register_client --client-id my-app --generate \
    --scope "system/*.read" --scope "system/Bundle.write" \
    --base-url https://ehds.joshpriebe.com --out json
```

## Deployed environment (the live demo box)

This codebase ships to a single demo VPS at `/srv/ehds-api` per `HANDOFF.md`.
**That path is hard-coded in `deploy/ehds-api.service`** (`WorkingDirectory`,
`ReadWritePaths`). Clones to other paths break the systemd unit.

```bash
# operating the deployed service (on the VPS)
sudo systemctl restart ehds-api        # gunicorn + uvicorn workers, 127.0.0.1:8000
sudo systemctl reload caddy            # after editing /etc/caddy/Caddyfile
sudo journalctl -u ehds-api -f         # tail structured request log
cat /etc/ehds-api/env                  # env file (different from .env.example)
```

`ProtectSystem=full` + `ProtectHome=true` on the unit means writes only land
under `ReadWritePaths` (`/srv/ehds-api/data` and `/var/log/ehds-api`).
Anything else needs the path added to that list.

## Architecture (the things that span files)

### Deterministic UUIDs everywhere — `app/fhir/ids.py`

Every synthetic resource id is a `uuid5(EHDS_NAMESPACE, canonical_path)`.
Hand-rolled labels (`p-001`, `obs-p-001-00`, `doc-p-001-patient-summary`) are
gone. **The single exception**: the slot label (`p-001` … `p-010`) survives
as `Patient.identifier` with system `urn:ehds-demo:slot` and `use: secondary`.

This means:
- `Patient.id` is the canonical uuid; tests use `patient_id("p-001")` to get it
- Bundle / DocumentReference / Composition ids are derived from the slot via
  `bundle_id(slot, category)` / `docref_id(slot, category)` / `composition_id(slot, category)`
- `compile_document` in `app/fhir/document.py` reads the slot from the Patient's
  identifier and feeds it to `bundle_id` so its output matches the URLs DocumentReference
  attachments point at AND what `bundle_router._build_reverse_index()` expects
- A regression here breaks the ITI-67 → ITI-68 round-trip silently

### Patient-reference search accepts 4 equivalent shapes

`?patient=` on any resource compartment resolves through `store.resolve_patient_ref()`
and `store.find_patient_ids_by_identifier()` in `app/fhir/store.py`. All four
forms hit the same Patient set:

```
?patient=<uuid>                                      # direct reference
?patient=<system>|<value>                            # identifier-token shorthand (HAPI-style)
?patient.identifier=<system>|<value>                 # chained search (MHD ITI-67 canonical)
?patient:identifier=<system>|<value>                 # FHIR ':identifier' modifier
```

`DocumentReference` has its own filter in `app/routers/docref.py` that
duplicates this logic — **keep them in sync if you touch one**.

### FHIR-R4 model binding (critical)

`app/fhir/validate.py` must import `get_fhir_model_class` from
`fhir.resources.R4B`, not the top-level `fhir.resources`. **`fhir.resources>=8.0`
defaults to R5 models** where e.g. `Composition.subject` is `0..*` (list).
Our server advertises `fhirVersion 4.0.1` and produces R4-shaped bundles
where it's `0..1`. Without the R4B import the validator silently rejects every
valid R4 bundle and accepts garbage instead.

### Auth has a dev-mode anonymous-read shortcut

`app/auth/verify.py`: in `ENV=dev`, GET requests with **no** `Authorization`
header read synthetic data anonymously (so QR codes resolve in a phone
browser). Sending an `Authorization` header — even an invalid one — triggers
strict validation. `ENV=prod` requires a bearer always.

The signing-alg check honours the registered key's `kty`: RSA keys verify
with `RSAAlgorithm`, EC keys with `ECAlgorithm`. Alg/kty mismatch is a 401
with the reason in `error_description`.

### Discovery is layered

A fresh agent / Epic-bridge client should reach everything from one URL.
`/.well-known/smart-configuration` (in `app/auth/smart.py`) publishes:

- `registration_endpoint` and `registration_management_endpoint_template`
  (RFC 7591 / 7592)
- `example_endpoints` (every one is a real working URL with a real uuid baked
  in at request time — `example_pid = patient_id("p-001")`)
- `example_patient_lookup` with the `urn:ehds-demo:slot` system + slot values

`POST /register-client` response hands back `registration_client_uri` and
`next_steps.manage_registration` so the agent never has to read docs.
The lifecycle (GET/PATCH/PUT/DELETE on `/register-client/{client_id}`) lives
in `app/routers/discovery.py` next to the POST.

### The submit handler is permissive (and known so)

`app/routers/docsubmit.py` accepts `Bundle.type=transaction` or `document`,
runs structural validation, persists the whole bundle to `data/inbox/`, and
**mirrors every entry whose `resourceType` is in `store.SUPPORTED_TYPES`**
into the type-folder store. It does not honour `entry.request.method/url` —
any supported resource gets written regardless of what the bundle says.

This is intentional (lowest-friction publish for synthetic-data demos) but
slightly deviates from strict FHIR transaction semantics. Documented as a
known deviation; flagged in `CapabilityStatement.implementation.description`.

### Tests reseed; never copy live `data/`

`tests/conftest.py` calls `scripts.seed.seed(_DATA_DIR, clean=True)` to mint
the panel into a temp dir. **Do not change this to copy `data/`** — Epic-style
ITI-105 submissions accumulate in the live dir and poison "count synthetic
patients" assertions.

### Audit log

`StructuredLogMiddleware` in `app/security.py` writes one JSON line per
request to both the python logger AND `data/audit/audit-YYYY-MM-DD.jsonl`.
Secrets are scrubbed; the JWT-claimed `client_id` is parsed (without
signature verification) so 401s still show who claimed to call.

`/ui/api/audit` + `/ui/api/audit/stats` read the JSONL files;
`/ui/#/logs` is the viewer.

## Things that are tracked vs not

`data/<type>/<uuid>.json` for seeded resources is tracked in git;
**`data/<type>/<non-uuid>.json` (ITI-105 submissions, anything else) is
gitignored**. `.gitignore` enforces this with patterns `data/**/[!0-9a-f]*.json`
and `data/**/?*[!0-9a-f-].json`. Don't `git add data/` blindly.

Also gitignored: `data/clients.json` (runtime registry, seeded from
`config/clients.json` by `deploy/first-deploy.sh`), `data/audit/`,
`data/inbox/`, `data/keys/` (server signing key).

## CI

`.github/workflows/ci.yml` runs ruff lint → `pytest -q` → fetch validator jar
→ `pytest tests/test_profile_validation.py`. Lint failures block; the
profile-validation step needs Java 21. Per-file ruff ignores live in
`pyproject.toml` (E402 / B904 for tests with intentional late imports;
S110/S310/S603/S607/B008 globally where the patterns are vetted).
