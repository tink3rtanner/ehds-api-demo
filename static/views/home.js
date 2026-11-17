// Home: what this is, what's on it, where to start. Agents go to /llms.txt.

import { el } from '../lib/dom.js';
import { pageShell, button, copyButton } from '../lib/components.js';

const IG = 'https://build.fhir.org/ig/euridice-org/eu-health-data-api/';

export async function render({ ctx }) {
  const { info, base } = ctx;
  const llms = `${base}/llms.txt`;
  const patients = info.patients ?? 0;
  const documents = info.by_type?.DocumentReference ?? 0;

  return pageShell({
    title: 'EU Health Data API: reference implementation',
    children: [
      el('p', { class: 'lede' },
        'This is a demo server for the ', el('a', { href: IG, target: '_blank', rel: 'noopener' }, 'EU Health Data API implementation guide'),
        '. It holds synthetic patients and documents. Browse them here, or call the API yourself.'),
      el('div', { class: 'btn-row' },
        button(`${patients} patients`, { kind: 'primary', href: '#/patients' }),
        button(`${documents} documents`, { href: '#/documents' }),
        button('Do your own demo', { href: '#/connect' })),
      el('div', { class: 'agent-line' },
        el('span', { class: 'label' }, 'Using an AI agent? Point it at'), el('code', {}, llms), copyButton(llms)),
    ],
  });
}
