// Connect — the one URL for agents, then the three steps a developer takes:
// register, mint, first call. All of it live, all of it in the browser.

import { el, download, fmtDate, relTime } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as api from '../lib/api.js';
import * as smart from '../lib/smart.js';
import {
  pageShell, section, button, badge, kv, codeBlock, requestChip, copyButton, table, note, spinner, errorBox, emptyState, drawer,
} from '../lib/components.js';

const SCOPE_GROUPS = [
  ['Read', ['system/*.read', 'system/Patient.read', 'system/DocumentReference.read', 'system/Binary.read', 'system/Observation.read', 'system/Condition.read', 'system/AllergyIntolerance.read', 'system/MedicationStatement.read', 'system/MedicationRequest.read', 'system/Immunization.read', 'system/Procedure.read', 'system/DiagnosticReport.read', 'system/ImagingStudy.read', 'system/Encounter.read']],
  ['Publish (ITI-105)', ['system/Bundle.write', 'system/*.write', 'system/DocumentReference.write', 'system/Patient.write', 'system/Observation.write', 'system/Condition.write', 'system/AllergyIntolerance.write', 'system/MedicationStatement.write', 'system/MedicationRequest.write', 'system/Immunization.write', 'system/Procedure.write', 'system/DiagnosticReport.write', 'system/ImagingStudy.write', 'system/Encounter.write']],
];

