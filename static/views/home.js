// Home: what this server does, how to get started. Agents go to /llms.txt.

import { el } from '../lib/dom.js';
import { pageShell, section, button, copyButton } from '../lib/components.js';
import { CATEGORIES, CATEGORY_META } from '../lib/fhir.js';

const IG = 'https://build.fhir.org/ig/euridice-org/eu-health-data-api/';
const ext = (href, text) => el('a', { href, target: '_blank', rel: 'noopener' }, text);

export async function render({ ctx }) {
  const { info, base } = ctx;
  const llms = `${base}/llms.txt`;
  const patients = info.patients ?? 0;
  const documents = info.by_type?.DocumentReference ?? 0;

  const categories = el('ul', { class: 'cat-list' },
    ...CATEGORIES.map((c) => el('li', {}, ext(CATEGORY_META[c].ig, CATEGORY_META[c].label))));

  return pageShell({
    title: 'EU Health Data API: reference implementation',
    children: [
      el('p', { class: 'lede' },
        'A working server for the ', ext(IG, 'EU Health Data API'),
        '. Query priority-category documents for synthetic patients, or publish your own and see them checked against the HL7 Europe profiles.'),

      el('div', { class: 'home-grid' },
        section('Query', [
          el('p', {}, 'Find a patient, list their documents, fetch each one. Standard FHIR R4 and IHE MHD, nothing custom.'),
          categories,
          button(`Browse ${patients} patients, ${documents} documents`, { href: '#/patients', small: true }),
        ]),
        section('Publish', [
          el('p', {}, 'Send a FHIR document with ITI-105 (POST /DocumentReference). The server stores it byte for byte, makes it searchable and validates it against its EU profile.'),
        ])),

      section('Get started', [
        el('ol', { class: 'steps' },
          el('li', {}, el('b', {}, 'Register a client'), ' with a public key (SMART Backend Services).'),
          el('li', {}, el('b', {}, 'Get a token'), ': sign a short JWT and trade it at /token.'),
          el('li', {}, el('b', {}, 'Call the API'), ' with the bearer token.')),
        el('div', { class: 'btn-row' },
          button('Quick start in your browser', { kind: 'primary', href: '#/connect' }),
          el('span', { class: 'muted' }, 'or from code: ', el('a', { href: `${base}/quickstart.py` }, el('code', {}, '/quickstart.py')),
            ' · every transaction with tests: ', el('a', { href: `${base}/bruno.zip` }, 'Bruno collection'))),
      ]),

      el('div', { class: 'agent-line' },
        el('span', { class: 'label' }, 'Using an AI agent? Point it at'), el('code', {}, llms), copyButton(llms)),
    ],
  });
}
