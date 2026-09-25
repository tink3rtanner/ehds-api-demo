// Scenario — six live requests in the order a clinic would make them. Step 1 is
// the real SMART Backend Services flow with a key held in this browser.

import { el, fmtDate, replace } from '../lib/dom.js';
import * as api from '../lib/api.js';
import * as smart from '../lib/smart.js';
import {
  pageShell, stepper, button, badge, kv, codeBlock, methodBadge, statusBadge, validationBadge, spinner, errorBox,
  note, emptyState, drawer,
} from '../lib/components.js';
import { humanName, slotOf, categoryOfDocRef, attachmentUrl, bundleIdFromAttachment, originOf, patientCountry, countryName, ccText, statusOf } from '../lib/fhir.js';
import { categoryBadge, categoryLabel, resourceRow } from '../lib/render.js';

const S = { run: null, patient: null, patients: null, auth: null, results: {}, done: new Set() };

const STEPS = [
  { short: 'Authorize', title: 'The clinic authenticates', std: 'SMART Backend Services',
    text: 'The clinic system signs a JWT with its own key and exchanges it for a short-lived bearer. No user logs in.' },
  { short: 'Find patient', title: 'Find the patient', std: 'IHE PDQm · ITI-78',
    text: 'The clinic sends name, birth date and sex and gets back scored candidates.' },
  { short: 'Find documents', title: 'Find her documents', std: 'IHE MHD · ITI-67',
    text: 'The registry lists her documents and where to fetch them. It contains no clinical content.' },
  { short: 'Retrieve', title: 'Get the Patient Summary', std: 'IHE MHD · ITI-68',
    text: 'The summary arrives as one FHIR document that follows the HL7 Europe profile, so any conforming system can render it.' },
  { short: 'Resources', title: 'Read single resources', std: 'HL7 IPA',
    text: 'The same bearer can read individual resources, here the allergies.' },
  { short: 'Receipt', title: 'The audit log for this run', std: 'Audit log',
    text: 'The server logged every request of this run with the client that made it.' },
];

function reset() { S.run = api.newRun('scenario'); S.auth = null; S.results = {}; S.done = new Set(); }
const token = () => S.auth?.token?.access_token || undefined;
const patientRef = () => (slotOf(S.patient) ? `patient.identifier=${encodeURIComponent('urn:ehds-demo:slot')}|${slotOf(S.patient)}` : `patient=${S.patient.id}`);

async function runAuthorize(ctx) {
  if (!smart.cryptoAvailable()) {
    S.auth = { token: { access_token: await api.viewerToken(), scope: 'system/*.read' } };
    return { request: { method: 'POST', path: '/ui/api/viewer-token' },
      summary: note('This browser cannot sign JWTs over plain HTTP, so the page uses its read-only viewer token instead.', { kind: 'warn' }) };
  }
  const r = await smart.scenarioToken({ tokenEndpoint: ctx.info.token_endpoint, run: S.run });
  S.auth = { client: r.client, token: r.token };
  return {
    request: { method: 'POST', path: '/token' },
    summary: el('div', { class: 'stack' },
      el('p', {}, 'This browser generated a key pair, registered the public key as client ', el('code', {}, r.client.clientId),
        ', signed an assertion with the private key and received a bearer. The following steps use that bearer.'),
      kv([
        { label: 'Assertion', value: `iss=sub=${r.client.clientId} · aud=${r.assertionClaims.aud} · 60 s · single-use jti`, mono: true },
        { label: 'Bearer', value: `${r.token.scope} · ${Math.round(r.token.expires_in / 60)} min`, mono: true },
      ], { columns: 2 })),
    raw: { assertion_claims: r.assertionClaims, token: r.token },
  };
}