export async function render({ ctx }) {
  const base = ctx.base;
  const tokenEndpoint = ctx.info.token_endpoint;
  const ex = ctx.examples;

  // ---- the one URL ----
  const agent = section(null, el('div', { class: 'stack' },
    el('div', { class: 'agent-line' }, el('span', { class: 'label' }, 'Point your agent at'), el('code', {}, base), copyButton(base)),
    el('p', { class: 'muted' }, 'A browser asking for HTML lands here. Anything else asking for that URL gets a discovery document: the capability statement, the SMART configuration, the registration endpoint, the four pillars with one live example each, and how to publish. ',
      el('a', { href: '/llms.txt', target: '_blank', rel: 'noopener' }, '/llms.txt'), ' says the same in prose.'),
    el('div', { class: 'btn-row' },
      button('GET / (discovery JSON)', { small: true, icon: 'external', href: `${base}/` }),
      button('/llms.txt', { small: true, icon: 'external', href: `${base}/llms.txt` }),
      button('/metadata', { small: true, icon: 'external', href: `${base}/metadata` }),
      button('/.well-known/smart-configuration', { small: true, icon: 'external', href: `${base}/.well-known/smart-configuration` }),
    ),
    recentClients(),
  ), { className: 'block-tint' });

  // ---- the three steps ----
  const st = { clientId: null, keys: null, token: null };
  const clientIdInput = el('input', { placeholder: 'e.g. my-clinic-connector', value: `dev-${Math.random().toString(36).slice(2, 7)}`, pattern: '[a-z0-9][a-z0-9-_]{1,62}[a-z0-9]' });
  const scopeBoxes = {};
  const scopes = el('div', { class: 'stack' }, SCOPE_GROUPS.map(([label, list]) => el('div', {}, el('div', { class: 'eyebrow' }, label),
    el('div', { class: 'scope-grid' }, list.map((s) => {
      const cb = el('input', { type: 'checkbox', value: s, checked: s === 'system/*.read' });
      scopeBoxes[s] = cb;
      return el('label', {}, cb, s);
    })))));
  const pem = el('textarea', { placeholder: 'Paste a PEM public key to register an existing key instead of generating one here.' });
  const regOut = el('div', {});
  const mintOut = el('div', {}, el('p', { class: 'muted' }, 'Register first.'));
  const callOut = el('div', {}, el('p', { class: 'muted' }, 'Mint a token first.'));
  const mintBtn = button('Sign an assertion and mint a token', { kind: 'primary', icon: 'key', disabled: true, onClick: () => mint() });
  const callBtn = button('Make the first call', { kind: 'primary', icon: 'play', disabled: true, onClick: () => firstCall() });

  const register = async () => {
    const clientId = clientIdInput.value.trim();
    if (!/^[a-z0-9][a-z0-9-_]{1,62}[a-z0-9]$/.test(clientId)) { regOut.replaceChildren(errorBox('client id: lowercase letters, digits, hyphens, 3–64 chars')); return; }
    const chosen = Object.values(scopeBoxes).filter((b) => b.checked).map((b) => b.value);
    regOut.replaceChildren(spinner('Registering…'));
    try {
      let body;
      let keys = null;
      if (pem.value.trim()) {
        body = { client_id: clientId, scopes: chosen, public_key_pem: pem.value.trim() };
      } else {
        if (!smart.cryptoAvailable()) throw new Error('This browser cannot generate keys here (needs HTTPS or localhost). Paste a PEM instead.');
        keys = await smart.generateKeypair(clientId);
        body = { client_id: clientId, scopes: chosen, jwk: keys.publicJwk };
      }
      const r = await api.fhir('/register-client', { method: 'POST', token: false, contentType: 'application/json', body, label: 'register client' });
      st.clientId = clientId; st.keys = keys; st.token = null;
      mintBtn.disabled = !keys;
      const privPem = keys ? await smart.privateKeyPem(keys.privateJwk) : null;
      regOut.replaceChildren(el('div', { class: 'stack' },
        note([el('strong', {}, `Registered ${clientId}.`), ' The server now trusts key ', el('code', {}, r.jwks?.keys?.[0]?.kid || keys?.kid), '. ',
          keys ? 'The private key exists only in this browser tab: download it now if you want to use this client from code.' : 'Sign assertions with the private half of the key you pasted.']),
        keys ? el('div', { class: 'btn-row' },
          button('Download private key (PEM)', { icon: 'key', onClick: () => download(`${clientId}.pem`, privPem) }),
          button('Download public JWK', { kind: 'ghost', onClick: () => download(`${clientId}.jwk.json`, JSON.stringify(keys.publicJwk, null, 2), 'application/json') }),
        ) : null,
        el('details', {}, el('summary', { class: 'muted' }, 'Registration response'), codeBlock(r, { json: true })),
      ));
    } catch (e) { regOut.replaceChildren(errorBox(e.message)); }
  };

  const mint = async () => {
    mintOut.replaceChildren(spinner('Signing and minting…'));
    try {
      const { jwt, claims } = await smart.signAssertion({ clientId: st.clientId, kid: st.keys.kid, privateJwk: st.keys.privateJwk, tokenEndpoint });
      const scope = Object.values(scopeBoxes).filter((b) => b.checked).map((b) => b.value).join(' ');
      const tok = await smart.mintToken({ assertion: jwt, scope });
      st.token = tok;
      callBtn.disabled = false;
      const decoded = smart.decodeJwt(tok.access_token);
      mintOut.replaceChildren(el('div', { class: 'stack' },
        kv([
          { label: 'Assertion (signed here)', value: `iss=sub=${claims.iss} · aud=${claims.aud} · exp in ${claims.exp - claims.iat}s · jti ${claims.jti.slice(0, 8)}…`, mono: true },
          { label: 'Granted scope', value: tok.scope, mono: true },
          { label: 'Expires in', value: `${tok.expires_in} s` },
          { label: 'Bearer claims', value: decoded ? `sub ${decoded.claims.sub} · aud ${decoded.claims.aud} · kid ${decoded.header.kid}` : '—', mono: true },
        ], { columns: 2 }),
        el('div', { class: 'row' }, el('div', { class: 'token-box' }, tok.access_token), copyButton(tok.access_token, { label: 'Copy bearer' })),
        el('details', {}, el('summary', { class: 'muted' }, 'The exact form POSTed to /token'), codeBlock(
          `POST ${tokenEndpoint}\nContent-Type: application/x-www-form-urlencoded\n\ngrant_type=client_credentials\n&client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer\n&client_assertion=${jwt.slice(0, 40)}…\n&scope=${encodeURIComponent(scope)}`, { lang: 'http' })),
      ));
    } catch (e) { mintOut.replaceChildren(errorBox(e.message)); }
  };

  const firstCall = async () => {
    callOut.replaceChildren(spinner('Calling…'));
    const path = ex.endpoints?.lookup_patient_by_slot ? new URL(ex.endpoints.lookup_patient_by_slot).pathname + new URL(ex.endpoints.lookup_patient_by_slot).search : '/Patient';
    try {
      const b = await api.fhir(path, { token: st.token.access_token, label: 'first call' });
      callOut.replaceChildren(el('div', { class: 'stack' },
        note([el('strong', {}, `HTTP 200 · ${b.total ?? (b.entry || []).length} result(s).`), ' Your client just read from this server with its own bearer. Everything else in ', el('a', { href: '/llms.txt', target: '_blank', rel: 'noopener' }, 'llms.txt'), ' works the same way.']),
        codeBlock(b, { json: true }),
      ));
    } catch (e) { callOut.replaceChildren(errorBox(e.message)); }
  };

  const steps = section('Register, mint, call', el('div', { class: 'stack connect-steps' },
    el('div', { class: 'connect-step stack' },
      el('h3', {}, 'Register a client'),
      el('p', { class: 'muted' }, 'RFC 7591 dynamic registration, open to anyone. Generate a keypair here (the private half never leaves this tab) or paste your own public key.'),
      el('div', { class: 'form' }, el('div', { class: 'field' }, el('label', {}, 'Client id'), clientIdInput)),
      scopes,
      el('div', { class: 'field' }, el('label', {}, 'Public key PEM (optional)'), pem),
      el('div', { class: 'btn-row' }, button('Register', { kind: 'primary', icon: 'shield', onClick: register }), requestChip('POST', '/register-client', { onRun: register }),
        requestChip('GET', '/register-client')),
      regOut,
    ),
    el('div', { class: 'connect-step stack' },
      el('h3', {}, 'Mint a token'),
      el('p', { class: 'muted' }, 'SMART Backend Services: sign a JWT (iss = sub = client id, aud = token endpoint, 60 s, single-use jti) with the private key, POST it as a client assertion, receive a bearer.'),
      el('div', { class: 'btn-row' }, mintBtn, requestChip('POST', '/token', { onRun: mint })),
      mintOut,
    ),
    el('div', { class: 'connect-step stack' },
      el('h3', {}, 'Make the first call'),
      el('p', { class: 'muted' }, 'Resolve the reference patient by her slot identifier. Then follow the pillars: $match, DocumentReference, Bundle, resources.'),
      el('div', { class: 'btn-row' }, callBtn),
      callOut,
    ),
  ), { sub: 'The same three steps an agent performs from the discovery document, done by hand.' });

  // ---- endpoint reference ----
  const endpointRows = [
    ['Discovery', 'GET', '/', 'What this server is and where everything lives. JSON unless you ask for HTML.'],
    ['CapabilityStatement', 'GET', '/metadata', 'Resources, interactions, search parameters, implementation guides.'],
    ['SMART configuration', 'GET', '/.well-known/smart-configuration', 'Token endpoint, algorithms, scopes, registration endpoint, live examples.'],
    ['Register', 'POST', '/register-client', 'RFC 7591. GET the same URL for the schema; manage at /register-client/{client_id}.'],
    ['Token', 'POST', '/token', 'client_credentials with a private_key_jwt client assertion.'],
    ['Find patient', 'GET', pathOf(ex.endpoints?.search_patient_by_demographics) || '/Patient?family=…&birthdate=…', 'PDQm demographic search.'],
    ['Match patient', 'POST', '/Patient/$match', 'PDQm scored match with a Parameters body.'],
    ['Resolve a slot', 'GET', pathOf(ex.endpoints?.lookup_patient_by_slot) || '/Patient?identifier=urn:ehds-demo:slot|p-001', 'The reference panel\'s slot labels as identifier tokens.'],
    ['Find documents', 'GET', pathOf(ex.endpoints?.document_search_by_identifier) || '/DocumentReference?patient.identifier=…', 'MHD ITI-67 with FHIR chaining; also type, category, date, _lastupdated, author.*.'],
    ['Retrieve document', 'GET', pathOf(ex.endpoints?.document_retrieve) || '/Bundle/{id}', 'MHD ITI-68: the compiled Bundle.type=document.'],
    ['Patient summary', 'GET', pathOf(ex.endpoints?.patient_summary_operation) || '/Patient/{id}/$summary', 'The IPS operation; same content as the Patient Summary bundle.'],
    ['Everything', 'GET', pathOf(ex.endpoints?.patient_everything) || '/Patient/{id}/$everything', 'The whole compartment.'],
    ['Resources', 'GET', pathOf(ex.endpoints?.allergies_for_patient) || '/AllergyIntolerance?patient={id}', 'IPA-style read and search on every supported type.'],
    ['Back to source', 'GET', '/{Type}/{id}/$source', '307 to where a community resource came from.'],
    ['Publish', 'POST', '/', 'MHD ITI-105: a transaction or document Bundle. Needs system/Bundle.write.'],
    ['All bundle ids', 'GET', '/spec/all-bundle-ids', 'Every compiled document id for the reference panel.'],
  ];
  const endpoints = section('Endpoint reference', el('div', {}, endpointRows.map(([what, method, path, desc]) => el('div', { class: 'endpoint-row' },
    el('div', { class: 'what' }, what, el('small', {}, desc)),
    method === 'GET' && !path.includes('{') ? requestChip('GET', path) : el('span', { class: 'row' }, el('span', { class: `method method-${method.toLowerCase()}` }, method), el('code', {}, path))),
  )), { sub: 'Every example carries a real id resolved from the store at request time. GET chips run live with the page\'s read-only bearer.' });

  // ---- capability summary ----
  const capHost = el('div', {}, spinner('Reading /metadata…'));
  api.fhir('/metadata', { label: 'capability statement' }).then((cs) => {
    const rest = (cs.rest || [])[0] || {};
    const rows = (rest.resource || []).map((r) => ({ type: r.type, interactions: (r.interaction || []).map((i) => i.code).join(', '), params: (r.searchParam || []).length, profile: (r.supportedProfile || [])[0] || r.profile || '' }));
    capHost.replaceChildren(
      kv([
        { label: 'FHIR version', value: cs.fhirVersion, mono: true },
        { label: 'Software', value: `${cs.software?.name || ''} ${cs.software?.version || ''}`.trim() },
        { label: 'Implementation guides', value: `${(cs.implementationGuide || []).length}` },
        { label: 'Security', value: (rest.security?.service || []).map((s) => s.coding?.[0]?.code || s.text).join(', ') || 'SMART-on-FHIR' },
      ], { columns: 4 }),
      table({ columns: [
        { label: 'Type', className: 'mono', render: (r) => r.type },
        { label: 'Interactions', render: (r) => r.interactions },
        { label: 'Search params', className: 'num', render: (r) => String(r.params) },
        { label: 'Profile', className: 'mono', render: (r) => r.profile ? r.profile.split('/').pop() : '' },
      ], rows, pageSize: 30 }),
    );
  }).catch((e) => capHost.replaceChildren(errorBox(e.message)));
  const capability = section('What the server declares', capHost, { sub: 'Straight from /metadata.', actions: [requestChip('GET', '/metadata')] });

  // ---- server facts ----
  const facts = el('div', {}, spinner());
  api.ui('build-info').then((b) => facts.replaceChildren(kv([
    { label: 'Issuer', value: ctx.info.issuer, mono: true },
    { label: 'Token TTL', value: `${ctx.info.token_ttl_seconds} s` },
    { label: 'Rate limit', value: `${ctx.info.rate_limit_per_min} req/min per client` },
    { label: 'Body cap', value: `${Math.round(ctx.info.body_max_bytes / 1048576)} MB` },
    { label: 'Build', value: b.git_sha || '—', mono: true },
    { label: 'Stack', value: `Python ${b.python} · FastAPI ${b.fastapi} · fhir.resources ${b.fhir_resources}` },
    { label: 'EU validator', value: b.validator.available ? `ready · ${b.validator.eu_packages.length} IG packages` : `unavailable · ${b.validator.reason || ''}` },
  ], { columns: 4 }))).catch(() => facts.replaceChildren());

  return pageShell({
    title: 'Connect',
    purpose: 'One URL for agents. Three steps for people. Everything a client needs to talk to this server, done live.',
    children: [agent, steps, endpoints, capability, section('Server facts', facts)],
  });

  function pathOf(url) {
    if (!url) return null;
    try { const u = new URL(url); return u.pathname + u.search; } catch { return url.startsWith('/') ? url : null; }
  }

  function recentClients() {
    const host = el('div', { class: 'row' }, spinner('Recent registrations…'));
    api.ui('clients').then((c) => {
      const rows = c.clients.filter((x) => x.registered_at).slice(0, 8);
      host.replaceChildren(el('span', { class: 'muted' }, 'Recently registered clients:'),
        ...(rows.length ? rows.map((x) => el('span', { class: 'chip', title: fmtDate(x.registered_at, { time: true }) }, el('code', {}, x.client_id), el('span', { class: 'muted' }, ` ${relTime(x.registered_at)}`)))
          : [el('span', { class: 'muted' }, 'none yet — be the first')]),
        el('span', { class: 'muted' }, `· ${c.total} total`));
    }).catch(() => host.replaceChildren());
    return host;
  }
}
