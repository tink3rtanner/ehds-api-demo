// ehds-api viewer — hash-routed SPA, no build step, vanilla JS.

const app = document.getElementById('app');
const envPill = document.getElementById('env-pill');

const CATEGORY_LABELS = {
    'patient-summary':   { label: 'Patient Summary', color: '#2a6f6b', icon: '📋' },
    'laboratory-report': { label: 'Laboratory Report', color: '#1a5a8c', icon: '🧪' },
    'discharge-report':  { label: 'Discharge Report', color: '#8c5a1a', icon: '🏥' },
    'imaging-report':    { label: 'Imaging Report', color: '#7c2a8c', icon: '🩻' },
};

const COUNTRY_NAMES = {
    AT: 'Austria', DE: 'Germany', IT: 'Italy', FR: 'France', ES: 'Spain',
    PT: 'Portugal', NL: 'Netherlands', PL: 'Poland', SE: 'Sweden', FI: 'Finland',
};

// ---------- helpers ----------
const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === 'class') node.className = v;
        else if (k === 'style') node.style.cssText = v;
        else if (k.startsWith('on')) node.addEventListener(k.slice(2).toLowerCase(), v);
        else if (k === 'html') node.innerHTML = v;
        else node.setAttribute(k, v);
    }
    for (const c of children.flat()) {
        if (c == null || c === false) continue;
        node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return node;
};

async function api(path) {
    const r = await fetch(path);
    if (!r.ok) {
        const text = await r.text();
        throw new Error(`${path}: ${r.status} ${text.slice(0, 200)}`);
    }
    return r.json();
}

function renderError(message) {
    app.innerHTML = '';
    app.appendChild(el('div', { class: 'error' }, message));
}

function setLoading() {
    app.innerHTML = '';
    app.appendChild(el('div', { class: 'loading' }, 'loading…'));
}

function pickName(resource) {
    const code = resource?.code?.text || resource?.code?.coding?.[0]?.display
              || resource?.vaccineCode?.text || resource?.vaccineCode?.coding?.[0]?.display
              || resource?.type?.text || resource?.type?.coding?.[0]?.display
              || resource?.medicationReference?.display
              || resource?.medicationCodeableConcept?.text
              || resource?.medicationCodeableConcept?.coding?.[0]?.display
              || resource?.description
              || resource?.modality?.[0]?.display
              || resource?.series?.[0]?.bodySite?.display
              || (resource?.resourceType === 'ImagingStudy' ? 'Imaging study' : '')
              || '';
    return code;
}

function pickDate(resource) {
    return resource?.effectiveDateTime || resource?.occurrenceDateTime
        || resource?.recordedDate || resource?.authoredOn || resource?.whenHandedOver
        || resource?.performedDateTime || resource?.period?.start
        || resource?.issued || resource?.started || resource?.collection?.collectedDateTime
        || resource?.date || '';
}

// ---------- patient list ----------
async function renderPatientList() {
    setLoading();
    try {
        const patients = await api('/ui/api/patients');
        const head = el('div', { class: 'page-head' },
            el('h1', {}, 'Patients'),
            el('div', { class: 'meta' }, `${patients.length} synthetic patients across the EU`),
        );
        const search = el('div', { class: 'search-box' },
            el('input', {
                placeholder: 'filter by name, country, id…',
                'aria-label': 'filter patients',
                oninput: (e) => filterCards(e.target.value),
            }),
        );
        const grid = el('div', { class: 'patient-grid', id: 'patient-grid' });

        for (const p of patients) {
            const fullName = `${p.given} ${p.family}`.trim();
            const card = el('a', {
                class: 'patient-card',
                href: `#/p/${p.id}`,
                'data-search': `${fullName} ${p.family} ${p.country} ${p.id} ${p.city || ''}`.toLowerCase(),
            },
                el('div', { class: 'name' }, fullName, ' ',
                    el('span', { class: 'country-pill', title: COUNTRY_NAMES[p.country] || p.country }, p.country),
                ),
                el('div', { class: 'meta' },
                    el('span', {}, p.gender || '—'),
                    el('span', {}, `born ${p.birthDate || '—'}`),
                    el('span', {}, p.city || ''),
                ),
                el('div', { class: 'id' }, `Patient/${p.id}`),
            );
            grid.appendChild(card);
        }

        app.innerHTML = '';
        app.append(head, search, grid);
    } catch (e) {
        renderError(e.message);
    }
}

function filterCards(q) {
    const needle = q.toLowerCase().trim();
    document.querySelectorAll('.patient-card').forEach((card) => {
        card.style.display = !needle || card.dataset.search.includes(needle) ? '' : 'none';
    });
}

