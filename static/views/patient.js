// Patient — one record: documents, timeline, compartment.

import { el, replace } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as api from '../lib/api.js';
import { pageShell, section, button, originBadge, countryBadge, emptyState, timeline, requestChip, spinner, errorBox, openResource } from '../lib/components.js';
import { humanName, originOf, slotOf, patientCountry, bucketize, timelineEvents, categoryOfDocRef, sourceHost, statusOf, enrichMedicationDisplays, CATEGORIES } from '../lib/fhir.js';
import { demographics, docCard, resourceRow } from '../lib/render.js';

export async function render({ id }) {
  let p;
  try {
    p = await api.fhir(`/Patient/${id}`, { label: 'read patient' });
  } catch (e) {
    return pageShell({ title: e.status === 404 ? 'Patient not found' : 'Could not load patient', purpose: e.message,
      children: [el('div', { class: 'btn-row' }, button('Patients', { href: '#/patients' }))] });
  }
  const o = originOf(p);
  const slot = slotOf(p);
  const chips = el('div', { class: 'chips' });
  const docs = el('div', { class: 'grid grid-3' }, spinner('Documents…'));
  const tl = el('div', {}, spinner());
  const comp = el('div', {}, spinner('Compartment…'));

  api.fhir(`/Patient/${p.id}/$everything`, { label: '$everything' }).then(async (b) => {
    const rs = (b.entry || []).map((e) => e.resource).filter((r) => r && r.id !== p.id);
    // Medication resources are not in the compartment; fetch the few that are referenced so rows show drug names
    const medIds = [...new Set(rs.map((r) => r.medicationReference?.reference).filter((ref) => ref && !ref.includes('display')).map((ref) => ref.split('/').pop()))];
    const meds = await Promise.all(medIds.slice(0, 12).map((id) => api.fhir(`/Medication/${id}`, { label: 'read medication', allowError: true })));
    enrichMedicationDisplays([...rs, ...meds.filter((m) => m?.resourceType === 'Medication')]);
    const buckets = bucketize(rs);
    const n = (t) => (buckets[t] || []).length;
    replace(chips, 
      el('span', { class: 'chip' }, el('span', { class: 'n' }, n('Condition')), 'problems'),
      el('span', { class: 'chip' }, el('span', { class: 'n' }, n('AllergyIntolerance')), 'allergies'),
      el('span', { class: 'chip' }, el('span', { class: 'n' }, n('MedicationStatement') + n('MedicationRequest')), 'medications'),
      el('span', { class: 'chip' }, el('span', { class: 'n' }, n('Immunization')), 'vaccinations'),
      el('span', { class: 'chip' }, el('span', { class: 'n' }, n('Observation')), 'observations'));
    replace(tl, timeline(timelineEvents(rs), { onSelect: (ev) => openResource(ev.resource.resourceType, ev.resource.id) }));
    const types = Object.keys(buckets).sort((a, b) => buckets[b].length - buckets[a].length || a.localeCompare(b));
    replace(comp, ...(types.length ? types.map((t) => el('details', { class: 'bucket' },
      el('summary', {}, t, requestChip('GET', `/${t}?patient=${p.id}`), el('span', { class: 'count' }, String(buckets[t].length))),
      el('div', {}, buckets[t].map((r) => resourceRow(r))))) : [emptyState('Nothing references this patient.')]));
  }).catch((e) => { replace(comp, errorBox(e.message)); replace(tl, errorBox(e.message)); });

  const docQuery = slot ? `/DocumentReference?patient.identifier=${encodeURIComponent('urn:ehds-demo:slot')}|${slot}` : `/DocumentReference?patient=${p.id}`;
  api.fhir(docQuery, { label: 'ITI-67' }).then((b) => {
    const list = (b.entry || []).map((e) => e.resource).sort((a, c) => CATEGORIES.indexOf(categoryOfDocRef(a)) - CATEGORIES.indexOf(categoryOfDocRef(c)));
    replace(docs, ...(list.length ? list.map((d) => docCard(d)) : [emptyState('No documents registered for this patient.')]));
  }).catch((e) => replace(docs, errorBox(e.message)));

  const hero = section(null, el('div', {},
    el('h1', {}, humanName(p) || `Patient/${p.id}`, countryBadge(patientCountry(p)), originBadge(p)),
    el('div', { class: 'demo' }, demographics(p)),
    chips,
    el('div', { class: 'row muted mono' }, `Patient/${p.id}`, slot ? ` · ${slot}` : '',
      o.source ? el('a', { href: o.source, target: '_blank', rel: 'noopener' }, sourceHost(o.source), ' ', icon('external', { size: 12 })) : null,
      o.submission ? el('a', { href: `#/coverage?submission=${o.submission}` }, 'submission') : null),
  ));

  return pageShell({
    children: [hero,
      section('Documents', docs, { actions: [requestChip('GET', docQuery)] }),
      section('Timeline', tl),
      section('Compartment', comp, { actions: [requestChip('GET', `/Patient/${p.id}/$everything`)] })],
  });
}
