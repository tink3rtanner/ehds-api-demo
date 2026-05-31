# Epic → HL7 EU document bundling — plan & implementation notes

Living doc. Goal: pull semantically-correct clinical data from the **Epic on
FHIR R4 sandbox**, repackage it into **HL7 Europe (EHDS) document bundles**,
validate each against the **official HL7 java validator using the real EU IG
packages**, and submit the validated bundles to this demo server under the
SMART client **`vps_bundler`**.

The genuinely new piece vs. prior work: (1) validating against the *real EU
profiles* (previously the repo only ran base-R4 validation — see the note in
`tests/test_profile_validation.py`), and (2) documenting exactly what had to
change in Epic's data to pass. Keep that transformation log current — it's a
primary deliverable.

## Status snapshot (update as we go)

- [x] Epic backend OAuth working. client_id `65ab2be2-7641-4ba2-bfb5-b5faf5e55746`,
      RS384, kid `6c67a423697d884f94e13c5ec30c515c`. Keys in `~/epic-fhir/`,
      JWKS at https://tink3rtanner.github.io/keys/jwks.json
- [x] Epic source adapter merged (`app/sources/epic_*.py`, `/Epic/$import`,
      `scripts/ingest_epic.py`). Camila Lopez (`erXuFYUfucBZaryVksYEcMg3`)
      ingested → local Patient `312045e4-ed34-5df0-befd-42a6ba6f1eeb`
      (data dir `~/epic-fhir/ingested`).
- [x] Patient-summary bundle compiles (285 entries) + passes validator in
      **IPS mode** (`-ips`, 0 errors). Saved `~/epic-fhir/proof/`.
- [x] All 8 EU IG packages downloaded → `.cache/eu-packages/*.tgz`.
- [ ] EU packages installed into validator cache `~/.fhir/packages`.
- [ ] Profile canonicals fixed in `app/fhir/capability.py` (current ones 404).
- [x] patient-summary validated against EU EPS profile: **0 errors** (with
      `-tx https://tx.fhir.org`; 189 warnings = narrative best-practice + Epic
      codes outside IPS free set). Journey 1088 → 44 → 8 → 4 → 0.
- [x] `vps_bundler` registered + patient-summary submitted (201 + 200 readback).
- [x] Transformation notes filled in per category (bottom of this doc).

## Key fact: EPS is IPS

The EU Patient Summary (EPS) profile **imposes** the International Patient
Summary (`hl7.fhir.uv.ips` 2.0.0). So an IPS-conformant PS bundle is the load-
bearing target; EPS adds EU-base constraints (identifier OIDs, nationality)
on top. Validate against IPS first, then add the EPS profile assertion.

## EU IG package inventory (resolved 2026-05-28 from hl7.eu/fhir/<ig>/package.tgz)

Canonical form is `http://hl7.eu/fhir/<ig>` — **note: no `/ig/` segment**.
The repo's old `app/fhir/capability.py` used `http://hl7.eu/fhir/ig/eps/...`
which 404s in the validator. Fix to the URLs below.

| IG | package id | version | canonical |
|----|-----------|---------|-----------|
| Patient Summary | `hl7.fhir.eu.eps` | 1.0.0-ci-build¹ | http://hl7.eu/fhir/eps |
| Laboratory | `hl7.fhir.eu.laboratory` | 2.0.0 | http://hl7.eu/fhir/laboratory |
| Hospital Discharge | `hl7.fhir.eu.hdr` | 0.1.0-ballot | http://hl7.eu/fhir/hdr |
| Imaging | `hl7.fhir.eu.imaging` | 1.0.0-ballot | http://hl7.eu/fhir/imaging |
| Medication P&D | `hl7.fhir.eu.mpd` | 1.0.0 | http://hl7.eu/fhir/mpd |
| EU Base/Core | `hl7.fhir.eu.base` | 2.0.0 | http://hl7.eu/fhir/base |
| Extensions | `hl7.fhir.eu.extensions` | 1.3.0 | http://hl7.eu/fhir/extensions (R5²) |
| Health Data API | `hl7.fhir.eu.health-data-api` | 1.0.0-ballot | http://hl7.eu/fhir/health-data-api |

¹ EPS isn't published at hl7.eu/fhir/eps yet; pulled from
  `https://build.fhir.org/ig/hl7-eu/eps/package.tgz`.
