# Resource identity, references & provenance

**Status:** **Tasks A + C, provenance back-link, and recipe step-5 IMPLEMENTED
2026-05-31.** The ITI-105 submit path naturalizes (local `uuid5` ids, rewritten
refs, origin `urn:ehds-demo:source-id` identifier, unresolved `urn:uuid:` refs
demoted to logical), both ingest paths stamp `meta.source`, `GET
/{Type}/{id}/$source` resolves the back-link, and searchsets carry a `self`
link. See `app/fhir/naturalize.py`, `app/routers/source_link.py`,
`tests/test_naturalize.py`, `tests/test_conformance_extras.py`. **Still open:**
a full `Provenance` resource in compiled documents (Task B) — deliberately not
added, because the EU document-Bundle profiles use closed entry slices and an
extra `Provenance` entry would break the *passing* EPS validation; the
`meta.source`/`$source` mechanism already covers source traceability for
ingested data (compiled docs are built from local synthetic seed with no
upstream source). See the [Implementation plan](#implementation-plan-for-an-implementer-agent).

This doc explains how FHIR resource identity is *supposed* to work when a
resource crosses a server boundary, how this server currently handles it, where
the two paths diverge, and exactly what to change. It exists because the server
plays three different FHIR roles (live client, persistent re-host, document
author) and only one of those paths handles identity coherently today.

Companion docs: [`conformance-deviations.md`](conformance-deviations.md) (known
intentional deviations — the permissive-submit note there overlaps with Gap 1
below), [`identity-cheatsheet.md`](identity-cheatsheet.md) (slot↔uuid mechanics),
[`epic-eu-bundling.md`](epic-eu-bundling.md) (the Epic→EU pipeline that already
does most of this right).

---

## 1. The FHIR identity model (the conventions)

Source of truth: HL7 FHIR R5
[Managing Resource Identity](https://www.hl7.org/fhir/R5/managing.html),
[References between Resources](https://www.hl7.org/fhir/references.html),
[Bundle](https://www.hl7.org/fhir/bundle.html),
[Documents](https://www.hl7.org/fhir/documents.html).

### Logical id vs business identifier — the central distinction

| | `Resource.id` (logical id) | `Resource.identifier` (business identifier) |
|---|---|---|
| Scope | **Server-local.** Means nothing off this server. | **Global.** `system\|value`, meaningful everywhere. |
| Assigned by | The server that stores the resource. | The business domain (MRN issuer, Epic, NHS, placer). |
| On copy to a new server | **Changes** — new server assigns a new id. | **Stays the same** — travels with the resource. |
| Mental model | Primary key in *this* database. | Who the resource *is*, across the whole ecosystem. |

One-line rule: **`id` is *where* the resource is; `identifier` is *who* it is.**
`Patient/7013306` on Epic and `Patient/7013306` here are unrelated resources
that happen to share a string. Persistent cross-server identity lives in
`identifier`, never in `id`. (managing.html §"Resource Identity".)

### Literal vs logical references

A `Reference` points one of two ways
([references.html](https://www.hl7.org/fhir/references.html)):

- **Literal** (`reference.reference`): a URL — `"Patient/abc"` or
  `"https://server/fhir/Patient/abc"`. Promise: *dereference this against the
  hosting server and you get the resource.* Only valid **relative to the server
  that serves the resource containing it.**
- **Logical** (`reference.identifier`): "the Patient *whose identifier is*
  `nhs|123`" — no URL, no commitment to where it lives. Used when you can name
  something but can't (or shouldn't) host/resolve it.

**Consequence that drives everything below:** when you copy a resource onto your
server, its literal references must be **rewritten** to resolve on *your* server,
or they dangle.

### Bundle `fullUrl` and in-bundle resolution

`Bundle.entry.fullUrl` is the absolute identity of each entry *as the bundle
author asserts it*. Inside a bundle, a reference resolves by matching entry
`fullUrl`s first — so `urn:uuid:` bundles resolve with **no server involved**.
R4/R5 rule this server leans on (`app/fhir/document.py:291`): a relative
`Patient/x` reference resolves to the entry whose `fullUrl` *ends in* `/Patient/x`.

### Provenance: `meta.source` and `Provenance`

- `meta.source` — a URI: "where *this copy* came from." Metadata about the copy,
  distinct from `identifier` (about the subject).
- `Provenance` resource / `Bundle.entry.link[relation=via]` — the auditable
  **chain of custody**: which hands the record passed through. The Intermediaries
  White Paper (below) recommends `link[via]` specifically for surfacing a
  record's source to a client.

---

## 2. Three server roles, and the document tension

"Whose URL does a reference carry?" depends on which role the server is playing.

1. **Live proxy / facade.** Holds no copy; forwards to the source in real time.
   References resolve because the truth is re-fetched every call (always
   current). Usually still re-ids into your URL space so the client dereferences
   *through you*. Cost: source must be **up + authed** for every navigation.
2. **Persistent re-hosted copy.** GET from source once, then *store* it. You
   become server-of-record for the copy. Clients dereference against **you**.
   *This is `app/fhir/store.py`.*
3. **Frozen document.** A `Bundle.type=document` — self-contained, point-in-time,
   like a signed PDF. References resolve *inside the bundle*, against nothing
   live. *This is `compile_document`.*

### Why a persistent copy breaks "resolve back to source" — and a proxy doesn't

A proxy stays resolvable because it has nothing of its own to disagree with. A
**copy** breaks it for two independent reasons:

- **Drift.** The instant you copy, your copy and the source's live resource are
  two things that merely *agree right now*. The source will update/merge/delete.
  A literal reference from your copy to the source's live URL is therefore a
  **lie about your own content**: "follow this to see this resource" → the link
  now shows something *different* from what you hold.
- **Reachability.** A copy travels. A document on its third hand sits with a
  clinician in another country who has **no network path and no credentials** to
  the origin (e.g. Epic's sandbox). Even a never-stale absolute URL is
  *un-clickable* downstream.

### The tension to internalize

A FHIR **document is valuable precisely because downstream hands *cannot* reach
the source.** If every recipient could auth into the origin and dereference
live, you'd skip the document and hand them the endpoint. So "self-contained
artifact that survives many hands" and "live clickable links back to origin" are
in **direct tension**. FHIR resolves it by splitting "resolve" into two jobs with
two mechanisms:

1. **Rendering resolution — offline, forever.** Intra-document references
   resolve against the bundle's own `fullUrl`s. This is the link you click to
   *navigate the document*; it works in 20 years when the source is gone.
2. **Provenance / lineage — auditable, not necessarily live.** `meta.source` +
   `identifier` + `Provenance`/`link[via]` record where it came from and through
   whom. "Going back to the source" means **following this trail** (and, if you
   happen to be authed, re-querying *by identifier*) — **not** blindly
   dereferencing a stale absolute URL. The trail is honest about drift; the blind
   deref pretends a frozen copy is a live window.

---

## 3. The "naturalization" recipe (how it *should* work, uniformly)

Governing principle: **the moment a resource crosses the boundary into the
store, the server becomes its server-of-record, so it must wear *our* identity
and carry *its origin's* identity separately.** Every inbound resource — from
*any* path — should pass through one recipe:

| Step | Action | Field |
|---|---|---|
| 1. Mint a local id | Assign an id in our namespace, **deterministically** (uuid5) so re-ingest is idempotent | `Resource.id ← uuid5(...)` |
| 2. Preserve origin identity | Keep the source's logical identity so the thread is never lost | `Resource.identifier += {system, value}` |
| 3. Rewrite references | Repoint every literal reference at the new local ids (scan + replace) | coherent rewrite pass |
| 4. Stamp provenance | Record where this copy came from | `meta.source` (+ `Provenance` for documents) |

For **references to things not ingested** (e.g. an Organization the source
referenced but we didn't pull) — the recommended policy for this self-contained
demo is to **demote to a logical reference** (`reference.identifier` = the source
identity) rather than leave a dangling absolute source URL. That keeps every
*literal* link resolvable on-box and degrades unknowns to "named, not hosted"
instead of "broken URL."

---

## 4. Connection to the HL7 Intermediaries White Paper

[*Intermediaries White Paper*](https://confluence.hl7.org/spaces/FHIR/pages/Intermediaries+White+Paper)
(Grahame Grieve / Josh Mandel) describes a facade server fronting multiple source
servers, and four ways to route follow-up queries to the right source:

- **Approach #1 — ask all servers / trust ids as globally unique.** Rejected in
  practice; ids aren't reliably unique. **Our ITI-105 submit path drifts toward
  this** by trusting submitted ids verbatim (Gap 1).
- **Approach #2 — re-identify everything.** Facade mints local ids, maintains a
  map, rewrites all references. Paper calls it *simplest for clients* (truly
  persistent ids). **Our Epic→EU pipeline is a clean Approach #2** (§5).
- **Approach #3 — system prefixes** (1–2 char prefix on source ids).
- **Approach #4 — microservice-per-source + `fullUrl`.**

Relevant conformance points from the paper:

- **CP #1** — clients must process a search *outcome* (the `OperationOutcome`
  entry + `self` link in a searchset). → Gap 3.
- **CP #3** — clients should show a record's source when `Bundle.entry.link[via]`
  is present. → Gap 4 (we emit no `via`/provenance at all).
- **CP #6/#7** — source ids ≤ 58 chars; facades use ≤ 2-char prefixes (only
  relevant if we ever adopt Approach #3 — we don't).

The paper's bottom line matches §2: a facade that doesn't fix up references
leaves broken links, because the reason a facade exists usually *precludes*
downstream from talking to the source directly.

---

## 5. How this server behaves today (audit)

### ✅ Epic→EU pipeline — a correct Approach #2 re-identifier

`app/sources/epic_transform.py`:

- **Step 1** local deterministic id: `local_id()` = `uuid5(ns, "Type/epic/<id>")`
  (`:71`).
- **Step 2** origin identity preserved: `urn:ehds-demo:epic-source-id` identifier
  (`_add_epic_source_identifier`, `:187`); `MedicationStatement.derivedFrom` keeps
  a *logical* ref (`.identifier`, not `.reference`) back to Epic's
  MedicationRequest so it survives rewriting (`:227`).
- **Step 3** references rewritten coherently: `_walk_replace_refs` over a full
  id-map (`:151`, applied `:341`).
- **Step 4** provenance: **MISSING** — no `meta.source`, no `Provenance`.

### ⚠️ Generic ITI-105 submit — skips the recipe

`app/routers/docsubmit.py`:

- Step 1 only degenerately: `res.setdefault("id", str(uuid.uuid4()))` (`:68`) —
  and the fallback is **non-deterministic** `uuid4`, undercutting the
  idempotence the rest of the system guarantees.
- Steps 2–4: **none.** No reference rewrite; `entry.fullUrl` is **ignored
  entirely** (`:61-70`).
- Effect: a bundle that arrives already-localized (our own compiled docs, Epic
  output) survives by luck; a **foreign-id** bundle gets foreign ids served as if
  authoritative (Approach #1 trap), and a **`urn:uuid:`-style** bundle gets
  random ids while inter-resource refs still say `urn:uuid:abc` → **dangling
  references.**

### Compiled documents — internally navigable, no source trail

`app/fhir/document.py`:

- `fullUrl`s are absolute `base/Type/id` against **our** server (`_full`, `:299`),
  references relative `Type/id` — so in-document navigation resolves against the
  store. ✅ correct for a self-contained doc.
- But the doc is **recompiled on demand**, so it's not a truly frozen artifact,
  and it carries **no provenance** (no `Provenance` entry, no `meta.source`, no
  `link[via]`) — so a recipient cannot mechanically trace any fact back to Epic.
  The only breadcrumb is the `epic-source-id` identifier on the resource.

### Searchset bundles — no outcome, no self link

`app/fhir/store.py:296` (`bundle_searchset`) emits `type`/`total`/`entry[].fullUrl`
but **no `link[self]`** and **no `OperationOutcome` entry** with
`search.mode=outcome` (White Paper CP #1).

---

## 6. Gaps (each tied to spec / white paper)

| # | Gap | Where | Standard violated | Severity | Status |
|---|---|---|---|---|---|
| 1 | Submit trusts foreign ids + doesn't rewrite refs; ignores `entry.fullUrl` → id collisions & dangling `urn:uuid:` refs | `app/routers/docsubmit.py:61-70` | references.html (literal refs must resolve on host); White Paper Approach #1 critique | **High** (correctness) | ✅ **Fixed** via `naturalize_bundle` — re-ids to `uuid5`, rewrites `Type/id` **and** `fullUrl` refs, and **demotes unresolved `urn:uuid:` refs to logical** (`Reference.identifier`) — recipe step 5 now done for the unambiguous (urn) case. |
| 2 | Non-deterministic `uuid4` fallback on submit breaks the "ids are truly persistent" property | `docsubmit.py:68` | managing.html (persistent identity); internal invariant | Medium | ✅ **Fixed** — `uuid5` when a stable origin (`fullUrl` or `Type/id`) exists; `uuid4` only when the entry carries no stable identity at all. |
| 3 | searchset has no `self` link / no search-outcome `OperationOutcome` | `store.py:296` | Bundle searchset; White Paper CP #1 | Low | ✅ **`self` link added** to `bundle_searchset` (threaded from the search routers); match entries already carry `search.mode`. An `OperationOutcome` outcome entry is still optional/unimplemented. |
| 4 | No `meta.source` / `Provenance` / `link[via]` anywhere → no source traceability for documents that pass through hands | ingest + `document.py` | managing.html (provenance); White Paper CP #3 | Medium (this is the *demo* story in §2) | ◐ **Partial** — `meta.source` now stamped on both ingest paths + resolvable via `/{Type}/{id}/$source`; a full `Provenance` resource in compiled documents remains (Task B). |

---

## 7. Implementation plan (for an implementer agent)

Resolve the gaps by **moving the naturalization recipe (§3) out of the
Epic-specific transform and making it the thing that happens at every ingest
boundary.** Design decisions are pre-resolved below so this can be picked up cold.

### Task A — `naturalize_bundle()` at the ITI-105 front door *(Gap 1, 2)* — ✅ DONE (2026-05-31)

Implemented in `app/fhir/naturalize.py` (`naturalize_bundle`), wired into
`app/routers/docsubmit.py`. Notes vs the original plan below: the origin-id
system is **`urn:ehds-demo:source-id`** (not `…submitted-source-id`); the local
id is `uuid5` over `submitted/{fullUrl|Type/id}`; **step 5 (demote unresolved
refs to logical) was deferred** — out-of-bundle refs are left literal for now.
`meta.source` (Task B's first half) is stamped here too. Tests:
`tests/test_naturalize.py`. The Epic path was NOT folded into this routine (it
does much more — IPS profiling, med-request→statement, EU sanitization); the two
share `set_source` and the concept, not the code. Original plan preserved below.

1. Add a `naturalize` step the submit handler calls *before* mirroring into the
   store. Model it on `epic_transform.transform_bundle` (same shape: build an
   id-map, rewrite refs, then persist).
2. For each entry: derive a **deterministic local id** via uuid5 over the entry's
   stable inbound identity — prefer `entry.fullUrl`, else `Resource.identifier`,
   else `Type/id`. **Never** mint `uuid4`.
3. Build `{inbound-fullUrl-or-Type/id → local Type/id}` and run a
   `_walk_replace_refs`-style pass so all intra-bundle references (including
   `urn:uuid:` ones keyed off `fullUrl`) repoint to local ids.
4. Preserve the inbound identity: if the resource had a foreign `id`/`fullUrl`,
   add it as a `Resource.identifier` (pick a system, e.g.
   `urn:ehds-demo:submitted-source-id`).
5. Unresolved references (target not in the bundle and not already local): demote
   to a **logical reference** (`reference.identifier`) per §3 policy; do not leave
   a foreign absolute URL.
6. Acceptance: submit a `Bundle.type=document` using `urn:uuid:` fullUrls + matching
   `urn:uuid:` references and resources *without* `.id`; after submit, every stored
   resource resolves and **no reference dangles**. Add a test alongside
   `tests/test_store_cache.py` / the submit tests. Refactor so the Epic path and
   the front door share the one naturalize routine (Epic output is already-local,
   so re-running it is a deterministic no-op).

### Task B — provenance / source traceability *(Gap 4)* — ◐ half done

1. ✅ Stamp `meta.source` at ingest in the naturalize step **and** in
   `epic_transform` (the source's absolute FHIR URL). Done; resolvable via
   `GET /{Type}/{id}/$source` (`app/routers/source_link.py`). **Remaining:**
   steps 2–3 below (a `Provenance` resource in compiled documents).
2. In `compile_document` (`app/fhir/document.py`), add a `Provenance` entry to the
   document Bundle whose `target` lists the composed resources and whose
   `entity`/`agent` records the origin (Epic) and this gateway — OR add
   `Bundle.entry.link[relation=via]` per White Paper CP #3. Provenance is the
   richer, more renderable choice.
3. Acceptance: a compiled document lets a recipient mechanically answer "where did
   this fact originate and through whose hands?" from the bundle alone. Demo:
   in-document links resolve on-box (offline), *and* the provenance trail names the
   source.

### Task C — searchset outcome + self link *(Gap 3)* — ◐ self link DONE

1. ✅ `bundle_searchset` (`app/fhir/store.py`) takes a `self_link` and emits
   `link[self]`; the Patient/DocumentReference/generic search routers pass
   `str(request.url)`. Test: `tests/test_conformance_extras.py`. **Remaining:**
   the optional `OperationOutcome` `search.mode=outcome` entry.
2. Acceptance: searchset matches the White Paper base-scenario shape (CP #1).

### Cross-cutting

- After Task A, update [`conformance-deviations.md`](conformance-deviations.md):
  the "permissive submit" note should now say submissions are **naturalized**
  (re-identified + reference-rewritten), not stored verbatim.
- Keep determinism: every new id is uuid5, never uuid4 — tests stay golden across
  reseed (see [`identity-cheatsheet.md`](identity-cheatsheet.md)).

---

## 8. References

- FHIR R5 — Managing Resource Identity: https://www.hl7.org/fhir/R5/managing.html
- FHIR — References between Resources: https://www.hl7.org/fhir/references.html
- FHIR — Bundle (searchset / fullUrl resolution): https://www.hl7.org/fhir/bundle.html
- FHIR — Documents (self-contained / frozen): https://www.hl7.org/fhir/documents.html
- FHIR — Provenance: https://www.hl7.org/fhir/provenance.html
- HL7 Intermediaries White Paper: https://confluence.hl7.org/spaces/FHIR/pages/Intermediaries+White+Paper
- IHE PMIR / PIXm (golden-patient + identifier cross-reference; see the paper's comment thread)
</content>
</invoke>
