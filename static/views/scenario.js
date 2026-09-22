// Scenario — the 90-second guided run. Six steps, each one real request
// through the public API, tagged with a run id so the last step can show the
// receipt. Step 1 is the genuine SMART Backend Services flow: this browser
// holds a private key, registers its public half, signs an assertion and
// mints its own bearer, which the following steps then use.

import { el, fmtDate, fmtMs, hashParams } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as api from '../lib/api.js';
import * as smart from '../lib/smart.js';
import {
  pageShell, stepper, button, badge, kv, codeBlock, methodBadge, statusBadge, validationBadge, spinner, errorBox,
  note, section, technicalMode, drawer, emptyState,
} from '../lib/components.js';
import { humanName, slotOf, categoryOfDocRef, attachmentUrl, bundleIdFromAttachment, CATEGORY_META, originOf, patientCountry, countryName, ccText, statusOf } from '../lib/fhir.js';
import { categoryBadge, categoryLabel, resourceRow } from '../lib/render.js';

// module state survives step-to-step navigation; a restart clears it
const S = { run: null, patient: null, patients: null, auth: null, results: {}, done: new Set() };

const STEPS = [
  { short: 'Authorize', title: 'The clinic proves who it is', pillar: 'SMART App Launch · Backend Services' },
  { short: 'Find patient', title: 'Do you have a record for this person?', pillar: 'IHE PDQm · ITI-78 · Patient $match' },
  { short: 'Find documents', title: 'What do you hold about her?', pillar: 'IHE MHD · ITI-67 · DocumentReference search' },
  { short: 'Retrieve', title: 'Show me the Patient Summary', pillar: 'IHE MHD · ITI-68 · Bundle retrieve' },
  { short: 'Resource access', title: 'Just the allergies, please', pillar: 'HL7 IPA · RESTful search' },
  { short: 'Receipt', title: 'Everything the clinic just did', pillar: 'Audit log' },
];

function reset() {
  S.run = api.newRun('scenario');
  S.auth = null;
  S.results = {};
  S.done = new Set();
}

function tokenFor() {
  return S.auth?.token?.access_token || undefined;
}

function patientRef() {
  const slot = slotOf(S.patient);
  return slot ? `patient.identifier=${encodeURIComponent('urn:ehds-demo:slot')}|${slot}` : `patient=${S.patient.id}`;
}

// ---------- step runners: each returns { request, summary, raw } ----------

async function runAuthorize(ctx) {
  const tokenEndpoint = ctx.info.token_endpoint;
  if (!smart.cryptoAvailable()) {
    const viewer = await api.viewerToken();
    S.auth = { viaViewer: true, token: { access_token: viewer, scope: 'system/*.read' } };
    return {
      request: { method: 'POST', path: '/ui/api/viewer-token' },
      summary: note('This browser cannot sign JWTs (no Web Crypto in an insecure context), so the page is using its read-only viewer token instead. Open the site over HTTPS to watch the full SMART flow.', { kind: 'warn' }),
      raw: smart.decodeJwt(viewer)?.claims,
    };
  }
  const r = await smart.scenarioToken({ tokenEndpoint, run: S.run });
  const claims = smart.decodeJwt(r.token.access_token)?.claims || {};
  S.auth = { client: r.client, token: r.token, assertionClaims: r.assertionClaims, tokenClaims: claims };
  const rows = [
    { label: 'Client', value: r.client.clientId, mono: true },
    { label: 'Key', value: `RSA 2048 · kid ${r.client.kid}`, mono: true },
    { label: 'Assertion aud', value: r.assertionClaims.aud, mono: true },
    { label: 'Assertion lifetime', value: `${r.assertionClaims.exp - r.assertionClaims.iat} s, single-use jti` },
    { label: 'Bearer scope', value: r.token.scope, mono: true },
    { label: 'Bearer expires', value: `${Math.round(r.token.expires_in / 60)} min` },
  ];
  return {
    request: { method: 'POST', path: '/token' },
    summary: el('div', { class: 'stack' },
      el('p', {}, r.client.registeredNow
        ? ['This browser generated a keypair and registered its public half as client ', el('code', {}, r.client.clientId), ' a moment ago. ']
        : ['This browser is registered as client ', el('code', {}, r.client.clientId), '. '],
      'It signed a JWT with the private key (which never left the browser), posted it to the token endpoint, and received a bearer. Every request that follows carries that bearer.'),
      kv(rows, { columns: 3 }),
    ),
    raw: { assertion_claims: r.assertionClaims, access_token_claims: claims },
  };
}

