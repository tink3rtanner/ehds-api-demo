// Map: which European countries have example data on this server.

import { el, svg, replace } from '../lib/dom.js';
import * as api from '../lib/api.js';
import { pageShell, section, button, badge, kv, countryBadge, europeMap } from '../lib/components.js';
import { CATEGORIES, CATEGORY_META, countryName } from '../lib/fhir.js';
import { categoryLabel } from '../lib/render.js';

const HATCH = `<svg><defs><pattern id="hatch-ref" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
  <rect width="6" height="6" fill="#dcdcd6"/><line x1="0" y1="0" x2="0" y2="6" stroke="#8fa6b2" stroke-width="2"/></pattern></defs></svg>`;

const tierOf = (c) => (!c ? null : c.score > 0 ? `tier-${Math.min(5, c.score)}` : c.reference?.categories?.length ? 'tier-ref' : null);

export async function render({ country }) {
  const [cov, map] = await Promise.all([api.ui('coverage'), europeMap()]);
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
    el('span', {}, el('span', { class: 'legend-swatch', style: 'background: repeating-linear-gradient(45deg,#dcdcd6 0 3px,#8fa6b2 3px 5px)' }), 'seeded examples only'),
    ...[1, 3, 5].map((n) => el('span', {}, el('span', { class: 'legend-swatch', style: `background: var(--map-${n})` }), `${n}/5 categories`)));

  const panel = el('div', { class: 'stack' });
  const paintPanel = () => {
    const c = selected ? countries[selected] : null;
    if (!c) {
      replace(panel, el('p', { class: 'muted' }, selected ? `${countryName(selected)}: no data yet.` : 'Click a country to see its documents.'));
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
  const select = (code) => { selected = code; history.replaceState(null, '', code ? `#/coverage/${code}` : '#/coverage'); paintMap(); paintPanel(); };

  paintMap(); paintPanel();

  return pageShell({
    title: 'Map',
    purpose: 'Countries with example data on this server. Hatched: seeded examples. Coloured: published by a client.',
    children: [el('div', { class: 'coverage-layout' }, el('div', {}, wrap, legend), section(null, panel))],
  });
}