// ---------- patient detail ----------
async function renderPatientDetail(pid) {
    setLoading();
    try {
        const data = await api(`/ui/api/patients/${pid}`);
        const p = data.patient;
        const name = (p.name || [{}])[0];
        const fullName = `${(name.given || []).join(' ')} ${name.family || ''}`.trim() || pid;

        const crumbs = el('div', { class: 'crumbs' },
            el('a', { href: '#/' }, '← Patients'),
            ' / ',
            el('span', {}, fullName),
        );

        const ident = (p.identifier || [{}])[0];

        const hero = el('section', { class: 'patient-hero' },
            el('h1', {},
                fullName, ' ',
                el('span', { class: 'country-pill' }, (p.address || [{}])[0]?.country || ''),
            ),
            el('div', { class: 'meta-row' },
                `${p.gender || '—'} · born ${p.birthDate || '—'} · ${(p.address || [{}])[0]?.city || ''}, ${(p.address || [{}])[0]?.country || ''}`,
            ),
            el('div', { class: 'ids' },
                `Patient/${pid}`,
                ident?.system ? ` · identifier: ${ident.value} (${ident.system})` : '',
            ),
        );

        const docHeader = el('h2', { style: 'font-size:16px;margin:16px 0 10px 0;' }, 'Compiled documents');
        const docRow = el('div', { class: 'doc-row' });
        for (const d of data.documents) {
            const meta = CATEGORY_LABELS[d.category];
            docRow.appendChild(el('a', { class: 'doc-card', href: `#/p/${pid}/doc/${d.category}` },
                el('div', { class: 'label' }, `${meta.icon}  ${meta.label}`),
                el('div', { class: 'sub' }, `Binary/${d.binary}`),
                el('div', { class: 'open' }, 'open document →'),
            ));
        }

        const resHeader = el('h2', { style: 'font-size:16px;margin:24px 0 10px 0;' }, 'All resources in this patient compartment');
        const buckets = data.buckets;
        const bucketsContainer = el('div', {});
        const sortedTypes = Object.keys(buckets).sort();
        for (const rtype of sortedTypes) {
            const items = buckets[rtype];
            const det = el('details', { class: 'resource-bucket' });
            det.appendChild(el('summary', {},
                el('span', {}, rtype),
                el('span', { class: 'count' }, String(items.length)),
            ));
            const list = el('div', { class: 'resource-list' });
            for (const r of items) {
                list.appendChild(el('div', { class: 'resource-row' },
                    el('div', { class: 'rid' }, `${rtype}/${r.id}`),
                    el('div', { class: 'display' }, pickName(r) || el('em', { style: 'color:#9c9c93' }, '(no display)')),
                    el('div', { class: 'ts' }, pickDate(r) || ''),
                ));
            }
            det.appendChild(list);
            bucketsContainer.appendChild(det);
        }

        app.innerHTML = '';
        app.append(crumbs, hero, docHeader, docRow, resHeader, bucketsContainer);
    } catch (e) {
        renderError(e.message);
    }
}

// ---------- document viewer ----------
async function renderDocument(pid, category) {
    setLoading();
    try {
        const bundle = await api(`/ui/api/patients/${pid}/doc/${category}`);
        const meta = CATEGORY_LABELS[category];
        const composition = bundle.entry?.[0]?.resource;
        const entries = bundle.entry || [];

        const crumbs = el('div', { class: 'crumbs' },
            el('a', { href: '#/' }, 'Patients'),
            ' / ',
            el('a', { href: `#/p/${pid}` }, `Patient/${pid}`),
            ' / ',
            el('span', {}, meta.label),
        );

        const header = el('section', { class: 'doc-header' },
            el('h1', {}, `${meta.icon}  ${meta.label}`),
            el('div', { class: 'summary' },
                el('span', {}, el('strong', {}, 'Bundle.id: '), bundle.id),
                el('span', {}, el('strong', {}, 'Entries: '), String(entries.length)),
                el('span', {}, el('strong', {}, 'Profile: '), (bundle.meta?.profile || [''])[0]),
                el('span', {}, el('strong', {}, 'Timestamp: '), bundle.timestamp || ''),
            ),
        );

        // sections from the Composition
        const sectionsBlock = el('div');
        if (composition?.section) {
            for (const sec of composition.section) {
                const code = sec.code?.coding?.[0];
                const block = el('section', { class: 'section-block' },
                    el('h3', {},
                        el('span', {}, sec.title || code?.display || 'Section'),
                        el('span', { class: 'count' }, `LOINC ${code?.code || ''} · ${(sec.entry || []).length} entries`),
                    ),
                );
                const entriesBlock = el('div', { class: 'entries' });
                for (const ent of sec.entry || []) {
                    const target = entries.find((e) => e.fullUrl === ent.reference || e.resource && `${e.resource.resourceType}/${e.resource.id}` === ent.reference);
                    const res = target?.resource;
                    entriesBlock.appendChild(el('div', { class: 'entry-row' },
                        el('div', { style: 'font-family:ui-monospace,monospace;font-size:11px;color:#6c6c63;margin-bottom:2px;' }, ent.reference),
                        el('div', {}, res ? pickName(res) || '(no display)' : '(unresolved reference)'),
                        res ? el('div', { style: 'font-size:11px;color:#6c6c63;' }, pickDate(res)) : null,
                    ));
                }
                block.appendChild(entriesBlock);
                sectionsBlock.appendChild(block);
            }
        }

        // raw json toggle
        const rawWrapper = el('div', { style: 'margin-top:18px' });
        const toggle = el('button', { class: 'raw-toggle' }, 'View raw FHIR Bundle');
        const pre = el('pre', { class: 'json-dump', style: 'display:none;' }, JSON.stringify(bundle, null, 2));
        toggle.addEventListener('click', () => {
            const showing = pre.style.display !== 'none';
            pre.style.display = showing ? 'none' : 'block';
            toggle.textContent = showing ? 'View raw FHIR Bundle' : 'Hide raw FHIR Bundle';
        });
        rawWrapper.append(toggle, pre);

        app.innerHTML = '';
        app.append(crumbs, header, sectionsBlock, rawWrapper);
    } catch (e) {
        renderError(e.message);
    }
}

