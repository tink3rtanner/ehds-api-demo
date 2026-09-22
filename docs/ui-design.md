# UI design — the human-facing side of the reference implementation

Status: **implemented** (2026-09-22). This is the design the `static/` app is
built to; the "Open decisions" at the end record what was decided and how.
The living copy that was used to align on this lives in a Claude doc; this
file is the one future agents should read.

## Purpose

The UI exists to make one argument: HL7 Europe has specified the data models,
nobody has specified how they move, and standardising four exchange pillars
(authorize, find the patient, find and retrieve documents, access resources)
with off-the-shelf profiles gets a vendor running in a day. The server is the
proof. The UI is how a human sees the proof.

It serves two audiences with one front door:

| Audience | What they need | How they arrive |
| --- | --- | --- |
| Policy and standards people, mostly non-technical | The exchange story mapped to a real care moment: a patient turns up somewhere their records are not | Josh narrating, or a conference slide with a QR code |
| FHIR implementers and connectathon participants | A real server to hit today, one URL to point an agent at, a place to see whose test data is there and whether it validates | Self-serve from the URL, no narrator |

Tone is reference implementation. It is the HL7 Europe Health Data API plus
IHE MHD and PDQm actors, implemented end to end, with synthetic data. Not a
product, not a portfolio, not an Epic thing. Epic is one hoped-for tenant
among many.

## Decisions

| Decision | Call | Why |
| --- | --- | --- |
| Organizing principle | The exchange story, not resource nouns | The argument is about movement, so the UI is structured as movement |
| Scope | Consolidate hard: 13 nav items became 6 sections | Duplication was the main source of sloppiness |
| Home page | The narrative: arriving for care, records elsewhere, four pillars | The person who needs convincing sees the argument first |
| Second area | Coverage map and community submissions | The connectathon hook: turn your country green |
| Access model | Open on the internet, easy to use, security tightened later | Success is many people using it and it becoming a test-data repository |
| Attribution | Derived from patient address country in the submitted data; no client data collection | GDPR aversion, and representative data is the actual point |
| Client identity | Client ids stay internal; the Connect page shows recent ids and times only | Can surface more later without asking anyone for anything |
| Validation | Badge, never gate; the real EU-profile validator runs asynchronously | A gate kills ease of use; the validator is flaky and slow |
| Seeded panel | Ten EU patients stay as reference examples, always present, visibly distinct, hatched on the map | The guided scenario needs data that never breaks |
| Browser upload | None; the UI shows the exact payload to POST | Clunky, and the standard flow is the thing being proved |
| Epic's role | One tenant among many, not the story | Neutral standards posture; the design is tenant-generic |
| Agent audience | Served by `GET /` and `/llms.txt`; the UI shows off the one URL | Agents read machine endpoints, not SPAs |
| Stack | Vanilla ES modules, no build step, served by FastAPI | Deploy path and systemd unit unchanged |

## Design goals

1. **The exchange story is the spine.** Navigation follows authorize, find the patient, find documents, retrieve, access resources.
2. **Everything on screen came through the public API.** FHIR content is fetched with a visible bearer through the same endpoints any client uses. Every page has a "requests behind this page" drawer. The `/ui/api` layer holds only non-FHIR concerns.
3. **Origin is always visible.** Reference data, community submissions and their source system are distinguishable at a glance, derived from `meta.tag` and `meta.source`, never from who the client was.
4. **Summary first, raw one click away, never both.** One token, minted in one place.
5. **Every URL shown is live and correct.** Example ids come from `app/fhir/examples.py` at request time. Nothing hard-coded (`tests/test_ui_api.py` greps the frontend for slot labels used as ids).
6. **Phone is a real target.** The smoke test asserts no horizontal overflow at 390 px.

## Information architecture

| Section | Route | What it holds | Old pages folded in |
| --- | --- | --- | --- |
| Story | `#/` | The narrative home: four pillars, each with a live request; the agent URL; the slide QR | Home, Authorization (explanation) |
| Scenario | `#/scenario/{step}` | Six guided steps, real requests, a real browser-side SMART client, the audit receipt | Client (walkthrough) |
| Patients | `#/patients`, `#/patients/{id}` | PDQm finder, the panel grouped by origin, one record: documents, timeline, compartment | Patients, Resources, QR |
| Documents | `#/documents`, `#/documents/{bundleId}` | ITI-67 finder, the registry, the rendered document with its EU-profile badge | Documents |
| Coverage | `#/coverage[/{CC}]` | Map of Europe, submissions with validation badges, how to submit | new; the submission demo |
| Connect | `#/connect` | The one URL, register → mint → first call, endpoint reference, capability summary | Implement, Register, Auth, Endpoints, Server |
| Activity | `#/activity` | Audit log with scanner noise filtered by default, filter by client / patient / run | Logs |

Legacy hashes (`#/p/{id}`, `#/logs`, `#/client`, `#/implement`, …) redirect.

## The scenario

Framing: Anna Müller from Vienna is seen at a clinic in another member state.
The clinic's system is the client; this server holds her record.

