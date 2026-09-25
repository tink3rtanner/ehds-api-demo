// Activity — the request log. Scanner noise hidden by default; filter by client or run.

import { el, fmtDate, hashParams, setHashParams, debounce, replace } from '../lib/dom.js';
import * as api from '../lib/api.js';
import { pageShell, section, button, statTile, table, methodBadge, statusBadge, drawer, errorBox } from '../lib/components.js';

export async function render() {
  const p = hashParams();
  const scope = el('select', {}, [['fhir', 'FHIR API'], ['ui', 'this UI'], ['noise', 'scanner noise'], ['all', 'everything']].map(([v, l]) => el('option', { value: v, selected: (p.scope || 'fhir') === v }, l)));
  const client = el('input', { placeholder: 'client id', value: p.client_id || '' });
  const run = el('input', { placeholder: 'run id', value: p.run || '' });
  const stats = el('div', { class: 'stats' });
  const rows = el('div', {});

  const load = async () => {
    const q = { scope: scope.value, client_id: client.value.trim(), run: run.value.trim(), limit: 200, days: 7 };
    setHashParams({ scope: q.scope === 'fhir' ? '' : q.scope, client_id: q.client_id, run: q.run });
    const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v !== '')).toString();
    try {
      const [log, st] = await Promise.all([api.ui(`audit?${qs}`), api.ui(`audit/stats?days=7&scope=${q.scope}`)]);
      replace(stats, 
        statTile('Requests · 7 d', st.total),
        statTile('4xx / 5xx', `${st.by_status_class['4xx'] || 0} / ${st.by_status_class['5xx'] || 0}`),
        statTile('Latency p50 / p95', st.latency_ms.p50 == null ? '—' : `${st.latency_ms.p50} / ${st.latency_ms.p95} ms`),
        statTile('Scanner noise hidden', st.by_scope.noise || 0));
      replace(rows, table({
        columns: [
          { label: 'Time', className: 'mono nowrap', render: (e) => fmtDate(e.ts, { time: true }).slice(5, 19) },
          { label: 'Method', render: (e) => methodBadge(e.method) },
          { label: 'Path', className: 'mono', render: (e) => el('span', {}, e.path, e.query ? el('span', { class: 'log-query' }, `?${e.query}`) : null) },
          { label: 'Status', render: (e) => statusBadge(e.status) },
          { label: 'ms', className: 'num mono', render: (e) => String(e.dur_ms ?? '') },
          { label: 'Client', className: 'mono', render: (e) => e.client_id || '—' },
          { label: 'Run', className: 'mono', render: (e) => e.run ? el('a', { href: '#', onclick: (ev) => { ev.preventDefault(); ev.stopPropagation(); run.value = e.run; load(); } }, e.run.slice(0, 14)) : '' },
        ],
        rows: log.entries, onRow: (e) => drawer.json(`${e.method} ${e.path}`, e), pageSize: 50,
        empty: q.run ? 'Nothing logged for this run yet.' : 'No requests match.',
      }));
    } catch (e) { replace(rows, errorBox(e.message, { retry: load })); }
  };
  scope.addEventListener('change', load);
  client.addEventListener('input', debounce(load, 300));
  run.addEventListener('input', debounce(load, 300));
  load();

  const filters = el('div', { class: 'filters' },
    el('div', { class: 'field' }, el('label', {}, 'Scope'), scope),
    el('div', { class: 'field' }, el('label', {}, 'Client'), client),
    el('div', { class: 'field' }, el('label', {}, 'Run'), run),
    el('div', { class: 'field' }, el('label', {}, ' '), button('Refresh', { icon: 'refresh', onClick: load })));

  return pageShell({ title: 'Activity', purpose: 'One line per request, as the server logged it.', children: [stats, filters, section(null, rows)] });
}
