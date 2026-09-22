// FHIR-aware render helpers shared by several views (patient cards, resource
// rows, document rows, narrative sanitising). Generic widgets live in
// components.js; these know what a Patient or a DocumentReference is.

import { el, fmtDate } from './dom.js';
import { icon } from './icons.js';
import { badge, card, codingChip, countryBadge, originBadge, openResource } from './components.js';
import {
  CATEGORY_META, attachmentUrl, bundleIdFromAttachment, categoryOfDocRef, humanName, originOf,
  patientCity, patientCountry, pickCoding, pickDate, pickName, slotOf, statusOf, sourceHost,
} from './fhir.js';

export function patientLabel(p) {
  return humanName(p) || `Patient/${p?.id || '?'}`;
}

export function demographics(p) {
  const bits = [];
  if (p?.gender) bits.push(p.gender);
  if (p?.birthDate) bits.push(`born ${p.birthDate}`);
  const city = patientCity(p);
  if (city) bits.push(city);
  return bits.join(' · ');
}

export function patientCard(p, { href = `#/patients/${p.id}`, extra } = {}) {
  const o = originOf(p);
  const slot = slotOf(p);
  return card({
    href,
    className: 'patient-card',
    body: el('div', { class: 'stack', style: null },
      el('div', { class: 'row row-between' },
        el('span', { class: 'name' }, patientLabel(p)),
        countryBadge(patientCountry(p)),
      ),
      el('div', { class: 'demo' }, demographics(p)),
      el('div', { class: 'row' },
        originBadge(p, { withSource: false }),
        slot ? el('span', { class: 'chip mono' }, slot) : null,
        o.source ? el('span', { class: 'muted', style: null }, sourceHost(o.source)) : null,
        extra || null,
      ),
    ),
  });
}

export function resourceRow(res, { onOpen } = {}) {
  const coding = pickCoding(res);
  const status = statusOf(res);
  return el('div', { class: 'resource-row', onclick: onOpen || (() => openResource(res.resourceType, res.id)) },
    el('div', { class: 'label' },
      pickName(res) || el('em', { class: 'muted' }, '(no display)'),
      coding ? codingChip(coding) : null,
      status ? el('span', { class: 'coding' }, status) : null),
    el('div', { class: 'when' }, fmtDate(pickDate(res))),
  );
}

export function categoryLabel(cat) {
  return CATEGORY_META[cat]?.label || (cat ? cat.replace(/-/g, ' ') : 'Document');
}

export function categoryBadge(cat) {
  const meta = CATEGORY_META[cat];
  return badge(meta ? meta.short : (cat || 'other'), meta ? 'ok' : 'neutral', { icon: meta?.icon });
}

/** Where a DocumentReference's content can be opened in this UI, if it can. */
export function docHref(dr) {
  const id = bundleIdFromAttachment(attachmentUrl(dr));
  return id ? `#/documents/${id}` : null;
}

export function docCard(dr, { patient } = {}) {
  const cat = categoryOfDocRef(dr);
  const meta = CATEGORY_META[cat];
  const href = docHref(dr);
  return card({
    href,
    icon: meta?.icon || 'documents',
    title: categoryLabel(cat),
    badge: originBadge(dr, { withSource: false }),
    sub: [patient ? patientLabel(patient) : null, dr.date ? fmtDate(dr.date) : null].filter(Boolean).join(' · '),
    body: meta ? meta.blurb : (dr.description || ''),
    footer: href ? [icon('documents', { size: 13 }), el('span', {}, 'Open document')]
      : [icon('alert', { size: 13 }), el('span', {}, 'Content not held on this server')],
  });
}

/** Composition narrative (text.div) with anything active stripped out. */
export function sanitizedNarrative(div) {
  if (!div) return null;
  const allowed = new Set(['DIV', 'P', 'SPAN', 'B', 'STRONG', 'I', 'EM', 'UL', 'OL', 'LI', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TD', 'TH', 'BR', 'H1', 'H2', 'H3', 'H4', 'SMALL', 'CAPTION']);
  const doc = new DOMParser().parseFromString(`<div>${div}</div>`, 'text/html');
  const root = doc.body.firstElementChild;
  const walk = (node) => {
    for (const child of Array.from(node.children)) {
      if (!allowed.has(child.tagName)) { child.replaceWith(doc.createTextNode(child.textContent || '')); continue; }
      for (const a of Array.from(child.attributes)) child.removeAttribute(a.name);
      walk(child);
    }
  };
  walk(root);
  const out = el('div', { class: 'narrative' });
  out.innerHTML = root.innerHTML;
  return out;
}
