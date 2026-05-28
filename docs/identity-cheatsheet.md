# Identity cheatsheet

One page covering: slot ↔ uuid mapping, the deterministic uuid
derivation chain, the 4 patient-reference search forms, and the one
intentional exception to "no hand-rolled ids anywhere."

CLAUDE.md describes the system in prose. This page is the lookup table
you copy from.

## The slot ↔ uuid mapping (worked examples)

`app/fhir/ids.py` defines:

```python
EHDS_NAMESPACE = uuid.UUID("9a3c7c3f-43a1-58a7-89f9-3ea8f1486d6b")
SLOT_IDENTIFIER_SYSTEM = "urn:ehds-demo:slot"
```

| Slot | Patient.id (uuid5) | What's in Patient.identifier |
|---|---|---|
| `p-001` | `uuid5(NS, "Patient/p-001")` | `{system: "urn:ehds-demo:slot", value: "p-001", use: "secondary"}` |
| `p-002` | `uuid5(NS, "Patient/p-002")` | …`value: "p-002"`… |
| … | … | … |
| `p-010` | `uuid5(NS, "Patient/p-010")` | …`value: "p-010"`… |

Get the actual uuid at runtime:

```python
from app.fhir.ids import patient_id
patient_id("p-001")     # → '<deterministic uuid string>'
```

In tests:

```python
def test_x(pid):       # fixture returns patient_id("p-001")
    ...

def test_y(pid_for):   # fixture returns the function itself
    assert pid_for("p-003") == "..."
```

## The full derivation table

| Helper | Input | Output |
|---|---|---|
| `patient_id(slot)` | `"p-001"` | `uuid5(NS, "Patient/p-001")` |
| `practitioner_id(slot)` | `"prac-001"` | `uuid5(NS, "Practitioner/prac-001")` |
| `organization_id(slot)` | `"org-eu"` | `uuid5(NS, "Organization/org-eu")` |
| `child_id(patient_slot, type, idx)` | `("p-001", "Observation", 5)` | `uuid5(NS, "Patient/p-001/Observation/5")` |
| `bundle_id(slot_or_pid, category)` | `("p-001", "patient-summary")` | `uuid5(NS, "Bundle/p-001/patient-summary")` |
| `docref_id(slot_or_pid, category)` | `("p-001", "patient-summary")` | `uuid5(NS, "DocumentReference/p-001/patient-summary")` |
| `composition_id(slot_or_pid, category)` | `("p-001", "patient-summary")` | `uuid5(NS, "Composition/p-001/patient-summary")` |

**Critical**: `bundle_id` / `docref_id` / `composition_id` take a
**slot**, not a uuid. `compile_document` reads the slot from
`Patient.identifier` and feeds it in. If a Patient lacks the
slot identifier, the derivation falls back to `Patient.id` and the
resulting bundle uuid will not match what `DocumentReference.content.attachment.url`
points at. Resulting symptom: 404 on `GET /Bundle/{id}`. See
`docs/TROUBLESHOOTING.md`.

## The one hand-rolled id

`p-001` … `p-010` survive as **`Patient.identifier.value`** only. They
do *not* appear as `Patient.id`, `Bundle.id`, child resource ids,
or anywhere else. They exist purely so humans can refer to a
demographically-named patient ("Anna Müller is p-001") without
memorising a uuid.

System: `urn:ehds-demo:slot` · Use: `secondary`.

## The 4 patient-reference search forms

All four resolve through `store.resolve_patient_ref()` +
`store.find_patient_ids_by_identifier()`. All four hit the same Patient
set.

| Form | Example | Origin |
|---|---|---|
| Direct uuid | `?patient=<uuid>` | FHIR canonical |
| Token shorthand | `?patient=urn:ehds-demo:slot\|p-001` | HAPI-influenced |
| Chained search | `?patient.identifier=urn:ehds-demo:slot\|p-001` | MHD ITI-67 canonical |
| `:identifier` modifier | `?patient:identifier=urn:ehds-demo:slot\|p-001` | FHIR `:identifier` modifier |

The MHD ITI-67 form is what Epic and other strict MHD clients will use.
The others exist so casual / HAPI-influenced clients also work.

**Implementation note**: `DocumentReference` has its own filter in
`app/routers/docref.py` that duplicates the resolution logic in
`store.search`. **Keep them in sync if you touch either.**

## The 4 places these helpers get called

1. **`scripts/seed.py`** — every seed resource id is derived via these
   helpers. The slot label is preserved as `Patient.identifier`.
2. **`app/fhir/document.py`** — `compile_document` calls
   `bundle_id(slot, category)` and `composition_id(slot, category)` so
   the compiled bundle's id matches what `DocumentReference.content.attachment.url`
   points at.
3. **`app/routers/bundle.py`** — `_build_reverse_index()` iterates the
   panel and category list to populate the bundle-id → (slot, category)
   reverse index served by `GET /Bundle/{id}`.
4. **`app/auth/smart.py`** — `/.well-known/smart-configuration`
   `example_endpoints` uses `patient_id("p-001")` at request time so
   discovery clients get a real, working uuid to try.

## Common pitfalls

- **"I'll just hardcode `p-001` in this URL"** — no. Use
  `patient_id("p-001")` so the URL stays correct if `EHDS_NAMESPACE` is
  ever rotated (it shouldn't be, but the deterministic property is the
  whole point).
- **"Bundle.id should be the patient uuid"** — no. It's
  `uuid5(NS, "Bundle/<slot>/<category>")`. Bundles are per-slot-per-category.
- **"Why does the docref URL not contain the patient uuid?"** — because
  it's a bundle uuid, not a patient uuid. Different namespace path.
  See the derivation table.
- **"I added a new resource type, why are its ids not deterministic?"**
  — `child_id(patient_slot, "MyType", idx)` is the helper. If you're
  not calling it, the resource will get a random uuid and break
  determinism across reseeds.

## Skills that exercise this

- `/ehds-deploy-and-verify` — smoke-tests `bundle_id` round-trip
- `/ehds-trace-submission` — checks dangling refs in submitted bundles
- `/ehds-reseed-safe` — verifies panel still has 10 slot-identified
  Patients after seed
