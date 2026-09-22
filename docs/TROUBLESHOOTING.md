# Troubleshooting

Keyed by **symptom**, not by cause. Each entry links to the actual file
or skill that fixes it. Add new entries as you encounter them — this is
a running list.

## "Validator silently passes everything"

**Symptom**: structurally invalid bundles get 201 from `POST /`; the
audit log shows `validator-unavailable: …` warnings instead of issue
lists; tests in `tests/test_profile_validation.py` pass when they should
fail.

**Root cause**: `fhir.resources >= 8.0` removed
`construct_fhir_element` *and* defaults to **R5** models at the
top-level import. Our server advertises `fhirVersion 4.0.1`, so R5
models reject valid R4 bundles (e.g. `Composition.subject` is `0..*` in
R5, `0..1` in R4).

**Fix**: `app/fhir/validate.py` imports `get_fhir_model_class` from
`fhir.resources.R4B`, not the top level. See the "FHIR-R4 model
binding" section of `CLAUDE.md`. If you bump `fhir.resources` to a new
major, re-verify this import path.

**Verification**:
```bash
cd /srv/ehds-api && source .venv/bin/activate
python -c 'from app.fhir.validate import structural_validate; \
  print(structural_validate({"resourceType":"Composition"}))'
# expect: (False, ["1 validation error for Composition..."]) — NOT (True, ["validator-unavailable..."])
```

## "All my POSTs 201 but DR search returns nothing relevant"

**Symptom**: client submits a stream of bundles, gets 201s for each,
but later `GET /DocumentReference?subject=Patient/{id}` returns only
the pre-seeded DRs (or nothing).

**Root cause**: the bundles don't contain `DocumentReference`
entries. Our permissive submit handler (`app/routers/docsubmit.py`)
mirrors anything in `store.SUPPORTED_TYPES`, but doesn't *synthesize* a
DR from a leading Composition. The Epic-side bridge as of 2026-05-28
ships bare `Bundle.type=transaction` with Composition as `entry[0]` and
no DR wrapper.

**Fix**: this is a sender-side bug (the canonical ITI-105 shape is
DocumentReference + Binary + Provide Bundle). On our side, document the
deviation (already in `docs/conformance-deviations.md`); optionally
add a server-side "synthesize DR from leading Composition" step in
`docsubmit.py`.

## "Token mints but every API call 401s with scope error"

**Symptom**: `POST /token` returns 200 with an access_token, but every
subsequent FHIR call returns 401 with
`error_description: scope X required, you have Y`.

**Root cause**: the scope you requested at `/token` got narrowed down to
what's in the registered scope set (`_ALLOWED_REG_SCOPES` in
`app/auth/smart.py`). The token has the intersection, which may be
empty or too narrow.

**Fix**: PATCH the registration with a broader scope set:
```bash
curl -fsS -X PATCH https://ehds.joshpriebe.com/register-client/<id> \
  -H 'content-type: application/json' \
  -d '{"scopes": ["system/*.read", "system/Bundle.write"]}'
```
Then re-mint. See `/ehds-explain-401` skill for the full decision tree.

## `gunicorn 203/EXEC` on `systemctl restart ehds-api`

**Symptom**: `journalctl -u ehds-api` shows `(code=exited, status=203/EXEC)`
or `Permission denied: '/home/deploy'`.

**Root causes (two)**:
1. Venv shebangs point at a path that no longer exists (e.g. after
   renaming `/srv/ehds-api-demo` → `/srv/ehds-api`). The venv was
   created with absolute interpreter paths baked in.
2. `ProtectHome=true` in the systemd unit denies access to the default
   `HOME=/home/deploy`, which gunicorn touches for `.gnupg`/etc.

**Fix (1)**: rebuild the venv in place:
```bash
cd /srv/ehds-api
rm -rf .venv
python3.12 -m venv .venv
.venv/bin/pip install -e .
```

**Fix (2)**: confirm `Environment=HOME=/tmp` is present in
`deploy/ehds-api.service`:
```bash
grep '^Environment=HOME' /etc/systemd/system/ehds-api.service
```
If missing, add it, `daemon-reload`, restart.

## "Tests count wrong number of Patients"

**Symptom**: a test that asserts `len(patients) == 10` (or similar)
fails with 11 or more — usually after Epic has been submitting against
the live box.

**Root cause**: someone changed `tests/conftest.py` to copy `data/`
instead of seeding from `scripts.seed`. The live `data/` accumulates
ITI-105 submissions over time.

**Fix**: revert to `_seed(_DATA_DIR, clean=True)` at conftest top.
There is a comment explicitly forbidding the copy-data variant — see
the "Tests reseed; never copy live data/" section in CLAUDE.md.

## "I get 404 for `/Bundle/{id}` but the DocumentReference advertises that URL"

**Symptom**: `DocumentReference.content[0].attachment.url` points to
`/Bundle/<uuid>`, but fetching that URL returns 404.

