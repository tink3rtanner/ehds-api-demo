// SMART Backend Services, client side: generate a keypair in the browser,
// register its public half, sign a JWT client assertion, exchange it for a
// bearer. The private key never leaves the browser. Used by the scenario (a
// real client, not the viewer token) and by the Connect page.

import * as api from './api.js';

const ALG = { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]) };

export function cryptoAvailable() {
  return typeof crypto !== 'undefined' && !!crypto.subtle && typeof crypto.subtle.generateKey === 'function';
}

function b64url(bytes) {
  const s = btoa(String.fromCharCode(...new Uint8Array(bytes)));
  return s.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function b64urlJson(obj) {
  return b64url(new TextEncoder().encode(JSON.stringify(obj)));
}

export async function generateKeypair(clientId) {
  const kp = await crypto.subtle.generateKey(ALG, true, ['sign', 'verify']);
  const publicJwk = await crypto.subtle.exportKey('jwk', kp.publicKey);
  const privateJwk = await crypto.subtle.exportKey('jwk', kp.privateKey);
  const kid = `${clientId}-key-1`;
  return {
    kid,
    publicJwk: { kty: publicJwk.kty, n: publicJwk.n, e: publicJwk.e, alg: 'RS256', use: 'sig', kid },
    privateJwk: { ...privateJwk, kid },
  };
}

export async function importPrivate(jwk) {
  return crypto.subtle.importKey('jwk', jwk, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['sign']);
}

export async function privateKeyPem(jwk) {
  const key = await crypto.subtle.importKey('jwk', jwk, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, true, ['sign']);
  const der = await crypto.subtle.exportKey('pkcs8', key);
  const b64 = btoa(String.fromCharCode(...new Uint8Array(der))).match(/.{1,64}/g).join('\n');
  return `-----BEGIN PRIVATE KEY-----\n${b64}\n-----END PRIVATE KEY-----\n`;
}

export async function publicKeyPem(jwk) {
  const key = await crypto.subtle.importKey('jwk', { kty: jwk.kty, n: jwk.n, e: jwk.e },
    { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, true, ['verify']);
  const der = await crypto.subtle.exportKey('spki', key);
  const b64 = btoa(String.fromCharCode(...new Uint8Array(der))).match(/.{1,64}/g).join('\n');
  return `-----BEGIN PUBLIC KEY-----\n${b64}\n-----END PUBLIC KEY-----\n`;
}

/** Build and sign the client assertion JWT (iss = sub = client_id, aud = token endpoint). */
export async function signAssertion({ clientId, kid, privateJwk, tokenEndpoint, ttlSeconds = 60 }) {
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: 'RS256', typ: 'JWT', kid };
  const claims = {
    iss: clientId, sub: clientId, aud: tokenEndpoint, iat: now, exp: now + ttlSeconds,
    jti: crypto.randomUUID ? crypto.randomUUID() : `${now}-${Math.random().toString(36).slice(2)}`,
  };
  const signingInput = `${b64urlJson(header)}.${b64urlJson(claims)}`;
  const key = await importPrivate(privateJwk);
  const sig = await crypto.subtle.sign({ name: 'RSASSA-PKCS1-v1_5' }, key, new TextEncoder().encode(signingInput));
  return { jwt: `${signingInput}.${b64url(sig)}`, header, claims };
}

export function decodeJwt(token) {
  try {
    const [h, p] = token.split('.');
    const dec = (s) => JSON.parse(atob(s.replace(/-/g, '+').replace(/_/g, '/')));
    return { header: dec(h), claims: dec(p) };
  } catch { return null; }
}

/** POST /register-client (public, RFC 7591 style). */
export async function registerClient({ clientId, scopes, publicJwk }) {
  return api.fhir('/register-client', {
    method: 'POST', token: false, contentType: 'application/json', label: 'register client',
    body: { client_id: clientId, scopes, jwk: publicJwk },
  });
}

/** Exchange a signed assertion for a bearer at POST /token. */
export async function mintToken({ assertion, scope, tokenEndpoint = '/token', run }) {
  const form = new URLSearchParams({
    grant_type: 'client_credentials',
    client_assertion_type: 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
    client_assertion: assertion,
    scope,
  }).toString();
  return api.fhir(tokenEndpoint, {
    method: 'POST', token: false, contentType: 'application/x-www-form-urlencoded', body: form, run,
    label: 'mint token', pillar: 'authorize',
  });
}

// ---------- the scenario's persistent browser client ----------

const STORE_KEY = 'ehds.scenario.client';

export function loadScenarioClient() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || 'null'); } catch { return null; }
}

export function saveScenarioClient(c) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(c)); } catch { /* private mode: fine, it just re-registers next time */ }
}

export function forgetScenarioClient() {
  try { localStorage.removeItem(STORE_KEY); } catch { /* ignore */ }
}

/**
 * Make sure this browser has a registered read-only client and return
 * { clientId, kid, publicJwk, privateJwk, registered: bool (this call) }.
 */
export async function ensureScenarioClient() {
  let c = loadScenarioClient();
  let registeredNow = false;
  if (!c || !c.privateJwk || !c.clientId) {
    const clientId = `browser-${Math.random().toString(36).slice(2, 8)}`;
    const keys = await generateKeypair(clientId);
    c = { clientId, ...keys, createdAt: new Date().toISOString() };
    await registerClient({ clientId, scopes: ['system/*.read'], publicJwk: c.publicJwk });
    registeredNow = true;
    saveScenarioClient(c);
  }
  return { ...c, registeredNow };
}

/** Full flow for the scenario: ensure client, sign, mint. Re-registers once if the server forgot us. */
export async function scenarioToken({ tokenEndpoint, run }) {
  let c = await ensureScenarioClient();
  const attempt = async () => {
    const { jwt, claims } = await signAssertion({ clientId: c.clientId, kid: c.kid, privateJwk: c.privateJwk, tokenEndpoint });
    const tok = await mintToken({ assertion: jwt, scope: 'system/*.read', run });
    return { client: c, assertion: jwt, assertionClaims: claims, token: tok };
  };
  try {
    return await attempt();
  } catch (e) {
    // registry reset on the server (reseed, redeploy) -> register again with the same key
    if (e.status === 401 || e.status === 400) {
      await registerClient({ clientId: c.clientId, scopes: ['system/*.read'], publicJwk: c.publicJwk });
      c = { ...c, registeredNow: true };
      saveScenarioClient(c);
      return attempt();
    }
    throw e;
  }
}
