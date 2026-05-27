#!/usr/bin/env bash
# shallow-clone the HL7 EU IG repos we depend on into ./ig/.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ig
declare -A REPOS=(
    [eu-core]="https://github.com/hl7-eu/base-multi-versions.git"
    [eps]="https://github.com/hl7-eu/eps.git"
    [laboratory]="https://github.com/hl7-eu/laboratory.git"
    [hdr]="https://github.com/hl7-eu/hdr.git"
    [imaging-r4]="https://github.com/hl7-eu/imaging-r4.git"
    [eu-health-data-api]="https://github.com/euridice-org/eu-health-data-api.git"
)
for name in "${!REPOS[@]}"; do
    url="${REPOS[$name]}"
    target="ig/$name"
    if [[ -d "$target/.git" ]]; then
        echo "==> $name: already cloned, pulling"
        git -C "$target" pull --ff-only || true
    else
        echo "==> $name: cloning $url"
        git clone --depth 1 "$url" "$target"
    fi
done
echo "done."