async function runFindPatient() {
  const p = S.patient;
  const name = (p.name || [])[0] || {};
  const params = {
    resourceType: 'Parameters',
    parameter: [
      { name: 'resource', resource: { resourceType: 'Patient', name: [{ family: name.family, given: name.given }], birthDate: p.birthDate, gender: p.gender } },
      { name: 'onlyCertainMatches', valueBoolean: false },
      { name: 'count', valueInteger: 3 },
    ],
  };
  const b = await api.fhir('/Patient/$match', { method: 'POST', body: params, token: tokenFor(), label: 'PDQm $match', pillar: 'find-patient' });
  const cands = (b.entry || []).map((e) => ({
    res: e.resource, score: e.search?.score,
    grade: (e.search?.extension || []).find((x) => x.url.endsWith('match-grade'))?.valueCode,
  }));
  const top = cands[0];
  return {
    request: { method: 'POST', path: '/Patient/$match', body: params },
    summary: el('div', { class: 'stack' },
      el('p', {}, `The clinic sends what it knows — name, date of birth, sex — and asks for scored candidates. ${b.total} came back.`),
      cands.length ? cands.map((c, i) => el('div', { class: 'candidate' },
        el('div', {}, el('strong', {}, humanName(c.res)), el('div', { class: 'muted' }, `${c.res.gender || ''} · born ${c.res.birthDate || '?'} · ${countryName(patientCountry(c.res))}`)),
        el('div', { class: 'row' }, badge(c.grade || '?', c.grade === 'certain' ? 'ok' : c.grade === 'probable' ? 'info' : 'warn'),
          el('span', { class: 'mono muted' }, c.score != null ? c.score.toFixed(2) : ''),
          i === 0 ? badge('selected', 'neutral') : null),
      )) : emptyState('No candidates. The record holder would answer the same way to a stranger: nothing.'),
      top ? el('p', { class: 'muted' }, 'Match grades come from the standard match-grade extension; the clinic decides what "certain" means for its use.') : null,
    ),
    raw: b,
  };
}

async function runFindDocuments() {
  const path = `/DocumentReference?${patientRef()}`;
  const b = await api.fhir(path, { token: tokenFor(), label: 'ITI-67 find documents', pillar: 'find-documents' });
  const docs = (b.entry || []).map((e) => e.resource);
  S.docs = docs;
  return {
    request: { method: 'GET', path },
    summary: el('div', { class: 'stack' },
      el('p', {}, `${b.total} document${b.total === 1 ? '' : 's'} are registered for this patient. Each entry says what the document is (LOINC type), when it was created, and where to fetch it; nothing clinical is in the list itself.`),
      docs.length ? el('div', { class: 'table-wrap' }, el('table', { class: 'table' },
        el('thead', {}, el('tr', {}, el('th', {}, 'Category'), el('th', {}, 'Type'), el('th', {}, 'Date'), el('th', {}, 'Where'))),
        el('tbody', {}, docs.map((d) => el('tr', {},
          el('td', {}, categoryBadge(categoryOfDocRef(d))),
          el('td', {}, ccText(d.type)),
          el('td', { class: 'mono nowrap' }, fmtDate(d.date)),
          el('td', { class: 'mono' }, attachmentUrl(d) || '—'),
        ))),
      )) : emptyState('No DocumentReferences. Community-submitted patients only have documents when the submitter registered them (an MHD Document Source would).'),
    ),
    raw: b,
  };
}

