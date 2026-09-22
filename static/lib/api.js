// Network layer.
//
//   fhir(path, opts)  -> the FRONT DOOR. Every FHIR request goes through the
//                        public API with a bearer, exactly like any client.
//                        Each call is recorded so the page can show "the
//                        requests behind this screen".
//   ui(path, opts)    -> the small non-FHIR helper API under /ui/api
//                        (audit log, coverage, validation badges, viewer token).
//
// The default bearer is the read-only viewer token the server mints for the
// page. Views can pass their own (the scenario mints a real client token).

const state = {
  token: null,
  tokenExp: 0,
  tokenClient: null,
  requests: [],
  listeners: new Set(),
  nextId: 1,
  currentRun: null,
};

export class ApiError extends Error {
  constructor(message, { status, outcome, path } = {}) {
    super(message);
    this.status = status;
    this.outcome = outcome;
    this.path = path;
  }
}

function outcomeText(json) {
  if (!json) return '';
  if (json.resourceType === 'OperationOutcome') {
    return (json.issue || []).map((i) => i.diagnostics || i.details?.text || i.code).filter(Boolean).join('; ');
  }
  if (json.detail) return typeof json.detail === 'string' ? json.detail : JSON.stringify(json.detail);
  if (json.error_description) return json.error_description;
  if (json.error) return String(json.error);
  return '';
}

// ---------- viewer token ----------

export async function viewerToken() {
  if (state.token && Date.now() < state.tokenExp - 30_000) return state.token;
  const r = await fetch('/ui/api/viewer-token', { method: 'POST' });
  if (!r.ok) throw new ApiError(`viewer token: HTTP ${r.status}`, { status: r.status, path: '/ui/api/viewer-token' });
  const j = await r.json();
  state.token = j.access_token;
  state.tokenExp = Date.now() + (j.expires_in || 900) * 1000;
  state.tokenClient = j.client_id || 'ui-viewer';
  return state.token;
}

export function currentTokenInfo() {
  return { token: state.token, client: state.tokenClient, expiresAt: state.tokenExp };
}

// ---------- request log ----------

export function subscribe(fn) {
  state.listeners.add(fn);
  return () => state.listeners.delete(fn);
}

function emit() {
  for (const fn of state.listeners) {
    try { fn(state.requests); } catch { /* listener errors never break the page */ }
  }
}

export function requests() { return state.requests; }

export function clearRequests() {
  state.requests = [];
  emit();
}

export function newRun(prefix = 'ui') {
  const id = `${prefix}-${Math.random().toString(36).slice(2, 8)}${Date.now().toString(36).slice(-4)}`;
  state.currentRun = id;
  return id;
}

export function currentRun() { return state.currentRun; }

function claimsOf(token) {
  try {
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(b64));
  } catch { return null; }
}

// ---------- the front door ----------

/**
 * @param {string} path e.g. '/Patient/123' or an absolute URL on this server
 * @param {object} opts { method, body, token, run, headers, allowError, label, pillar, tag }
 * @returns {Promise<any>} parsed JSON (or text when not JSON)
 */
export async function fhir(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  // token: a bearer string, `false` for an unauthenticated call (token/register
  // endpoints), or undefined for the page's read-only viewer token
  const token = opts.token === false ? null : (opts.token || await viewerToken());
  const url = path;
  const headers = {
    Accept: 'application/fhir+json, application/json;q=0.9',
    ...(opts.headers || {}),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const run = opts.run || state.currentRun;
  if (run) headers['X-Demo-Run'] = run;
  let body;
  if (opts.body !== undefined) {
    headers['Content-Type'] = opts.contentType || 'application/fhir+json';
    body = typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body);
  }
  const started = performance.now();
  const entry = {
    id: state.nextId++, method, path: url, url, status: null, ms: null, at: new Date().toISOString(),
    client: token ? (claimsOf(token)?.client_id || claimsOf(token)?.sub || null) : null,
    run, label: opts.label || null, pillar: opts.pillar || null,
    requestHeaders: token ? { ...headers, Authorization: `Bearer ${token.slice(0, 16)}…` } : { ...headers },
    requestBody: opts.body !== undefined ? opts.body : null,
    response: null, error: null, bytes: null,
  };
  state.requests.push(entry);
  emit();
  let res;
  try {
    res = await fetch(url, { method, headers, body });
  } catch (e) {
    entry.ms = performance.now() - started;
    entry.error = e.message;
    emit();
    throw new ApiError(`network error: ${e.message}`, { path: url });
  }
  entry.ms = performance.now() - started;
  entry.status = res.status;
  const text = await res.text();
  entry.bytes = text.length;
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch { json = null; }
  entry.response = json ?? text;
  emit();
  if (!res.ok && !opts.allowError) {
    const msg = outcomeText(json) || `HTTP ${res.status}`;
    throw new ApiError(`${method} ${url} → ${res.status}: ${msg}`, { status: res.status, outcome: json, path: url });
  }
  return opts.raw ? { status: res.status, json, text, headers: res.headers } : (json ?? text);
}

/** GET a whole searchset, following `next` links (the store is small). */
export async function fhirAll(path, opts = {}) {
  const out = [];
  let next = path;
  let guard = 0;
  while (next && guard++ < 20) {
    const bundle = await fhir(next, opts);
    for (const e of bundle.entry || []) if (e.resource) out.push(e.resource);
    const link = (bundle.link || []).find((l) => l.relation === 'next');
    next = link ? link.url : null;
  }
  return out;
}

// ---------- the helper API ----------

export async function ui(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  const headers = { Accept: 'application/json', ...(opts.headers || {}) };
  let body;
  if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }
  const url = path.startsWith('/') ? path : `/ui/api/${path}`;
  const res = await fetch(url, { method, headers, body });
  const text = await res.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch { json = null; }
  if (!res.ok && !opts.allowError) {
    throw new ApiError(`${method} ${url} → ${res.status}: ${outcomeText(json) || text.slice(0, 120)}`,
      { status: res.status, outcome: json, path: url });
  }
  return opts.raw ? { status: res.status, json, text } : json;
}

/** Plain JSON fetch without auth, for public discovery endpoints. */
export async function publicJson(path) {
  const res = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new ApiError(`GET ${path} → ${res.status}`, { status: res.status, path });
  return res.json();
}
