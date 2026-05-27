#!/usr/bin/env bash
# shallow-clone the HL7 EU IG repos we depend on into ./ig/. portable bash.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ig

clone_one() {
    local name="$1" url="$2" target="ig/$1"
    if [ -d "$target/.git" ]; then
        echo "==> $name: already cloned, pulling"
        git -C "$target" pull --ff-only || true
    else
        echo "==> $name: cloning $url"
        git clone --depth 1 "$url" "$target"
    fi
}

clone_one eu-core            https://github.com/hl7-eu/base-multi-versions.git
clone_one eps                https://github.com/hl7-eu/eps.git
clone_one laboratory         https://github.com/hl7-eu/laboratory.git
clone_one hdr                https://github.com/hl7-eu/hdr.git
clone_one imaging-r4         https://github.com/hl7-eu/imaging-r4.git
clone_one eu-health-data-api https://github.com/euridice-org/eu-health-data-api.git

echo "done."
