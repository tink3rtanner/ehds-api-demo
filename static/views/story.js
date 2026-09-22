// Story — the narrative home. The argument first, each pillar runnable, then
// where to go next. Everything shown is fetched live through the front door.

import { el, copyText } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as api from '../lib/api.js';
import {
  pageShell, card, button, badge, statTile, section, note, qrImage, requestChip, copyButton, spinner,
} from '../lib/components.js';
import { CATEGORY_META, humanName, patientCountry, countryName, categoryOfDocRef } from '../lib/fhir.js';

const PILLARS = [
  {
    n: '01', key: 'authorize', title: 'Authorize',
    standard: 'SMART Backend Services',
    what: 'A clinic proves who it is with a signed JWT and gets a short-lived bearer. No shared secrets, no user in the loop.',
    request: (ex) => ({ method: 'POST', path: '/token' }),
    run: null, // demonstrated in the scenario, where the browser signs a real assertion
  },
  {
    n: '02', key: 'find-patient', title: 'Find the patient',
    standard: 'IHE PDQm · ITI-78',
    what: 'Do you hold a record for this person? Demographics in, scored candidates out.',
    request: () => ({ method: 'POST', path: '/Patient/$match' }),
    run: async (ex) => {
      const b = await api.fhir('/Patient/$match', { method: 'POST', body: ex.match_parameters, label: 'PDQm $match', pillar: 'find-patient' });
      const top = (b.entry || [])[0];
      const grade = top?.search?.extension?.find((x) => x.url.endsWith('match-grade'))?.valueCode || top?.search?.score;
      return top ? [el('strong', {}, humanName(top.resource)), ` found · match ${grade ?? ''}`, ` · ${b.total} candidate${b.total === 1 ? '' : 's'}`]
        : ['No candidate'];
    },
  },
  {
    n: '03', key: 'find-documents', title: 'Find documents',
    standard: 'IHE MHD · ITI-67',
    what: 'Which documents exist for her, by category: summary, labs, discharge, imaging, prescription.',
    request: (ex) => ({ method: 'GET', path: `/DocumentReference?patient.identifier=${ex.slot_identifier_system}|${ex.patient.slot}` }),
    run: async (ex) => {
      const b = await api.fhir(`/DocumentReference?patient.identifier=${ex.slot_identifier_system}|${ex.patient.slot}`, { label: 'ITI-67 find documents', pillar: 'find-documents' });
      const cats = (b.entry || []).map((e) => categoryOfDocRef(e.resource)).filter(Boolean);
      return [el('strong', {}, `${b.total} documents`), ' · ', cats.map((c) => CATEGORY_META[c]?.short || c).join(', ')];
    },
  },
  {
    n: '04', key: 'retrieve', title: 'Retrieve a document',
    standard: 'IHE MHD · ITI-68',
    what: 'The Patient Summary as one Bundle.type=document, shaped by the HL7 Europe EPS profile.',
    request: (ex) => ({ method: 'GET', path: ex.documents['patient-summary'].path }),
    run: async (ex) => {
      const b = await api.fhir(ex.documents['patient-summary'].path, { label: 'ITI-68 retrieve', pillar: 'retrieve-document' });
      const comp = b.entry?.[0]?.resource;
      return [el('strong', {}, `${(b.entry || []).length} entries`), ` · ${(comp?.section || []).length} sections · `,
        el('a', { href: `#/documents/${b.id}` }, 'open it')];
    },
  },
  {
    n: '05', key: 'resource-access', title: 'Access resources',
    standard: 'HL7 IPA',
    what: 'Just the allergies, just the labs: atomic resources instead of whole documents.',
    request: (ex) => ({ method: 'GET', path: `/AllergyIntolerance?patient=${ex.patient.id}` }),
    run: async (ex) => {
      const b = await api.fhir(`/AllergyIntolerance?patient=${ex.patient.id}`, { label: 'IPA search', pillar: 'resource-access' });
      const active = (b.entry || []).filter((e) => e.resource.clinicalStatus?.coding?.some((c) => c.code === 'active')).length;
      return [el('strong', {}, `${b.total} allergies`), ` · ${active} active`];
    },
  },
];

function pillarCard(p, ex) {
  const req = p.request(ex);
  const result = el('div', { class: 'pillar-result' });
  const tryIt = p.run
    ? button('Try it live', { small: true, icon: 'play', onClick: async () => {
        result.replaceChildren(spinner('Calling…'));
        try { result.replaceChildren(...await p.run(ex)); } catch (e) { result.textContent = e.message; }
      } })
    : button('Run the scenario', { small: true, icon: 'play', href: '#/scenario' });
  return card({
    className: 'card-pillar',
    title: el('span', {}, el('span', { class: 'pillar-n' }, p.n), ' ', p.title),
    body: el('div', { class: 'stack' },
      el('div', {}, p.what),
      el('div', { class: 'pillar-std' }, p.standard),
      requestChip(req.method, req.path, { label: p.title }),
      result,
    ),
    actions: [tryIt],
  });
}

