// Patients — find by demographics (PDQm), then the panel grouped by origin.

import { el, debounce } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as api from '../lib/api.js';
import { pageShell, section, button, badge, emptyState, spinner, errorBox, requestChip } from '../lib/components.js';
import { humanName, originOf, slotOf, patientCountry, countryName, patientCity } from '../lib/fhir.js';
import { patientCard } from '../lib/render.js';

function field(label, input) {
  return el('div', { class: 'field' }, el('label', {}, label), input);
}

export async function render({ ctx }) {
  const all = await api.fhirAll('/Patient', { label: 'list patients' });
  const reference = all.filter((p) => originOf(p).kind === 'reference').sort((a, b) => (slotOf(a) || '').localeCompare(slotOf(b) || ''));
  const community = all.filter((p) => originOf(p).kind !== 'reference').sort((a, b) => humanName(a).localeCompare(humanName(b)));

  // ---- PDQm finder ----
  const family = el('input', { placeholder: 'e.g. Müller', value: ctx.examples.patient?.family || '' });
  const given = el('input', { placeholder: 'e.g. Anna', value: ctx.examples.patient?.given || '' });
  const birth = el('input', { type: 'date', value: ctx.examples.patient?.birthDate || '' });
  const results = el('div', { class: 'stack' });
  const runMatch = async () => {
    results.replaceChildren(spinner('Matching…'));
    const name = {};
    if (family.value.trim()) name.family = family.value.trim();
    if (given.value.trim()) name.given = [given.value.trim()];
    const params = { resourceType: 'Parameters', parameter: [
      { name: 'resource', resource: { resourceType: 'Patient', ...(Object.keys(name).length ? { name: [name] } : {}), ...(birth.value ? { birthDate: birth.value } : {}) } },
      { name: 'count', valueInteger: 5 }] };
    try {
      const b = await api.fhir('/Patient/$match', { method: 'POST', body: params, label: 'PDQm $match', pillar: 'find-patient' });
      const cands = (b.entry || []);
      if (!cands.length) { results.replaceChildren(emptyState('No candidate. The server answers a stranger with nothing.')); return; }
      results.replaceChildren(el('div', { class: 'grid grid-3' }, cands.map((e) => {
        const grade = (e.search?.extension || []).find((x) => x.url.endsWith('match-grade'))?.valueCode;
        return patientCard(e.resource, { extra: badge(`${grade || 'match'} · ${(e.search?.score ?? 0).toFixed(2)}`, grade === 'certain' ? 'ok' : 'info') });
      })));
    } catch (e) { results.replaceChildren(errorBox(e.message)); }
  };
  const finder = section('Find a patient by demographics', el('div', { class: 'stack' },
    el('div', { class: 'form' },
      field('Family name', family), field('Given name', given), field('Birth date', birth),
      el('div', { class: 'field' }, el('label', {}, ' '), button('Run $match', { kind: 'primary', icon: 'search', onClick: runMatch })),
    ),
    el('div', { class: 'row' }, requestChip('POST', '/Patient/$match', { onRun: runMatch }),
      requestChip('GET', `/Patient?family=${encodeURIComponent(family.value || 'Müller')}&birthdate=${birth.value || '1968-03-14'}`),
      el('span', { class: 'muted' }, 'PDQm ITI-78: scored candidates, or a plain demographic search.')),
    results,
  ), { sub: 'This is how a clinic in another country asks whether a record exists. Identifiers rarely cross borders; demographics do.' });

  // ---- panel ----
  const q = el('input', { type: 'search', placeholder: 'Filter by name, country, city, id…', 'aria-label': 'Filter patients' });
  const refGrid = el('div', { class: 'grid grid-3' });
  const comGrid = el('div', { class: 'grid grid-3' });
  const paint = () => {
    const needle = q.value.trim().toLowerCase();
    const match = (p) => !needle || [humanName(p), patientCountry(p), countryName(patientCountry(p)), patientCity(p), p.id, slotOf(p),
      ...(p.identifier || []).map((i) => i.value)].join(' ').toLowerCase().includes(needle);
    const r = reference.filter(match);
    const c = community.filter(match);
    refGrid.replaceChildren(...(r.length ? r.map((p) => patientCard(p)) : [emptyState('No reference patient matches.')]));
    comGrid.replaceChildren(...(c.length ? c.map((p) => patientCard(p)) : [emptyState(community.length ? 'No community patient matches.' : 'No community submissions yet. Anyone with a registered client can publish one.')]));
  };
  q.addEventListener('input', debounce(paint, 120));
  paint();

  const panel = section('Reference panel', refGrid, {
    sub: `${reference.length} seeded patients, one per country, deterministic and re-seedable. The guaranteed-working path for the scenario.`,
    actions: [badge('Reference', 'reference')],
  });
  const comm = section('Community submissions', comGrid, {
    sub: `${community.length} patients arrived over ITI-105 or an import. Their country comes from the address in the data; the source system is the host in meta.source.`,
    actions: [badge('Community', 'community'), button('Coverage map', { small: true, icon: 'coverage', href: '#/coverage' })],
  });

  return pageShell({
    title: 'Patients',
    purpose: `${all.length} patients on this server, all synthetic. Read through the public API with the page's read-only bearer.`,
    actions: [el('div', { class: 'search' }, icon('search', { size: 16 }), q)],
    children: [finder, panel, comm],
  });
}
