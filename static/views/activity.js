// Activity — the request log. Debugging first, connectathon receipt second.
// Scanner noise is filtered out by default; a run id links straight back to
// what the scenario just did.

import { el, fmtDate, hashParams, setHashParams, debounce } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as api from '../lib/api.js';
import { pageShell, section, button, badge, statTile, table, methodBadge, statusBadge, drawer, kv, spinner, errorBox } from '../lib/components.js';

function field(label, input) {
  return el('div', { class: 'field' }, el('label', {}, label), input);
}

export async function render({ ctx }) {
  const p = hashParams();
  const scope = el('select', {}, ['fhir', 'ui', 'noise', 'all'].map((s) => el('option', { value: s, selected: (p.scope || 'fhir') === s }, s === 'fhir' ? 'FHIR API (default)' : s === 'ui' ? 'this UI' : s === 'noise' ? 'scanner noise' : 'everything')));
  const method = el('select', {}, ['', 'GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => el('option', { value: m, selected: (p.method || '') === m }, m || 'any method')));
  const status = el('select', {}, [['', 'any status'], ['2', '2xx'], ['3', '3xx'], ['4', '4xx'], ['5', '5xx']].map(([v, l]) => el('option', { value: v, selected: (p.status || '') === v }, l)));
  const client = el('input', { placeholder: 'client id', value: p.client_id || '' });
  const patient = el('input', { placeholder: 'patient id', value: p.patient || '' });
  const run = el('input', { placeholder: 'run id', value: p.run || '' });
  const days = el('select', {}, [1, 3, 7, 30].map((d) => el('option', { value: d, selected: Number(p.days || 7) === d }, `${d} day${d > 1 ? 's' : ''}`)));
  const auto = el('input', { type: 'checkbox' });

  const stats = el('div', { class: 'stats' }, spinner());
  const rows = el('div', {}, spinner('Reading the audit log…'));
  const summaryLine = el('p', { class: 'muted' });

  const params = () => {
    const q = { scope: scope.value, method: method.value, client_id: client.value.trim(), patient: patient.value.trim(), run: run.value.trim(), days: days.value, limit: 200 };
    if (status.value) { q.status_min = Number(status.value) * 100; q.status_max = Number(status.value) * 100 + 99; }
    return q;
  };
  const load = async () => {
    const q = params();
    setHashParams({ scope: q.scope === 'fhir' ? '' : q.scope, method: q.method, status: status.value, client_id: q.client_id, patient: q.patient, run: q.run, days: q.days === '7' ? '' : q.days });
    const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v !== '' && v != null)).toString();
    try {
      const [log, st] = await Promise.all([api.ui(`audit?${qs}`), api.ui(`audit/stats?days=${q.days}&scope=${q.scope}`)]);
      stats.replaceChildren(
        statTile('Requests', st.total, `${q.scope === 'all' ? 'all' : q.scope} · ${q.days} d`),
        statTile('2xx', st.by_status_class['2xx'] || 0, `${st.by_status_class['4xx'] || 0} × 4xx · ${st.by_status_class['5xx'] || 0} × 5xx`),
        statTile('Latency p50 / p95', st.latency_ms.p50 == null ? '—' : `${st.latency_ms.p50} / ${st.latency_ms.p95} ms`, `max ${st.latency_ms.max ?? '—'} ms`),
        statTile('Scanner noise', st.by_scope.noise || 0, 'requests hidden by the default scope'),
        statTile('Top client', st.top_clients[0]?.[0] || '—', st.top_clients[0] ? `${st.top_clients[0][1]} requests` : 'no bearer traffic'),
      );
      summaryLine.textContent = `${log.total} entr${log.total === 1 ? 'y' : 'ies'}${log.truncated ? ' (showing the most recent 200)' : ''}${q.run ? ` for run ${q.run}` : ''}.`;
      rows.replaceChildren(table({
        columns: [
          { label: 'Time', className: 'mono nowrap', render: (e) => fmtDate(e.ts, { time: true }).slice(5, 19) },
          { label: 'Method', render: (e) => methodBadge(e.method) },
          { label: 'Path', className: 'mono', render: (e) => el('span', {}, e.path, e.query ? el('span', { class: 'log-query' }, `?${e.query}`) : null) },
          { label: 'Status', render: (e) => statusBadge(e.status) },
          { label: 'ms', className: 'num mono', render: (e) => String(e.dur_ms ?? '') },
          { label: 'Client', className: 'mono', render: (e) => e.client_id || el('span', { class: 'muted' }, '—') },
          { label: 'Run', className: 'mono', render: (e) => e.run ? el('a', { href: `#/activity?run=${encodeURIComponent(e.run)}`, onclick: (ev) => { ev.stopPropagation(); run.value = e.run; load(); } }, e.run.slice(0, 14)) : '' },
        ],
        rows: log.entries,
        onRow: (e) => drawer.json(`${e.method} ${e.path}`, e, { subtitle: e.ts }),
        empty: q.run ? 'Nothing logged for this run yet. The audit file is written per request; try again in a moment.' : 'No requests match.',
        pageSize: 50,
        rowClass: (e) => (e.status >= 500 ? 'is-5xx' : e.status >= 400 ? 'is-4xx' : ''),
      }));
    } catch (e) { rows.replaceChildren(errorBox(e.message, { retry: load })); }
  };
  for (const c of [scope, method, status, days]) c.addEventListener('change', load);
  for (const c of [client, patient, run]) c.addEventListener('input', debounce(load, 300));
  let timer = null;
  auto.addEventListener('change', () => { clearInterval(timer); if (auto.checked) timer = setInterval(load, 8000); });
  const obs = new MutationObserver(() => { if (!document.body.contains(rows)) { clearInterval(timer); obs.disconnect(); } });
  obs.observe(document.body, { childList: true, subtree: true });

  const filters = section('Filter', el('div', { class: 'filters' },
    field('Scope', scope), field('Method', method), field('Status', status), field('Client', client), field('Patient', patient), field('Run', run), field('Window', days),
    el('div', { class: 'field' }, el('label', {}, 'Auto-refresh'), el('label', { class: 'row' }, auto, el('span', { class: 'muted' }, 'every 8 s')))),
  { actions: [button('Refresh', { small: true, icon: 'refresh', onClick: load }), button('Clear', { small: true, kind: 'ghost', onClick: () => { for (const c of [client, patient, run]) c.value = ''; scope.value = 'fhir'; method.value = ''; status.value = ''; load(); } })] });

  load();

  return pageShell({
    title: 'Activity',
    purpose: 'One line per request, written by the server as it happened. Scanner probes are hidden unless you ask for them; a run id shows exactly what the scenario did.',
    actions: [badge('audit log', 'neutral', { icon: 'activity' })],
    children: [stats, filters, section('Requests', el('div', { class: 'stack' }, summaryLine, rows), { sub: 'Bearer secrets are never written to the log; the client id is read from the token claims.' })],
  });
}