² extensions package declares fhirVersion 5.0.0 — watch for R4/R5 mismatch.

### Document Bundle + Composition profiles per IG (extracted from the packages)

| Category | Bundle profile | Composition profile | Composition.type |
|----------|---------------|--------------------|------------------|
| patient-summary | `http://hl7.eu/fhir/eps/StructureDefinition/bundle-eu-eps` | `http://hl7.eu/fhir/eps/StructureDefinition/composition-eu-eps` | 60591-5 (from IPS) |
| laboratory-report | `http://hl7.eu/fhir/laboratory/StructureDefinition/Bundle-eu-lab` | `http://hl7.eu/fhir/laboratory/StructureDefinition/Composition-eu-lab` | 11502-2 (preferred binding, not fixed) |
| discharge-report | `http://hl7.eu/fhir/hdr/StructureDefinition/bundle-eu-hdr` | `http://hl7.eu/fhir/hdr/StructureDefinition/composition-eu-hdr` | 34105-7 |
| imaging-report | `http://hl7.eu/fhir/imaging/StructureDefinition/BundleReportEuImaging` | `http://hl7.eu/fhir/imaging/StructureDefinition/CompositionEuImaging` | (imaging) |

HDR also ships `bundle-obl-eu-hdr` / `composition-obl-eu-hdr` (obligation
variants). Imaging also ships `BundleReportMinimalMetadataEuImaging` +
`composition-obligation-eu-imaging`.

Binding strength of Composition.type per IG (matters for whether a wrong code
is an error vs warning):
- **HDR**: hard-fixed `34105-7` via patternCodeableConcept → wrong code = error.
- **Lab**: preferred binding (11502-2 conventional) → wrong code = warning.
- **Imaging**: preferred binding to ValueSet (LOINC `is-a 18726-0`, radiology
  studies); conventional generic `18748-4` → warning only.
- **EPS**: `composition-eu-eps` derives from `composition-eu-core` (EU base),
  doesn't hard-fix; IPS parent fixes `60591-5`. Use `60591-5`.
- **EU Base** `composition-eu-core` is the generic abstract parent (no type).

### ⚠ MPD has NO R4 document bundle

R4 `hl7.fhir.eu.mpd` 1.0.0 ships **only resource-level profiles**:
`Medication-eu-mpd`, `MedicationRequest-eu-mpd`, `MedicationDispense-eu-mpd`,
`Dosage-eu-mpd`. There is no `Bundle`/`Composition` document profile in R4
(document packaging for eP/eD lives in the R5 `mpd-r5` IG and/or is assembled
via the health-data-api IG). So "prescription as a document bundle" is **not a
standalone EU R4 document** — options:
  1. Drop the `prescription` document category from the EU-validated set, OR
  2. Emit the eP/eD as a `collection` bundle of the MPD resource profiles
     (validate resources against MedicationRequest-eu-mpd etc.), not a
     `Bundle.type=document`.
Decision: treat eP/eD as resource-profile conformance only for now; note it.

## Validator invocation

Jar: `.cache/validator_cli.jar` (178 MB, fetched). Java 21 present.

Install local packages once so runs are offline/repeatable:
```bash
for f in .cache/eu-packages/*.tgz; do
  java -jar .cache/validator_cli.jar -install-pack "$f"  # or just -ig <path.tgz>
done
```
(Simplest: pass `-ig <local.tgz>` per run; the validator resolves transitive
deps from packages.fhir.org — the box has internet.)

Per-category validate (example, laboratory):
```bash
java -jar .cache/validator_cli.jar bundle-lab.json -version 4.0.1 \
  -ig .cache/eu-packages/base.tgz \
  -ig .cache/eu-packages/laboratory.tgz \
  -profile http://hl7.eu/fhir/laboratory/StructureDefinition/Bundle-eu-lab \
  -tx https://tx.fhir.org
```
For PS use `-ig hl7.fhir.uv.ips#2.0.0 -ig .cache/eu-packages/base.tgz -ig
.cache/eu-packages/eps.tgz` and `-profile .../bundle-eu-eps`. `-check-ips-codes`
to surface SNOMED-outside-IPS-free-set (info, not error).

## Wiring in this repo

