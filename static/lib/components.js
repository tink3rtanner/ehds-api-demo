// The component inventory (docs/ui-design.md "Visual and component system").
// One implementation each; views compose these and never style inline.

import { el, clear, copyText, colorizeJson, prettyJson, fmtMs, fmtBytes, fmtDate, svg } from './dom.js';
import { icon } from './icons.js';
import * as api from './api.js';
import { codingLink, shortSystem, countryName, originOf, sourceHost } from './fhir.js';

// ---------- page shell ----------

/**
 * pageShell({ title, purpose, actions:[node], children:[node], wide })
 * Every section has the same shell: title, one-line purpose, actions on the
 * right, the content, and the requests drawer trigger at the bottom.
 */
export function pageShell({ title, purpose, actions = [], children = [], eyebrow = null, kicker = null } = {}) {
  const head = el('header', { class: 'page-head' },
    el('div', { class: 'page-head-text' },
      eyebrow ? el('div', { class: 'eyebrow' }, eyebrow) : null,
      el('h1', {}, title),
      purpose ? el('p', { class: 'purpose' }, purpose) : null,
    ),
    actions.length ? el('div', { class: 'page-actions' }, actions) : null,
  );
  const body = el('div', { class: 'page-body' }, children);
  const foot = requestsBar();
  return el('section', { class: 'page' }, kicker, head, body, foot);
}

function requestsBar() {
  const count = el('span', { class: 'count' }, '0');
  const list = el('div', { class: 'req-list' });
  const details = el('details', { class: 'requests-bar' },
    el('summary', {}, icon('code', { size: 16 }), el('span', {}, 'Requests behind this page'), count,
      el('span', { class: 'hint' }, 'every FHIR call this page made, through the public API with a visible bearer')),
    list,
  );
  const render = (reqs) => {
    count.textContent = String(reqs.length);
    clear(list);
    if (!reqs.length) { list.appendChild(el('div', { class: 'muted pad' }, 'No FHIR requests yet.')); return; }
    for (const r of reqs) list.appendChild(requestRow(r));
  };
  render(api.requests());
  const unsub = api.subscribe(render);
  details.addEventListener('toggle', () => { if (details.open) render(api.requests()); });
  // detach when removed from the DOM
  const obs = new MutationObserver(() => { if (!document.body.contains(details)) { unsub(); obs.disconnect(); } });
  obs.observe(document.body, { childList: true, subtree: true });
  if (technicalMode()) details.open = true;
  return details;
}

// ---------- technical mode ----------

const TECH_KEY = 'ehds.technical';
export function technicalMode() {
  try { return localStorage.getItem(TECH_KEY) === '1'; } catch { return false; }
}
export function setTechnicalMode(on) {
  try { localStorage.setItem(TECH_KEY, on ? '1' : '0'); } catch { /* private mode */ }
  document.documentElement.classList.toggle('technical', on);
}

// ---------- cards ----------

export function card({ title, sub, body, href, onClick, badge, actions, className = '', icon: ic, footer } = {}) {
  const tag = href ? 'a' : 'div';
  const node = el(tag, { class: `card ${className} ${href || onClick ? 'is-link' : ''}`.trim(), href, onclick: onClick },
    (title || badge || ic) ? el('div', { class: 'card-head' },
      ic ? el('span', { class: 'card-icon' }, icon(ic, { size: 18 })) : null,
      title ? el('div', { class: 'card-title' }, title) : null,
      badge ? el('div', { class: 'card-badge' }, badge) : null,
    ) : null,
    sub ? el('div', { class: 'card-sub' }, sub) : null,
    body ? el('div', { class: 'card-body' }, body) : null,
    actions ? el('div', { class: 'card-actions' }, actions) : null,
    footer ? el('div', { class: 'card-foot' }, footer) : null,
  );
  return node;
}

export function statTile(label, value, sub) {
  return el('div', { class: 'stat' },
    el('div', { class: 'stat-label' }, label),
    el('div', { class: 'stat-value' }, value == null ? '—' : String(value)),
    sub ? el('div', { class: 'stat-sub' }, sub) : null,
  );
}

export function section(title, children, { sub, actions, className = '' } = {}) {
  return el('section', { class: `block ${className}`.trim() },
    (title || actions) ? el('div', { class: 'block-head' },
      el('div', {}, title ? el('h2', {}, title) : null, sub ? el('p', { class: 'muted' }, sub) : null),
      actions ? el('div', { class: 'block-actions' }, actions) : null,
    ) : null,
    children,
  );
}

// ---------- key/value list ----------

