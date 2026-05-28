---
description: Scaffold a new on-demand FHIR document category end-to-end (compile_document, seed DRs, smart-config example, UI).
allowed-tools: Bash, Read, Edit, Grep
---

# /ehds-add-document-category

Add a 6th (or Nth) on-demand `Bundle.type=document` category. Walks
every file that has to change so nothing drifts.

## When NOT to use

If the thing you want to add isn't actually a static narrative
**document**, do not add it here. eRx, scheduling, claims, etc. are
**resource flows** — they live as first-class FHIR resources accessible
via `GET /{ResourceType}?patient=…`, no `compile_document` involved.

See `docs/conformance-deviations.md` ("prescription is a resource flow,
not a document") for why we removed prescription from this list.

## Usage

```
/ehds-add-document-category <slug> <loinc-code> <display>
# e.g.
/ehds-add-document-category vaccination-record 11369-6 "History of immunization"
```

## Files to touch (in this order)

1. **`app/fhir/document.py`** — extend `compile_document` with a
   `_compile_<slug>(patient, base)` branch:
   - pick relevant resources from the store
     (`store.search("Resource", patient=patient_id)`)
   - synthesize a Composition with the given LOINC code as
     `Composition.type.coding`
   - append the section + entries
   - return the assembled Bundle

2. **`app/fhir/ids.py`** — confirm the new slug works with `bundle_id`,
   `docref_id`, `composition_id`. These accept any slug string; no
   change needed unless you want a typed enum.

3. **`scripts/seed.py`** — per-patient, add a `DocumentReference`
   pointing at `Bundle/{bundle_id(slot, slug)}` and a stub Composition
   under `data/Composition/` if you want the doc to exist as a static
   resource too (compile-on-demand bundles don't strictly need this).
   Re-run `python -m scripts.seed --clean` to mint the panel.

4. **`app/auth/smart.py`** — add the new doc to `example_endpoints` in
   `/.well-known/smart-configuration` so discovery-driven agents see it.

5. **`app/main.py`** (CapabilityStatement assembly) — ensure the new
   category surfaces under `rest.resource[DocumentReference].searchParam`
   or the `implementation.description` notes.

6. **UI**:
   - `static/main.js` — add to the category list in the Documents page
     renderer and to the QR-codes page
   - `static/index.html` — if there's a hardcoded category dropdown

7. **Tests**:
   - `tests/test_document.py` — add a positive-path test
     `test_compile_<slug>` that round-trips compile → fetch → validate
   - `tests/test_profile_validation.py` will pick up the new category
     automatically if the document fixture generator iterates the
     category list

8. **Docs**:
   - `README.md` IG-actor matrix — bump category count if listed
   - `CLAUDE.md` — update the leading paragraph's "Five EHDS
     priority-category documents" count

## Verify

```bash
pytest -q tests/test_document.py -k <slug>
/ehds-deploy-and-verify
curl -fsS https://ehds.joshpriebe.com/.well-known/smart-configuration \
  | jq '.example_endpoints[] | select(.label | contains("<slug>"))'
```

## Notes
- The `bundle_id(slot, category)` reverse index in
  `app/routers/bundle.py` (`_build_reverse_index`) iterates the
  category list at module load — no change needed there as long as
  `compile_document` accepts the new slug.
- Keep the category slug stable forever — it's part of the deterministic
  uuid namespace via `bundle_id`. Renaming changes the uuid.
