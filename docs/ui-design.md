# UI design

Implemented 2026-09-22, simplified 2026-09-25 and again 2026-11-17. This is what the `static/` app
does and why. Read it before changing the UI.

## Purpose

A demo server for the EU Health Data API implementation guide, with synthetic
data. The UI shows what is on it and lets people run their own client.

| Audience | What they get |
| --- | --- |
| People | What this is, the data (patients, documents, a map), and a page to register a client, get a token and call the API in the browser |
| AI agents | One link: `/llms.txt`. The UI is not the agent surface |

Keep it short. Plain words, no pitch, no story.

## Decisions

| Decision | Choice | Reason |
| --- | --- | --- |
| Structure | Six sections: Home, Patients, Documents, Map, Do your own demo, Activity | The old UI had 13 pages, then a story and a guided scenario that repeated Connect |
| Data access | The UI reads FHIR through the public API with a read-only viewer token; `/ui/api` serves no FHIR | Everything on screen is what any client would get |
| Attribution | A submission counts for the country in the patient's address; no client data is collected or shown | Josh does not want to collect organisation or contact data |
| Validation | Runs after the 201, result is a badge; a failed validation never rejects a submission | Easy to use; the validator is slow and sometimes fails for reasons unrelated to the bundle |
| Reference panel | Ten seeded patients, hatched on the map, never counted as community data | Examples need data that always works; ten green countries on day one would be misleading |
| Upload | None in the UI; `/llms.txt` documents ITI-105 publish | ITI-105 is the standard flow being demonstrated |
| Agents | Home links `/llms.txt`; `GET /` is the same as JSON | Agents read endpoints, not pages |
| Anonymous read | Removed; a bearer is always required | The UI has its own token and QR codes open UI pages |
| Stack | ES modules, no build step, served by FastAPI | No change to deployment |

## Sections

| Route | Content |
| --- | --- |
| `#/` | Home: one heading, one sentence linking the IG, buttons with live patient and document counts, the `/llms.txt` link |
| `#/patients`, `#/patients/{id}` | The panel grouped by origin; one record with documents, timeline and compartment |
| `#/documents`, `#/documents/{bundleId}` | The registry filtered by category; one document rendered by section with the raw bundle one click away and its EU validation result |
| `#/coverage[/{CC}]` | Map: European countries with data; click one for its categories |
| `#/connect` | Do your own demo: register, get a token, call the API in the browser; the endpoint list |
| `#/activity` | The audit log, scanner noise hidden by default, filter by client |

Every page that calls the API ends with a collapsed list of the requests it
made. Old routes (`#/p/{id}`, `#/logs`, `#/scenario`, `#/implement`, …) redirect.

## Map details

* Country: `Patient.address.country` normalised to ISO alpha-2
  (`app/fhir/origin.py`). Fallback: the Composition custodian's address, then
  any Organization, then unknown. Countries outside Europe are not shown.
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
| `GET /ui/api/coverage` | Map |
| `POST /ui/api/submissions/{id}/validate` | re-validate a published document |
| `GET|POST /ui/api/validation/reference/{pid}/{category}`, `GET /ui/api/validation/{key}` | badges |
| `GET /ui/api/audit`, `/ui/api/audit/stats` | Activity |
| `GET /ui/api/server-info` | counts for Home |

## Code

```
static/
  index.html, app.css, app.js       shell, tokens and components, router
  lib/api.js                        fhir() records every request; ui() for /ui/api
  lib/dom.js, lib/fhir.js           DOM and FHIR helpers
  lib/render.js                     patient cards, resource rows, document cards
  lib/smart.js                      Web Crypto key pair, JWT assertion, register, mint
  lib/components.js                 page shell, card, key-value list, table, request chip,
                                    code block, badge, drawer, timeline, map
  lib/icons.js                      inline SVG icons
  views/*.js                        one render() per section
  assets/europe.svg                 from scripts/build_europe_map.py (Natural Earth)
```

Rules: no inline styles, no emoji, no hard-coded ids (examples come from
`app/fhir/examples.py`; `tests/test_ui_api.py` checks the frontend for slot
labels used as ids). Tests: `tests/test_ui_api.py` and `tests/test_ui_smoke.py`
(playwright: every route at 1440 and 390 px without console errors, home
links, the register/token/call flow, the drawer).
