// Router + boot. Views live in views/*.js and export render(params) -> Node|Promise<Node>.
// Shared state that crosses views (examples, server info) is loaded here and
// passed in; views never read globals.

import { el, clear } from './lib/dom.js';
import { icon } from './lib/icons.js';
import { spinner, errorBox, drawer } from './lib/components.js';
import * as api from './lib/api.js';

const app = document.getElementById('app');
const navToggle = document.getElementById('nav-toggle');
const nav = document.getElementById('nav');

navToggle.appendChild(icon('menu', { size: 20 }));
navToggle.addEventListener('click', () => {
  const open = nav.classList.toggle('is-open');
  navToggle.setAttribute('aria-expanded', String(open));
});
nav.addEventListener('click', (e) => { if (e.target.closest('a')) nav.classList.remove('is-open'); });

// ---------- shared context ----------

const ctx = {
  examples: null,   // /ui/api/examples — live ids for the reference patient + documents
  info: null,       // /ui/api/server-info
  base: location.origin,
};

async function loadContext() {
  if (ctx.examples && ctx.info) return ctx;
  const [examples, info] = await Promise.all([api.ui('examples'), api.ui('server-info')]);
  ctx.examples = examples;
  ctx.info = info;
  ctx.base = info.base_url || location.origin;
  return ctx;
}

// ---------- routes ----------

const ROUTES = [
  { re: /^#\/?$/, view: 'story', nav: 'story' },
  { re: /^#\/scenario(?:\/(\d+))?$/, view: 'scenario', nav: 'story', params: (m) => ({ step: m[1] ? Number(m[1]) : 0 }) },
  { re: /^#\/patients$/, view: 'patients', nav: 'patients' },
  { re: /^#\/patients\/([^/?]+)$/, view: 'patient', nav: 'patients', params: (m) => ({ id: m[1] }) },
  { re: /^#\/documents$/, view: 'documents', nav: 'documents' },
  { re: /^#\/documents\/([^/?]+)$/, view: 'document', nav: 'documents', params: (m) => ({ id: m[1] }) },
  { re: /^#\/coverage(?:\/([A-Za-z]{2}))?$/, view: 'coverage', nav: 'coverage', params: (m) => ({ country: m[1] ? m[1].toUpperCase() : null }) },
  { re: /^#\/connect$/, view: 'connect', nav: 'connect' },
  { re: /^#\/activity$/, view: 'activity', nav: 'activity' },
];

// routes from the previous UI keep working
const LEGACY = [
  [/^#\/p\/([^/]+)\/doc\/([^/]+)$/, (m) => `#/patients/${m[1]}`],
  [/^#\/p\/([^/]+)$/, (m) => `#/patients/${m[1]}`],
  [/^#\/(logs|audit)$/, () => '#/activity'],
  [/^#\/(client|demo)$/, () => '#/scenario'],
  [/^#\/(implement|register|authorization|endpoints|server)$/, () => '#/connect'],
  [/^#\/(qr|resources)$/, () => '#/patients'],
];

const views = {};
async function loadView(name) {
  if (!views[name]) views[name] = import(`./views/${name}.js`);
  return views[name];
}

function highlightNav(name) {
  document.querySelectorAll('[data-nav]').forEach((a) => a.classList.toggle('is-active', a.dataset.nav === name));
}

let renderSeq = 0;
async function route() {
  drawer.close();
  const full = location.hash || '#/';
  const hash = full.split('?')[0];
  for (const [re, to] of LEGACY) {
    const m = hash.match(re);
    if (m) { location.replace(to(m)); return; }
  }
  const match = ROUTES.map((r) => ({ r, m: hash.match(r.re) })).find((x) => x.m);
  const seq = ++renderSeq;
  api.clearRequests();
  clear(app);
  app.appendChild(spinner());
  if (!match) {
    highlightNav(null);
    clear(app);
    app.appendChild(el('section', { class: 'page' },
      el('header', { class: 'page-head' }, el('div', {}, el('h1', {}, 'Nothing here'),
        el('p', { class: 'purpose' }, `No view matches ${hash}.`))),
      el('div', { class: 'btn-row' }, el('a', { class: 'btn btn-primary', href: '#/' }, 'Story'),
        el('a', { class: 'btn', href: '#/patients' }, 'Patients'))));
    return;
  }
  highlightNav(match.r.nav);
  try {
    const [mod, context] = await Promise.all([loadView(match.r.view), loadContext()]);
    const params = match.r.params ? match.r.params(match.m) : {};
    const node = await mod.render({ ...params, ctx: context });
    if (seq !== renderSeq) return; // a newer navigation won
    clear(app);
    app.appendChild(node);
    window.scrollTo({ top: 0 });
    app.focus({ preventScroll: true });
  } catch (e) {
    if (seq !== renderSeq) return;
    console.error(e);
    clear(app);
    app.appendChild(el('section', { class: 'page' }, errorBox(e.message || String(e), { retry: route })));
  }
}

window.addEventListener('hashchange', route);
route();
