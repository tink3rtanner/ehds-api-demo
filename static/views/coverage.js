// Coverage — the map, the submissions, how to add yours.

import { el, fmtDate, hashParams, relTime, svg, replace } from '../lib/dom.js';
import * as api from '../lib/api.js';
import { pageShell, section, button, badge, kv, table, validationBadge, countryBadge, europeMap, emptyState, drawer, codeBlock, note, spinner, errorBox } from '../lib/components.js';
import { CATEGORIES, CATEGORY_META, countryName } from '../lib/fhir.js';
import { categoryBadge, categoryLabel } from '../lib/render.js';

const HATCH = `<svg><defs><pattern id="hatch-ref" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
  <rect width="6" height="6" fill="#dcdcd6"/><line x1="0" y1="0" x2="0" y2="6" stroke="#8fa6b2" stroke-width="2"/></pattern></defs></svg>`;

const tierOf = (c) => (!c ? null : c.score > 0 ? `tier-${Math.min(5, c.score)}` : c.reference?.categories?.length ? 'tier-ref' : null);

const EXAMPLE = {
  resourceType: 'Bundle', type: 'transaction', entry: [
    { fullUrl: 'urn:uuid:11111111-1111-4111-8111-111111111111', resource: { resourceType: 'Patient', id: 'pat-1', name: [{ family: 'Novak', given: ['Eva'] }], birthDate: '1979-03-02', gender: 'female', address: [{ city: 'Ljubljana', country: 'SI' }] }, request: { method: 'POST', url: 'Patient' } },
    { fullUrl: 'urn:uuid:22222222-2222-4222-8222-222222222222', resource: { resourceType: 'AllergyIntolerance', id: 'alg-1', clinicalStatus: { coding: [{ system: 'http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical', code: 'active' }] }, code: { coding: [{ system: 'http://snomed.info/sct', code: '764146007', display: 'Penicillin' }] }, patient: { reference: 'urn:uuid:11111111-1111-4111-8111-111111111111' } }, request: { method: 'POST', url: 'AllergyIntolerance' } },
    { fullUrl: 'urn:uuid:33333333-3333-4333-8333-333333333333', resource: { resourceType: 'Composition', id: 'comp-1', status: 'final', type: { coding: [{ system: 'http://loinc.org', code: '60591-5', display: 'Patient summary Document' }] }, subject: { reference: 'urn:uuid:11111111-1111-4111-8111-111111111111' }, date: '2026-09-22T10:00:00Z', author: [{ display: 'Example Clinic' }], title: 'Patient Summary', section: [{ title: 'Allergies', code: { coding: [{ system: 'http://loinc.org', code: '48765-2' }] }, entry: [{ reference: 'urn:uuid:22222222-2222-4222-8222-222222222222' }] }] }, request: { method: 'POST', url: 'Composition' } },
  ],
};