export async function render({ ctx }) {
  const { examples: ex, info, base } = ctx;
  const uiUrl = `${base}/ui/`;
  const hasRef = !!ex.patient;

  const hero = el('section', { class: 'hero' },
    el('div', {},
      el('div', { class: 'eyebrow' }, 'HL7 Europe Health Data API · reference implementation'),
      el('h1', {}, 'The data models are specified. How they move is not.'),
      el('p', { class: 'lede' },
        'HL7 Europe has defined what a patient summary, a lab report or a discharge report looks like. Nothing yet says how a clinic in one country asks a system in another for them. ',
        el('strong', {}, 'This server shows that four off-the-shelf exchange pillars are enough'),
        ', end to end, with real requests you can watch.'),
      el('div', { class: 'btn-row', style: null },
        button('Run the 90-second scenario', { kind: 'primary', icon: 'play', href: '#/scenario' }),
        button('Browse the reference patients', { href: '#/patients' }),
        button('See who has submitted data', { kind: 'ghost', icon: 'coverage', href: '#/coverage' }),
      ),
    ),
    el('div', { class: 'hero-aside' },
      el('div', { class: 'agent-line' },
        el('span', { class: 'label' }, 'Point your agent at'),
        el('code', {}, base),
        copyButton(base),
      ),
      el('div', { class: 'muted', style: null }, 'One URL. ', el('a', { href: '/', target: '_blank', rel: 'noopener' }, 'GET /'), ' answers with a discovery document, ',
        el('a', { href: '/llms.txt', target: '_blank', rel: 'noopener' }, '/llms.txt'), ' with the same in prose.'),
      qrImage(uiUrl, { size: 132, caption: 'This page, for the slide' }),
    ),
  );

  const scene = note(el('div', {},
    el('h3', {}, 'A patient arrives for care. Her records are somewhere else.'),
    el('p', {},
      hasRef ? `${ex.patient.given} ${ex.patient.family} from ${ex.patient.city || countryName(ex.patient.country)} is seen at a clinic in another member state. ` : 'A patient is seen at a clinic in another member state. ',
      'The clinic system has to prove who it is, ask whether a record exists, find what documents there are, fetch the one it needs, and sometimes ask for a single fact. Each of those is one standard transaction, and each is live below.'),
  ));

  const pillars = el('div', { class: 'pillars' }, hasRef ? PILLARS.map((p) => pillarCard(p, ex)) : []);

  // what is here, live
  const stats = el('div', { class: 'stats' }, spinner('Counting…'));
  api.ui('coverage').then((cov) => {
    stats.replaceChildren(
      statTile('Reference patients', info.patients_by_origin.reference, 'seeded, ten countries'),
      statTile('Community submissions', cov.totals.submissions, `${cov.totals.validated_submissions} validated`),
      statTile('Countries with community data', cov.totals.countries_with_community_data, 'turn yours green'),
      statTile('Priority categories', info.categories.length, 'compiled on demand per patient'),
    );
  }).catch(() => stats.replaceChildren(statTile('Reference patients', info.patients_by_origin.reference)));

  const proves = section('What this proves', el('div', { class: 'gap-argument' },
    card({ title: 'Off the shelf is enough', body: 'SMART Backend Services, PDQm, MHD and IPA already exist and already interoperate. Standardising the exchange layer of the EHDS on them costs a profile, not a new protocol.' }),
    card({ title: 'A vendor can be running in a day', body: [el('a', { href: '#/connect' }, 'Register a client'), ', mint a token, make the five calls. An agent pointed at the base URL works it out on its own.'] }),
    card({ title: 'Conformance is visible', body: ['Every document carries its HL7 Europe profile and can be validated on demand; every submission on the ', el('a', { href: '#/coverage' }, 'coverage map'), ' shows whether it passed.'] }),
    card({ title: 'Nothing here is real', body: 'Every patient is synthetic. The reference panel is deterministic and re-seedable; community submissions are example data contributed by anyone who registers a client.' }),
  ), { className: 'block-plain' });

  return pageShell({
    title: null,
    kicker: null,
    children: [hero, scene,
      section('Four exchange pillars, five live requests', pillars, {
        className: 'block-plain',
        sub: hasRef ? `Each card runs the real request against the reference patient ${ex.patient.given} ${ex.patient.family}. Click a request chip to see the raw response.` : 'Reference panel not seeded; run scripts.seed to enable live examples.',
      }),
      section('What is on this server', stats, { className: 'block-plain' }),
      proves,
      el('div', { class: 'row' }, badge('synthetic data only', 'neutral'), badge('FHIR R4 4.0.1', 'neutral'), badge('Apache-2.0', 'neutral'),
        el('span', { class: 'muted' }, 'Countries shown: ', [...new Set(Object.values(ex.documents || {}).length ? [ex.patient.country] : [])].map(countryName).join(', ') || '—')),
    ],
  });
}
