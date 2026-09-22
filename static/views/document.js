// Document — one Bundle.type=document rendered as a readable document, with
// the raw bundle one click away and the EU-profile validation badge.

import { el, fmtDate, fmtBytes, prettyJson } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as api from '../lib/api.js';
import {
  pageShell, section, button, kv, badge, originBadge, countryBadge, codingChip, validationBadge, requestChip,
  drawer, emptyState, note, qrImage, copyButton, spinner, openResource,
} from '../lib/components.js';
import { CATEGORY_META, categoryOfComposition, humanName, patientCountry, resolveInBundle, ccText, pickName, originOf, enrichMedicationDisplays } from '../lib/fhir.js';
import { categoryLabel, demographics, resourceRow, sanitizedNarrative } from '../lib/render.js';

export async function render({ id, ctx }) {
  let bundle;
  try {
    bundle = await api.fhir(`/Bundle/${id}`, { label: 'ITI-68 retrieve document', pillar: 'retrieve-document' });
  } catch (e) {
    return pageShell({
      title: e.status === 404 ? 'Document not held here' : 'Could not retrieve document',
      purpose: e.status === 404
        ? 'This server compiles documents on demand for the reference panel. A community DocumentReference may point at a document that only its source system holds.'
        : e.message,
      children: [el('div', { class: 'btn-row' }, button('Back to documents', { href: '#/documents' }), requestChip('GET', `/Bundle/${id}`))],
    });
  }
  const entries = bundle.entry || [];
  enrichMedicationDisplays(entries.map((e) => e.resource).filter(Boolean));
  const comp = entries[0]?.resource?.resourceType === 'Composition' ? entries[0].resource : entries.find((e) => e.resource?.resourceType === 'Composition')?.resource;
  const patient = entries.find((e) => e.resource?.resourceType === 'Patient')?.resource;
  const category = categoryOfComposition(comp);
  const meta = CATEGORY_META[category];
  const profile = (bundle.meta?.profile || [])[0];
  const json = prettyJson(bundle);
  const sizeKb = fmtBytes(json.length);
  const typeCounts = entries.reduce((acc, e) => { const t = e.resource?.resourceType || '?'; acc[t] = (acc[t] || 0) + 1; return acc; }, {});

  // ---- validation badge (reference documents only: we know patient + category) ----
  const vHost = el('span', {}, validationBadge({ state: 'none' }));
  // compiled Compositions carry no tag of their own; the patient they were compiled for does
  const origin = originOf(patient || comp);
  const canValidate = !!(patient && category && origin.kind === 'reference');
  const refresh = async () => {
    const rec = await api.ui(`validation/reference/${patient.id}/${category}`);
    vHost.replaceChildren(validationBadge(rec));
    return rec;
  };
  if (canValidate) refresh().catch(() => {});
  const validateBtn = canValidate ? button('Validate now', { small: true, icon: 'shield', onClick: async () => {
    validateBtn.disabled = true;
    try {
      await api.ui(`validation/reference/${patient.id}/${category}`, { method: 'POST' });
      let rec = await refresh();
      const t0 = Date.now();
      while (rec.state === 'pending' && Date.now() - t0 < 10 * 60_000) {
        await new Promise((r) => setTimeout(r, 3000));
        rec = await refresh();
      }
      showValidation(rec);
    } finally { validateBtn.disabled = false; }
  } }) : null;
  const showValidation = (rec) => {
    if (!rec || rec.state === 'none') return;
    drawer.open('EU profile validation', el('div', { class: 'stack' },
      kv([{ label: 'State', value: validationBadge(rec) }, { label: 'Profile', value: rec.profile || 'base R4', mono: true },
        { label: 'Errors', value: String(rec.errors ?? '—') }, { label: 'Warnings', value: String(rec.warnings ?? '—') },
        { label: 'Attempts', value: String(rec.attempts ?? 1) }, { label: 'Updated', value: fmtDate(rec.updated, { time: true }) }], { columns: 3 }),
      rec.reason ? note(rec.reason, { kind: 'warn' }) : null,
      (rec.issues || []).length ? el('div', {}, el('h3', {}, 'Distinct issues'), el('ul', {}, rec.issues.map((i) => el('li', {}, badge(i.severity, i.severity === 'error' ? 'danger' : 'warn'), ` ×${i.count} `, el('code', {}, i.text))))) : null,
    ));
  };
  vHost.addEventListener('click', () => refresh().then(showValidation).catch(() => {}));

  // ---- header ----
  const head = section(null, el('div', { class: 'doc-head' },
    el('div', {},
      el('div', { class: 'eyebrow' }, meta ? meta.standard : 'FHIR document'),
      el('h1', {}, icon(meta?.icon || 'documents', { size: 24 }), docTitle(), patient ? originBadge(patient, { withSource: false }) : null),
      patient ? el('div', { class: 'demo' }, el('a', { href: `#/patients/${patient.id}` }, humanName(patient)), ' · ', demographics(patient), ' ', countryBadge(patientCountry(patient))) : null,
      el('div', { class: 'chips' },
        el('span', { class: 'chip' }, el('span', { class: 'n' }, entries.length), 'entries'),
        ...Object.entries(typeCounts).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([t, n]) => el('span', { class: 'chip' }, el('span', { class: 'n' }, n), t)),
      ),
    ),
    qrImage(`${ctx.base}/ui/#/documents/${bundle.id}`, { size: 120, caption: 'This document on a phone' }),
  ));
  head.appendChild(kv([
    { label: 'Bundle', value: `Bundle/${bundle.id}`, mono: true, copy: true },
    { label: 'Document type', value: comp?.type?.coding?.[0] ? el('span', {}, ccText(comp.type), codingChip(comp.type.coding[0])) : '—' },
    { label: 'Profile', value: profile ? el('a', { href: profile, target: '_blank', rel: 'noopener' }, profile) : 'base FHIR R4 document (no EU profile in R4)', mono: !!profile },
    { label: 'EU profile validation', value: el('span', { class: 'row' }, vHost, validateBtn) },
    { label: 'Composed', value: fmtDate(comp?.date || bundle.timestamp, { time: true }) },
    { label: 'Author', value: (comp?.author || []).map((a) => a.display || pickName(resolveInBundle(bundle, a.reference)) || a.reference).join(', ') },
    { label: 'Custodian', value: comp?.custodian ? (comp.custodian.display || pickName(resolveInBundle(bundle, comp.custodian.reference)) || comp.custodian.reference) : null },
    { label: 'Size', value: sizeKb },
  ], { columns: 4 }));

  // ---- sections ----
  const sections = (comp?.section || []).map((s) => {
    const refs = s.entry || [];
    const code = s.code?.coding?.[0];
    const resolved = refs.map((r) => resolveInBundle(bundle, r.reference));
    return el('div', { class: 'doc-section' },
      el('div', { class: 'doc-section-head' },
        el('h3', {}, s.title || ccText(s.code) || 'Section'),
        code ? codingChip(code) : null,
        el('span', { class: 'n' }, `${refs.length} ${refs.length === 1 ? 'entry' : 'entries'}`)),
      refs.length
        ? el('div', { class: 'entries' }, resolved.map((r, i) => r ? resourceRow(r, { onOpen: () => drawer.json(`${r.resourceType}/${r.id}`, r, { subtitle: 'from the document bundle' }) })
          : el('div', { class: 'resource-row' }, el('span', { class: 'muted mono' }, `unresolved: ${refs[i].reference}`))))
        : (s.text?.div ? sanitizedNarrative(s.text.div) : el('div', { class: 'narrative muted' }, 'No entries in this section.')),
    );
  });

  const actions = el('div', { class: 'btn-row' },
    button('View raw Bundle', { icon: 'code', onClick: () => drawer.json(`Bundle/${bundle.id}`, bundle, { subtitle: `GET /Bundle/${bundle.id}` }) }),
    copyButton(json, { label: 'Copy JSON' }),
    requestChip('GET', `/Bundle/${bundle.id}`),
    meta ? button(`${meta.standard} IG`, { icon: 'external', kind: 'ghost', href: meta.ig }) : null,
  );

  function docTitle() {
    // the compiler titles documents "<type> - <patient uuid>"; people want the category and the person
    const t = comp?.title || '';
    if (!t || /[0-9a-f]{8}-[0-9a-f]{4}/i.test(t)) return `${categoryLabel(category)}${patient ? ` · ${humanName(patient)}` : ''}`;
    return t;
  }

  return pageShell({
    eyebrow: null,
    title: null,
    children: [
      head,
      el('div', { class: 'stack' }, sections.length ? sections : emptyState('This bundle has no Composition sections.')),
      section(null, actions, { className: 'block-plain' }),
    ],
  });
}
