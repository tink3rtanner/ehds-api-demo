#!/usr/bin/env bash
# fetch the HL7 FHIR validator jar into .cache/. idempotent.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p .cache
JAR=".cache/validator_cli.jar"
URL="https://github.com/hapifhir/org.hl7.fhir.core/releases/latest/download/validator_cli.jar"
if [[ -s "$JAR" ]]; then
    echo "validator already cached at $JAR ($(du -h "$JAR" | cut -f1))"
    exit 0
fi
echo "==> downloading $URL"
curl -fL --retry 3 --retry-delay 2 -o "$JAR.partial" "$URL"
mv "$JAR.partial" "$JAR"
echo "done: $JAR ($(du -h "$JAR" | cut -f1))"