async function runFindPatient() {
  const p = S.patient;
  const name = (p.name || [])[0] || {};
  const params = { resourceType: 'Parameters', parameter: [
    { name: 'resource', resource: { resourceType: 'Patient', name: [{ family: name.family, given: name.given }], birthDate: p.birthDate, gender: p.gender } },
    { name: 'count', valueInteger: 3 }] };
  const b = await api.fhir('/Patient/$match', { method: 'POST', body: params, token: token(), label: '$match' });
  const cands = (b.entry || []).map((e) => ({ res: e.resource, score: e.search?.score,
    grade: (e.search?.extension || []).find((x) => x.url.endsWith('match-grade'))?.valueCode }));
  return {
    request: { method: 'POST', path: '/Patient/$match', body: params },
    summary: cands.length ? el('div', { class: 'stack' }, cands.map((c, i) => el('div', { class: 'candidate' },
      el('div', {}, el('strong', {}, humanName(c.res)), el('div', { class: 'muted' }, `${c.res.gender || ''} · born ${c.res.birthDate || '?'} · ${countryName(patientCountry(c.res))}`)),
      el('div', { class: 'row' }, badge(c.grade || '?', c.grade === 'certain' ? 'ok' : 'info'), el('span', { class: 'mono muted' }, c.score?.toFixed(2) || ''), i === 0 ? badge('selected', 'neutral') : null),
    ))) : emptyState('No candidate.'),
    raw: b,
  };
}

async function runFindDocuments() {
  const path = `/DocumentReference?${patientRef()}`;
  const b = await api.fhir(path, { token: token(), label: 'ITI-67' });
  S.docs = (b.entry || []).map((e) => e.resource);
  return {
    request: { method: 'GET', path },
    summary: S.docs.length ? el('table', { class: 'table' }, el('tbody', {}, S.docs.map((d) => el('tr', {},
      el('td', {}, categoryBadge(categoryOfDocRef(d))), el('td', {}, ccText(d.type)),
      el('td', { class: 'mono nowrap' }, fmtDate(d.date)), el('td', { class: 'mono' }, attachmentUrl(d) || '—')))))
      : emptyState('No documents registered for this patient.'),
    raw: b,
  };
}

async function runRetrieve() {
  const doc = (S.docs || []).find((d) => categoryOfDocRef(d) === 'patient-summary') || (S.docs || [])[0];
  const id = doc && bundleIdFromAttachment(attachmentUrl(doc));
  if (!id) return { request: { method: 'GET', path: '/Bundle/{id}' }, summary: emptyState('Nothing retrievable for this patient. Pick a reference patient.') };
  const category = categoryOfDocRef(doc) || 'patient-summary';
  const path = `/Bundle/${id}`;
  const b = await api.fhir(path, { token: token(), label: 'ITI-68' });
  const sections = b.entry?.[0]?.resource?.section || [];
  const vBadge = el('span', {}, validationBadge({ state: 'none' }));
  const refresh = () => api.ui(`validation/reference/${S.patient.id}/${category}`).then((rec) => { replace(vBadge, validationBadge(rec)); return rec; });
  refresh().catch(() => {});
  const validate = button('Validate now', { small: true, icon: 'shield', onClick: async () => {
    validate.disabled = true;
    try {
      await api.ui(`validation/reference/${S.patient.id}/${category}`, { method: 'POST' });
      let rec = await refresh();
      const t0 = Date.now();
      while (rec.state === 'pending' && Date.now() - t0 < 15 * 60_000) { await new Promise((r) => setTimeout(r, 4000)); rec = await refresh(); }
      if (rec.reason) drawer.open('Validator', note(rec.reason, { kind: 'warn' }));
    } finally { validate.disabled = false; }
  } });
  return {
    request: { method: 'GET', path },
    summary: el('div', { class: 'stack' },
      el('div', { class: 'row' }, el('strong', {}, `${categoryLabel(category)} · ${(b.entry || []).length} entries`), vBadge, validate),
      el('div', { class: 'row' }, sections.map((s) => el('span', { class: 'chip' }, el('span', { class: 'n' }, (s.entry || []).length), s.title || ccText(s.code)))),
      button('Open the document', { small: true, icon: 'documents', href: `#/documents/${id}` })),
    raw: b,
  };
}

async function runResources() {
  const path = `/AllergyIntolerance?patient=${S.patient.id}`;
  const b = await api.fhir(path, { token: token(), label: 'IPA' });
  const rows = (b.entry || []).map((e) => e.resource);
  return {
    request: { method: 'GET', path },
    summary: el('div', { class: 'stack' },
      el('div', { class: 'muted' }, `${b.total} allergies, ${rows.filter((r) => statusOf(r) === 'active').length} active`),
      rows.length ? el('div', {}, rows.map((r) => resourceRow(r))) : emptyState('None recorded.')),
    raw: b,
  };
}

