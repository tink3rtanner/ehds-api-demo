# ehds-api — EU Health Data API reference implementation

An open-source FHIR R4 server implementing the exchange layer of the
[HL7 Europe Health Data API IG](https://build.fhir.org/ig/euridice-org/eu-health-data-api/en/)
together with the HL7 EU Patient Summary, Laboratory, Hospital Discharge Report
and Imaging IGs: SMART Backend Services authorisation, IHE PDQm patient lookup,
IHE MHD document search / retrieve / publish, IPA resource access.

Synthetic data only. Single-process deployment, file-backed storage. Documents
are compiled on demand from atomic FHIR resources.

`GET /` returns a discovery document for agents and `/llms.txt` the same in
prose, both with working example URLs. A browser at the same URL opens the UI
(`/ui/`): a home page that links the IG and `/llms.txt`, the
reference patients, the documents with their EU-profile validation badge, a
map of which countries have example data, a page to register a client and
call the API in the browser, and the request log.
See [`docs/ui-design.md`](docs/ui-design.md).

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

Every FHIR request needs a bearer. The UI uses a read-only one from
`POST /ui/api/viewer-token`; the "Quick start" page registers a
client and mint a token in the browser.

## What it implements

| IG actor                                | Status |
| --------------------------------------- | ------ |
| EEHRxF Document Access Provider         | yes    |
| EEHRxF Document Access Provider (subm.) | yes — ITI-105 |
| EEHRxF Document Publisher               | yes — internal compilers |
| EEHRxF Grouped Publisher/Access         | yes    |
| EEHRxF Resource Access Provider         | yes — IPA set + Med* |
| EEHRxF Resource Consumer                | tested via mocks |

The six EHDS priority categories. Four are `Bundle.type=document`, compiled on
demand for the seeded patients:

- patient-summary   (HL7 EU Patient Summary / EPS, LOINC 60591-5)
- laboratory-report (HL7 EU Laboratory Report 2.1.0, LOINC 11502-2)
- discharge-report  (HL7 EU Hospital Discharge Report 1.0.0, LOINC 34105-7; also found by 18842-5)
- imaging-report    (HL7 EU Imaging, LOINC 18748-4; also found by 85430-7)

Two are resources on the HL7 EU MPD profiles: ePrescription (MedicationRequest)
and eDispensation (MedicationDispense). `python -m scripts.validate_examples`
validates one example of each of the six with the HL7 java validator.

Publish with ITI-105 Simplified Publish: `POST /DocumentReference`, document
inline as base64 (`/llms.txt` has the full recipe; `/quickstart.py` is a read-only
single-file Python client that gets a token and prints it for Bruno/Postman;
`/bruno.zip` is a Bruno collection of every transaction with tests, from
`examples/bruno`). The server accepts every publish, then validates it against the
HL7 Europe profile for its category with the HL7 java validator and shows the
result on the Coverage page. A failed validation does not reject the
submission. A submission counts for the country in the patient's address; no
client details are collected or shown.

See [`HANDOFF.md`](HANDOFF.md) for the VPS bring-up runbook and
[`docs/`](docs/) for design notes, troubleshooting and audit recipes.

## License

Apache-2.0. See [LICENSE](LICENSE).