- Document compiler: `app/fhir/document.py` → `compile_document(patient_id, category)`.
  - `CATEGORY_TO_DOC_TYPE`, `SECTION_CODES`, `CATEGORY_SECTIONS` drive sections.
  - `PROFILE_EU_BUNDLE` (in `app/fhir/capability.py`) → fix canonicals here.
- Bundle ids are deterministic from the Patient slot identifier
  (`urn:ehds-demo:slot`); Epic patients get slot `epic-<epicId>` (see
  `app/sources/epic_transform.py`).
- Submission path: `POST /` or `/Bundle` (ITI-105) in `app/routers/docsubmit.py`;
  accepts `Bundle.type=transaction|document`, mirrors supported resources into
  the store. Scope `system/Bundle.write`.
- Client registry: `app/tools/register_client.py` (CLI) or `POST /register-client`.

## Plan (ordered)

1. Install EU packages into `~/.fhir/packages` (or use `-ig <tgz>`). [task 9]
2. Fix `PROFILE_EU_BUNDLE` canonicals + ensure Composition profile is stamped
   per category. [task 10]
3. For Camila (and 1–2 more Epic patients), compile each category, validate
   against the EU profile, iterate transforms to 0 errors. Log every change
   below. [task 11]
4. Register `vps_bundler`, submit each validated bundle, confirm 200 + that a
   read-back round-trips. [task 12]
5. Finalise transformation notes. [task 13]

## Epic test patients (sandbox)

- Camila Lopez `erXuFYUfucBZaryVksYEcMg3` (rich: 7 cond, 259 obs, labs, vitals)
- Derrick Lin `eq081-VQEgP8drUUqCWzHfw3`
- Desiree Powell `eAB3mDIBBcyUKviyzrxsnAw3`
- Warren McGinnis `e0w0LEDCYtfckT6N.CkJKCw3`

## Transformation log (Epic R4 → EU IG) — fill as discovered

Already implemented in `app/sources/epic_transform.py`:
- Re-id every resource to deterministic uuid5; rewrite all references.
- `MedicationRequest` → `MedicationStatement` (IPS Medication Summary prefers
  statements); Epic provenance kept in `derivedFrom.identifier`.
- Stamp IPS resource profiles (`*-uv-ips`).
- Default `Condition.category = problem-list-item` when Epic omits it.
- Insert IPS absent-data placeholders (`no-allergy-info` / `no-problem-info`
  / `no-medication-info`) so required PS sections are never empty.

### patient-summary (EPS) — validated against `bundle-eu-eps` + IPS 2.0.0

First raw run (Camila Lopez, 285 entries): **1088 errors**. Root causes and
the transforms applied (all in `app/sources/epic_transform.py` /
`app/fhir/document.py`):

1. **Proprietary extensions rejected** (~520 errors). Epic ships
   `http://open.epic.com/FHIR/StructureDefinition/extension/*` (template-id,
   legal-sex, sex-for-clinical-use, calculated-pronouns,
   temperature-in-fahrenheit, observation-datetime, specialty) and some
   Nictiz `http://nictiz.nl/fhir/StructureDefinition/*` (CopyIndicator,
   BodySite-Qualifier). The validator errors "extension could not be found so
   is not allowed here." → **`_sanitize_for_eu` strips every extension whose
   url is not under `hl7.org/fhir` or `hl7.eu/fhir`.** EHDS cross-border docs
   must not carry source-proprietary extensions.

2. **Patient profile mismatch** (~275 errors). Every `subject`/`patient`
   reference failed "Unable to find a profile match for Patient/X among
   choices: Patient-uv-ips" — because the Patient carried the Epic extensions
   above and so didn't conform to `Patient-uv-ips` / `patient-eu-eps`. Largely
   cascades away once (1) strips the extensions.

3. **Section-entry profile mismatch** (~265). Observations/Conditions didn't
   match the EU-core profiles the EPS Composition slices require
   (`medicalTestResult-eu-core`, `condition-eu-core`, …). Also driven by the
   bad extensions; remainder tracked below.

4. **Non-canonical displays** (~6). Epic's CPT/ICD-9 `display` strings
   ("PR APPENDECTOMY", "Ischemic chest pain (CMS/HCC)") don't match the code
   system's official display → "Wrong Display Name". → **`_sanitize_for_eu`
   drops `display` on codings whose system is CPT / ICD-9-CM / ICD-10-CM**
   (the code is authoritative; display is optional).

