// Coverage — the map of Europe, who has submitted what, and whether it
// validates. Attribution comes from the data (patient address country), never
// from the client. Reference examples are hatched; community data is solid.

import { el, fmtDate, hashParams, relTime, svg } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as api from '../lib/api.js';
import {
  pageShell, section, button, badge, kv, table, validationBadge, countryBadge, europeMap, statTile, emptyState,
  drawer, codeBlock, note, spinner, errorBox,
} from '../lib/components.js';
import { CATEGORIES, CATEGORY_META, countryName } from '../lib/fhir.js';
import { categoryBadge, categoryLabel } from '../lib/render.js';

const HATCH = `<defs><pattern id="hatch-ref" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
  <rect width="6" height="6" fill="#dcdcd6"/><line x1="0" y1="0" x2="0" y2="6" stroke="#8fa6b2" stroke-width="2"/></pattern></defs>`;

function tierOf(c) {
  if (!c) return null;
  if (c.score > 0) return `tier-${Math.min(5, c.score)}`;
  if (c.reference?.categories?.length) return 'tier-ref';
  return null;
}

export async function render({ country, ctx }) {
  const params = hashParams();
  const [cov, subsRes, mapNode] = await Promise.all([api.ui('coverage'), api.ui('submissions'), europeMap()]);
  const subs = subsRes.submissions;
  const countries = cov.countries;

  // ---- map ----
  mapNode.insertAdjacentElement('afterbegin', svg(`<svg>${HATCH}</svg>`).firstElementChild);
  const tip = el('div', { class: 'map-tip', hidden: true });
  const wrap = el('div', { class: 'map-wrap' }, mapNode, tip);
  let selected = country || null;
  const paintMap = () => {
    for (const path of mapNode.querySelectorAll('.country')) {
      const code = path.id;
      path.classList.remove('tier-ref', 'tier-1', 'tier-2', 'tier-3', 'tier-4', 'tier-5', 'is-selected');
      const t = tierOf(countries[code]);
      if (t) path.classList.add(t);
      if (code === selected) path.classList.add('is-selected');
      path.setAttribute('tabindex', '0');
      path.setAttribute('aria-label', `${path.dataset.name || code}: ${describe(countries[code])}`);
    }
  };
  const describe = (c) => {
    if (!c) return 'no data';
    const bits = [];
    if (c.community.submissions) bits.push(`${c.community.submissions} submission${c.community.submissions === 1 ? '' : 's'}, ${c.score}/5 categories`);
    if (c.reference.categories.length) bits.push('reference examples');
    return bits.join(' · ') || 'no data';
  };
  mapNode.addEventListener('click', (e) => {
    const p = e.target.closest('.country');
    if (!p) return;
    select(p.id === selected ? null : p.id);
  });
  mapNode.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.classList.contains('country')) select(e.target.id); });
  mapNode.addEventListener('mousemove', (e) => {
    const p = e.target.closest('.country');
    if (!p) { tip.hidden = true; return; }
    const r = wrap.getBoundingClientRect();
    tip.textContent = `${p.dataset.name || p.id} — ${describe(countries[p.id])}`;
    tip.style.left = `${e.clientX - r.left}px`;
    tip.style.top = `${e.clientY - r.top}px`;
    tip.hidden = false;
  });
  mapNode.addEventListener('mouseleave', () => { tip.hidden = true; });

  const legend = el('div', { class: 'map-legend' },
    el('span', {}, el('span', { class: 'legend-swatch', style: 'background: repeating-linear-gradient(45deg,#dcdcd6 0 3px,#8fa6b2 3px 5px)' }), 'reference examples only'),
    ...[1, 2, 3, 4, 5].map((n) => el('span', {}, el('span', { class: 'legend-swatch', style: `background: var(--map-${n})` }), `${n}/5 categories`)),
    el('span', {}, el('span', { class: 'legend-swatch', style: 'background: var(--map-none)' }), 'nothing yet'),
  );

  // ---- side panel ----
  const panel = el('div', { class: 'country-panel stack' });
  const ranking = () => {
    const rows = Object.values(countries).filter((c) => c.european && (c.community.submissions || c.reference.categories.length))
      .sort((a, b) => b.score - a.score || b.community.submissions - a.community.submissions || a.name.localeCompare(b.name));
    return rows.length ? el('ol', { class: 'ranking' }, rows.map((c) => el('li', { onclick: () => select(c.code) },
      el('span', {}, countryBadge(c.code), ' ', c.name),
      el('span', { class: 'row' }, el('span', { class: 'bar' }, el('span', { style: `width:${c.score * 20}%` })), el('span', { class: 'muted mono' }, `${c.score}/5`)),
    ))) : emptyState('No data yet.');
  };
  const paintPanel = () => {
    panel.replaceChildren();
    const c = selected ? countries[selected] : null;
    if (!selected || !c) {
      panel.append(
        el('h2', {}, selected ? `${countryName(selected)}: nothing yet` : 'Pick a country'),
        selected ? el('p', { class: 'muted' }, 'No example data has been submitted for this country. Be the first: publish a document whose patient lives here.') : el('p', { class: 'muted' }, 'Click the map or a row. Shading follows how many of the five priority categories have community data.'),
        el('div', { class: 'stats' },
          statTile('Submissions', cov.totals.submissions, `${cov.totals.validated_submissions} validated`),
          statTile('Countries with data', cov.totals.countries_with_community_data, `${cov.totals.complete_countries} complete`),
        ),
        el('h3', {}, 'Ranking'), ranking(),
        cov.non_european.length ? el('p', { class: 'muted' }, 'Outside the map: ', cov.non_european.map((code) => `${countryName(code)} (${countries[code].community.submissions})`).join(', ')) : null,
        cov.unknown_country_submissions ? el('p', { class: 'muted' }, `${cov.unknown_country_submissions} submission(s) with no recognisable country.`) : null,
      );
      return;
    }
    const cells = CATEGORIES.map((cat) => {
      const n = c.community.categories[cat] || 0;
      const ref = c.reference.categories.includes(cat);
      return el('div', { class: `cat-cell ${n ? 'has-com' : ref ? 'has-ref' : ''}`.trim(), title: `${categoryLabel(cat)}: ${n} community${ref ? ', reference example' : ''}` },
        el('span', { class: 'cat-n' }, n || (ref ? 'ref' : '–')), CATEGORY_META[cat].short);
    });
    panel.append(
      el('h2', {}, countryBadge(c.code), c.name, c.complete ? badge('complete', 'ok', { icon: 'check' }) : null),
      el('div', { class: 'cat-grid' }, cells),
      kv([
        { label: 'Community submissions', value: String(c.community.submissions) },
        { label: 'Validated', value: `${c.community.validated} ok · ${c.community.failed} failed · ${c.community.pending} pending · ${c.community.unavailable} unavailable` },
        { label: 'Source systems', value: c.community.sources.length ? c.community.sources.join(', ') : '—', mono: true },
        { label: 'Reference examples', value: c.reference.categories.length ? `${c.reference.categories.length} categories (seeded panel)` : 'none' },
      ], { columns: 2 }),
      el('p', { class: 'muted' }, c.complete ? 'All five categories have at least one validated document. This is what "green" means.'
        : `To complete ${c.name}: submit ${5 - c.score === 5 ? 'a document in each category' : `${5 - c.score} more categor${5 - c.score === 1 ? 'y' : 'ies'}`} and get them past the EU validator.`),
      el('div', { class: 'btn-row' }, button('Show all countries', { small: true, kind: 'ghost', onClick: () => select(null) })),
    );
  };
  const select = (code) => {
    selected = code;
    history.replaceState(null, '', code ? `#/coverage/${code}` : '#/coverage');
    paintMap(); paintPanel();
    subsTable.replaceChildren(buildSubsTable());
  };

  // ---- submissions ----
  const openSubmission = async (s) => {
    drawer.open(`Submission ${s.id.slice(0, 8)}…`, spinner());
    try {
      const d = await api.ui(`submissions/${s.id}`);
      const rec = d.validation_record;
      drawer.open(`Submission ${s.id.slice(0, 8)}…`, el('div', { class: 'stack' },
        kv([
          { label: 'Received', value: fmtDate(d.received, { time: true }) },
          { label: 'Category', value: categoryBadge(d.category) },
          { label: 'Country', value: el('span', {}, countryBadge(d.country), d.country_how ? el('span', { class: 'muted' }, ` from ${d.country_how}`) : null) },
          { label: 'Source system', value: d.source_host || '—', mono: true },
          { label: 'Bundle type', value: d.bundle_type, mono: true },
          { label: 'Entries', value: Object.entries(d.resource_types).map(([t, n]) => `${n} ${t}`).join(', ') },
        ], { columns: 2 }),
        el('div', { class: 'row' }, el('h3', {}, 'EU profile validation'), validationBadge(d.validation),
          button('Run again', { small: true, icon: 'refresh', onClick: async () => { await api.ui(`submissions/${s.id}/validate`, { method: 'POST' }); openSubmission(s); } })),
        rec?.reason ? note(rec.reason, { kind: 'warn' }) : null,
        rec?.profile ? el('p', { class: 'muted mono' }, rec.profile) : null,
        (rec?.issues || []).length ? el('ul', {}, rec.issues.map((i) => el('li', {}, badge(i.severity, i.severity === 'error' ? 'danger' : 'warn'), ` ×${i.count} `, el('code', {}, i.text)))) : null,
        el('h3', {}, `Resources stored (${d.resources.length})`),
        d.resources.length ? el('div', { class: 'row' }, d.resources.slice(0, 60).map((ref) => {
          const [t, rid] = ref.split('/');
          return t === 'Patient' ? el('a', { class: 'chip', href: `#/patients/${rid}`, onclick: () => drawer.close() }, ref)
            : el('code', { class: 'chip' }, ref);
        }), d.resources.length > 60 ? el('span', { class: 'muted' }, `+${d.resources.length - 60} more`) : null)
          : el('p', { class: 'muted' }, 'Nothing mirrored into the store from this bundle.'),
        el('p', { class: 'muted' }, 'Stored resources were re-identified on arrival; the original ids are kept as urn:ehds-demo:source-id identifiers and meta.source links back.'),
      ));
    } catch (e) { drawer.open('Submission', errorBox(e.message)); }
  };
  const buildSubsTable = () => {
    const rows = selected ? subs.filter((s) => s.country === selected) : subs;
    return table({
      columns: [
        { label: 'Received', className: 'mono nowrap', render: (s) => el('span', { title: s.received }, relTime(s.received) || fmtDate(s.received)) },
        { label: 'Country', render: (s) => el('span', {}, countryBadge(s.country), s.country ? ` ${s.country_name || ''}` : '') },
        { label: 'Category', render: (s) => categoryBadge(s.category) },
        { label: 'Source', className: 'mono', render: (s) => s.source_host || '—' },
        { label: 'Entries', className: 'num', render: (s) => String(s.entry_count) },
        { label: 'EU profile', render: (s) => validationBadge(s.validation) },
        { label: 'Id', className: 'mono', render: (s) => `${s.id.slice(0, 8)}…` },
      ],
      rows, onRow: openSubmission, pageSize: 20,
      empty: selected ? `No submissions for ${countryName(selected)} yet.` : 'No submissions yet.',
    });
  };
  const subsTable = el('div', {}, buildSubsTable());

  // ---- how to submit ----
  const base = ctx.base;
  const example = {
    resourceType: 'Bundle', type: 'transaction',
    entry: [
      { fullUrl: 'urn:uuid:11111111-1111-4111-8111-111111111111', resource: { resourceType: 'Patient', id: 'pat-1', name: [{ family: 'Novak', given: ['Eva'] }], birthDate: '1979-03-02', gender: 'female', address: [{ city: 'Ljubljana', country: 'SI' }] }, request: { method: 'POST', url: 'Patient' } },
      { fullUrl: 'urn:uuid:22222222-2222-4222-8222-222222222222', resource: { resourceType: 'AllergyIntolerance', id: 'alg-1', clinicalStatus: { coding: [{ system: 'http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical', code: 'active' }] }, code: { coding: [{ system: 'http://snomed.info/sct', code: '764146007', display: 'Penicillin' }] }, patient: { reference: 'urn:uuid:11111111-1111-4111-8111-111111111111' } }, request: { method: 'POST', url: 'AllergyIntolerance' } },
      { fullUrl: 'urn:uuid:33333333-3333-4333-8333-333333333333', resource: { resourceType: 'Composition', id: 'comp-1', status: 'final', type: { coding: [{ system: 'http://loinc.org', code: '60591-5', display: 'Patient summary Document' }] }, subject: { reference: 'urn:uuid:11111111-1111-4111-8111-111111111111' }, date: '2026-09-22T10:00:00Z', author: [{ display: 'Example Clinic' }], title: 'Patient Summary', section: [{ title: 'Allergies', code: { coding: [{ system: 'http://loinc.org', code: '48765-2' }] }, entry: [{ reference: 'urn:uuid:22222222-2222-4222-8222-222222222222' }] }] }, request: { method: 'POST', url: 'Composition' } },
    ],
  };
  const curl = `curl -sS -X POST ${base}/ \\\n  -H "Authorization: Bearer $TOKEN" \\\n  -H "Content-Type: application/fhir+json" \\\n  --data @bundle.json`;
  const howTo = section('Turn your country green', el('div', { class: 'split split-even' },
    el('div', { class: 'stack' },
      el('ol', { class: 'stack' },
        el('li', {}, el('a', { href: '#/connect' }, 'Register a client'), ' with scope ', el('code', {}, 'system/Bundle.write'), ' and mint a token (two minutes, no approval needed).'),
        el('li', {}, 'POST a Bundle to the base URL. Transaction or document type; the patient\'s ', el('code', {}, 'address.country'), ' decides which country it counts for.'),
        el('li', {}, 'You get a 201 and local ids immediately. The EU-profile validation runs afterwards and shows up here as a badge; a failed validation still counts as a submission, and you can see exactly why it failed.'),
      ),
      codeBlock(curl, { lang: 'bash' }),
      note(['Synthetic data only. Do not submit anything derived from a real person. Submissions are public and are re-identified on arrival (see ', el('a', { href: '/llms.txt', target: '_blank', rel: 'noopener' }, 'llms.txt'), ').'], { kind: 'warn' }),
    ),
    el('div', {}, el('div', { class: 'muted', style: null }, 'A minimal bundle that counts for Slovenia:'), codeBlock(example, { json: true, lang: 'bundle.json' })),
  ), { sub: 'Anyone can contribute example data for their country. Attribution comes from the data, not from who you are; no client details are shown.' });

  paintMap();
  paintPanel();
  if (params.submission) {
    const s = subs.find((x) => x.id === params.submission);
    if (s) openSubmission(s);
  }

  return pageShell({
    title: 'Coverage',
    purpose: 'Which countries have example data on this server, from which source systems, and whether it validates against the HL7 Europe profiles.',
    actions: [badge(`${cov.totals.submissions} submissions`, 'community'), badge(`${cov.totals.countries_with_reference_data} reference countries`, 'reference')],
    children: [
      el('div', { class: 'coverage-layout' }, el('div', {}, wrap, legend), section(null, panel)),
      section('Submissions', subsTable, { sub: selected ? `Filtered to ${countryName(selected)}. Click a row for the validation report and the resources it produced.` : 'Newest first. Click a row for the validation report and the resources it produced.' }),
      howTo,
    ],
  });
}
