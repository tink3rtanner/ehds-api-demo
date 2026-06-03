# DocumentReference search via the reference graph (MHD ITI-67)

How this server implements the MHD ITI-67 *Find Document References* search
parameters — specifically the ones that became **SHALL** in the EU Health Data
API IG (euridice-org PR #87, "ITI-67 / MHD alignment").

The short version: **the DocumentReference is a thin spine; the clinical context
that the XDS-era search params describe lives on the real Encounter /
Practitioner resources, and we resolve those params by *chaining* through the
document's reference graph at query time — we do not denormalise the values onto
the DocumentReference.**

## Why chaining instead of denormalising

MHD inherits a set of search params from XDS document metadata —
`setting` (practice setting / clinical specialty), `facility` (facility type),
`event` (clinical acts), `author.*` (who wrote it). In a classic XDS registry
those are flat metadata slots on the document entry, copied off the source
document at registration time.

That copy is the problem: it drifts. The `practiceSetting` on the
DocumentReference and the `class` on the Encounter the document actually
describes can disagree, and nothing reconciles them.

This server already breaks every submitted/compiled document **Bundle** open
into its constituent FHIR resources (see *Break open the bundle*, below), so the
Encounter, Practitioner, Organization, etc. are first-class, individually
addressable resources in the store. Given that, the honest implementation of
`setting`/`facility`/`event`/`author.*` is to **read them live off those
resources** when a query comes in. The metadata can't drift because there's only
one copy.

## Break open the bundle (ingest)

Two ingest boundaries decompose a document Bundle into addressable resources:

- **ITI-105 submit** (`app/routers/docsubmit.py`) — every entry whose
  `resourceType` is in `store.SUPPORTED_TYPES` (which includes `Encounter`,
  `Practitioner`, `PractitionerRole`, `Organization`) is naturalized and
  mirrored into the type-folder store.
- **Epic `/Epic/$import`** (`app/sources/epic_transform.py`) — same idea on the
  pull side.

Naturalization (`app/fhir/naturalize.py`) is what makes the chain resolvable
afterwards: every entry's id is re-minted to a local `uuid5`, **and every
internal `reference` is rewritten to point at the new local id** (`_rewrite_refs`
walks the whole resource recursively, so `DocumentReference.author`,
`DocumentReference.context.encounter`, `Composition.author`, `Encounter.*` are
all rewritten, not just `subject`). The original id is preserved as a
`urn:ehds-demo:source-id` identifier and `meta.source` records the origin
back-link (resolvable via `GET /{Type}/{id}/$source`). See
`docs/resource-identity.md`.

So after ingest the graph is intact and local: `DocumentReference.author ->
Practitioner/<local-uuid>`, `DocumentReference.context.encounter ->
Encounter/<local-uuid>`.

## How a chained query resolves (mechanics)

A param like `author.family=McGinnis` is parsed into
`(reference-element = author, terminal-param = family)`. The engine, for each
candidate DocumentReference:

1. reads the reference element (`DocumentReference.author[*].reference`),
2. loads each referenced resource from the store
   (`store.resolve_reference("Practitioner/<id>")`),
3. evaluates the **terminal** search param (`family`) against the loaded target
   (`Practitioner.name.family`, case-insensitive starts-with),
4. the DocumentReference matches if **any** referenced target matches.

This is the exact machinery that already powered `?patient.identifier=` (which
chains `DocumentReference.subject -> Patient.identifier`); PR #87's params are
just more chains, into `Encounter` and `Practitioner`. The reusable pieces live
in `app/fhir/store.py` (`resolve_reference`, `token_in`, `match_date`) and the
DocumentReference-specific wiring is in `app/routers/docref.py`.

## Parameter → resolution map

| ITI-67 param | Kind | Resolved against |
|---|---|---|
| `patient`, `patient.identifier` | reference / chained | `DocumentReference.subject -> Patient` (4 forms — see `docs/identity-cheatsheet.md`) |
| `_id`, `identifier`, `status`, `category`, `type` | local token | the DocumentReference itself |
| `date` | local date | `DocumentReference.date` (metadata indexing time) |
| `creation` | local date | `DocumentReference.content.attachment.creation` (clinical document creation time — **distinct** from `date`, per FHIR-56851) |
| `_lastupdated` | local date | `DocumentReference.meta.lastUpdated` |
| `format` | local token | `DocumentReference.content.format` |
| `security-label` | local token | `DocumentReference.securityLabel` |
| `related` | local reference | `DocumentReference.context.related` |
| `period` | chained date | `DocumentReference.context.encounter -> Encounter.period` |
| `setting` | **chained** token | `DocumentReference.context.encounter -> Encounter.serviceType` (practice setting / clinical specialty) |
| `facility` | **chained** token | `DocumentReference.context.encounter -> Encounter.class` (care-setting type, e.g. IMP / AMB) |
| `event` | **chained** token | `DocumentReference.context.encounter -> Encounter.type` (clinical act) |
| `author.given`, `author.family` | **chained** string | `DocumentReference.author -> Practitioner.name` |

### Note on the `setting`/`facility` mapping

XDS `practiceSetting` and `facilityType` have no single canonical FHIR R4 home.
We map them onto the most defensible Encounter elements: `serviceType` (the
clinical specialty ≈ practice setting) and `class` (inpatient/ambulatory ≈
facility/care-setting type). A fuller deployment could add a second hop —
`Encounter.location -> Location.type` for `facility`, or
`Encounter.serviceProvider -> Organization.type` — using the same
`resolve_reference` building block; `Location` is not a stored type on this demo
yet, so the single hop to `Encounter` is what ships. This mapping is a documented
local decision, not an IG requirement.

## Date matching

`store.match_date` does FHIR prefix-aware comparison (`eq`/`ne`/`gt`/`lt`/`ge`/
`le`/`sa`/`eb`/`ap`) by **lexical** ISO-8601 comparison, which is order-correct
for the consistently-formatted timestamps this server emits. `eq`/`ne` compare
on the shared prefix so a day-granularity query (`2024-04-15`) matches a stored
`dateTime` that starts with it. `period` matches when the query hits either bound
of the Encounter window.

## Tested by

`tests/test_iti67_mhd_search.py` — every SHALL param positively and negatively,
the chains through Encounter/Practitioner, the CapabilityStatement SHALL
declarations, and an end-to-end ITI-105 round-trip that proves a chained search
resolves through the *rewritten* reference graph after naturalization.
