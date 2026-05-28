"""CLI: ingest one Epic patient into the local store + compile their IPS bundle.

    python -m scripts.ingest_epic --patient erXuFYUfucBZaryVksYEcMg3
    python -m scripts.ingest_epic --patient erXuFYUfucBZaryVksYEcMg3 --no-bundle
    python -m scripts.ingest_epic --patient erXuFYUfucBZaryVksYEcMg3 --dry-run

Env: EPIC_CLIENT_ID, EPIC_PRIVATE_KEY_PATH, EPIC_KID (see app.sources.epic_client).
"""
from __future__ import annotations

import argparse
import json
import sys

from app.fhir.document import compile_document
from app.sources.epic_client import EpicClient
from app.sources.epic_ingest import ingest_patient


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient", required=True, help="Epic Patient.id")
    ap.add_argument("--dry-run", action="store_true", help="fetch + transform but don't write to store")
    ap.add_argument("--no-bundle", action="store_true", help="skip compiling the patient-summary bundle")
    ap.add_argument("--out", help="write the compiled bundle JSON here (default: stdout)")
    args = ap.parse_args()

    client = EpicClient()
    print(f"epic: token endpoint {client.token_url}", file=sys.stderr)
    print(f"epic: fhir base    {client.fhir_base}", file=sys.stderr)

    summary = ingest_patient(client, args.patient, dry_run=args.dry_run)
    print(f"\ningested Patient {args.patient} -> {summary.patient_id}", file=sys.stderr)
    for k, v in sorted(summary.counts.items()):
        print(f"  {k}: {v}", file=sys.stderr)
    if summary.skipped:
        print(f"  skipped: {len(summary.skipped)}", file=sys.stderr)

    if args.no_bundle or args.dry_run:
        return 0

    bundle = compile_document(summary.patient_id, "patient-summary")
    out_json = json.dumps(bundle, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(out_json)
        print(f"\nbundle written -> {args.out}", file=sys.stderr)
    else:
        print(out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