**Root cause**: the bundle id derivation drifted. The reverse index in
`app/routers/bundle.py` (`_build_reverse_index`) builds keys from
`bundle_id(slot, category)`, where `slot` comes from
`Patient.identifier` (system `urn:ehds-demo:slot`). If `compile_document`
falls back to `Patient.id` (when the slot identifier is missing or
typo'd), the bundle id will not match the docref URL.

**Fix**: ensure every seeded Patient has an `identifier` entry with
`system: urn:ehds-demo:slot` and `use: secondary`. See
`scripts/seed.py`. See `docs/identity-cheatsheet.md` for the full
derivation chain.

## "Cron / scheduler / qrcode dep missing" on first deploy

**Symptom**: `import qrcode` or similar fails during gunicorn startup.

**Root cause**: a new dep was added to `pyproject.toml` but
`pip install -e .` wasn't re-run after the pull.

**Fix**: `deploy/post-deploy.sh` (or equivalent) should `pip install -e .`
before `systemctl restart ehds-api`. Confirm the unit's
`ExecStartPre=/bin/bash -c '.venv/bin/pip install -q -e .'` is in place,
or do it manually after each pull.

## "DNS works but Caddy returns 502"

**Symptom**: `curl https://ehds.joshpriebe.com` returns
`502 Bad Gateway`; `systemctl status ehds-api` shows it's running.

**Root cause**: most often the Caddy reverse proxy expects port 8000 on
127.0.0.1, but gunicorn is bound elsewhere (or vice versa).

**Fix**: confirm `ss -tlnp | grep 8000` shows gunicorn on `127.0.0.1:8000`,
and `/etc/caddy/Caddyfile` has `reverse_proxy 127.0.0.1:8000`. Caddy
reload: `sudo systemctl reload caddy`.

## "GET /Patient/… returns 401 without a bearer, it used to work"

**Symptom**: a QR code, a bookmark or a script that read FHIR resources with no
`Authorization` header now gets `401 missing bearer token`.

**Root cause**: the dev-mode anonymous-read shortcut was removed (see
`docs/conformance-deviations.md` §2). The FHIR surface is authorised in every
environment.

**Fix**: mint a token (register a client at `/register-client`, sign an
assertion, `POST /token`; the UI's Connect page does it in the browser), or
open the UI route instead: `/ui/#/patients/{id}` and `/ui/#/documents/{bundleId}`
render the same content with the page's read-only viewer token.

## Every submission's badge says "Validator unavailable"

**Symptom**: the Coverage page shows `unavailable` for every submission;
`/ui/api/build-info` reports `validator.available: false`.

**Root cause**: `app/fhir/validation_queue.py` needs both the java validator
jar (`EHDS_VALIDATOR_JAR`) and a folder of HL7 Europe IG packages
(`EHDS_EU_PACKAGES_DIR`, default `.cache/eu-packages/*.tgz`). Either is missing,
or java is not installed, or the first run timed out while downloading the
validator's own dependencies into `EHDS_VALIDATOR_HOME`.

If `build-info.validator.reason` is null but every record ends `unavailable`
with `Error occurred during initialization of VM … Failed to mark memory page
as executable`, the systemd unit has `MemoryDenyWriteExecute=true`; the JVM's
JIT needs W+X pages. `deploy/ehds-api.service` sets it to `false` for this
reason; reinstall the unit and `systemctl daemon-reload`.

A different symptom, every reference document `failed` with "Element matches
more than one slice - observation-pregnancy-edd, …": the validator ran offline
(`EHDS_VALIDATOR_TX=n/a`). Those are terminology artefacts, not bundle bugs
(`docs/epic-eu-bundling.md`); point it at a terminology server and re-queue.

**Fix**: `./fetch_validator.sh`; copy the `hl7.fhir.eu.*.tgz` packages into
`.cache/eu-packages/`; check `build-info.validator.reason`; then re-queue with
`POST /ui/api/submissions/{id}/validate` (or the "Run again" button). The
service runs with `ProtectHome=true`, so the packages must live under
`/srv/ehds-api`, not under `~`.

## Patients or resources show "Origin unknown" in the UI

**Symptom**: cards carry a grey `Origin unknown` badge instead of `Reference`
or `Community`; the coverage map ignores them.

**Root cause**: the resource was written before origin tagging existed
(`app/fhir/origin.py`), so it has no `urn:ehds-demo:origin` tag.

**Fix**: `python -m scripts.seed` (re-stamps the reference panel without
touching `inbox/`) then `python -m scripts.backfill_origin` (tags everything
else as community and links it to the inbox bundle it came from by
recomputing the naturalised id).

## Adding a new entry

When you fix something new, append a section here following the format:
**Symptom**, **Root cause**, **Fix**, optional **Verification**. Keep
each entry self-contained — readers will land here from search, not
from the top of the file.
