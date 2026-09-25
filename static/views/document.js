// Document — one Bundle.type=document, readable, with the raw bundle one click away.

import { el, fmtDate, prettyJson, replace } from '../lib/dom.js';
import * as api from '../lib/api.js';
import {
  pageShell, section, button, kv, badge, originBadge, countryBadge, codingChip, validationBadge, requestChip,
  drawer, emptyState, note, copyButton,
} from '../lib/components.js';
import { CATEGORY_META, categoryOfComposition, humanName, patientCountry, resolveInBundle, ccText, originOf, enrichMedicationDisplays } from '../lib/fhir.js';
import { categoryLabel, demographics, resourceRow, sanitizedNarrative } from '../lib/render.js';

export async function render({ id }) {
  let bundle;
  try {
    bundle = await api.fhir(`/Bundle/${id}`, { label: 'ITI-68' });
  } catch (e) {
    return pageShell({ title: e.status === 404 ? 'Document not held here' : 'Could not retrieve document',
      purpose: e.status === 404 ? 'Only the reference panel\'s documents are compiled on this server.' : e.message,
      children: [el('div', { class: 'btn-row' }, button('Documents', { href: '#/documents' }), requestChip('GET', `/Bundle/${id}`))] });
  }
  const entries = bundle.entry || [];
  enrichMedicationDisplays(entries.map((e) => e.resource).filter(Boolean));
  const comp = entries.find((e) => e.resource?.resourceType === 'Composition')?.resource;
  const patient = entries.find((e) => e.resource?.resourceType === 'Patient')?.resource;
  const category = categoryOfComposition(comp);
  const meta = CATEGORY_META[category];
  const profile = (bundle.meta?.profile || [])[0];
  const title = `${categoryLabel(category)}${patient ? ` · ${humanName(patient)}` : ''}`;

  const vHost = el('span', {}, validationBadge({ state: 'none' }));
  const canValidate = !!(patient && category && originOf(patient).kind === 'reference');
  const refresh = () => api.ui(`validation/reference/${patient.id}/${category}`).then((rec) => { replace(vHost, validationBadge(rec)); return rec; });
  if (canValidate) refresh().catch(() => {});
  const show = (rec) => rec && rec.state !== 'none' && drawer.open('EU profile validation', el('div', { class: 'stack' },
    kv([{ label: 'State', value: validationBadge(rec) }, { label: 'Profile', value: rec.profile || 'base R4', mono: true },
      { label: 'Errors / warnings', value: `${rec.errors ?? '—'} / ${rec.warnings ?? '—'}` }], { columns: 3 }),
    rec.reason ? note(rec.reason, { kind: 'warn' }) : null,
    (rec.notes || []).map((n) => note(n)),
    (rec.issues || []).length ? el('ul', {}, rec.issues.map((i) => el('li', {}, badge(i.severity, i.severity === 'error' ? 'danger' : 'warn'), ` ×${i.count} `, el('code', {}, i.text)))) : null));
  vHost.addEventListener('click', () => refresh().then(show).catch(() => {}));
  const validate = canValidate ? button('Validate now', { small: true, icon: 'shield', onClick: async () => {
    validate.disabled = true;
    try {
      await api.ui(`validation/reference/${patient.id}/${category}`, { method: 'POST' });
      let rec = await refresh();
      const t0 = Date.now();
      while (rec.state === 'pending' && Date.now() - t0 < 15 * 60_000) { await new Promise((r) => setTimeout(r, 4000)); rec = await refresh(); }
      show(rec);
    } finally { validate.disabled = false; }
  } }) : null;

  const head = section(null, el('div', { class: 'stack' },
    el('div', {},
      el('div', { class: 'eyebrow' }, meta ? meta.standard : 'FHIR document'),
      el('h1', {}, title, patient ? originBadge(patient, { withSource: false }) : null),
      patient ? el('div', { class: 'demo' }, el('a', { href: `#/patients/${patient.id}` }, humanName(patient)), ' · ', demographics(patient), ' ', countryBadge(patientCountry(patient))) : null),
    kv([
      { label: 'Document type', value: comp?.type?.coding?.[0] ? el('span', {}, ccText(comp.type), codingChip(comp.type.coding[0])) : '—' },
      { label: 'Profile', value: profile ? el('a', { href: profile, target: '_blank', rel: 'noopener' }, profile.split('/').pop()) : 'base FHIR R4 document', mono: !!profile },
      { label: 'Composed', value: fmtDate(comp?.date || bundle.timestamp) },
      { label: 'EU profile validation', value: el('span', { class: 'row' }, vHost, validate) },
    ], { columns: 4 })));

  const sections = (comp?.section || []).map((s) => {
    const refs = s.entry || [];
    const code = s.code?.coding?.[0];
    return el('div', { class: 'doc-section' },
      el('div', { class: 'doc-section-head' }, el('h3', {}, s.title || ccText(s.code) || 'Section'), code ? codingChip(code) : null, el('span', { class: 'n' }, `${refs.length}`)),
      refs.length
        ? el('div', { class: 'entries' }, refs.map((r) => { const res = resolveInBundle(bundle, r.reference);
          return res ? resourceRow(res, { onOpen: () => drawer.json(`${res.resourceType}/${res.id}`, res) })
            : el('div', { class: 'resource-row' }, el('span', { class: 'muted mono' }, `unresolved: ${r.reference}`)); }))
        : (s.text?.div ? sanitizedNarrative(s.text.div) : el('div', { class: 'narrative muted' }, 'Empty section.')));
  });

  return pageShell({
    children: [head,
      el('div', { class: 'stack' }, sections.length ? sections : emptyState('No sections.')),
      el('div', { class: 'btn-row' },
        button('Raw bundle', { icon: 'code', onClick: () => drawer.json(`Bundle/${bundle.id}`, bundle) }),
        copyButton(prettyJson(bundle), { label: 'Copy JSON' }),
        requestChip('GET', `/Bundle/${bundle.id}`),
        meta ? button(meta.standard, { icon: 'external', kind: 'ghost', href: meta.ig }) : null)],
  });
}
