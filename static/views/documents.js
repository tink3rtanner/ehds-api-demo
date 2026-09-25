// Documents — the registry, filterable by category.

import { el, fmtDate, replace } from '../lib/dom.js';
import * as api from '../lib/api.js';
import { pageShell, table, button, originBadge, requestChip } from '../lib/components.js';
import { CATEGORIES, CATEGORY_META, categoryOfDocRef, ccText, attachmentUrl } from '../lib/fhir.js';
import { categoryBadge, docHref, patientLabel } from '../lib/render.js';

export async function render() {
  const [docs, patients] = await Promise.all([
    api.fhirAll('/DocumentReference', { label: 'registry' }),
    api.fhirAll('/Patient', { label: 'list patients' }),
  ]);
  const byId = Object.fromEntries(patients.map((p) => [p.id, p]));
  const rows = docs.map((d) => ({ d, cat: categoryOfDocRef(d), p: byId[(d.subject?.reference || '').split('/').pop()] }))
    .sort((a, b) => patientLabel(a.p || {}).localeCompare(patientLabel(b.p || {})) || CATEGORIES.indexOf(a.cat) - CATEGORIES.indexOf(b.cat));

  let filter = null;
  const host = el('div', {});
  const pills = el('div', { class: 'row' });
  const paint = () => {
    replace(pills, 
      button(`All · ${rows.length}`, { small: true, kind: filter ? 'ghost' : '', onClick: () => { filter = null; paint(); } }),
      ...CATEGORIES.map((c) => button(`${CATEGORY_META[c].short} · ${rows.filter((r) => r.cat === c).length}`,
        { small: true, kind: filter === c ? '' : 'ghost', icon: CATEGORY_META[c].icon, onClick: () => { filter = c; paint(); } })));
    replace(host, table({
      columns: [
        { label: 'Patient', render: (r) => r.p ? el('a', { href: `#/patients/${r.p.id}`, onclick: (e) => e.stopPropagation() }, patientLabel(r.p)) : el('span', { class: 'muted' }, '—') },
        { label: 'Category', render: (r) => categoryBadge(r.cat) },
        { label: 'Type', render: (r) => ccText(r.d.type) },
        { label: 'Date', className: 'mono nowrap', render: (r) => fmtDate(r.d.date) },
        { label: 'Origin', render: (r) => originBadge(r.d, { withSource: false }) },
        { label: 'Where', className: 'mono', render: (r) => attachmentUrl(r.d) || '—' },
        { label: '', className: 'shrink', render: (r) => docHref(r.d) ? el('a', { href: docHref(r.d) }, 'Open') : el('span', { class: 'muted' }, 'not held here') },
      ],
      rows: filter ? rows.filter((r) => r.cat === filter) : rows,
      onRow: (r) => { const h = docHref(r.d); if (h) location.hash = h; },
      empty: 'No documents.',
      pageSize: 25,
    }));
  };
  paint();

  return pageShell({
    title: 'Documents',
    purpose: 'Every registered document. Click a row to open it.',
    actions: [requestChip('GET', '/DocumentReference')],
    children: [pills, host],
  });
}
