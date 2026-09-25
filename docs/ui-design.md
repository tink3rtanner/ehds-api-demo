# UI design

Implemented 2026-09-22, simplified 2026-09-25. This is what the `static/` app
does and why. Read it before changing the UI.

## Purpose

HL7 Europe has specified the health data models. Nothing yet specifies how to
exchange them. This server shows that four existing standards are enough:
SMART Backend Services to authorize, IHE PDQm to find a patient, IHE MHD to
find and retrieve documents, HL7 IPA to read single resources. The UI shows
that to two audiences:

| Audience | What they get |
| --- | --- |
| Policy and standards people | A care scenario: a patient visits a clinic in another country and the clinic gets her records. Six steps, real requests. |
| FHIR implementers and connectathon participants | A live server, one URL to point an agent at, a map of which countries have submitted example data and whether it validates. |

The tone is a reference implementation. Epic is one possible submitter among
many.

## Decisions

| Decision | Choice | Reason |
| --- | --- | --- |
| Structure | Six sections: Story, Patients, Documents, Coverage, Connect, Activity; the scenario hangs off Story | The old UI had 13 pages with repeated content |
| Data access | The UI reads FHIR through the public API with a read-only viewer token; `/ui/api` serves no FHIR | Everything on screen is what any client would get |
| Attribution | A submission counts for the country in the patient's address; no client data is collected or shown | Josh does not want to collect organisation or contact data |
| Validation | Runs after the 201, result is a badge; a failed validation never rejects a submission | Easy to use; the validator is slow and sometimes fails for reasons unrelated to the bundle |
| Reference panel | Ten seeded patients, hatched on the map, never counted as community data | The scenario needs data that always works; ten green countries on day one would be misleading |
| Upload | None; the page shows the curl and an example bundle | ITI-105 is the standard flow being demonstrated |
| Agents | `GET /` returns a discovery document, `/llms.txt` the same in prose | Agents read endpoints, not pages |
| Anonymous read | Removed; a bearer is always required | The UI has its own token and QR codes open UI pages |
| Stack | ES modules, no build step, served by FastAPI | No change to deployment |

## Sections

| Route | Content |
| --- | --- |
| `#/` | The pitch in three sentences, the agent URL, a QR for the page, five request cards that run live |
| `#/scenario/{step}` | Authenticate (the browser generates a key, registers it, mints a token), `$match`, ITI-67, ITI-68 with the validation badge, IPA read, audit receipt |
| `#/patients`, `#/patients/{id}` | The panel grouped by origin; one record with documents, timeline and compartment |
| `#/documents`, `#/documents/{bundleId}` | The registry filtered by category; one document rendered by section with the raw bundle one click away |
| `#/coverage[/{CC}]` | The map, the submissions with validation badges, how to submit |
| `#/connect` | The agent URL, register → mint → first call in the browser, the endpoint list |
| `#/activity` | The audit log, scanner noise hidden by default, filter by client or run |

Every page ends with a collapsed list of the FHIR requests it made. Old
routes (`#/p/{id}`, `#/logs`, `#/client`, `#/implement`, …) redirect.

## Scenario details

Requests carry `X-Demo-Run: <run id>`; the audit middleware stores it, so step
6 and the Activity page can show one run. The browser client is kept in
`localStorage` (`ehds.scenario.client`) and re-registers if the server no
longer knows it. Without Web Crypto (plain HTTP on a remote host) step 1 uses
the viewer token and says so.

## Coverage details

* Country: `Patient.address.country` normalised to ISO alpha-2
  (`app/fhir/origin.py`). Fallback: the Composition custodian's address, then
  any Organization, then unknown. Non-European countries are listed under the map.
* Shading: reference examples hatched; community data solid, darker with more
  of the five categories covered. "Complete" means all five categories have a
  validated document.
* Validation states: `pending`, `validated`, `failed`, `unavailable`
  (`app/fhir/validation_queue.py`). The validator runs offline by default;
  the one finding that cannot be evaluated offline (slice ambiguity on
  `Bundle.entry`) is counted as a warning and the badge says "Validated · offline".

## Backend used by the UI

| Endpoint | Purpose |
| --- | --- |
| `POST /ui/api/viewer-token` | read-only bearer for the page |
| `GET /ui/api/examples` | live ids for the reference patient and her documents |
| `GET /ui/api/coverage`, `/ui/api/submissions[/{id}]`, `POST …/{id}/validate` | Coverage |
| `GET|POST /ui/api/validation/reference/{pid}/{category}`, `GET /ui/api/validation/{key}` | badges |
| `GET /ui/api/audit`, `/ui/api/audit/stats` | Activity |
| `GET /ui/api/server-info`, `/ui/api/build-info`, `/ui/api/clients`, `/ui/api/qr` | facts |

## Code

```
static/
  index.html, app.css, app.js       shell, tokens and components, router
  lib/api.js                        fhir() records every request; ui() for /ui/api
  lib/dom.js, lib/fhir.js           DOM and FHIR helpers
  lib/render.js                     patient cards, resource rows, document cards
  lib/smart.js                      Web Crypto key pair, JWT assertion, register, mint
  lib/components.js                 page shell, card, key-value list, table, request chip,
                                    code block, badge, drawer, stepper, timeline, map
  lib/icons.js                      inline SVG icons
  views/*.js                        one render() per section
  assets/europe.svg                 from scripts/build_europe_map.py (Natural Earth)
```

Rules: no inline styles, no emoji, no hard-coded ids (examples come from
`app/fhir/examples.py`; `tests/test_ui_api.py` checks the frontend for slot
labels used as ids). Tests: `tests/test_ui_api.py` and `tests/test_ui_smoke.py`
(playwright: every route at 1440 and 390 px without console errors, the
scenario, the drawer).
