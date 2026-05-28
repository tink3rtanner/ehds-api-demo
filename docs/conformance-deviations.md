# Conformance deviations

Single page listing **intentional** deviations from strict FHIR R4 / EHDS
IG / IHE MHD behaviour. Each entry exists because the demo prioritises
low-friction synthetic-data flows over strict spec compliance. Anything
not listed here is a bug — file it as one.

Skim before you "fix" any of these.

## 1. Submit handler ignores `Bundle.entry.request.method/url`

`app/routers/docsubmit.py` accepts `Bundle.type=transaction` or
`document`, runs structural validation, persists the whole bundle to
`data/inbox/`, and then mirrors **every entry whose `resourceType` is in
`store.SUPPORTED_TYPES`** into the type-folder store.

**Spec says**: a transaction Bundle uses `entry.request.method` (POST /
PUT / DELETE / GET) and `entry.request.url` to specify how each entry
should be processed; PUT honours the explicit id, POST mints one,
conditional updates use search params.

**We do**: write `resource.id` to `data/<resourceType>/<id>.json`
unconditionally. We do not differentiate POST vs PUT; we do not honour
conditional create. This is documented in
`CapabilityStatement.implementation.description`.

**Why**: connectathon submitters ship bundles with inconsistent or
absent `request` blocks, and friction here would block every demo. The
mirror is "best effort, side-effect-free for the sender."

## 2. Dev-mode anonymous read

In `ENV=dev`, **GET** requests with **no `Authorization` header at all**
get a `dev-anon` principal with `system/*.read` scope (see
`app/auth/verify.py`).

**Spec says**: SMART Backend Services requires a bearer always.

**We do**: shortcut for QR-code-on-phone demos so a fresh browser can
resolve a Bundle URL without first running a `/token` flow. Sending
*any* `Authorization` header — even `Bearer junk` — disables this and
goes through strict checks.

**Why**: phone browsers can't run the JWT client_assertion handshake.
The shortcut is gated to GET only and to `ENV=dev`.

`ENV=prod` enforces bearer always.

## 3. The "5th priority category" prescription was a modeling bug

We initially compiled `Bundle.type=document` with
`Composition.type = 57833-6` (Prescription for medication) as a 5th
EHDS priority category alongside patient-summary, lab, discharge, and
imaging.

**Spec says**: eRx / prescription is a **transactional resource flow**,
not a narrative document. The relevant resources are
`MedicationRequest`, `MedicationDispense`, optionally
`MedicationAdministration` — accessed via `GET /MedicationRequest?patient=…`
(IPA-style), not as a compiled document Bundle.

**We do**: still expose `MedicationRequest` as a first-class searchable
resource via the generic store. The `compile_document(category="prescription")`
path is preserved (don't break URLs in existing QR codes / docrefs) but
should be deprecated and eventually removed.

**Why this entry exists**: so the next agent doesn't "add prescription
back to the IG-actor matrix" thinking it was missing. It's correctly
absent as a document; present as a resource.

## 4. Compiled-bundle `fullUrl` is absolute, not `urn:uuid:`

`compile_document` in `app/fhir/document.py` builds entries with
`fullUrl: {base}/{ResourceType}/{id}` (absolute URLs), not
`urn:uuid:<uuid>` placeholders.

**Spec says**: documents and transactions may use either. `urn:uuid:` is
common for self-contained Provide Bundles where ids are randomly
generated per submission.

**We do**: absolute URLs because every resource has a deterministic
uuid and a stable server URL — the document is meant to be navigable
back to the live REST view of each resource.

**Implication**: a strict reader expecting `urn:uuid:` style won't be
broken (it's a valid alternative), but a reader that does string
matching on `urn:uuid:` prefixes will see no matches.

## 5. ITI-67 reference search accepts 4 equivalent forms

Documented in CLAUDE.md ("Patient-reference search accepts 4 equivalent
shapes"). All four return the same Patient set:

```
?patient=<uuid>
?patient=<system>|<value>
?patient.identifier=<system>|<value>
?patient:identifier=<system>|<value>
```

**Spec says**: MHD ITI-67 canonical is the chained form. The shorthand
form is HAPI-influenced; the modifier form is FHIR-canonical.

**We do**: all four. This is a *superset* of spec, not a deviation —
listed here so a reviewer doesn't flag "why does shorthand work, the
spec only requires chained?" as a bug.

## 6. CapabilityStatement gates are coarse-grained

We don't publish per-operation `security.service` constraints. Read
endpoints accept any `system/<type>.read` (or `system/*.read`); ITI-105
needs `system/Bundle.write` or a per-type write scope.

**Spec says**: SMART supports finer-grained scopes including
constraints like `system/Observation.rs?category=laboratory`.

**We do**: ignore the constraint syntax. If you register
`system/Observation.read`, you can read every Observation.

**Why**: synthetic data; no real PHI; demo doesn't gain from
constraint-language correctness.

## 7. `Patient/$summary` is a thin wrapper around `compile_document`

`Patient/{id}/$summary` (IPS operation) returns the patient-summary
compiled bundle directly, instead of building a fresh IPS-profiled
bundle that conforms to the IPS IG's structure.

**Spec says**: IPS bundles use IPS-specific profiles
(`http://hl7.org/fhir/uv/ips/StructureDefinition/Bundle-uv-ips`) with
required and recommended sections per the IPS IG.

**We do**: return the EHDS patient-summary bundle, which is
EHDS-shaped but not IPS-profiled.

**Why**: connectathon scope. Adding strict IPS profile conformance is
tracked separately.

## Adding to this list

If you make a change that intentionally deviates from a referenced
spec (FHIR R4, EHDS IG, MHD ITI-67/68/105, SMART Backend Services, IPS,
PDQm), add a section here describing:
- What we do
- What the spec says
- Why we deviate
- (Optionally) what would have to change to come into compliance
