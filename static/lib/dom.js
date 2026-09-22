// DOM helpers shared by every view. No framework: `el()` builds nodes, the
// rest are small formatting utilities. Views import from lib/ only.

/** el('div', {class:'x', onclick: fn, 'data-id': 1}, child, [more, children]) */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'html') node.innerHTML = v;
    else if (v === true) node.setAttribute(k, '');
    else node.setAttribute(k, String(v));
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === '') continue;
    node.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function fragment(...children) {
  const f = document.createDocumentFragment();
  append(f, children);
  return f;
}

/** SVG from a markup string (used for the inline icon set and the map). */
export function svg(markup) {
  const tpl = document.createElement('template');
  tpl.innerHTML = markup.trim();
  return tpl.content.firstElementChild;
}

export const escapeHtml = (s) => String(s ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export function fmtDate(iso, { time = false } = {}) {
  if (!iso) return '';
  const s = String(iso);
  if (!time) return s.slice(0, 10);
  return s.replace('T', ' ').replace(/\.\d+/, '').replace(/(\+00:00|Z)$/, ' UTC').slice(0, 23);
}

export function fmtBytes(n) {
  if (n == null) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtMs(ms) {
  if (ms == null) return '';
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function relTime(iso) {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return String(iso);
  const d = Math.round((Date.now() - t) / 1000);
  if (d < 60) return `${d}s ago`;
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  return `${Math.round(d / 86400)} d ago`;
}

export function truncate(s, n = 24) {
  s = String(s ?? '');
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export function shortId(id) {
  return id ? `${String(id).slice(0, 8)}…` : '';
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied');
    return true;
  } catch {
    const ta = el('textarea', { style: 'position:fixed;opacity:0' }, text);
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
    toast(ok ? 'Copied' : 'Copy failed');
    return ok;
  }
}

let toastTimer = null;
export function toast(msg, kind = '') {
  let host = document.getElementById('toast');
  if (!host) {
    host = el('div', { id: 'toast', class: 'toast', role: 'status' });
    document.body.appendChild(host);
  }
  host.textContent = msg;
  host.className = `toast ${kind}`.trim();
  host.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { host.hidden = true; }, 2200);
}

export function download(filename, text, type = 'text/plain') {
  const blob = new Blob([text], { type });
  const a = el('a', { href: URL.createObjectURL(blob), download: filename });
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
}

/** Query-string style params after a hash route: '#/activity?run=x' -> {run:'x'} */
export function hashParams() {
  const q = location.hash.split('?')[1] || '';
  return Object.fromEntries(new URLSearchParams(q).entries());
}

export function setHashParams(obj) {
  const [path] = location.hash.split('?');
  const qs = new URLSearchParams(Object.entries(obj).filter(([, v]) => v !== '' && v != null)).toString();
  const next = qs ? `${path}?${qs}` : path;
  if (next !== location.hash) history.replaceState(null, '', next);
}

export function debounce(fn, ms = 200) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function prettyJson(obj) {
  return JSON.stringify(obj, null, 2);
}

/** Syntax-tint a JSON string as HTML (keys, strings, numbers, literals). */
export function colorizeJson(text) {
  const esc = escapeHtml(text);
  return esc.replace(
    /("(\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
    (m) => {
      let cls = 'j-num';
      if (m.startsWith('"')) cls = m.endsWith(':') ? 'j-key' : 'j-str';
      else if (/true|false|null/.test(m)) cls = 'j-lit';
      return `<span class="${cls}">${m}</span>`;
    },
  );
}

export function isPhone() {
  return window.matchMedia('(max-width: 720px)').matches;
}