// ---------- server page ----------
async function renderServerPage() {
    setLoading();
    try {
        const [info, cap] = await Promise.all([
            api('/ui/api/server-info'),
            api('/metadata'),
        ]);
        const head = el('div', { class: 'page-head' },
            el('h1', {}, 'Server'),
            el('div', { class: 'meta' }, `${info.base_url} · ${cap.fhirVersion}`),
        );
        const stats = el('div', { class: 'stats-grid' },
            el('div', { class: 'stat-card' },
                el('div', { class: 'label' }, 'Patients'),
                el('div', { class: 'value' }, String(info.patients)),
            ),
            el('div', { class: 'stat-card' },
                el('div', { class: 'label' }, 'Total atomic resources'),
                el('div', { class: 'value' }, String(info.total_resources)),
            ),
            el('div', { class: 'stat-card' },
                el('div', { class: 'label' }, 'Resource types served'),
                el('div', { class: 'value' }, String(Object.keys(info.by_type).length)),
            ),
            el('div', { class: 'stat-card' },
                el('div', { class: 'label' }, 'Priority categories'),
                el('div', { class: 'value' }, String(info.categories.length)),
            ),
            el('div', { class: 'stat-card' },
                el('div', { class: 'label' }, 'Implementation Guides'),
                el('div', { class: 'value' }, String(cap.implementationGuide?.length || 0)),
            ),
        );

        const supportedH = el('h2', { style: 'font-size:16px;margin:24px 0 10px 0;' }, 'Supported resources');
        const tbl = el('table', { class: 'endpoints-table' },
            el('thead', {}, el('tr', {},
                el('th', {}, 'Type'),
                el('th', {}, 'Interactions'),
                el('th', {}, 'Search params'),
                el('th', {}, 'Stored'),
            )),
            el('tbody', {}, ...(cap.rest?.[0]?.resource || []).map((r) => el('tr', {},
                el('td', {}, el('code', {}, r.type)),
                el('td', {}, (r.interaction || []).map((i) => i.code).join(', ')),
                el('td', { style: 'font-size:12px;color:#6c6c63;' }, (r.searchParam || []).map((p) => p.name).join(', ')),
                el('td', {}, String(info.by_type[r.type] || 0)),
            ))),
        );

        const igH = el('h2', { style: 'font-size:16px;margin:24px 0 10px 0;' }, 'Implementation Guides referenced');
        const igList = el('ul', { style: 'list-style:none;padding:0;' });
        for (const ig of cap.implementationGuide || []) {
            igList.appendChild(el('li', { style: 'padding:6px 0;font-family:ui-monospace,monospace;font-size:12px;color:#6c6c63;' }, ig));
        }

        app.innerHTML = '';
        app.append(head, stats, supportedH, tbl, igH, igList);
    } catch (e) {
        renderError(e.message);
    }
}

// ---------- router ----------
function highlightNav(hash) {
    document.querySelectorAll('[data-nav]').forEach((a) => {
        a.classList.toggle('active',
            (hash === '#/' && a.getAttribute('href') === '#/')
            || (hash === '#/server' && a.getAttribute('href') === '#/server')
        );
    });
}

async function route() {
    const hash = location.hash || '#/';
    highlightNav(hash);
    const m_doc = hash.match(/^#\/p\/([^/]+)\/doc\/([^/]+)$/);
    const m_pat = hash.match(/^#\/p\/([^/]+)$/);
    if (m_doc) return renderDocument(m_doc[1], m_doc[2]);
    if (m_pat) return renderPatientDetail(m_pat[1]);
    if (hash === '#/server') return renderServerPage();
    return renderPatientList();
}

// ---------- boot ----------
(async () => {
    try {
        const info = await api('/ui/api/server-info');
        envPill.textContent = `env: ${info.env}`;
    } catch (e) {
        envPill.textContent = 'env: ?';
    }
})();

window.addEventListener('hashchange', route);
window.addEventListener('load', route);
route();