async function runReceipt() {
  const log = await api.ui(`audit?run=${encodeURIComponent(S.run)}&scope=all&limit=200`);
  const entries = [...log.entries].reverse();
  return {
    request: { method: 'GET', path: `/ui/api/audit?run=${S.run}` },
    summary: el('div', { class: 'stack' },
      entries.length ? el('div', {}, entries.map((e) => el('div', { class: 'receipt-row' },
        methodBadge(e.method), el('code', {}, e.path + (e.query ? `?${e.query}` : '')), statusBadge(e.status), el('span', { class: 'muted mono' }, e.client_id || '—'))))
        : emptyState('Nothing logged yet; reload this step in a moment.'),
      el('div', { class: 'btn-row' }, button('Open in the log', { small: true, icon: 'activity', href: `#/activity?run=${encodeURIComponent(S.run)}` }))),
    raw: log,
  };
}

const RUNNERS = [runAuthorize, runFindPatient, runFindDocuments, runRetrieve, runResources, runReceipt];

async function loadPatients() {
  if (S.patients) return S.patients;
  const all = await api.fhirAll('/Patient', { label: 'list patients' });
  const ref = all.filter((p) => originOf(p).kind === 'reference' && slotOf(p)).sort((a, b) => slotOf(a).localeCompare(slotOf(b)));
  S.patients = [...ref, ...all.filter((p) => !ref.includes(p))];
  return S.patients;
}

export async function render({ step = 0, ctx }) {
  step = Math.max(0, Math.min(STEPS.length - 1, Number(step) || 0));
  if (!S.run) reset();
  const patients = await loadPatients();
  if (!S.patient) S.patient = patients.find((p) => slotOf(p) === 'p-001') || patients[0];
  if (!S.patient) return pageShell({ title: 'Scenario', children: [emptyState('No patients on this server.')] });
  if (step > 0 && !S.auth) { location.replace('#/scenario/0'); return el('div'); }

  const goto = (i) => { location.hash = `#/scenario/${i}`; };
  const result = el('div', { class: 'scene-result' }, spinner('Sending…'));
  const request = el('div', {});
  const steps = el('div', {}, stepper(STEPS, step, goto, { done: [...S.done] }));
  const run = async () => {
    try {
      const res = S.results[step] || (S.results[step] = await RUNNERS[step](ctx));
      S.done.add(step);
      replace(result, res.summary);
      replace(request, 
        el('div', { class: 'row' }, methodBadge(res.request.method), el('code', {}, res.request.path)),
        res.request.body ? el('details', {}, el('summary', { class: 'muted' }, 'Request body'), codeBlock(res.request.body, { json: true })) : null,
        res.raw ? el('details', {}, el('summary', { class: 'muted' }, 'Raw response'), codeBlock(res.raw, { json: true })) : null);
      replace(steps, stepper(STEPS, step, goto, { done: [...S.done] }));
    } catch (e) {
      delete S.results[step];
      replace(result, errorBox(e.message, { retry: run }));
    }
  };
  queueMicrotask(run);

  const select = el('select', { onchange: (e) => { S.patient = patients.find((p) => p.id === e.target.value); reset(); goto(0); if (step === 0) route(); } },
    patients.map((p) => el('option', { value: p.id, selected: p.id === S.patient.id }, `${humanName(p)} · ${countryName(patientCountry(p))}${slotOf(p) ? '' : ' · community'}`)));
  const route = () => render({ step: 0, ctx }).then((n) => document.getElementById('app').replaceChildren(n));

  const s = STEPS[step];
  const scene = el('section', { class: 'block stack' },
    el('div', {}, el('div', { class: 'eyebrow' }, `Step ${step + 1} · ${s.std}`), el('h2', {}, s.title), el('p', { class: 'muted' }, s.text)),
    result,
    request,
    el('div', { class: 'scene-controls' },
      button('Back', { icon: 'back', disabled: step === 0, onClick: () => goto(step - 1) }),
      step < STEPS.length - 1 ? button('Next', { kind: 'primary', icon: 'next', onClick: () => goto(step + 1) })
        : button('Run again', { kind: 'primary', icon: 'restart', onClick: () => { reset(); goto(0); } }),
      el('span', { class: 'spacer' }),
      el('label', { class: 'scene-select' }, 'Patient ', select)),
  );

  return pageShell({ title: 'A patient visits a clinic in another country', children: [steps, scene] });
}
