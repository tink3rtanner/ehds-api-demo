# ehds-api — EU Health Data API reference implementation

An open-source FHIR R4 server implementing the exchange layer of the
[HL7 Europe Health Data API IG](https://build.fhir.org/ig/euridice-org/eu-health-data-api/en/)
together with the HL7 EU Patient Summary, Laboratory, Hospital Discharge Report
and Imaging IGs: SMART Backend Services authorisation, IHE PDQm patient lookup,
IHE MHD document search / retrieve / publish, IPA resource access.

Synthetic data only. Single-process deployment, file-backed storage. Documents
are compiled on demand from atomic FHIR resources.

**Point an agent (or curl) at the base URL.** `GET /` answers with a discovery
document and `/llms.txt` says the same in prose; both carry live example URLs.
A browser at the same URL lands on the human-facing UI (`/ui/`): the exchange
story, a six-step guided scenario that runs a real SMART client in the
browser, the reference patients, the documents with their EU-profile
validation badge, a coverage map of who has submitted example data for which
country, and the request audit log. Design and rationale: [`docs/ui-design.md`](docs/ui-design.md).

## Quick start

```bash
./download_igs.sh        # clones HL7 EU IG repos into ig/
./fetch_validator.sh     # caches the HL7 java validator jar (~70 MB)
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m scripts.seed   # writes 10 patients + dependent resources into data/
./run.sh                 # dev server on :8000  (PORT=8088 ./run.sh for a different port)
pytest -q                # full test suite (~30s; profile-validation layer skipped if no JRE)
```

### Try it

```bash
# register a client and mint a keypair locally
python -m app.tools.register_client --client-id me --generate --scope "system/*.read"

# mint a JWT client assertion and exchange for a bearer
# (or use the e2e snippet in tests/conftest.py as a reference)
curl -H 'Accept: application/json' http://localhost:8000/ | jq .     # discovery document
curl http://localhost:8000/llms.txt                                   # the same, in prose
curl http://localhost:8000/metadata | jq .resourceType                # CapabilityStatement
curl http://localhost:8000/.well-known/smart-configuration | jq .     # SMART config
open http://localhost:8000/ui/                                        # the UI
```

The FHIR surface always needs a bearer. The UI mints itself a read-only one
(`POST /ui/api/viewer-token`); the Connect page and the scenario show the full
register → sign assertion → `/token` flow in the browser.

## What it implements

| IG actor                                | Status |
| --------------------------------------- | ------ |
| EEHRxF Document Access Provider         | yes    |
| EEHRxF Document Access Provider (subm.) | yes — ITI-105 |
| EEHRxF Document Publisher               | yes — internal compilers |
| EEHRxF Grouped Publisher/Access         | yes    |
| EEHRxF Resource Access Provider         | yes — IPA set + Med* |
| EEHRxF Resource Consumer                | tested via mocks |

Priority categories produced as `Bundle.type=document`:

- patient-summary  (HL7 EU Patient Summary / EPS)
- laboratory-report (HL7 EU Laboratory Report)
- discharge-report  (HL7 EU Hospital Discharge Report)
- imaging-report    (HL7 EU Imaging)
- prescription      (base R4 document; the R4 MPD IG has no bundle profile)

Every ITI-105 submission is accepted first and then validated asynchronously
against the HL7 Europe profile for its category with the official java
validator; the result is a badge on the Coverage page, never a reason for
rejection. Submissions count for the country in the patient's address; no
client details are collected or shown.

See [`HANDOFF.md`](HANDOFF.md) for the VPS bring-up runbook and
[`docs/`](docs/) for design notes, troubleshooting and audit recipes.

## License

Apache-2.0. See [LICENSE](LICENSE).
