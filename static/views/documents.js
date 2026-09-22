// Documents — find (ITI-67) and retrieve (ITI-68). The registry as a whole,
// grouped by category and origin, plus a live finder.

import { el, fmtDate } from '../lib/dom.js';
import * as api from '../lib/api.js';
import { pageShell, section, button, card, table, badge, originBadge, requestChip, emptyState, spinner, errorBox } from '../lib/components.js';
import { CATEGORIES, CATEGORY_META, categoryOfDocRef, humanName, slotOf, originOf, ccText, attachmentUrl } from '../lib/fhir.js';
import { categoryBadge, categoryLabel, docHref, patientLabel } from '../lib/render.js';

export async function render({ ctx }) {
  const [docs, patients] = await Promise.all([
    api.fhirAll('/DocumentReference', { label: 'document registry', pillar: 'find-documents' }),
    api.fhirAll('/Patient', { label: 'list patients' }),
  ]);
  const byId = Object.fromEntries(patients.map((p) => [p.id, p]));
  const patientOf = (d) => byId[(d.subject?.reference || '').split('/').pop()];
  const withCat = docs.map((d) => ({ d, cat: categoryOfDocRef(d), p: patientOf(d) }));
  const counts = Object.fromEntries(CATEGORIES.map((c) => [c, withCat.filter((x) => x.cat === c).length]));

  const tiles = el('div', { class: 'doc-tiles' }, CATEGORIES.map((c) => card({
    className: 'doc-tile', icon: CATEGORY_META[c].icon, title: CATEGORY_META[c].label,
    body: el('div', {}, el('div', { class: 'n' }, counts[c]), el('div', { class: 'muted' }, CATEGORY_META[c].blurb)),
    footer: [el('a', { href: CATEGORY_META[c].ig, target: '_blank', rel: 'noopener' }, CATEGORY_META[c].standard)],
  })));

  // ---- finder ----
  const refPatients = patients.filter((p) => slotOf(p)).sort((a, b) => slotOf(a).localeCompare(slotOf(b)));
  const others = patients.filter((p) => !slotOf(p));
  const patientSel = el('select', {}, [...refPatients, ...others].map((p) => el('option', { value: p.id }, `${patientLabel(p)}${slotOf(p) ? ` · ${slotOf(p)}` : ' · community'}`)));
  const catSel = el('select', {}, el('option', { value: '' }, 'any category'), CATEGORIES.map((c) => el('option', { value: c }, CATEGORY_META[c].label)));
  const chipHost = el('div', { class: 'row' });
  const out = el('div', {});
  const queryFor = () => {
    const p = byId[patientSel.value];
    const slot = slotOf(p);
    const who = slot ? `patient.identifier=${encodeURIComponent('urn:ehds-demo:slot')}|${slot}` : `patient=${p.id}`;
    const type = catSel.value ? `&type=${encodeURIComponent('http://loinc.org')}|${CATEGORY_META[catSel.value].loinc}` : '';
    return `/DocumentReference?${who}${type}`;
  };
  const paintChip = () => chipHost.replaceChildren(requestChip('GET', queryFor(), { onRun: runFind }), el('span', { class: 'muted' }, 'MHD ITI-67, with FHIR chaining on the patient identifier.'));
  const runFind = async () => {
    out.replaceChildren(spinner('Searching…'));
    try {
      const b = await api.fhir(queryFor(), { label: 'ITI-67 find documents', pillar: 'find-documents' });
      const rows = (b.entry || []).map((e) => e.resource);
      out.replaceChildren(rows.length ? docTable(rows.map((d) => ({ d, cat: categoryOfDocRef(d), p: patientOf(d) }))) : emptyState('No documents match.'));
    } catch (e) { out.replaceChildren(errorBox(e.message)); }
  };
  patientSel.addEventListener('change', paintChip);
  catSel.addEventListener('change', paintChip);
  paintChip();
  const finder = section('Find documents for a patient', el('div', { class: 'stack' },
    el('div', { class: 'form' },
      el('div', { class: 'field' }, el('label', {}, 'Patient'), patientSel),
      el('div', { class: 'field' }, el('label', {}, 'Category'), catSel),
      el('div', { class: 'field' }, el('label', {}, ' '), button('Search', { kind: 'primary', icon: 'search', onClick: runFind })),
    ),
    chipHost, out,
  ), { sub: 'What a clinic asks once it knows the patient is here: which documents exist, of which kind, from when.' });

  const registry = section('Every registered document', docTable(withCat), {
    sub: `${docs.length} DocumentReferences. Reference documents are compiled on demand from the panel; community ones point wherever the submitter said.`,
    actions: [badge(`${withCat.filter((x) => originOf(x.d).kind === 'reference').length} reference`, 'reference'),
      badge(`${withCat.filter((x) => originOf(x.d).kind !== 'reference').length} community`, 'community')],
  });

  return pageShell({
    title: 'Documents',
    purpose: 'The five priority categories, each a FHIR document conforming to its HL7 Europe profile. Found with ITI-67, retrieved with ITI-68.',
    children: [tiles, finder, registry],
  });

  function docTable(rows) {
    rows = [...rows].sort((a, b) => (patientLabel(a.p || {}) || '').localeCompare(patientLabel(b.p || {}) || '') || CATEGORIES.indexOf(a.cat) - CATEGORIES.indexOf(b.cat));
    return table({
      columns: [
        { label: 'Patient', render: (r) => r.p ? el('a', { href: `#/patients/${r.p.id}`, onclick: (e) => e.stopPropagation() }, patientLabel(r.p)) : el('span', { class: 'muted mono' }, (r.d.subject?.reference || '').split('/').pop()) },
        { label: 'Category', render: (r) => categoryBadge(r.cat) },
        { label: 'Type', render: (r) => ccText(r.d.type) },
        { label: 'Date', className: 'mono nowrap', render: (r) => fmtDate(r.d.date) },
        { label: 'Origin', render: (r) => originBadge(r.d, { withSource: false }) },
        { label: 'Where', className: 'mono', render: (r) => attachmentUrl(r.d) || '—' },
        { label: '', className: 'shrink', render: (r) => docHref(r.d) ? button('Open', { small: true, href: docHref(r.d) }) : el('span', { class: 'muted' }, 'not held here') },
      ],
      rows,
      onRow: (r) => { const h = docHref(r.d); if (h) location.hash = h; },
      empty: 'No documents registered.',
      pageSize: 25,
    });
  }
}
