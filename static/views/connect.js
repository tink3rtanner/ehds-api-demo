// Connect — one URL for agents; register, mint, call for people; the endpoints.

import { el, download, relTime, replace } from '../lib/dom.js';
import * as api from '../lib/api.js';
import * as smart from '../lib/smart.js';
import { pageShell, section, button, kv, codeBlock, requestChip, copyButton, note, spinner, errorBox } from '../lib/components.js';

const SCOPES = ['system/*.read', 'system/Bundle.write', 'system/*.write'];

export async function render({ ctx }) {
  const base = ctx.base;
  const ex = ctx.examples;
  const st = { clientId: null, keys: null, token: null };

  const clients = el('span', { class: 'muted' });
  api.ui('clients').then((c) => {
    const recent = c.clients.filter((x) => x.registered_at).slice(0, 5);
    replace(clients, `${c.total} clients registered`, recent.length ? ' · latest ' : '', ...recent.map((x, i) => el('span', {}, i ? ', ' : '', el('code', {}, x.client_id), ` ${relTime(x.registered_at)}`)));
  }).catch(() => {});

  const agent = section(null, el('div', { class: 'stack' },
    el('div', { class: 'agent-line' }, el('span', { class: 'label' }, 'Point your agent at'), el('code', {}, base), copyButton(base)),
    el('div', { class: 'btn-row' },
      button('GET / discovery', { small: true, icon: 'external', href: `${base}/` }),
      button('/llms.txt', { small: true, icon: 'external', href: `${base}/llms.txt` }),
      button('/metadata', { small: true, icon: 'external', href: `${base}/metadata` }),
      button('smart-configuration', { small: true, icon: 'external', href: `${base}/.well-known/smart-configuration` }),
      clients),
  ), { className: 'block-tint' });

  const clientId = el('input', { value: `dev-${Math.random().toString(36).slice(2, 7)}` });
  const scopeBoxes = SCOPES.map((s) => el('input', { type: 'checkbox', value: s, checked: s === 'system/*.read' }));
  const out = [el('div', {}), el('div', {}), el('div', {})];
  const mintBtn = button('Mint a token', { kind: 'primary', icon: 'key', disabled: true, onClick: () => mint() });
  const callBtn = button('First call', { kind: 'primary', icon: 'play', disabled: true, onClick: () => call() });
  const chosen = () => scopeBoxes.filter((b) => b.checked).map((b) => b.value);

  const register = async () => {
    const id = clientId.value.trim();
    if (!/^[a-z0-9][a-z0-9-_]{1,62}[a-z0-9]$/.test(id)) { replace(out[0], errorBox('client id: lowercase letters, digits, hyphens, 3–64 chars')); return; }
    replace(out[0], spinner('Registering…'));
    try {
      if (!smart.cryptoAvailable()) throw new Error('This browser cannot generate keys here (needs HTTPS). Use the CLI: python -m app.tools.register_client');
      const keys = await smart.generateKeypair(id);
      await api.fhir('/register-client', { method: 'POST', token: false, contentType: 'application/json', body: { client_id: id, scopes: chosen(), jwk: keys.publicJwk }, label: 'register' });
      st.clientId = id; st.keys = keys; st.token = null; mintBtn.disabled = false;
      const pem = await smart.privateKeyPem(keys.privateJwk);
      replace(out[0], note([el('strong', {}, `Registered ${id}.`), ' The private key is only in this tab. ',
        el('a', { href: '#', onclick: (e) => { e.preventDefault(); download(`${id}.pem`, pem); } }, 'Download it'), ' if you want to use this client from code.']));
    } catch (e) { replace(out[0], errorBox(e.message)); }
  };
  const mint = async () => {
    replace(out[1], spinner('Signing…'));
    try {
      const { jwt, claims } = await smart.signAssertion({ clientId: st.clientId, kid: st.keys.kid, privateJwk: st.keys.privateJwk, tokenEndpoint: ctx.info.token_endpoint });
      const tok = await smart.mintToken({ assertion: jwt, scope: chosen().join(' ') });
      st.token = tok; callBtn.disabled = false;
      replace(out[1], el('div', { class: 'stack' },
        kv([{ label: 'Assertion', value: `iss=sub=${claims.iss} · aud=${claims.aud} · ${claims.exp - claims.iat} s`, mono: true },
          { label: 'Bearer', value: `${tok.scope} · ${tok.expires_in} s`, mono: true }], { columns: 2 }),
        el('div', { class: 'row' }, el('div', { class: 'token-box' }, tok.access_token), copyButton(tok.access_token, { label: 'Copy' }))));
    } catch (e) { replace(out[1], errorBox(e.message)); }
  };
  const call = async () => {
    replace(out[2], spinner('Calling…'));
    const path = pathOf(ex.endpoints?.lookup_patient_by_slot) || '/Patient';
    try {
      const b = await api.fhir(path, { token: st.token.access_token, label: 'first call' });
      replace(out[2], note(`HTTP 200 · ${b.total ?? (b.entry || []).length} result(s) using your bearer.`), codeBlock(b, { json: true }));
    } catch (e) { replace(out[2], errorBox(e.message)); }
  };

  const steps = section('Register, mint, call', el('div', { class: 'stack connect-steps' },
    el('div', { class: 'connect-step stack' }, el('h3', {}, 'Register a client'),
      el('div', { class: 'form' }, el('div', { class: 'field' }, el('label', {}, 'Client id'), clientId)),
      el('div', { class: 'scope-grid' }, scopeBoxes.map((b) => el('label', {}, b, b.value))),
      el('div', { class: 'btn-row' }, button('Register', { kind: 'primary', icon: 'shield', onClick: register }), requestChip('GET', '/register-client')),
      out[0]),
    el('div', { class: 'connect-step stack' }, el('h3', {}, 'Mint a token'),
      el('p', { class: 'muted' }, 'The browser signs a JWT with the private key (iss = sub = client id, aud = token endpoint, 60 s) and POSTs it to /token for a bearer.'),
      el('div', { class: 'btn-row' }, mintBtn), out[1]),
    el('div', { class: 'connect-step stack' }, el('h3', {}, 'Make the first call'), el('div', { class: 'btn-row' }, callBtn), out[2]),
  ));

  const rows = [
    ['Discovery', 'GET', '/'], ['CapabilityStatement', 'GET', '/metadata'], ['SMART configuration', 'GET', '/.well-known/smart-configuration'],
    ['Register', 'POST', '/register-client'], ['Token', 'POST', '/token'],
    ['Find patient', 'GET', pathOf(ex.endpoints?.search_patient_by_demographics)], ['Match patient', 'POST', '/Patient/$match'],
    ['Resolve a slot', 'GET', pathOf(ex.endpoints?.lookup_patient_by_slot)],
    ['Find documents', 'GET', pathOf(ex.endpoints?.document_search_by_identifier)], ['Retrieve document', 'GET', pathOf(ex.endpoints?.document_retrieve)],
    ['Patient summary', 'GET', pathOf(ex.endpoints?.patient_summary_operation)], ['Everything', 'GET', pathOf(ex.endpoints?.patient_everything)],
    ['Resources', 'GET', pathOf(ex.endpoints?.allergies_for_patient)], ['Back to source', 'GET', '/{Type}/{id}/$source'], ['Publish', 'POST', '/'],
  ].filter((r) => r[2]);
  const endpoints = section('Endpoints', el('div', {}, rows.map(([what, method, path]) => el('div', { class: 'endpoint-row' },
    el('div', { class: 'what' }, what),
    method === 'GET' && !path.includes('{') ? requestChip('GET', path) : el('span', { class: 'row' }, el('span', { class: `method method-${method.toLowerCase()}` }, method), el('code', {}, path))))));

  return pageShell({ title: 'Connect', children: [agent, steps, endpoints] });

  function pathOf(url) {
    if (!url) return null;
    try { const u = new URL(url); return u.pathname + u.search; } catch { return url.startsWith('/') ? url : null; }
  }
}