function pickSummaryDoc(ctx) {
  const docs = S.docs || [];
  const summary = docs.find((d) => categoryOfDocRef(d) === 'patient-summary') || docs[0];
  const id = summary ? bundleIdFromAttachment(attachmentUrl(summary)) : null;
  if (id) return { id, category: categoryOfDocRef(summary) || 'patient-summary' };
  if (slotOf(S.patient) && ctx.examples.documents) {
    // reference patients always have a compiled summary even if the registry lookup failed
    return null;
  }
  return null;
}

async function runRetrieve(ctx) {
  const pick = pickSummaryDoc(ctx);
  if (!pick) {
    return {
      request: { method: 'GET', path: '/Bundle/{id}' },
      summary: emptyState('There is no retrievable document for this patient on this server, so ITI-68 has nothing to fetch. Pick a reference patient to see a full Patient Summary.'),
      raw: null,
    };
  }
  const path = `/Bundle/${pick.id}`;
  const b = await api.fhir(path, { token: tokenFor(), label: 'ITI-68 retrieve document', pillar: 'retrieve-document' });
  const comp = b.entry?.[0]?.resource;
  const sections = comp?.section || [];
  const profile = (b.meta?.profile || [])[0];
  S.bundle = b;
  const vBadge = el('span', {}, validationBadge({ state: 'none' }));
  const pid = S.patient.id;
  const refreshBadge = async () => {
    const rec = await api.ui(`validation/reference/${pid}/${pick.category}`);
    vBadge.replaceChildren(validationBadge(rec));
    return rec;
  };
  refreshBadge().catch(() => {});
  const validateBtn = button('Validate against the EU profile now', { small: true, icon: 'shield', onClick: async () => {
    validateBtn.disabled = true;
    try {
      await api.ui(`validation/reference/${pid}/${pick.category}`, { method: 'POST' });
      let rec = await refreshBadge();
      const t0 = Date.now();
      while (rec.state === 'pending' && Date.now() - t0 < 10 * 60_000) {
        await new Promise((r) => setTimeout(r, 3000));
        rec = await refreshBadge();
      }
      if (rec.state === 'unavailable') drawer.open('Validator', note(rec.reason || 'The validator could not run.', { kind: 'warn' }));
    } finally { validateBtn.disabled = false; }
  } });
  return {
    request: { method: 'GET', path },
    summary: el('div', { class: 'stack' },
      el('p', {}, `One request, one document: a Bundle.type=document with ${(b.entry || []).length} entries. The Composition comes first and says what the sections are; everything it points at is inside the same bundle.`),
      kv([
        { label: 'Document', value: `${categoryLabel(pick.category)} · ${humanName(S.patient)}` },
        { label: 'Profile', value: profile ? el('a', { href: profile, target: '_blank', rel: 'noopener' }, profile.split('/').pop()) : 'base FHIR R4 document', mono: !!profile },
        { label: 'EU profile validation', value: el('span', { class: 'row' }, vBadge, validateBtn) },
      ], { columns: 3 }),
      el('div', { class: 'row' }, sections.map((s) => el('span', { class: 'chip' }, el('span', { class: 'n' }, (s.entry || []).length), s.title || ccText(s.code)))),
      el('div', { class: 'btn-row' }, button('Open the rendered document', { icon: 'documents', href: `#/documents/${pick.id}` })),
    ),
    raw: b,
  };
}

async function runResources() {
  const pid = S.patient.id;
  const path = `/AllergyIntolerance?patient=${pid}`;
  const b = await api.fhir(path, { token: tokenFor(), label: 'IPA search', pillar: 'resource-access' });
  const rows = (b.entry || []).map((e) => e.resource);
  const extra = el('div', { class: 'scene-result' });
  const more = (type, label, q = '') => button(label, { small: true, kind: 'ghost', onClick: async () => {
    extra.replaceChildren(spinner());
    try {
      const bb = await api.fhir(`/${type}?patient=${pid}${q}`, { token: tokenFor(), label: 'IPA search', pillar: 'resource-access' });
      const rs = (bb.entry || []).map((e) => e.resource);
      extra.replaceChildren(el('div', {}, el('div', { class: 'muted' }, `${bb.total} ${type}`), ...rs.slice(0, 8).map((r) => resourceRow(r))));
    } catch (e) { extra.replaceChildren(errorBox(e.message)); }
  } });
  return {
    request: { method: 'GET', path },
    summary: el('div', { class: 'stack' },
      el('p', {}, `Sometimes the clinic does not want a whole document, just one fact. The same bearer reads the patient compartment resource by resource. ${b.total} allergies, ${rows.filter((r) => statusOf(r) === 'active').length} active.`),
      rows.length ? el('div', {}, rows.map((r) => resourceRow(r))) : emptyState('No allergies recorded.'),
      el('div', { class: 'btn-row' }, el('span', { class: 'muted' }, 'Also ask for:'), more('Condition', 'problems'), more('MedicationStatement', 'medications'), more('Observation', 'lab results', '&category=laboratory'), more('Immunization', 'vaccinations')),
      extra,
    ),
    raw: b,
  };
}