5. **EPS Composition structure** (~3). `Composition.identifier` is 1..1 in
   EPS (Epic/our compiler didn't set it) → **compile_document now mints a
   deterministic `Composition.identifier`.** EPS also requires these sections
   (min=1): Problems, Allergies, Medications, **Procedures**, **Medical
   Devices**. → **`_ensure_required_eps_sections` injects any missing
   required section with an `emptyReason` (list-empty-reason `unavailable`).**

Cascade result: **1088 → 44 → 8 errors**. The drop from 44→8 came from fixing
the sanitizer: it had been stripping *relative-url sub-extensions* (e.g.
`level`/`type` inside the complex `patient-proficiency` extension), leaving an
empty extension that violated ext-1 and made the Patient fail `Patient-uv-ips`
— which cascaded to every `subject`/`patient` reference. Fix: only strip
absolute foreign extension urls, drop extensions left with no value/sub-ext,
and prune elements emptied to `{}`/`[]` (fixed `Encounter.hospitalization {}`).

**Final result: 0 errors** against `bundle-eu-eps` (with `-tx https://tx.fhir.org`).
The last errors and their fixes:
- The "8 errors / matches more than one slice" were a **`-tx n/a` artifact** —
  the IPS Observation slices discriminate partly by required ValueSet bindings,
  which the validator can't evaluate without terminology. With a real `-tx`
  they vanished. (Lesson: iterate with `-tx n/a`, get the verdict with real tx.)
- With tx on, real issues surfaced: Epic's LOINC displays are non-canonical
  ("Vital signs" vs "Vital signs note" for 8716-3) → added `http://loinc.org`
  to the display-strip set; and vital-signs Observations were filed in the IPS
  Results section (which requires `medicalTestResult-eu-core`) → split them
  into a Vital Signs section (8716-3) vs Results (30954-2) in compile_document.
- Stamping the category-specific IPS Observation profile (vitalsigns /
  results-laboratory-pathology / results-radiology) makes the bundle's
  by-profile entry slicing unambiguous.

189 warnings remain (dom-6 "should have narrative" best-practice on
Encounter/Organization/DiagnosticReport, and Epic codes outside the IPS free
set) — warnings, not errors.

Submission: `scripts/submit_bundle.py` POSTed the patient-summary under
`vps_bundler` → 201 Created, read-back 200 (574 KB). The demo's docsubmit only
does structural validation, so EU-profile cleanliness isn't required to submit.

- laboratory-report (EU Lab `Bundle-eu-lab`): **validated, 23 errors (xfail).**
  Profile wants exactly one DiagnosticReport (`one-dr`), a
  `DiagnosticReportCompositionR5` extension, performer = Organization (we emit
  Practitioner), Observation-eu-lab profile matches, and `dr-comp-*` SHALL
  constraints (shared identifier/category/subject). Genuine compiler+data work.
- discharge-report (EU HDR `bundle-eu-hdr`): **validated, 59 errors (xfail).**
  Missing required `sectionHospitalCourse` slice, fixed `type` pattern, closed
  section-entry target types, unresolved `patient-eu-core`/`CodeableConcept-uv-ips`.
- imaging-report (EU Imaging `CompositionEuImaging`): **validated, 19 errors
  (xfail).** `Composition.category` min 2, four required section slices
  (imagingstudy/order/history/procedure), author slicing, `dr-comp-author-org`.
- prescription (MPD): **FIXED** — the Bundle no longer stamps the *resource*
  profile `MedicationRequest-eu-mpd` on itself (that made the validator treat
  the Bundle as a MedicationRequest). It now validates clean as a profile-less
  base-R4 document. (`PROFILE_EU_BUNDLE["prescription"] = None`.)
- **patient-summary (EPS): passes** EU `bundle-eu-eps`.

The three xfails above are tracked in `tests/test_profile_validation.py`
(non-strict xfail) — flipping any to a pass will xpass and flag it. Each needs
per-IG document restructuring + seed-data shape changes; see the per-category
error breakdown above.

Terminology note: Epic uses proprietary `open.epic.com` code systems on ~253
Observation codes and its CPT/RxNorm/ICD codes are not in the IPS/EU free
sets — these surface as warnings (info with `-tx n/a`); full conformance would
need SNOMED/LOINC/ATC/EDQM concept-map translation, out of scope for now.
