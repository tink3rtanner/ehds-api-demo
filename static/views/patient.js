// Patient — one record: who, where from, documents, timeline, compartment.

import { el, fmtDate } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as api from '../lib/api.js';
import {
  pageShell, section, button, kv, badge, originBadge, countryBadge, emptyState, timeline, qrImage, requestChip,
  drawer, spinner, errorBox, openResource,
} from '../lib/components.js';
import { humanName, originOf, slotOf, patientCountry, countryName, bucketize, timelineEvents, categoryOfDocRef, sourceHost, statusOf, enrichMedicationDisplays } from '../lib/fhir.js';
import { demographics, docCard, resourceRow, docHref } from '../lib/render.js';

const BUCKET_ORDER = ['AllergyIntolerance', 'Condition', 'MedicationStatement', 'MedicationRequest', 'MedicationDispense', 'Immunization', 'Procedure', 'Observation', 'DiagnosticReport', 'ImagingStudy', 'Encounter', 'Specimen', 'Composition', 'DocumentReference', 'Practitioner', 'Organization', 'Medication'];

export async function render({ id, ctx }) {
  let p;
  try {
    p = await api.fhir(`/Patient/${id}`, { label: 'read patient', pillar: 'resource-access' });
  } catch (e) {
    return pageShell({ title: e.status === 404 ? 'Patient not found' : 'Could not load patient',
      purpose: e.message, children: [el('div', { class: 'btn-row' }, button('Back to patients', { href: '#/patients' }))] });
  }
  const name = humanName(p) || `Patient/${p.id}`;
  const o = originOf(p);
  const slot = slotOf(p);
  const country = patientCountry(p);
  const base = ctx.base;

  // the compartment and the registry, in parallel
  const everythingHost = el('div', {}, spinner('Loading the compartment…'));
  const docsHost = el('div', { class: 'grid grid-3' }, spinner('Looking up documents…'));
  const chips = el('div', { class: 'chips' });
  const timelineHost = el('div', {}, spinner());

  api.fhir(`/Patient/${p.id}/$everything`, { label: '$everything', pillar: 'resource-access' }).then((b) => {
    const resources = enrichMedicationDisplays((b.entry || []).map((e) => e.resource).filter((r) => r && r.id !== p.id));
    const buckets = bucketize(resources);
    const count = (t) => (buckets[t] || []).length;
    const active = (t) => (buckets[t] || []).filter((r) => statusOf(r) === 'active').length;
    chips.replaceChildren(
      el('span', { class: 'chip' }, el('span', { class: 'n' }, resources.length), 'resources'),
      el('span', { class: 'chip' }, el('span', { class: 'n' }, count('Condition')), 'problems', active('Condition') ? ` · ${active('Condition')} active` : ''),
      el('span', { class: 'chip' }, el('span', { class: 'n' }, count('AllergyIntolerance')), 'allergies'),
      el('span', { class: 'chip' }, el('span', { class: 'n' }, count('MedicationStatement') + count('MedicationRequest')), 'medications'),
      el('span', { class: 'chip' }, el('span', { class: 'n' }, count('Immunization')), 'vaccinations'),
      el('span', { class: 'chip' }, el('span', { class: 'n' }, count('Observation')), 'observations'),
    );
    timelineHost.replaceChildren(timeline(timelineEvents(resources), { onSelect: (ev) => openResource(ev.resource.resourceType, ev.resource.id) }));
    const types = Object.keys(buckets).sort((a, b) => (BUCKET_ORDER.indexOf(a) + 100) % 100 - (BUCKET_ORDER.indexOf(b) + 100) % 100 || a.localeCompare(b));
    everythingHost.replaceChildren(...(types.length ? types.map((t) => el('details', { class: 'bucket' },
      el('summary', {}, t, requestChip('GET', `/${t}?patient=${p.id}`), el('span', { class: 'count' }, `${buckets[t].length}`)),
      el('div', {}, buckets[t].map((r) => resourceRow(r))),
    )) : [emptyState('Nothing references this patient.')]));
  }).catch((e) => { everythingHost.replaceChildren(errorBox(e.message)); timelineHost.replaceChildren(errorBox(e.message)); });

  const docQuery = slot ? `/DocumentReference?patient.identifier=${encodeURIComponent('urn:ehds-demo:slot')}|${slot}` : `/DocumentReference?patient=${p.id}`;
  api.fhir(docQuery, { label: 'ITI-67 find documents', pillar: 'find-documents' }).then((b) => {
    const docs = (b.entry || []).map((e) => e.resource);
    docs.sort((a, b2) => ['patient-summary', 'laboratory-report', 'discharge-report', 'imaging-report', 'prescription'].indexOf(categoryOfDocRef(a)) - ['patient-summary', 'laboratory-report', 'discharge-report', 'imaging-report', 'prescription'].indexOf(categoryOfDocRef(b2)));
    docsHost.replaceChildren(...(docs.length ? docs.map((d) => docCard(d)) : [emptyState(o.kind === 'community'
      ? 'No documents registered. This patient arrived as resources; the submitter did not register a DocumentReference.'
      : 'No documents registered for this patient.')]));
  }).catch((e) => docsHost.replaceChildren(errorBox(e.message)));

  const identifiers = (p.identifier || []).filter((i) => i.system !== 'urn:ehds-demo:slot');
  const facts = kv([
    { label: 'FHIR id', value: `Patient/${p.id}`, mono: true, copy: true },
    slot ? { label: 'Reference slot', value: slot, mono: true } : null,
    ...identifiers.slice(0, 2).map((i) => ({ label: i.system === 'urn:ehds-demo:source-id' ? 'Id at source' : 'Identifier', value: `${i.value}`, mono: true })),
    identifiers[0]?.system && identifiers[0].system !== 'urn:ehds-demo:source-id' ? { label: 'Identifier system', value: identifiers[0].system, mono: true } : null,
    { label: 'Address', value: [(p.address?.[0]?.line || []).join(' '), p.address?.[0]?.postalCode, p.address?.[0]?.city, countryName(country)].filter(Boolean).join(', ') },
    { label: 'Languages', value: (p.communication || []).map((c) => c.language?.coding?.[0]?.code).filter(Boolean).join(', ') },
    o.source ? { label: 'Source', value: el('a', { href: o.source, target: '_blank', rel: 'noopener' }, sourceHost(o.source), ' ', icon('external', { size: 12 })), mono: true } : null,
    o.submission ? { label: 'Submission', value: el('a', { href: `#/coverage?submission=${o.submission}` }, o.submission.slice(0, 8), '…'), mono: true } : null,
  ], { columns: 4 });

  const hero = section(null, el('div', { class: 'patient-hero' },
    el('div', {},
      el('h1', {}, name, countryBadge(country), originBadge(p)),
      el('div', { class: 'demo' }, demographics(p)),
      chips,
      el('div', { class: 'row', style: null }),
    ),
    qrImage(`${base}/ui/#/patients/${p.id}`, { size: 120, caption: 'This record on a phone' }),
  ), { className: '' });
  hero.appendChild(el('div', { style: null }, el('hr', { class: 'sep' }), facts));

  const actions = el('div', { class: 'btn-row' },
    requestChip('GET', `/Patient/${p.id}`),
    requestChip('GET', `/Patient/${p.id}/$everything`),
    requestChip('GET', docQuery),
    o.source ? requestChip('GET', `/Patient/${p.id}/$source`) : null,
    button('Run the scenario with this patient', { small: true, icon: 'play', kind: 'ghost', onClick: () => { location.hash = '#/scenario/0'; } }),
  );

  return pageShell({
    eyebrow: 'Patient record',
    title: null,
    children: [
      hero,
      section('Documents', docsHost, { sub: 'Registered via ITI-67. Open one to see the compiled document and its EU-profile badge.' }),
      section('Timeline', timelineHost, { sub: 'Every dated event in the compartment, newest first. Click to see the resource.' }),
      section('Compartment', everythingHost, { sub: 'Everything that references this patient, grouped by type, straight from $everything.' }),
      section('Requests', actions, { className: 'block-plain', sub: 'The exact calls behind this page. Click one to run it and see the raw response.' }),
    ],
  });
}
