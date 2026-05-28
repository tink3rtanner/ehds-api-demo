"""GET /Binary/{id} — legacy alias.

Compiled FHIR documents now live at /Bundle/{uuid}. This router preserves
backward compatibility for two URL shapes:

  - /Binary/{bundle-uuid}  -> 301 to /Bundle/{bundle-uuid}
  - /Binary/doc-{patient}-{category}  (the old hand-rolled id scheme)
        -> resolved to the deterministic uuid and 301 to /Bundle/{uuid}

Real FHIR Binary resources (PDFs, opaque blobs) are not served by this
demo — Binary stays as a routing-only shim.
"""
from __future__ import annotations

import re

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.fhir import document as doc_compile
from app.fhir.ids import bundle_id

router = APIRouter()

# build regex once from the source-of-truth category list
_LEGACY_DOC_ID_RE = re.compile(
    r"^doc-(?P<patient>[A-Za-z0-9._]+(?:-[A-Za-z0-9._]+)*?)"
    r"-(?P<category>" + "|".join(re.escape(k) for k in doc_compile.CATEGORY_TO_DOC_TYPE.keys()) + ")$"
)


@router.get("/Binary/{rid}", name="read_Binary_legacy")
async def read_binary(rid: str):
    m = _LEGACY_DOC_ID_RE.match(rid)
    target_id = bundle_id(m.group("patient"), m.group("category")) if m else rid
    return RedirectResponse(url=f"/Bundle/{target_id}", status_code=301)
