// Patients — the panel, grouped by origin, with a filter box.

import { el, debounce, replace } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as api from '../lib/api.js';
import { pageShell, section, badge, emptyState } from '../lib/components.js';
import { humanName, originOf, slotOf, patientCountry, countryName, patientCity } from '../lib/fhir.js';
import { patientCard } from '../lib/render.js';

export async function render() {
  const all = await api.fhirAll('/Patient', { label: 'list patients' });
  const reference = all.filter((p) => originOf(p).kind === 'reference').sort((a, b) => (slotOf(a) || '').localeCompare(slotOf(b) || ''));
  const community = all.filter((p) => originOf(p).kind !== 'reference').sort((a, b) => humanName(a).localeCompare(humanName(b)));

  const q = el('input', { type: 'search', placeholder: 'Filter by name, country, city, id', 'aria-label': 'Filter patients' });
  const refGrid = el('div', { class: 'grid grid-3' });
  const comGrid = el('div', { class: 'grid grid-3' });
  const paint = () => {
    const needle = q.value.trim().toLowerCase();
    const match = (p) => !needle || [humanName(p), patientCountry(p), countryName(patientCountry(p)), patientCity(p), p.id, slotOf(p)].join(' ').toLowerCase().includes(needle);
    const r = reference.filter(match);
    const c = community.filter(match);
    replace(refGrid, ...(r.length ? r.map((p) => patientCard(p)) : [emptyState('No match.')]));
    replace(comGrid, ...(c.length ? c.map((p) => patientCard(p)) : [emptyState(community.length ? 'No match.' : 'No community submissions yet.')]));
  };
  q.addEventListener('input', debounce(paint, 120));
  paint();

  return pageShell({
    title: 'Patients',
    purpose: `${all.length} synthetic patients, read through the public API.`,
    actions: [el('div', { class: 'search' }, icon('search', { size: 16 }), q)],
    children: [
      section('Reference panel', refGrid, { actions: [badge(`${reference.length}`, 'reference')] }),
      section('Community submissions', comGrid, { actions: [badge(`${community.length}`, 'community')] }),
    ],
  });
}