/** kv([{label, value, mono, copy}]) */
export function kv(rows, { columns = 3 } = {}) {
  return el('dl', { class: `kv cols-${columns}` },
    rows.filter(Boolean).map((r) => el('div', { class: 'kv-row' },
      el('dt', {}, r.label),
      el('dd', { class: r.mono ? 'mono' : '' , title: typeof r.value === 'string' ? r.value : null },
        r.value == null || r.value === '' ? el('span', { class: 'muted' }, '—') : r.value,
        r.copy && typeof r.value === 'string' ? copyButton(r.value) : null),
    )),
  );
}

// ---------- badges ----------

export function badge(text, kind = 'neutral', { title, icon: ic } = {}) {
  return el('span', { class: `badge badge-${kind}`, title }, ic ? icon(ic, { size: 12 }) : null, text);
}

export function originBadge(res, { withSource = true } = {}) {
  const o = originOf(res);
  if (o.kind === 'reference') return badge('Reference', 'reference', { title: 'Seeded reference example' });
  if (o.kind === 'community') {
    const label = withSource && o.source ? `Community · ${sourceHost(o.source)}` : 'Community';
    return badge(label, 'community', { title: o.source ? `Submitted; source ${o.source}` : 'Community submission' });
  }
  return badge('Origin unknown', 'neutral');
}

export function methodBadge(method) {
  return el('span', { class: `method method-${(method || 'GET').toLowerCase()}` }, method || 'GET');
}

export function statusBadge(status) {
  const cls = status == null ? 'neutral' : status < 300 ? 'ok' : status < 400 ? 'info' : status < 500 ? 'warn' : 'danger';
  return badge(status == null ? '…' : String(status), cls);
}

export function validationBadge(v, { compact = false } = {}) {
  const state = v?.state || 'unknown';
  const map = {
    validated: ['ok', 'check', 'Validated', 'No errors against the EU profile'],
    failed: ['danger', 'x', `Failed${v?.errors != null ? ` · ${v.errors}` : ''}`, `${v?.errors ?? '?'} error(s) against the EU profile`],
    pending: ['info', 'clock', 'Validating…', 'Queued for the HL7 validator'],
    unavailable: ['warn', 'alert', 'Validator unavailable', v?.reason || 'The validator could not give an answer'],
    none: ['neutral', null, 'Not validated', 'No validation run yet'],
    unknown: ['neutral', null, 'Not validated', 'No validation record'],
  };
  const [kind, ic, label, title] = map[state] || map.unknown;
  return badge(compact ? label.split(' ')[0] : label, kind, { icon: ic, title });
}

export function countryBadge(code) {
  if (!code) return badge('Country unknown', 'neutral');
  return badge(code, 'country', { title: countryName(code) });
}

export function codingChip(coding) {
  if (!coding) return null;
  const href = codingLink(coding);
  const label = `${shortSystem(coding.system)} ${coding.code || ''}`.trim();
  const attrs = { class: 'coding', title: `${coding.system || ''} ${coding.code || ''} ${coding.display || ''}`.trim() };
  return href ? el('a', { ...attrs, href, target: '_blank', rel: 'noopener' }, label)
    : el('span', attrs, label);
}

// ---------- buttons ----------

export function button(label, { onClick, kind = '', icon: ic, href, title, disabled, small, type = 'button' } = {}) {
  const cls = `btn ${kind ? `btn-${kind}` : ''} ${small ? 'btn-sm' : ''}`.trim();
  if (href) return el('a', { class: cls, href, title, target: href.startsWith('http') ? '_blank' : null, rel: href.startsWith('http') ? 'noopener' : null }, ic ? icon(ic, { size: 15 }) : null, label);
  return el('button', { class: cls, onclick: onClick, title, disabled, type }, ic ? icon(ic, { size: 15 }) : null, label);
}

export function copyButton(text, { label = '' } = {}) {
  return el('button', { class: 'btn btn-ghost btn-sm copy', title: 'Copy', onclick: (e) => { e.preventDefault(); e.stopPropagation(); copyText(text); } },
    icon('copy', { size: 14 }), label);
}

// ---------- request chip + rows ----------

/** A request chip: method + path, click opens the drawer with the live response. */
export function requestChip(method, path, { onRun, token, label, run } = {}) {
  const chip = el('button', { class: 'req-chip', title: 'Run this request and show the response' },
    methodBadge(method), el('code', {}, path), icon('external', { size: 12, className: 'faint' }));
  chip.addEventListener('click', async () => {
    chip.classList.add('busy');
    try {
      if (onRun) { await onRun(); return; }
      const res = await api.fhir(path, { method, token, label, run, allowError: true, raw: true });
      drawer.json(`${method} ${path}`, res.json ?? res.text, { subtitle: `HTTP ${res.status}` });
    } catch (e) {
      drawer.open(`${method} ${path}`, errorBox(e.message));
    } finally {
      chip.classList.remove('busy');
    }
  });
  return chip;
}