export async function render({ country, ctx }) {
  const [cov, subsRes, map] = await Promise.all([api.ui('coverage'), api.ui('submissions'), europeMap()]);
  const subs = subsRes.submissions;
  const countries = cov.countries;
  let selected = country || null;

  map.insertAdjacentElement('afterbegin', svg(HATCH).firstElementChild);
  const tip = el('div', { class: 'map-tip', hidden: true });
  const wrap = el('div', { class: 'map-wrap' }, map, tip);
  const describe = (c) => (!c ? 'nothing yet' : c.community.submissions ? `${c.community.submissions} submissions · ${c.score}/5 categories` : 'reference examples');
  const paintMap = () => {
    for (const path of map.querySelectorAll('.country')) {
      path.classList.remove('tier-ref', 'tier-1', 'tier-2', 'tier-3', 'tier-4', 'tier-5', 'is-selected');
      const t = tierOf(countries[path.id]);
      if (t) path.classList.add(t);
      if (path.id === selected) path.classList.add('is-selected');
    }
  };
  map.addEventListener('click', (e) => { const p = e.target.closest('.country'); if (p) select(p.id === selected ? null : p.id); });
  map.addEventListener('mousemove', (e) => {
    const p = e.target.closest('.country');
    if (!p) { tip.hidden = true; return; }
    const r = wrap.getBoundingClientRect();
    tip.textContent = `${p.dataset.name || p.id} — ${describe(countries[p.id])}`;
    tip.style.left = `${e.clientX - r.left}px`; tip.style.top = `${e.clientY - r.top}px`; tip.hidden = false;
  });
  map.addEventListener('mouseleave', () => { tip.hidden = true; });
  const legend = el('div', { class: 'map-legend' },
    el('span', {}, el('span', { class: 'legend-swatch', style: 'background: repeating-linear-gradient(45deg,#dcdcd6 0 3px,#8fa6b2 3px 5px)' }), 'reference only'),
    ...[1, 3, 5].map((n) => el('span', {}, el('span', { class: 'legend-swatch', style: `background: var(--map-${n})` }), `${n}/5 categories`)));

  const panel = el('div', { class: 'stack' });
  const paintPanel = () => {
    const c = selected ? countries[selected] : null;
    if (!c) {
      const ranked = Object.values(countries).filter((x) => x.european && x.community.submissions).sort((a, b) => b.score - a.score || b.community.submissions - a.community.submissions);
      replace(panel, 
        el('h2', {}, selected ? `${countryName(selected)}: nothing yet` : 'Click a country'),
        el('p', { class: 'muted' }, `${cov.totals.submissions} submissions · ${cov.totals.countries_with_community_data} countries with community data · ${cov.totals.validated_submissions} validated.`),
        ranked.length ? el('ol', { class: 'ranking' }, ranked.map((x) => el('li', { onclick: () => select(x.code) }, el('span', {}, countryBadge(x.code), ' ', x.name), el('span', { class: 'mono muted' }, `${x.score}/5`)))) : null,
        cov.non_european.length ? el('p', { class: 'muted' }, 'Outside the map: ', cov.non_european.map((code) => `${countryName(code)} (${countries[code].community.submissions})`).join(', ')) : null);
      return;
    }
    replace(panel, 
      el('h2', {}, countryBadge(c.code), c.name, c.complete ? badge('complete', 'ok', { icon: 'check' }) : null),
      el('div', { class: 'cat-grid' }, CATEGORIES.map((cat) => {
        const n = c.community.categories[cat] || 0;
        const ref = c.reference.categories.includes(cat);
        return el('div', { class: `cat-cell ${n ? 'has-com' : ref ? 'has-ref' : ''}`.trim(), title: categoryLabel(cat) }, el('span', { class: 'cat-n' }, n || (ref ? 'ref' : '–')), CATEGORY_META[cat].short);
      })),
      kv([
        { label: 'Submissions', value: `${c.community.submissions} · ${c.community.validated} validated · ${c.community.failed} failed` },
        { label: 'Sources', value: c.community.sources.join(', ') || '—', mono: true },
      ], { columns: 2 }),
      button('All countries', { small: true, kind: 'ghost', onClick: () => select(null) }));
  };
  const subsHost = el('div', {});
  const select = (code) => { selected = code; history.replaceState(null, '', code ? `#/coverage/${code}` : '#/coverage'); paintMap(); paintPanel(); replace(subsHost, subsTable()); };

  const openSubmission = async (s) => {
    drawer.open(`Submission ${s.id.slice(0, 8)}…`, spinner());
    try {
      const d = await api.ui(`submissions/${s.id}`);
      const rec = d.validation_record;
      drawer.open(`Submission ${s.id.slice(0, 8)}…`, el('div', { class: 'stack' },
        kv([{ label: 'Received', value: fmtDate(d.received, { time: true }) }, { label: 'Category', value: categoryBadge(d.category) },
          { label: 'Country', value: countryBadge(d.country) }, { label: 'Source', value: d.source_host || '—', mono: true },
          { label: 'Entries', value: Object.entries(d.resource_types).map(([t, n]) => `${n} ${t}`).join(', ') }], { columns: 2 }),
        el('div', { class: 'row' }, validationBadge(d.validation), button('Validate again', { small: true, icon: 'refresh', onClick: async () => { await api.ui(`submissions/${s.id}/validate`, { method: 'POST' }); openSubmission(s); } })),
        rec?.reason ? note(rec.reason, { kind: 'warn' }) : null,
        (rec?.notes || []).map((n) => note(n)),
        (rec?.issues || []).length ? el('ul', {}, rec.issues.map((i) => el('li', {}, badge(i.severity, i.severity === 'error' ? 'danger' : 'warn'), ` ×${i.count} `, el('code', {}, i.text)))) : null,
        el('div', { class: 'row' }, d.resources.slice(0, 40).map((ref) => { const [t, rid] = ref.split('/');
          return t === 'Patient' ? el('a', { class: 'chip', href: `#/patients/${rid}`, onclick: () => drawer.close() }, ref) : el('code', { class: 'chip' }, ref); }))));
    } catch (e) { drawer.open('Submission', errorBox(e.message)); }
  };
  const subsTable = () => table({
    columns: [
      { label: 'Received', className: 'mono nowrap', render: (s) => el('span', { title: s.received }, relTime(s.received) || fmtDate(s.received)) },
      { label: 'Country', render: (s) => countryBadge(s.country) },
      { label: 'Category', render: (s) => categoryBadge(s.category) },
      { label: 'Source', className: 'mono', render: (s) => s.source_host || '—' },
      { label: 'EU profile', render: (s) => validationBadge(s.validation) },
    ],
    rows: selected ? subs.filter((s) => s.country === selected) : subs, onRow: openSubmission, pageSize: 15,
    empty: 'No submissions yet.',
  });
  subsHost.appendChild(subsTable());

  const howTo = section('Turn your country green', el('div', { class: 'stack' },
    el('p', {}, el('a', { href: '#/connect' }, 'Register a client'), ' with ', el('code', {}, 'system/Bundle.write'), ', then POST a transaction or document Bundle to the base URL. The submission counts for the country in the patient\'s address. Validation against the EU profile runs afterwards and shows here as a badge.'),
    codeBlock(`curl -sS -X POST ${ctx.base}/ -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/fhir+json" --data @bundle.json`, { lang: 'bash' }),
    el('div', { class: 'btn-row' }, button('Example bundle', { small: true, icon: 'code', onClick: () => drawer.json('bundle.json', EXAMPLE) }),
      el('span', { class: 'muted' }, 'Synthetic data only.'))));

  paintMap(); paintPanel();
  const p = hashParams();
  if (p.submission) { const s = subs.find((x) => x.id === p.submission); if (s) openSubmission(s); }

  return pageShell({
    title: 'Coverage',
    purpose: 'Which countries have example data on this server, and whether it validates against the EU profiles.',
    children: [el('div', { class: 'coverage-layout' }, el('div', {}, wrap, legend), section(null, panel)), section('Submissions', subsHost), howTo],
  });
}