| Step | Pillar | Live request | Spec |
| --- | --- | --- | --- |
| 1 | Authorize | The browser generates an RSA key, registers it (`POST /register-client`), signs an assertion, `POST /token` | SMART Backend Services |
| 2 | Find the patient | `POST /Patient/$match` | PDQm, ITI-78 |
| 3 | Find documents | `GET /DocumentReference?patient.identifier=…` | MHD, ITI-67 |
| 4 | Retrieve | `GET /Bundle/{id}`, with the EU validation badge and a "validate now" button | MHD, ITI-68 |
| 5 | Resource access | `GET /AllergyIntolerance?patient=…` | IPA |
| 6 | Receipt | `GET /ui/api/audit?run=…`, every request above with its client id | audit |

Every request carries `X-Demo-Run: <run id>`; the middleware writes it to the
audit line so step 6 and the Activity page can filter on it. The browser
client persists in `localStorage` (`ehds.scenario.client`) and re-registers
itself if the server's registry was reset. Without Web Crypto (plain HTTP on a
non-localhost host) step 1 falls back to the viewer token and says so.

## Coverage

* **Country** = the Patient's `address.country`, normalised to ISO alpha-2
  (`app/fhir/origin.py::normalize_country` handles alpha-3 and common names).
  Fallback: Composition custodian's Organization address, then any
  Organization. Never guessed; unknown is shown as unknown. Non-European
  countries are listed beside the map.
* **Two tiers.** Reference examples are hatched (`tier-ref`). Community
  submissions fill solid with intensity by how many of the five categories
  have data (`tier-1` … `tier-5`). "Complete" = all five categories with at
  least one validated document.
* **Validation states**: `pending`, `validated`, `failed` (error count and
  distinct issues), `unavailable` (validator could not answer: jar or packages
  missing, JVM crash, terminology-server timeout), with retry. The validator runs
  offline by default (`EHDS_VALIDATOR_TX=n/a`); the one offline artefact
  (slice ambiguity on `Bundle.entry`) is downgraded to a labelled warning and
  the badge reads "Validated · offline". A terminology server URL gives the
  full verdict. See `app/fhir/validation_queue.py`.
* Nothing about the submitter is displayed or collected. "Source" is the host
  of `meta.source` / an absolute `fullUrl`.

## Agent entry point

`GET /` content-negotiates: browsers (`Accept: text/html`) are redirected to
`/ui/`; everything else gets a discovery document (`app/routers/root.py`) with
the pillars, live examples, priority categories and identity notes.
`/llms.txt` is the prose version. `smart-configuration.example_endpoints`,
the root document and `/ui/api/examples` all come from
`app/fhir/examples.py::live_examples`.

## Backend surface the UI uses

| Endpoint | Purpose |
| --- | --- |
| `POST /ui/api/viewer-token` | read-only bearer (`system/*.read`, client `ui-viewer`) for the page |
| `GET /ui/api/examples` | live ids for the reference patient and her documents |
| `GET /ui/api/coverage`, `/ui/api/submissions[/{id}]`, `POST …/{id}/validate` | Coverage page |
| `GET|POST /ui/api/validation/reference/{pid}/{category}`, `GET /ui/api/validation/{key}` | badges |
| `GET /ui/api/audit`, `/ui/api/audit/stats` | Activity page (`scope=fhir|ui|noise|all`, `patient`, `run`, `client_id`, …) |
| `GET /ui/api/server-info`, `/ui/api/build-info`, `/ui/api/clients`, `/ui/api/qr` | facts |

Everything FHIR-shaped goes through the public API with the viewer token.
There is no proxy, no raw-resource endpoint and no dev token with write scope.

## Visual and component system

Tokens live in `static/app.css` `:root`. Eleven components in
`static/lib/components.js`: page shell, card, key-value list, table, request
chip, code block, badge, drawer, stepper, timeline, map. Banned: inline
`style` attributes, emoji as icons (`static/lib/icons.js` is an inline SVG
set), per-page CSS sections.

## Technical approach

```
static/
  index.html            shell: header, nav, main, footer
  app.css               tokens + components only
  app.js                router, boot, shared context (examples, server-info)
  lib/
    api.js              fhir() through the front door (recorded), ui() for /ui/api
    dom.js              el(), formatting, clipboard, toast
    fhir.js             pickName/pickDate/pickCoding, origin tags, category meta
    render.js           patient cards, resource rows, document cards, narrative sanitiser
    smart.js            Web Crypto keypair, JWT assertion, register + mint
    components.js       the eleven components
    icons.js            inline SVG icon set
  views/                one render({params, ctx}) per section
  assets/europe.svg     generated by scripts/build_europe_map.py (Natural Earth via world-atlas)
```

Testing: `tests/test_ui_api.py` (helper endpoints, static serving, no stale
ids), `tests/test_ui_smoke.py` (playwright: every route at 1440 and 390 px
without console errors, the six-step scenario, the drawer). CI installs
chromium for the latter.

## Open decisions and how they were resolved

| # | Decision | Resolution |
| --- | --- | --- |
| 1 | Front door for UI reads | Front door. The page holds a read-only viewer token; `/ui/api` serves no FHIR |
| 2 | Where the QR code lands | The UI: Story for the slide, the patient page and the document page for their own codes. Raw JSON is one click away |
| 3 | Anonymous read in dev mode | Removed. The FHIR surface requires a bearer in every environment |
| 4 | Reference panel on the map | Hatched, never scored |
| 5 | Country when the patient has none | Custodian, then any Organization, then unknown |
| 6 | Validate reference documents too | Yes: the document page and scenario step 4 have "validate now" |
| 7 | Section name for the map area | Coverage |
| 8 | Requests drawer default | Collapsed; the header's "Technical" toggle (persisted) opens it everywhere |
