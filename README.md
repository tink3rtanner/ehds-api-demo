# ehds-api

A minimum-viable, open-source FHIR R4 resource server implementing the
[EU Health Data API (EHDS) IG](https://build.fhir.org/ig/euridice-org/eu-health-data-api/en/)
together with the HL7 EU Patient Summary, Laboratory, Hospital Discharge Report
and Imaging IGs.

Synthetic data only. Single-binary deployment, file-backed storage, SMART
Backend Services auth. Documents are compiled on demand from atomic FHIR
resources.

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
curl http://localhost:8000/metadata | jq .resourceType                # CapabilityStatement
curl http://localhost:8000/.well-known/smart-configuration | jq .     # SMART config
```

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

See [`deploy/README.md`](deploy/README.md) for the VPS bring-up runbook.

## License

Apache-2.0. See [LICENSE](LICENSE).