export function requestRow(r) {
  const row = el('div', { class: `req-row ${r.error ? 'is-error' : ''}`, onclick: () => drawer.request(r) },
    methodBadge(r.method),
    el('code', { class: 'req-path' }, r.path),
    el('span', { class: 'req-meta' },
      r.label ? el('span', { class: 'req-label' }, r.label) : null,
      statusBadge(r.error ? 0 : r.status),
      el('span', { class: 'muted' }, fmtMs(r.ms)),
    ),
  );
  return row;
}

// ---------- code block ----------

export function codeBlock(text, { lang = '', copy = true, json = false, wrap = true, className = '' } = {}) {
  const body = json ? colorizeJson(typeof text === 'string' ? text : prettyJson(text)) : null;
  const pre = el('pre', { class: `code ${wrap ? 'wrap' : ''} ${className}`.trim(), 'data-lang': lang });
  const codeEl = el('code');
  if (body) codeEl.innerHTML = body; else codeEl.textContent = text;
  pre.appendChild(codeEl);
  return el('div', { class: 'code-wrap' }, pre,
    copy ? el('div', { class: 'code-tools' }, lang ? el('span', { class: 'code-lang' }, lang) : null,
      copyButton(typeof text === 'string' ? text : prettyJson(text))) : null);
}

// ---------- table ----------

/**
 * table({ columns:[{key,label,render,className,width}], rows, onRow, empty, pageSize })
 * Sticky header, paginated past pageSize, row click opens whatever the view says.
 */
export function table({ columns, rows, onRow, empty = 'Nothing here.', pageSize = 25, className = '', rowClass } = {}) {
  let page = 0;
  const host = el('div', { class: `table-wrap ${className}`.trim() });
  const render = () => {
    clear(host);
    if (!rows.length) { host.appendChild(emptyState(empty)); return; }
    const start = page * pageSize;
    const slice = rows.slice(start, start + pageSize);
    const t = el('table', { class: 'table' },
      el('thead', {}, el('tr', {}, columns.map((c) => el('th', { class: c.className || '', style: c.width ? `width:${c.width}` : null }, c.label)))),
      el('tbody', {}, slice.map((row, i) => el('tr', {
        class: `${onRow ? 'is-link' : ''} ${rowClass ? rowClass(row) : ''}`.trim(),
        onclick: onRow ? () => onRow(row, start + i) : null,
      }, columns.map((c) => el('td', { class: c.className || '' }, c.render ? c.render(row) : (row[c.key] ?? '')))))),
    );
    host.appendChild(t);
    if (rows.length > pageSize) {
      const pages = Math.ceil(rows.length / pageSize);
      host.appendChild(el('div', { class: 'pager' },
        button('Prev', { small: true, kind: 'ghost', disabled: page === 0, onClick: () => { page--; render(); } }),
        el('span', { class: 'muted' }, `${start + 1}–${Math.min(start + pageSize, rows.length)} of ${rows.length}`),
        button('Next', { small: true, kind: 'ghost', disabled: page >= pages - 1, onClick: () => { page++; render(); } }),
      ));
    }
  };
  render();
  return host;
}

// ---------- drawer ----------

function buildDrawer() {
  const title = el('div', { class: 'drawer-title' });
  const subtitle = el('div', { class: 'drawer-subtitle muted' });
  const tools = el('div', { class: 'drawer-tools' });
  const body = el('div', { class: 'drawer-body' });
  const panel = el('aside', { class: 'drawer', role: 'dialog', 'aria-modal': 'true', hidden: true },
    el('div', { class: 'drawer-head' },
      el('div', { class: 'drawer-head-text' }, title, subtitle),
      el('div', { class: 'drawer-actions' }, tools,
        el('button', { class: 'btn btn-ghost btn-sm', 'aria-label': 'Close', onclick: () => close() }, icon('x', { size: 16 }))),
    ),
    body,
  );
  const backdrop = el('div', { class: 'drawer-backdrop', hidden: true, onclick: () => close() });
  document.body.append(backdrop, panel);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panel.hidden) close(); });

  function open(t, content, { subtitle: st = '', tools: tl = [] } = {}) {
    title.textContent = t;
    subtitle.textContent = st;
    clear(tools); tools.append(...tl);
    clear(body); body.appendChild(content);
    panel.hidden = false; backdrop.hidden = false;
    document.body.classList.add('drawer-open');
  }
  function close() {
    panel.hidden = true; backdrop.hidden = true;
    document.body.classList.remove('drawer-open');
  }
  function json(t, obj, { subtitle: st = '' } = {}) {
    const text = typeof obj === 'string' ? obj : prettyJson(obj);
    open(t, codeBlock(text, { json: typeof obj !== 'string', copy: false, wrap: false, className: 'drawer-code' }),
      { subtitle: st, tools: [copyButton(text, { label: 'Copy JSON' })] });
  }
  function request(r) {
    const content = el('div', { class: 'req-detail' },
      kv([
        { label: 'Status', value: r.error ? `network error: ${r.error}` : `HTTP ${r.status}` },
        { label: 'Time', value: fmtMs(r.ms) },
        { label: 'Size', value: fmtBytes(r.bytes) },
        { label: 'Client', value: r.client, mono: true },
        { label: 'Run', value: r.run, mono: true },
        { label: 'At', value: fmtDate(r.at, { time: true }) },
      ], { columns: 3 }),
      el('h3', {}, 'Request'),
      codeBlock(`${r.method} ${r.path}\n${Object.entries(r.requestHeaders).map(([k, v]) => `${k}: ${v}`).join('\n')}${r.requestBody ? `\n\n${prettyJson(r.requestBody)}` : ''}`, { lang: 'http' }),
      el('h3', {}, 'Response'),
      typeof r.response === 'object' && r.response !== null
        ? codeBlock(prettyJson(r.response), { json: true, wrap: false })
        : codeBlock(String(r.response ?? ''), {}),
    );
    open(`${r.method} ${r.path}`, content, { subtitle: r.label || '' });
  }
  return { open, close, json, request, isOpen: () => !panel.hidden };
}