async function runReceipt() {
  const log = await api.ui(`audit?run=${encodeURIComponent(S.run)}&scope=all&limit=200`);
  const entries = [...log.entries].reverse();
  const client = S.auth?.client?.clientId || S.auth?.tokenClaims?.client_id || 'ui-viewer';
  return {
    request: { method: 'GET', path: `/ui/api/audit?run=${S.run}` },
    summary: el('div', { class: 'stack' },
      el('p', {}, `The record holder logged every request this run made, attributed to client `, el('code', {}, client), `. This is the receipt a connectathon monitor or a patient-facing audit would read.`),
      entries.length ? el('div', {}, entries.map((e) => el('div', { class: 'receipt-row' },
        methodBadge(e.method), el('code', {}, e.path + (e.query ? `?${e.query}` : '')), statusBadge(e.status), el('span', { class: 'muted mono' }, `${e.dur_ms} ms`),
      ))) : emptyState('No entries yet for this run; the audit file may lag a second. Reload this step.'),
      el('div', { class: 'btn-row' }, button('Open in Activity', { icon: 'activity', href: `#/activity?run=${encodeURIComponent(S.run)}` }),
        button('Run it again', { icon: 'restart', onClick: () => { reset(); location.hash = '#/scenario/0'; } })),
    ),
    raw: log,
  };
}

const RUNNERS = [runAuthorize, runFindPatient, runFindDocuments, runRetrieve, runResources, runReceipt];

// ---------- rendering ----------

async function loadPatients() {
  if (S.patients) return S.patients;
  const all = await api.fhirAll('/Patient', { label: 'list patients' });
  const refs = all.filter((p) => originOf(p).kind === 'reference' && slotOf(p)).sort((a, b) => (slotOf(a) || '').localeCompare(slotOf(b) || ''));
  const community = all.filter((p) => originOf(p).kind !== 'reference');
  S.patients = [...refs, ...community];
  return S.patients;
}

function meaningFor(step) {
  const p = S.patient;
  const name = p ? humanName(p) : 'the patient';
  const city = p ? (p.address?.[0]?.city || countryName(patientCountry(p))) : '';
  return [
    `${name} from ${city} is being seen at a clinic in another member state. Before it may ask anything, the clinic system has to prove its identity to the system that holds her record. No user logs in; this is machine to machine.`,
    `Identity proven, the clinic asks the obvious question: is ${name} known here at all? It offers demographics, not an identifier, because identifiers rarely cross borders.`,
    `She is known. Now: what exists? The clinic asks for the document registry entries, optionally narrowed to a category such as the Patient Summary.`,
    `The clinic fetches the Patient Summary itself. It arrives as a single FHIR document shaped by the HL7 Europe profile, so a system that has never seen this record holder before can still render it.`,
    `Later, a pharmacist needs one thing: allergies. Rather than a whole document, the same bearer reads individual resources from her compartment.`,
    `Every request above was logged by the record holder with the client that made it. Transparency is part of the exchange, not an add-on.`,
  ][step];
}

