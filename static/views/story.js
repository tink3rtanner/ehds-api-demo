// Story — the argument in one screen: the pitch, the agent URL, five live requests.

import { el, replace } from '../lib/dom.js';
import * as api from '../lib/api.js';
import { pageShell, card, button, requestChip, copyButton, qrImage, spinner } from '../lib/components.js';
import { CATEGORY_META, humanName, categoryOfDocRef } from '../lib/fhir.js';

const PILLARS = [
  { n: '01', title: 'Authorize', standard: 'SMART Backend Services',
    request: () => ({ method: 'POST', path: '/token' }) },
  { n: '02', title: 'Find the patient', standard: 'IHE PDQm · ITI-78',
    request: () => ({ method: 'POST', path: '/Patient/$match' }),
    run: async (ex) => {
      const b = await api.fhir('/Patient/$match', { method: 'POST', body: ex.match_parameters, label: '$match' });
      const top = (b.entry || [])[0];
      const grade = top?.search?.extension?.find((x) => x.url.endsWith('match-grade'))?.valueCode;
      return top ? `${humanName(top.resource)} · ${grade}` : 'no candidate';
    } },
  { n: '03', title: 'Find documents', standard: 'IHE MHD · ITI-67',
    request: (ex) => ({ method: 'GET', path: `/DocumentReference?patient.identifier=${ex.slot_identifier_system}|${ex.patient.slot}` }),
    run: async (ex) => {
      const b = await api.fhir(`/DocumentReference?patient.identifier=${ex.slot_identifier_system}|${ex.patient.slot}`, { label: 'ITI-67' });
      return `${b.total} documents · ${(b.entry || []).map((e) => CATEGORY_META[categoryOfDocRef(e.resource)]?.short).filter(Boolean).join(', ')}`;
    } },
  { n: '04', title: 'Retrieve a document', standard: 'IHE MHD · ITI-68',
    request: (ex) => ({ method: 'GET', path: ex.documents['patient-summary'].path }),
    run: async (ex) => {
      const b = await api.fhir(ex.documents['patient-summary'].path, { label: 'ITI-68' });
      return `${(b.entry || []).length} entries · ${(b.entry?.[0]?.resource?.section || []).length} sections`;
    } },
  { n: '05', title: 'Access resources', standard: 'HL7 IPA',
    request: (ex) => ({ method: 'GET', path: `/AllergyIntolerance?patient=${ex.patient.id}` }),
    run: async (ex) => {
      const b = await api.fhir(`/AllergyIntolerance?patient=${ex.patient.id}`, { label: 'IPA' });
      return `${b.total} allergies`;
    } },
];

function pillarCard(p, ex) {
  const req = p.request(ex);
  const result = el('div', { class: 'pillar-result' });
  const action = p.run
    ? button('Try it', { small: true, icon: 'play', onClick: async () => {
        replace(result, spinner(''));
        try { result.textContent = await p.run(ex); } catch (e) { result.textContent = e.message; }
      } })
    : button('See it in the scenario', { small: true, icon: 'play', href: '#/scenario' });
  return card({
    className: 'card-pillar',
    title: el('span', {}, el('span', { class: 'pillar-n' }, p.n), ' ', p.title),
    sub: p.standard,
    body: el('div', { class: 'stack' }, requestChip(req.method, req.path, { label: p.title }), result),
    actions: [action],
  });
}

export async function render({ ctx }) {
  const { examples: ex, base } = ctx;
  const who = ex.patient ? `${ex.patient.given} ${ex.patient.family} from ${ex.patient.city}` : 'a patient';

  const hero = el('section', { class: 'hero' },
    el('div', {},
      el('div', { class: 'eyebrow' }, 'HL7 Europe Health Data API · reference implementation'),
      el('h1', {}, 'HL7 Europe has specified the health data models. Nothing yet specifies how to exchange them.'),
      el('p', { class: 'lede' },
        `${who} visits a clinic in another EU country. This server shows the clinic authorizing itself, finding her, finding her documents and reading them with four existing standards. The requests below run against this server.`),
      el('div', { class: 'btn-row' },
        button('Run the scenario', { kind: 'primary', icon: 'play', href: '#/scenario' }),
        button('Patients', { href: '#/patients' }),
        button('Coverage map', { href: '#/coverage' }),
      ),
    ),
    el('div', { class: 'hero-aside' },
      el('div', { class: 'agent-line' }, el('span', { class: 'label' }, 'Point your agent at'), el('code', {}, base), copyButton(base)),
      qrImage(`${base}/ui/`, { size: 120 }),
    ),
  );

  return pageShell({
    children: [hero, el('div', { class: 'pillars' }, ex.patient ? PILLARS.map((p) => pillarCard(p, ex)) : [])],
  });
}