export const drawer = buildDrawer();

/** Open a resource fetched through the front door in the drawer. */
export async function openResource(type, id, { token } = {}) {
  const path = `/${type}/${id}`;
  try {
    const res = await api.fhir(path, { token, allowError: true, raw: true, label: 'open resource' });
    drawer.json(`${type}/${id}`, res.json ?? res.text, { subtitle: `GET ${path} · HTTP ${res.status}` });
  } catch (e) {
    drawer.open(`${type}/${id}`, errorBox(e.message));
  }
}

// ---------- stepper ----------

/** stepper(steps:[{title, short}], active, onSelect) */
export function stepper(steps, active, onSelect, { done = [] } = {}) {
  return el('ol', { class: 'stepper' }, steps.map((s, i) => el('li', {
    class: `step ${i === active ? 'is-active' : ''} ${done.includes(i) ? 'is-done' : ''}`.trim(),
    onclick: onSelect ? () => onSelect(i) : null,
  },
  el('span', { class: 'step-n' }, done.includes(i) && i !== active ? icon('check', { size: 12 }) : String(i + 1)),
  el('span', { class: 'step-t' }, s.short || s.title))));
}

// ---------- timeline ----------

export function timeline(events, { onSelect } = {}) {
  if (!events.length) return emptyState('No dated events in this record.');
  return el('ol', { class: 'timeline' }, events.map((e) => el('li', { class: 'tl-row', onclick: onSelect ? () => onSelect(e) : null },
    el('span', { class: 'tl-date mono' }, fmtDate(e.date)),
    el('span', { class: 'tl-icon' }, icon(e.icon, { size: 14 })),
    el('span', { class: 'tl-body' },
      el('span', { class: 'tl-label' }, e.label, e.status ? el('span', { class: 'tl-status' }, e.status) : null),
      e.detail ? el('span', { class: 'tl-detail' }, e.detail) : null),
    el('span', { class: 'tl-type mono' }, e.resource?.resourceType || ''),
  )));
}

// ---------- states ----------

export function emptyState(text, { action } = {}) {
  return el('div', { class: 'empty' }, el('p', {}, text), action || null);
}

export function spinner(text = 'Loading…') {
  return el('div', { class: 'spinner' }, el('span', { class: 'dot' }), el('span', {}, text));
}

export function errorBox(message, { retry } = {}) {
  return el('div', { class: 'error-box', role: 'alert' }, icon('alert', { size: 16 }),
    el('span', {}, message), retry ? button('Retry', { small: true, onClick: retry }) : null);
}

export function note(children, { kind = 'info' } = {}) {
  return el('div', { class: `note note-${kind}` }, children);
}

// ---------- QR ----------

export function qrImage(text, { size = 160, caption } = {}) {
  const url = `/ui/api/qr?text=${encodeURIComponent(text)}`;
  return el('figure', { class: 'qr' },
    el('img', { src: url, width: size, height: size, alt: `QR code for ${text}` }),
    caption ? el('figcaption', {}, caption) : null);
}

// ---------- map ----------

let mapCache = null;
/** Inline the Europe SVG (once) and hand back a fresh clone. */
export async function europeMap() {
  if (!mapCache) {
    const r = await fetch('/ui/assets/europe.svg');
    mapCache = await r.text();
  }
  const node = svg(mapCache);
  node.classList.add('europe');
  node.removeAttribute('width'); node.removeAttribute('height');
  node.setAttribute('role', 'img');
  node.setAttribute('aria-label', 'Map of Europe showing which countries have example data');
  return node;
}