function requestPanel(step, res) {
  const open = technicalMode();
  return el('div', { class: 'stack' },
    el('div', { class: 'row' }, methodBadge(res.request.method), el('code', {}, res.request.path)),
    res.request.body ? el('details', { class: 'tech-details', open },
      el('summary', { class: 'muted' }, 'Request body'), codeBlock(res.request.body, { json: true })) : null,
    res.raw != null ? el('details', { open },
      el('summary', { class: 'muted' }, 'Raw response'), codeBlock(res.raw, { json: true })) : null,
    el('div', { class: 'muted' }, 'Standard: ', STEPS[step].pillar),
  );
}

export async function render({ step = 0, ctx }) {
  step = Math.max(0, Math.min(STEPS.length - 1, Number(step) || 0));
  if (!S.run) reset();
  const patients = await loadPatients();
  if (!S.patient) {
    S.patient = patients.find((p) => slotOf(p) === 'p-001') || patients[0];
  }
  if (!S.patient) {
    return pageShell({ title: 'Scenario', purpose: 'No patients on this server.', children: [emptyState('Seed the reference panel first.')] });
  }
  // steps after the first need the bearer from step 1
  if (step > 0 && !S.auth) {
    location.replace('#/scenario/0');
    return el('div');
  }

  const resultHost = el('div', { class: 'scene-result' }, spinner('Sending the request…'));
  const requestHost = el('div', {}, spinner());
  const runStep = async () => {
    try {
      const res = S.results[step] || (S.results[step] = await RUNNERS[step](ctx));
      S.done.add(step);
      resultHost.replaceChildren(res.summary);
      requestHost.replaceChildren(requestPanel(step, res));
      stepperHost.replaceChildren(stepper(STEPS, step, goto, { done: [...S.done] }));
    } catch (e) {
      delete S.results[step];
      resultHost.replaceChildren(errorBox(e.message, { retry: runStep }));
      requestHost.replaceChildren(el('div', { class: 'muted' }, 'The request failed. Retry, or restart the run.'));
    }
  };
  const goto = (i) => { location.hash = `#/scenario/${i}`; };

  const patientSelect = el('select', { class: 'field-select', onchange: (e) => {
    S.patient = patients.find((p) => p.id === e.target.value);
    reset();
    location.hash = '#/scenario/0';
    if (step === 0) render({ step: 0, ctx }).then((n) => document.getElementById('app').replaceChildren(n));
  } }, patients.map((p) => el('option', { value: p.id, selected: p.id === S.patient.id },
    `${humanName(p)} · ${countryName(patientCountry(p))}${slotOf(p) ? '' : ' · community'}`)));

  const stepperHost = el('div', {}, stepper(STEPS, step, goto, { done: [...S.done] }));
  const controls = el('div', { class: 'scene-controls' },
    button('Back', { icon: 'back', disabled: step === 0, onClick: () => goto(step - 1) }),
    step < STEPS.length - 1
      ? button('Next', { kind: 'primary', icon: 'next', onClick: () => goto(step + 1) })
      : button('Restart', { kind: 'primary', icon: 'restart', onClick: () => { reset(); goto(0); } }),
    el('span', { class: 'spacer' }),
    el('label', { class: 'scene-select' }, 'Patient ', patientSelect),
    button('Restart', { kind: 'ghost', icon: 'restart', small: true, onClick: () => { reset(); goto(0); } }),
  );

  const scene = el('div', { class: 'scene' },
    section(null, el('div', { class: 'stack' },
      el('div', { class: 'scene-story' },
        el('span', { class: 'who' }, step === 5 ? 'The record holder' : 'The clinic system'),
        el('h2', {}, STEPS[step].title),
        el('p', {}, meaningFor(step))),
      resultHost,
      controls,
    )),
    section('The request', requestHost, { sub: `run ${S.run}` }),
  );

  queueMicrotask(runStep);

  return pageShell({
    eyebrow: `Scenario · step ${step + 1} of ${STEPS.length}`,
    title: 'A patient arrives for care. Her records are elsewhere.',
    purpose: 'Six live requests against this server, in the order a clinic would make them. Nothing is mocked; the receipt at the end comes from the server\'s own audit log.',
    actions: [button('Story', { kind: 'ghost', icon: 'story', href: '#/' })],
    children: [stepperHost, scene],
  });
}
