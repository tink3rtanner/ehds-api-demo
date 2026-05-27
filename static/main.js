// ehds-api viewer — hash-routed SPA, no build step, vanilla JS.
// shows the underlying FHIR REST URLs + identifier systems + profile URLs +
// terminology codes + curl snippets so a connectathon attendee can see the
// wire-level shape behind the pretty UI.

const app = document.getElementById('app');
const footerTags = document.getElementById('footer-tags');

const CATEGORY_LABELS = {
    'patient-summary':   { label: 'Patient Summary',     icon: '📋',
                           profile: 'http://hl7.eu/fhir/ig/eps/StructureDefinition/Bundle-eu-eps',
                           ig: 'https://build.fhir.org/ig/hl7-eu/eps/' },
    'laboratory-report': { label: 'Laboratory Report',   icon: '🧪',
                           profile: 'http://hl7.eu/fhir/ig/laboratory/StructureDefinition/Bundle-eu-lab',
                           ig: 'https://build.fhir.org/ig/hl7-eu/laboratory/' },
    'discharge-report':  { label: 'Discharge Report',    icon: '🏥',
                           profile: 'http://hl7.eu/fhir/ig/hdr/StructureDefinition/Bundle-eu-hdr',
                           ig: 'https://build.fhir.org/ig/hl7-eu/hdr/' },
    'imaging-report':    { label: 'Imaging Report',      icon: '🩻',
                           profile: 'http://hl7.eu/fhir/ig/imaging/StructureDefinition/Bundle-eu-imaging',
                           ig: 'https://build.fhir.org/ig/hl7-eu/imaging-r4/' },
};

const COUNTRY_NAMES = {
    AT: 'Austria', DE: 'Germany', IT: 'Italy', FR: 'France', ES: 'Spain',
    PT: 'Portugal', NL: 'Netherlands', PL: 'Poland', SE: 'Sweden', FI: 'Finland',
};

// known systems we link out to
const SYSTEM_LINKS = {
    'http://loinc.org': (code) => `https://loinc.org/${encodeURIComponent(code)}/`,
    'http://snomed.info/sct': (code) => `https://browser.ihtsdotools.org/?perspective=full&conceptId1=${encodeURIComponent(code)}`,
    'http://hl7.org/fhir/sid/cvx': (code) => `https://www2a.cdc.gov/vaccines/iis/iisstandards/vaccines.asp?rpt=cvx`,
    'http://hl7.org/fhir/sid/ndc': (code) => `https://dailymed.nlm.nih.gov/dailymed/search.cfm?query=${encodeURIComponent(code)}`,
    'http://dicom.nema.org/resources/ontology/DCM': (code) => `https://dicom.nema.org/medical/dicom/current/output/chtml/part16/sect_CID_29.html`,
};

// ---------- tiny helpers ----------
const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') node.className = v;
        else if (k === 'style') node.style.cssText = v;
        else if (k.startsWith('on')) node.addEventListener(k.slice(2).toLowerCase(), v);
        else if (k === 'html') node.innerHTML = v;
        else node.setAttribute(k, v);
    }
    for (const c of children.flat()) {
        if (c == null || c === false) continue;
        node.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
    }
    return node;
};

async function api(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${path}: ${r.status} ${(await r.text()).slice(0, 200)}`);
    return r.json();
}

function toast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast._h);
    toast._h = setTimeout(() => { t.hidden = true; }, 1700);
}

async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        toast('copied');
    } catch {
        // fallback
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
        toast('copied');
    }
}

function renderError(message) {
    app.innerHTML = '';
    app.appendChild(el('div', { class: 'error' }, message));
}

function setLoading() {
    app.innerHTML = '';
    app.appendChild(el('div', { class: 'loading' }, 'loading…'));
}

// ---------- URL chips ----------
// GET chips are clickable links that open the actual server response in a new
// tab via /ui/api/proxy (which injects a dev bearer server-side, so the link
// Just Works in a browser without the user needing a token). non-GET methods
// stay as spans because clicking can't POST/PUT/DELETE. opts.noLink forces a
// non-link span (used for illustrative paths like `/Patient/{id}`).
function urlChip(method, path, opts = {}) {
    const display = `${method} ${path}`;
    const hasPlaceholder = /\{[^}]+\}|\\\$/.test(path);
    const linkable = method === 'GET' && !opts.noLink && !hasPlaceholder;
    const tag = linkable ? 'a' : 'span';
    const attrs = { class: 'url-chip with-copy' + (linkable ? ' linkable' : '') };
    if (linkable) {
        attrs.href = `/ui/api/proxy?path=${encodeURIComponent(path)}`;
        attrs.target = '_blank';
        attrs.rel = 'noopener';
        attrs.title = 'open live JSON from the server';
    }
    return el(tag, attrs,
        el('span', { class: 'verb' }, method),
        el('span', {}, ` ${path}`),
        el('button', {
            class: 'copy-btn', title: 'copy URL',
            onclick: (e) => {
                e.preventDefault();
                e.stopPropagation();
                copyText(opts.toCopy || display);
            },
        }, '⧉'),
    );
}

// ---------- JSON modal ----------
const modal = document.getElementById('json-modal');
const modalTitle = document.getElementById('modal-title-text');
const modalUrl = document.getElementById('modal-url');
const modalBody = document.getElementById('modal-body');
document.getElementById('modal-close').addEventListener('click', closeModal);
document.getElementById('modal-copy').addEventListener('click', () => {
    copyText(modalBody.textContent);
});
modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
window.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

function openModalText(title, urlLabel, text) {
    modalTitle.textContent = title;
    modalUrl.textContent = urlLabel || '';
    modalBody.innerHTML = colorizeJson(text);
    modal.hidden = false;
}

async function openResourceModal(rtype, rid) {
    try {
        const json = await api(`/ui/api/raw/${rtype}/${rid}`);
        openModalText(`${rtype}/${rid}`, `GET /${rtype}/${rid}  →  application/fhir+json`, JSON.stringify(json, null, 2));
    } catch (e) {
        openModalText(`error`, '', `// ${e.message}`);
    }
}

function openBundleModal(pid, category, bundle) {
    openModalText(
        `Bundle/${bundle.id}`,
        `GET /Binary/doc-${pid}-${category}  →  Bundle.type=document  (${bundle.entry?.length || 0} entries)`,
        JSON.stringify(bundle, null, 2),
    );
}

function closeModal() { modal.hidden = true; }

function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function colorizeJson(text) {
    const escaped = escapeHtml(text);
    return escaped.replace(
        /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)/g,
        (m) => {
            let cls = 'json-number';
            if (/^"/.test(m)) cls = /:$/.test(m) ? 'json-key' : 'json-string';
            else if (m === 'true' || m === 'false') cls = 'json-boolean';
            else if (m === 'null') cls = 'json-null';
            return `<span class="${cls}">${m}</span>`;
        },
    );
}

// ---------- coding helpers ----------
function codingLink(coding) {
    if (!coding || !coding.system || !coding.code) return null;
    const linkFn = SYSTEM_LINKS[coding.system];
    if (!linkFn) return null;
    return linkFn(coding.code);
}

function codingChip(coding) {
    const href = codingLink(coding);
    const label = `${shortSystem(coding.system)} ${coding.code}`;
    if (href) {
        return el('a', { class: 'code-chip', href, target: '_blank', rel: 'noopener', title: coding.display || '' }, label);
    }
    return el('span', { class: 'code-chip', title: coding.display || '' }, label);
}

function shortSystem(sys) {
    if (!sys) return '';
    if (sys.includes('loinc')) return 'LOINC';
    if (sys.includes('snomed')) return 'SNOMED';
    if (sys.includes('cvx')) return 'CVX';
    if (sys.includes('ndc')) return 'NDC';
    if (sys.includes('dicom')) return 'DCM';
    if (sys.includes('terminology.hl7.org')) return 'HL7';
    return sys.replace(/^https?:\/\//, '').slice(0, 18);
}

function pickName(resource) {
    return resource?.code?.text || resource?.code?.coding?.[0]?.display
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
}

function pickDate(resource) {
    return resource?.effectiveDateTime || resource?.occurrenceDateTime
        || resource?.recordedDate || resource?.authoredOn || resource?.whenHandedOver
        || resource?.performedDateTime || resource?.period?.start
        || resource?.issued || resource?.started || resource?.collection?.collectedDateTime
        || resource?.date || '';
}

function pickCoding(resource) {
    return resource?.code?.coding?.[0]
        || resource?.vaccineCode?.coding?.[0]
        || resource?.type?.coding?.[0]
        || resource?.medicationCodeableConcept?.coding?.[0]
        || null;
}

// ---------- patient list ----------
const PATIENT_VIEW_KEY = 'ehds-patient-view';

async function renderPatientList() {
    setLoading();
    try {
        const patients = await api('/ui/api/patients');
        const view = localStorage.getItem(PATIENT_VIEW_KEY) || 'grid';

        const head = el('div', { class: 'page-head' },
            el('h1', {}, 'Patients'),
            el('div', { class: 'meta' },
                `${patients.length} synthetic patients · ITI-78-style PDQm + IPA · all read-only · `,
                el('a', { href: '/ui/api/proxy?path=/Patient', target: '_blank', rel: 'noopener', class: 'mono', style: 'color:var(--text-muted);' },
                    'GET /Patient ↗'),
            ),
        );

        const viewToggle = el('div', { class: 'view-toggle', role: 'tablist' },
            viewBtn('grid', '▦ Grid', view),
            viewBtn('table', '☰ Table', view),
        );
        const search = el('div', { class: 'search-box' },
            el('input', {
                placeholder: 'filter by name, country, city, id…',
                'aria-label': 'filter patients',
                oninput: (e) => filterPatientRows(e.target.value),
            }),
        );

        const body = view === 'table'
            ? renderPatientTable(patients)
            : renderPatientGrid(patients);

        app.innerHTML = '';
        app.append(head, el('div', { class: 'list-controls' }, search, viewToggle), body);
    } catch (e) {
        renderError(e.message);
    }
}

function viewBtn(key, label, current) {
    return el('button', {
        class: 'btn-ghost' + (current === key ? ' active' : ''),
        onclick: () => {
            localStorage.setItem(PATIENT_VIEW_KEY, key);
            renderPatientList();
        },
    }, label);
}

function docBadges(pid) {
    return el('span', { class: 'doc-badges' },
        ...Object.entries(CATEGORY_LABELS).map(([cat, meta]) =>
            el('a', {
                class: 'doc-badge',
                href: `#/p/${pid}/doc/${cat}`,
                title: meta.label,
                onclick: (e) => e.stopPropagation(),  // don't bubble to card link
            }, meta.icon),
        ),
    );
}

function renderPatientGrid(patients) {
    const grid = el('div', { class: 'patient-grid', id: 'patient-grid' });
    for (const p of patients) {
        const fullName = `${p.given} ${p.family}`.trim();
        const card = el('a', {
            class: 'patient-card patient-row',
            href: `#/p/${p.id}`,
            'data-search': `${fullName} ${p.family} ${p.country} ${COUNTRY_NAMES[p.country] || ''} ${p.id} ${p.city || ''} ${p.identifier_value || ''}`.toLowerCase(),
        },
            el('div', { class: 'name' }, fullName,
                el('span', { class: 'country-pill', title: COUNTRY_NAMES[p.country] || p.country }, p.country),
            ),
            el('div', { class: 'meta' },
                el('span', {}, p.gender || '—'),
                el('span', {}, `born ${p.birthDate || '—'}`),
                el('span', {}, p.city || ''),
            ),
            p.identifier_value ? el('div', { class: 'ident',
                title: `identifier system: ${p.identifier_system}`,
                style: 'display:block;margin-top:6px;font-size:11px;color:var(--text-faint);font-family:ui-monospace,monospace;',
            }, p.identifier_value) : null,
            el('div', { class: 'ident' },
                el('span', { class: 'id' }, `Patient/${p.id}`),
                docBadges(p.id),
            ),
        );
        grid.appendChild(card);
    }
    return grid;
}

function renderPatientTable(patients) {
    const tbl = el('table', { class: 'patients-table' },
        el('thead', {}, el('tr', {},
            el('th', {}, 'FHIR id'),
            el('th', {}, 'Name'),
            el('th', {}, 'Country'),
            el('th', {}, 'Birth'),
            el('th', {}, 'Sex'),
            el('th', {}, 'Identifier'),
            el('th', {}, 'Documents'),
            el('th', {}, ''),
        )),
        el('tbody', {}, ...patients.map((p) => {
            const fullName = `${p.given} ${p.family}`.trim();
            return el('tr', {
                class: 'patient-row',
                'data-search': `${fullName} ${p.country} ${COUNTRY_NAMES[p.country] || ''} ${p.id} ${p.city || ''} ${p.identifier_value || ''}`.toLowerCase(),
            },
                el('td', { class: 'mono' },
                    el('a', { href: `#/p/${p.id}` }, `Patient/${p.id}`),
                ),
                el('td', {}, fullName),
                el('td', {}, el('span', { class: 'country-pill', title: COUNTRY_NAMES[p.country] || p.country }, p.country)),
                el('td', { class: 'mono' }, p.birthDate || '—'),
                el('td', {}, p.gender || '—'),
                el('td', { class: 'mono', style: 'font-size:11px;color:var(--text-faint);' }, p.identifier_value || '—'),
                el('td', {}, docBadges(p.id)),
                el('td', {}, el('a', { href: `#/p/${p.id}`, class: 'btn-ghost', style: 'padding:3px 10px;font-size:11px;' }, 'open →')),
            );
        })),
    );
    return tbl;
}

function filterPatientRows(q) {
    const needle = q.toLowerCase().trim();
    document.querySelectorAll('.patient-row').forEach((row) => {
        row.style.display = !needle || (row.dataset.search || '').includes(needle) ? '' : 'none';
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
        const addr = (p.address || [{}])[0] || {};
        const ident = (p.identifier || [{}])[0] || {};
        const lang = p.communication?.[0]?.language?.coding?.[0]?.code;

        const counts = data.buckets;
        const activeConditions = (counts.Condition || []).filter(c =>
            c.clinicalStatus?.coding?.some(s => s.code === 'active')).length;
        const activeMeds = (counts.MedicationStatement || []).filter(m => m.status === 'active').length;
        const lastEncounter = (counts.Encounter || [])
            .map(e => e.period?.start || e.period?.end)
            .filter(Boolean)
            .sort()
            .pop();
        const totalRes = Object.values(counts).reduce((a, b) => a + b.length, 0);

        const crumbs = el('div', { class: 'crumbs' },
            el('a', { href: '#/' }, 'Patients'),
            el('span', { class: 'sep' }, '/'),
            el('span', {}, fullName),
        );

        const hero = el('section', { class: 'patient-hero' },
            el('h1', {}, fullName,
                addr.country ? el('span', { class: 'country-pill', title: COUNTRY_NAMES[addr.country] || addr.country }, addr.country) : null,
            ),
            el('div', { class: 'meta-row' },
                `${p.gender || '—'} · born ${p.birthDate || '—'} · ${addr.city || ''}${addr.country ? `, ${addr.country}` : ''}${lang ? ` · speaks ${lang}` : ''}`,
            ),
            el('div', { class: 'entry-bar', style: 'margin-top:12px;' },
                el('span', { class: 'chip' }, el('span', { class: 'n' }, String(totalRes)), ' resources'),
                el('span', { class: 'chip' }, el('span', { class: 'n' }, String((counts.Condition || []).length)),
                    ' conditions',
                    activeConditions ? el('span', { style: 'color:var(--accent);margin-left:4px;' }, `(${activeConditions} active)`) : null,
                ),
                el('span', { class: 'chip' }, el('span', { class: 'n' }, String((counts.MedicationStatement || []).length)),
                    ' med statements',
                    activeMeds ? el('span', { style: 'color:var(--accent);margin-left:4px;' }, `(${activeMeds} active)`) : null,
                ),
                el('span', { class: 'chip' }, el('span', { class: 'n' }, String((counts.AllergyIntolerance || []).length)), ' allergies'),
                el('span', { class: 'chip' }, el('span', { class: 'n' }, String((counts.Immunization || []).length)), ' immunizations'),
                el('span', { class: 'chip' }, el('span', { class: 'n' }, String((counts.Observation || []).length)), ' observations'),
                lastEncounter ? el('span', { class: 'chip' }, 'last encounter ', el('span', { class: 'n' }, lastEncounter.slice(0,10))) : null,
            ),
            el('div', { class: 'hero-grid' },
                fieldBlock('FHIR id',  el('span', { class: 'mono' }, `Patient/${p.id}`)),
                fieldBlock('Identifier system', el('span', { class: 'mono' }, ident.system || '—')),
                fieldBlock('Identifier value', el('span', { class: 'mono' }, ident.value || '—')),
                fieldBlock('Address',
                    el('span', {}, [(addr.line || []).join(' '), addr.postalCode, addr.city, addr.country].filter(Boolean).join(', ') || '—')),
                fieldBlock('Telecom', el('span', {}, (p.telecom || []).map(t => `${t.system}:${t.value}`).join(' · ') || '—')),
                fieldBlock('Languages', el('span', {}, (p.communication || []).map(c => c.language?.coding?.[0]?.code).filter(Boolean).join(', ') || '—')),
            ),
        );

        const tech = el('section', { class: 'tech-block' },
            el('h3', {}, 'Behind the scenes · raw FHIR endpoints'),
            techRow('Read Patient',                urlChip('GET',  `/Patient/${pid}`)),
            techRow('All resources in compartment', urlChip('GET',  `/Patient/${pid}/$everything`)),
            techRow('DocumentReferences',           urlChip('GET',  `/DocumentReference?patient=${pid}`)),
            techRow('PDQm $match (example)',        urlChip('POST', `/Patient/$match`)),
            techRow('Compiled FHIR documents',
                el('span', { class: 'btn-group' },
                    ...Object.entries(CATEGORY_LABELS).map(([cat, meta]) =>
                        el('a', { class: 'btn-ghost', href: `#/p/${pid}/doc/${cat}` }, `${meta.icon} ${cat}`),
                    ),
                ),
            ),
            techRow('Open Patient JSON',
                el('button', { class: 'btn-ghost', onclick: () => openResourceModal('Patient', pid) }, '{ } view raw JSON'),
            ),
        );

        const docHeader = el('section', { class: 'section-title' },
            el('span', {}, 'Compiled documents (on demand)'),
            el('span', { style: 'font-weight:500;color:var(--text-muted);text-transform:none;letter-spacing:normal;font-size:11px;' },
                'click a category → Bundle compiled from the resources below'),
        );
        const docRow = el('div', { class: 'doc-row' });
        for (const d of data.documents) {
            const meta = CATEGORY_LABELS[d.category];
            docRow.appendChild(el('a', { class: 'doc-card', href: `#/p/${pid}/doc/${d.category}` },
                el('div', { class: 'label' }, `${meta.icon}  ${meta.label}`),
                el('div', { class: 'sub' }, `GET /Binary/${d.binary}`),
                el('div', { class: 'open' }, 'open document →'),
            ));
        }

        const resHeader = el('section', { class: 'section-title' },
            el('span', {}, 'Patient compartment resources'),
            el('span', { style: 'font-weight:500;color:var(--text-muted);text-transform:none;letter-spacing:normal;font-size:11px;' },
                'click a resource id → raw JSON'),
        );
        const buckets = data.buckets;
        const bucketsContainer = el('div', {});
        const sortedTypes = Object.keys(buckets).sort();
        for (const rtype of sortedTypes) {
            const items = buckets[rtype];
            const det = el('details', { class: 'resource-bucket' });
            det.appendChild(el('summary', {},
                el('span', {}, rtype,
                    el('span', { style: 'color:var(--text-faint);font-weight:500;margin-left:8px;font-family:ui-monospace,monospace;font-size:11px;' }, `GET /${rtype}?patient=${pid}`),
                ),
                el('span', { class: 'count' }, String(items.length)),
            ));
            const list = el('div', { class: 'resource-list' });
            for (const r of items) {
                const coding = pickCoding(r);
                list.appendChild(el('div', { class: 'resource-row', onclick: () => openResourceModal(rtype, r.id) },
                    el('div', { class: 'rid', title: 'view raw JSON' },
                        el('span', { style: 'color:var(--text-faint);margin-right:6px;' }, '{ }'),
                        `${rtype}/${r.id}`,
                    ),
                    el('div', { class: 'display' },
                        pickName(r) || el('em', { style: 'color:var(--text-faint)' }, '(no display)'),
                        coding ? codingChip(coding) : null,
                    ),
                    el('div', { class: 'ts' }, pickDate(r) || ''),
                ));
            }
            det.appendChild(list);
            bucketsContainer.appendChild(det);
        }

        app.innerHTML = '';
        app.append(crumbs, hero, tech, docHeader, docRow, resHeader, bucketsContainer);
    } catch (e) {
        renderError(e.message);
    }
}

function fieldBlock(label, value) {
    return el('div', {},
        el('div', { class: 'field-label' }, label),
        el('div', { class: 'field-value' }, value),
    );
}

function techRow(label, value) {
    return el('div', { class: 'row' },
        el('div', { class: 'lbl' }, label),
        el('div', {}, value),
    );
}

// ---------- document viewer ----------
async function renderDocument(pid, category) {
    setLoading();
    try {
        const t0 = performance.now();
        const bundle = await api(`/ui/api/patients/${pid}/doc/${category}`);
        const compileMs = Math.round(performance.now() - t0);
        const bundleJson = JSON.stringify(bundle);
        const sizeKb = (bundleJson.length / 1024).toFixed(1);
        const meta = CATEGORY_LABELS[category];
        const composition = bundle.entry?.[0]?.resource;
        const entries = bundle.entry || [];

        const crumbs = el('div', { class: 'crumbs' },
            el('a', { href: '#/' }, 'Patients'),
            el('span', { class: 'sep' }, '/'),
            el('a', { href: `#/p/${pid}` }, `Patient/${pid}`),
            el('span', { class: 'sep' }, '/'),
            el('span', {}, meta.label),
        );

        const header = el('section', { class: 'doc-header' },
            el('h1', {}, meta.icon, el('span', {}, meta.label)),
            el('div', { class: 'meta-row' }, `On-demand FHIR document Bundle (type=document) for Patient/${pid}`),
            el('div', { class: 'summary' },
                summaryItem('FHIR REST', urlChip('GET', `/Binary/doc-${pid}-${category}`)),
                summaryItem('Bundle.id', el('span', { class: 'val mono' }, bundle.id || '—')),
                summaryItem('Composition.id', el('span', { class: 'val mono' }, composition?.id || '—')),
                summaryItem('Entries', el('span', { class: 'val' }, String(entries.length))),
                summaryItem('Profile',
                    el('a', { class: 'val mono', href: meta.ig, target: '_blank', rel: 'noopener' }, meta.profile),
                ),
                summaryItem('Timestamp', el('span', { class: 'val mono' }, bundle.timestamp || '—')),
                summaryItem('Document type (LOINC)',
                    composition?.type?.coding?.[0]
                        ? codingChip(composition.type.coding[0])
                        : el('span', { class: 'val' }, '—'),
                ),
                summaryItem('Bundle.identifier',
                    el('span', { class: 'val mono' }, bundle.identifier?.value || '—')),
                summaryItem('Size · compile time',
                    el('span', { class: 'val mono' }, `${sizeKb} KB · ${compileMs} ms`)),
            ),
        );

        const types = entries.reduce((acc, e) => {
            const t = e?.resource?.resourceType || '?';
            acc[t] = (acc[t] || 0) + 1;
            return acc;
        }, {});
        const entryBar = el('div', { class: 'entry-bar' });
        const validationChip = el('span', { class: 'chip' }, '… validating');
        entryBar.appendChild(validationChip);
        for (const [t, n] of Object.entries(types).sort((a, b) => b[1] - a[1])) {
            entryBar.appendChild(el('span', { class: 'chip' }, el('span', { class: 'n' }, n), ' ', t));
        }
        // fire-and-forget validation, update chip when done
        api(`/ui/api/validate/${pid}/${category}`).then((res) => {
            validationChip.innerHTML = '';
            const ok = res.ok;
            validationChip.style.background = ok ? 'var(--accent-soft)' : 'var(--danger-soft)';
            validationChip.style.color = ok ? 'var(--accent)' : 'var(--danger)';
            validationChip.style.borderColor = ok ? 'var(--accent-soft)' : '#fbcaca';
            validationChip.append(ok ? '✓ structurally valid' : '✗ ' + (res.issues?.[0] || 'validation failed').slice(0, 70));
            validationChip.title = ok ? 'fhir.resources pydantic R4 validation passed'
                                      : (res.issues || []).join('\n');
        }).catch(() => {
            validationChip.textContent = 'validation skipped';
        });

        const sectionsBlock = el('div');
        if (composition?.section) {
            for (const sec of composition.section) {
                const code = sec.code?.coding?.[0];
                const block = el('section', { class: 'section-block' },
                    el('h3', {},
                        el('span', {}, sec.title || code?.display || 'Section'),
                        el('div', { class: 'meta-right' },
                            code ? el('a', {
                                class: 'loinc-pill',
                                href: codingLink(code) || '#',
                                target: '_blank', rel: 'noopener',
                                title: code.display || '',
                            }, `LOINC ${code.code}`) : null,
                            el('span', {}, `${(sec.entry || []).length} entries`),
                        ),
                    ),
                );
                const entriesBlock = el('div', { class: 'entries' });
                for (const ent of sec.entry || []) {
                    const target = entries.find((e) =>
                        e.fullUrl === ent.reference
                        || (e.resource && `${e.resource.resourceType}/${e.resource.id}` === ent.reference)
                    );
                    const res = target?.resource;
                    const refStr = ent.reference;
                    entriesBlock.appendChild(el('div', { class: 'entry-row' },
                        el('div', { class: 'left' },
                            el('div', {
                                class: 'ref',
                                onclick: () => res && openResourceModal(res.resourceType, res.id),
                            }, refStr),
                            el('div', { class: 'display' },
                                res ? (pickName(res) || el('em', { style: 'color:var(--text-faint)' }, '(no display)')) : '(unresolved reference)',
                                res && pickCoding(res) ? codingChip(pickCoding(res)) : null,
                            ),
                        ),
                        el('div', { class: 'right' }, res ? pickDate(res) || '' : ''),
                    ));
                }
                block.appendChild(entriesBlock);
                sectionsBlock.appendChild(block);
            }
        }

        const actionRow = el('div', { class: 'btn-group', style: 'margin-top:18px;' },
            el('button', { class: 'btn-primary', onclick: () => openBundleModal(pid, category, bundle) },
                '{ } view raw FHIR Bundle'),
            el('a', { class: 'btn', href: `/ui/api/patients/${pid}/doc/${category}`, target: '_blank', rel: 'noopener' },
                'open Bundle JSON in new tab ↗'),
            el('button', { class: 'btn', onclick: () => copyText(JSON.stringify(bundle, null, 2)) },
                'copy Bundle JSON'),
            el('a', { class: 'btn', href: meta.ig, target: '_blank', rel: 'noopener' },
                'EU IG ↗'),
        );

        app.innerHTML = '';
        app.append(crumbs, header, entryBar, sectionsBlock, actionRow);
    } catch (e) {
        renderError(e.message);
    }
}

function summaryItem(label, value) {
    return el('div', {},
        el('div', { class: 'lbl' }, label),
        value,
    );
}

// ---------- server page ----------
async function renderServerPage() {
    setLoading();
    try {
        const [info, build, cap, smart] = await Promise.all([
            api('/ui/api/server-info'),
            api('/ui/api/build-info'),
            api('/metadata'),
            api('/.well-known/smart-configuration'),
        ]);
        const head = el('div', { class: 'page-head' },
            el('h1', {}, 'Server'),
            el('div', { class: 'meta' }, `${info.base_url} · FHIR R4 · synthetic data`),
        );
        const stats = el('div', { class: 'stats-grid' },
            statCard('Patients', info.patients, 'PDQm-searchable'),
            statCard('Total atomic resources', info.total_resources, 'across compartments'),
            statCard('Resource types', Object.keys(info.by_type).length, 'first-class'),
            statCard('Priority categories', info.categories.length, 'compiled on demand'),
            statCard('Implementation Guides', cap.implementationGuide?.length || 0, 'referenced'),
            statCard('Token TTL', `${build.token_ttl_seconds}s`, 'short-lived JWT bearers'),
        );

        // SMART config
        const smartBlock = el('section', { class: 'tech-block' },
            el('h3', {}, 'SMART backend services configuration'),
            techRow('Issuer', el('span', { class: 'mono' }, smart.issuer)),
            techRow('Token endpoint', urlChip('POST', new URL(smart.token_endpoint).pathname)),
            techRow('JWKS', urlChip('GET', new URL(smart.jwks_uri).pathname)),
            techRow('Grant types', el('span', {}, smart.grant_types_supported.join(', '))),
            techRow('Auth methods', el('span', {}, smart.token_endpoint_auth_methods_supported.join(', '))),
            techRow('Signing algs', el('span', {}, smart.token_endpoint_auth_signing_alg_values_supported.join(', '))),
            techRow('Supported scopes', el('span', {},
                smart.scopes_supported.map(s => el('code', { style: 'margin:0 4px 4px 0;display:inline-block;background:var(--surface-3);padding:1px 6px;border-radius:4px;' }, s)),
            )),
        );

        // Build info
        const buildBlock = el('section', { class: 'tech-block' },
            el('h3', {}, 'Build & runtime'),
            techRow('Git', el('span', { class: 'mono' }, build.git_sha || 'untracked')),
            techRow('Python', el('span', { class: 'mono' }, build.python)),
            techRow('FastAPI', el('span', { class: 'mono' }, build.fastapi)),
            techRow('fhir.resources', el('span', { class: 'mono' }, build.fhir_resources)),
            techRow('HL7 validator jar',
                build.validator_jar_present
                    ? el('span', {}, '✓ cached', el('span', { style: 'color:var(--text-faint);margin-left:8px;' }, `${build.validator_jar_size_mb} MB`))
                    : el('span', { style: 'color:var(--warn);' }, '✗ not cached (run ./fetch_validator.sh)'),
            ),
            techRow('Data directory', el('span', { class: 'mono' }, build.data_dir)),
            techRow('Rate limit', el('span', {}, `${build.rate_limit_per_min} req/min/client`)),
            techRow('Body cap', el('span', {}, `${(build.body_max_bytes / 1_048_576).toFixed(1)} MB`)),
        );

        // IGs cloned
        const igBlock = el('section', { class: 'tech-block' },
            el('h3', {}, 'HL7 Europe IG packages on disk'),
            ...(build.ig_packages.length
                ? build.ig_packages.map(ig => techRow(ig.name, el('span', { class: 'mono' }, ig.path)))
                : [el('div', { style: 'font-size:12px;color:var(--text-faint);' }, '(none — run ./download_igs.sh)')]),
        );

        // supported resources table
        const supportedH = el('section', { class: 'section-title' }, 'Supported resources');
        const tbl = el('table', { class: 'endpoints-table' },
            el('thead', {}, el('tr', {},
                el('th', {}, 'Type'),
                el('th', {}, 'Interactions'),
                el('th', {}, 'Search params'),
                el('th', { style: 'text-align:right;' }, 'Stored'),
            )),
            el('tbody', {}, ...(cap.rest?.[0]?.resource || []).map((r) => el('tr', {},
                el('td', {}, el('code', {}, r.type)),
                el('td', { style: 'font-size:11px;color:var(--text-muted);' }, (r.interaction || []).map((i) => i.code).join(', ')),
                el('td', { style: 'font-size:11px;color:var(--text-faint);' }, (r.searchParam || []).map((p) => p.name).join(', ')),
                el('td', { style: 'text-align:right;font-family:ui-monospace,monospace;' }, String(info.by_type[r.type] || 0)),
            ))),
        );

        // IG list
        const igH = el('section', { class: 'section-title' }, 'Implementation Guides referenced');
        const igList = el('div', { style: 'background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:6px 14px;' });
        for (const ig of cap.implementationGuide || []) {
            igList.appendChild(el('div', { style: 'padding:6px 0;font-family:ui-monospace,monospace;font-size:12px;color:var(--text-muted);border-bottom:1px solid var(--border-soft);display:flex;justify-content:space-between;gap:10px;' },
                el('span', {}, ig),
                el('button', { class: 'copy-btn', style: 'background:transparent;border:none;color:var(--text-faint);cursor:pointer;', onclick: () => copyText(ig) }, '⧉'),
            ));
        }

        app.innerHTML = '';
        app.append(head, stats, smartBlock, buildBlock, igBlock, supportedH, tbl, igH, igList);
    } catch (e) {
        renderError(e.message);
    }
}

function statCard(label, value, sub) {
    return el('div', { class: 'stat-card' },
        el('div', { class: 'label' }, label),
        el('div', { class: 'value' }, String(value)),
        sub ? el('div', { class: 'sub' }, sub) : null,
    );
}

// ---------- endpoints page (with live dev token) ----------
async function renderEndpointsPage() {
    setLoading();
    try {
        const [endpoints, token] = await Promise.all([
            api('/ui/api/endpoints'),
            fetch('/ui/api/dev-token', { method: 'POST' }).then(r => r.json()),
        ]);
        const head = el('div', { class: 'page-head' },
            el('h1', {}, 'Endpoints'),
            el('div', { class: 'meta' }, 'every demo-relevant route + a fresh dev bearer for copy-paste cURL'),
        );

        const tokenCard = el('section', { class: 'token-card' },
            el('div', { class: 'head' },
                el('h2', {}, 'Dev bearer (5 min TTL)'),
                el('div', { class: 'btn-group' },
                    el('button', { class: 'btn-ghost', onclick: () => copyText(token.access_token) }, 'Copy token'),
                    el('button', { class: 'btn-ghost', onclick: () => copyText(`export TOKEN=${token.access_token}`) }, 'Copy export TOKEN=…'),
                    el('button', { class: 'btn-ghost', onclick: () => renderEndpointsPage() }, 'Refresh'),
                ),
            ),
            el('div', { class: 'meta' }, `scope: ${token.scope}  ·  type: ${token.token_type}  ·  expires in ${token.expires_in}s`),
            el('div', { class: 'token-display' }, token.access_token),
            el('div', { class: 'meta' }, 'Signed by the server key (RS256). Verifiable via ',
                el('a', { href: '/.well-known/jwks.json', target: '_blank', rel: 'noopener' }, '/.well-known/jwks.json'),
                '. In production this bearer would be minted via /token after sending a JWT client assertion.',
            ),
        );

        const matchCard = buildMatchPlayground(token.access_token);
        const submitCard = buildSubmissionDemo(token.access_token);

        const list = el('div', {});
        for (const ep of endpoints) {
            const fullCurl = ep.curl.replace(/\$TOKEN/g, token.access_token);
            list.appendChild(el('section', { class: 'endpoint-card' },
                el('div', { class: 'head' },
                    el('span', { class: `method-badge ${ep.method.toLowerCase()}` }, ep.method),
                    el('span', { class: 'path mono' }, ep.path),
                    el('span', { class: `auth-badge ${ep.auth === 'none' ? 'none' : ''}` }, ep.auth),
                    el('span', { class: 'label', style: 'margin-left:auto;color:var(--text-muted);font-weight:500;font-size:12px;' }, ep.label),
                ),
                el('pre', {}, fullCurl),
                el('div', { class: 'copy-row' },
                    el('button', { class: 'btn-ghost', onclick: () => copyText(fullCurl) }, 'Copy curl'),
                ),
            ));
        }

        app.innerHTML = '';
        app.append(head, tokenCard, matchCard, submitCard, list);
    } catch (e) {
        renderError(e.message);
    }
}

function buildMatchPlayground(bearer) {
    const card = el('section', { class: 'token-card' });
    card.appendChild(el('div', { class: 'head' },
        el('h2', {}, 'Patient $match playground (PDQm)'),
        el('div', { class: 'btn-group' },
            el('button', { class: 'btn-ghost', onclick: () => prefillExample() }, 'Prefill example'),
            el('button', { class: 'btn-ghost', onclick: () => runMatch() }, 'Run $match →'),
        ),
    ));
    card.appendChild(el('div', { class: 'meta' },
        'POST /Patient/$match with a Parameters resource. Weighted scoring over the 10-patient panel; returns match-grade.',
    ));

    const formGrid = el('div', {
        style: 'display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:12px;',
    });
    const inputs = {};
    for (const f of [
        ['family',     'Family name',  'Müller'],
        ['given',      'Given name',   'Anna'],
        ['birthdate',  'Birthdate',    '1968-03-14'],
        ['identifier', 'Identifier',   ''],
        ['gender',     'Gender',       'female'],
    ]) {
        const id = `match-${f[0]}`;
        const input = el('input', {
            id, placeholder: f[2] || '',
            'data-key': f[0],
            style: 'padding:7px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);font-family:inherit;font-size:13px;background:var(--surface);',
        });
        inputs[f[0]] = input;
        formGrid.appendChild(el('div', {},
            el('div', { class: 'field-label' }, f[1]),
            input,
        ));
    }
    card.appendChild(formGrid);

    const out = el('div', { style: 'margin-top:12px;' });
    card.appendChild(out);

    function prefillExample() {
        inputs.family.value = 'Rossi';
        inputs.given.value = 'Giulia';
        inputs.birthdate.value = '1981-11-02';
        inputs.gender.value = 'female';
    }

    async function runMatch() {
        out.innerHTML = '';
        out.appendChild(el('div', { class: 'meta' }, 'sending POST /Patient/$match…'));
        const resource = { resourceType: 'Patient' };
        if (inputs.family.value || inputs.given.value) {
            resource.name = [{
                ...(inputs.family.value ? { family: inputs.family.value } : {}),
                ...(inputs.given.value ? { given: [inputs.given.value] } : {}),
            }];
        }
        if (inputs.birthdate.value) resource.birthDate = inputs.birthdate.value;
        if (inputs.gender.value) resource.gender = inputs.gender.value;
        if (inputs.identifier.value) resource.identifier = [{ value: inputs.identifier.value }];
        const body = {
            resourceType: 'Parameters',
            parameter: [{ name: 'resource', resource }, { name: 'count', valueInteger: 5 }],
        };
        try {
            const t0 = performance.now();
            const r = await fetch('/Patient/$match', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/fhir+json',
                    'Authorization': `Bearer ${bearer}`,
                },
                body: JSON.stringify(body),
            });
            const ms = Math.round(performance.now() - t0);
            if (!r.ok) {
                out.innerHTML = '';
                out.appendChild(el('div', { class: 'error' }, `${r.status}: ${await r.text()}`));
                return;
            }
            const bundle = await r.json();
            renderMatchResults(out, bundle, ms, body);
        } catch (e) {
            out.innerHTML = '';
            out.appendChild(el('div', { class: 'error' }, e.message));
        }
    }

    return card;
}

function buildSubmissionDemo(bearer) {
    const card = el('section', { class: 'token-card' });
    const out = el('div', { style: 'margin-top:12px;' });

    function exampleBundle() {
        const submissionId = `demo-${Date.now().toString(36)}`;
        return {
            resourceType: 'Bundle',
            type: 'transaction',
            entry: [{
                fullUrl: `DocumentReference/${submissionId}`,
                resource: {
                    resourceType: 'DocumentReference',
                    id: submissionId,
                    status: 'current',
                    type: { coding: [{ system: 'http://loinc.org', code: '60591-5', display: 'Patient summary' }] },
                    category: [{ coding: [{
                        system: 'http://hl7.eu/fhir/ig/eu-health-data-api/CodeSystem/eehrxf-document-priority-category',
                        code: 'patient-summary',
                    }] }],
                    subject: { reference: 'Patient/p-001' },
                    description: 'Demo ITI-105 submission from /ui#/endpoints',
                    content: [{
                        attachment: { contentType: 'application/fhir+json', url: `Binary/${submissionId}` },
                    }],
                },
                request: { method: 'POST', url: 'DocumentReference' },
            }],
        };
    }

    let currentBundle = exampleBundle();

    const bodyView = el('pre', {
        style: 'background:var(--code-bg);color:var(--code-text);border-radius:var(--radius-sm);padding:10px 14px;font-size:11px;margin-top:8px;overflow:auto;max-height:200px;',
        html: colorizeJson(JSON.stringify(currentBundle, null, 2)),
    });

    async function submit() {
        out.innerHTML = '';
        out.appendChild(el('div', { class: 'meta' }, 'POST / (ITI-105)…'));
        try {
            const t0 = performance.now();
            const r = await fetch('/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/fhir+json',
                    'Authorization': `Bearer ${bearer}`,
                },
                body: JSON.stringify(currentBundle),
            });
            const ms = Math.round(performance.now() - t0);
            const body = await r.json();
            out.innerHTML = '';
            const statusColor = r.ok ? 'var(--accent)' : 'var(--danger)';
            out.appendChild(el('div', { class: 'meta' },
                el('span', { style: `font-weight:600;color:${statusColor};` }, `HTTP ${r.status}`),
                ` · ${ms} ms · Location: `,
                el('span', { class: 'mono', style: 'font-size:11px;' }, r.headers.get('Location') || '—'),
            ));
            out.appendChild(el('pre', {
                style: 'background:var(--code-bg);color:var(--code-text);border-radius:var(--radius-sm);padding:10px 14px;font-size:11px;margin-top:8px;overflow:auto;max-height:200px;',
                html: colorizeJson(JSON.stringify(body, null, 2)),
            }));
            // refresh example for next submission so the id is unique
            currentBundle = exampleBundle();
            bodyView.innerHTML = colorizeJson(JSON.stringify(currentBundle, null, 2));
        } catch (e) {
            out.innerHTML = '';
            out.appendChild(el('div', { class: 'error' }, e.message));
        }
    }

    card.appendChild(el('div', { class: 'head' },
        el('h2', {}, 'ITI-105 document submission'),
        el('div', { class: 'btn-group' },
            el('button', { class: 'btn-ghost', onclick: () => { currentBundle = exampleBundle(); bodyView.innerHTML = colorizeJson(JSON.stringify(currentBundle, null, 2)); } }, 'Regenerate'),
            el('button', { class: 'btn-primary', onclick: () => submit() }, 'POST submission →'),
        ),
    ));
    card.appendChild(el('div', { class: 'meta' },
        'Submits a small Bundle.type=transaction containing a DocumentReference for Patient/p-001 to /. Server validates structurally, persists into data/inbox/, mirrors into the store so it surfaces in subsequent searches.',
    ));
    card.appendChild(el('details', { style: 'margin-top:10px;' },
        el('summary', { style: 'cursor:pointer;color:var(--text-muted);font-size:12px;' }, 'show request bundle'),
        bodyView,
    ));
    card.appendChild(out);

    return card;
}

function renderMatchResults(out, bundle, ms, requestBody) {
    out.innerHTML = '';
    out.appendChild(el('div', { class: 'meta' },
        `${bundle.total} candidates · ${ms} ms · click a row to view the candidate JSON`,
    ));
    const grid = el('div', { style: 'margin-top:8px;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;background:var(--surface);' });
    for (const e of (bundle.entry || [])) {
        const r = e.resource;
        const grade = e.search?.extension?.find(x => x.url.endsWith('match-grade'))?.valueCode || '—';
        const score = e.search?.score ?? '—';
        const gradeColor = grade === 'certain' ? 'var(--accent)'
                        : grade === 'probable' ? 'var(--info)'
                        : grade === 'possible' ? 'var(--warn)'
                        : 'var(--text-faint)';
        grid.appendChild(el('div', {
            style: 'padding:10px 14px;border-bottom:1px solid var(--border-soft);display:grid;grid-template-columns:auto 110px 110px 1fr;gap:14px;align-items:center;cursor:pointer;',
            onclick: () => openResourceModal('Patient', r.id),
        },
            el('span', { class: 'mono', style: 'font-size:12px;' }, `Patient/${r.id}`),
            el('span', { style: `font-weight:600;color:${gradeColor};font-size:13px;` }, grade),
            el('span', { class: 'mono', style: 'font-size:12px;' }, `score ${score}`),
            el('span', { style: 'font-size:13px;' }, `${r.name?.[0]?.given?.[0] || ''} ${r.name?.[0]?.family || ''} · born ${r.birthDate || '—'}`),
        ));
    }
    out.appendChild(grid);

    const reqPretty = JSON.stringify(requestBody, null, 2);
    const view = el('details', { style: 'margin-top:10px;' },
        el('summary', { style: 'cursor:pointer;color:var(--text-muted);font-size:12px;' }, 'show request body'),
        el('pre', { style: 'background:var(--code-bg);color:var(--code-text);border-radius:var(--radius-sm);padding:10px 14px;font-size:11px;margin-top:6px;overflow:auto;' }, reqPretty),
    );
    out.appendChild(view);
}

// ---------- home page ----------
async function renderHomePage() {
    setLoading();
    try {
        const info = await api('/ui/api/server-info');
        const hero = el('section', { class: 'hero-home' },
            el('h1', {}, 'EHDS Demo · EU Health Data API'),
            el('p', { class: 'tagline' },
                'Open-source reference FHIR R4 server implementing the ',
                el('a', { href: 'https://build.fhir.org/ig/euridice-org/eu-health-data-api/en/', target: '_blank', rel: 'noopener' }, 'EU Health Data API IG'),
                ' end-to-end. Synthetic data only. Built to demonstrate the wire-level shape of cross-border health data exchange — SMART backend services auth, PDQm patient discovery, IPA resource access, ITI-67/68/105 document exchange, and on-demand FHIR document bundles for the four EU priority categories.',
            ),
            el('div', { class: 'hero-stats' },
                statCard('Patients', info.patients, 'EU panel'),
                statCard('Total resources', info.total_resources, 'across compartments'),
                statCard('Resource types', Object.keys(info.by_type).length, 'first-class'),
                statCard('Priority categories', info.categories.length, 'compiled on demand'),
            ),
        );
        const cards = el('div', { class: 'capability-grid' },
            capCard('🔐', 'Authorization',     'SMART backend services — JWT client assertion → bearer token. RS256/ES256.', '#/authorization'),
            capCard('🩺', 'Resource exchange', 'IPA + PDQm $match — read patients & dependent resources.',                  '#/resources'),
            capCard('📄', 'Document exchange', 'ITI-67/68/105 — search, retrieve, submit Bundle documents.',                 '#/documents'),
            capCard('▶️', 'Consumer demo',     'Live end-to-end walkthrough of the API as a real client.',                    '#/demo'),
            capCard('👥', 'Patient browser',   'Browse the 10 EU synthetic patients and their compartments.',                 '#/patients'),
            capCard('📐', 'Endpoints',         'Every URL the server exposes, with copy-paste cURL.',                         '#/endpoints'),
            capCard('📡', 'Server info',       'Capability statement, SMART config, build details.',                          '#/server'),
            capCard('📱', 'Share via QR',      'Scan QR codes for demo URLs from a phone or tablet.',                         '#/qr'),
        );
        const note = el('section', { class: 'note-block' },
            el('h3', {}, 'No real patient data'),
            el('p', {},
                'Every resource on this server is synthesised. The 10 patients are EU-flavoured composites (Vienna, Berlin, Rome, Paris, Madrid, Lisbon, Amsterdam, Warsaw, Stockholm, Helsinki). Re-seed deterministically with ',
                el('code', {}, 'python -m scripts.seed --clean'),
                '. The compiled document bundles are produced on demand and pass base FHIR R4 validation.',
            ),
        );
        app.innerHTML = '';
        app.append(hero, cards, note);
    } catch (e) {
        renderError(e.message);
    }
}

function capCard(icon, title, desc, href) {
    return el('a', { class: 'cap-card', href },
        el('div', { class: 'cap-head' },
            el('span', { class: 'cap-icon' }, icon),
            el('span', { class: 'cap-title' }, title),
        ),
        el('div', { class: 'cap-desc' }, desc),
        el('div', { class: 'cap-go' }, 'open →'),
    );
}

// ---------- authorization page ----------
async function renderAuthorizationPage() {
    setLoading();
    try {
        const [smart, token] = await Promise.all([
            api('/.well-known/smart-configuration'),
            fetch('/ui/api/dev-token', { method: 'POST' }).then(r => r.json()),
        ]);
        const head = el('div', { class: 'page-head' },
            el('h1', {}, 'Authorization'),
            el('div', { class: 'meta' },
                'SMART App Launch · Backend Services profile. Asymmetric ',
                el('code', {}, 'private_key_jwt'),
                ' client assertion → short-lived bearer.',
            ),
        );

        const flow = el('section', { class: 'doc-block' },
            el('h3', {}, 'Flow (server → server)'),
            el('ol', { class: 'flow-list' },
                el('li', {}, 'Client builds a JWT with ',
                    el('code', {}, 'iss=sub=client_id'),
                    ', ',
                    el('code', {}, `aud=${smart.token_endpoint}`),
                    ', short ',
                    el('code', {}, 'exp'),
                    ', and signs it with the private key whose JWK was registered with the server.',
                ),
                el('li', {}, 'Client POSTs to ',
                    urlChip('POST', new URL(smart.token_endpoint).pathname),
                    ' with ',
                    el('code', {}, 'grant_type=client_credentials'),
                    ', ',
                    el('code', {}, 'client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer'),
                    ', ',
                    el('code', {}, 'client_assertion=$JWT'),
                    ', and ',
                    el('code', {}, 'scope=…'),
                    '.',
                ),
                el('li', {}, 'Server validates the assertion against the registered JWK, returns an RS256-signed bearer (5 min TTL).'),
                el('li', {}, 'Client sends ',
                    el('code', {}, 'Authorization: Bearer <token>'),
                    ' on subsequent FHIR REST requests.',
                ),
            ),
        );

        const config = el('section', { class: 'tech-block' },
            el('h3', {}, 'SMART configuration (live)'),
            techRow('Issuer', el('span', { class: 'mono' }, smart.issuer)),
            techRow('Token endpoint', urlChip('POST', new URL(smart.token_endpoint).pathname)),
            techRow('Server JWKS', urlChip('GET', new URL(smart.jwks_uri).pathname)),
            techRow('Grant types', el('span', {}, smart.grant_types_supported.join(', '))),
            techRow('Auth methods', el('span', {}, smart.token_endpoint_auth_methods_supported.join(', '))),
            techRow('Signing algs', el('span', {}, smart.token_endpoint_auth_signing_alg_values_supported.join(', '))),
            techRow('Scopes', el('span', {},
                ...smart.scopes_supported.map(s => el('code', { style: 'margin:0 4px 4px 0;display:inline-block;background:var(--surface-3);padding:1px 6px;border-radius:4px;' }, s)),
            )),
        );

        const tokenCard = el('section', { class: 'token-card' },
            el('div', { class: 'head' },
                el('h2', {}, 'Dev bearer'),
                el('div', { class: 'btn-group' },
                    el('button', { class: 'btn-ghost', onclick: () => copyText(token.access_token) }, 'Copy token'),
                    el('button', { class: 'btn-ghost', onclick: () => copyText(`export TOKEN=${token.access_token}`) }, 'Copy export TOKEN=…'),
                    el('button', { class: 'btn-ghost', onclick: () => renderAuthorizationPage() }, 'Refresh'),
                ),
            ),
            el('div', { class: 'meta' },
                `scope: ${token.scope}  ·  type: ${token.token_type}  ·  expires in ${token.expires_in}s · `,
                'minted by the server-side dev shortcut. In production a real client would mint this via the SMART flow above using its own private key.',
            ),
            el('div', { class: 'token-display' }, token.access_token),
        );

        const cli = el('section', { class: 'doc-block' },
            el('h3', {}, 'Register a new client'),
            el('p', { class: 'meta' }, 'On a client machine, generate a keypair and upload the public JWK:'),
            el('pre', { class: 'code-snippet' },
                '# on the client (do not put the private key on the server)\n' +
                'python -m app.tools.register_client \\\n' +
                '  --client-id my-app --generate --scope "system/*.read"',
            ),
            el('p', { class: 'meta' }, 'Or, on the server, register an inbound client by its public JWK:'),
            el('pre', { class: 'code-snippet' },
                'cd /srv/ehds-api && source .venv/bin/activate\n' +
                'python -m app.tools.register_client \\\n' +
                '  --client-id partner-a \\\n' +
                '  --jwk-from-pem /tmp/partner-a-pubkey.pem \\\n' +
                '  --scope "system/*.read" --scope "system/Bundle.write"\n' +
                'sudo systemctl restart ehds-api  # pick up the new registry',
            ),
        );

        app.innerHTML = '';
        app.append(head, flow, config, tokenCard, cli);
    } catch (e) {
        renderError(e.message);
    }
}

// ---------- document exchange page ----------
async function renderDocumentsPage() {
    setLoading();
    try {
        const token = await fetch('/ui/api/dev-token', { method: 'POST' }).then(r => r.json());
        const head = el('div', { class: 'page-head' },
            el('h1', {}, 'Document exchange'),
            el('div', { class: 'meta' },
                'IHE MHD-on-FHIR transactions. EU IG mandates four priority categories — Patient Summary (EPS), Laboratory Report (Lab), Hospital Discharge Report (HDR), Imaging Report.',
            ),
        );

        const ti = el('section', { class: 'doc-block' },
            el('h3', {}, 'Transactions implemented'),
            el('table', { class: 'endpoints-table' },
                el('thead', {}, el('tr', {},
                    el('th', {}, 'Transaction'),
                    el('th', {}, 'Method'),
                    el('th', {}, 'URL'),
                    el('th', {}, 'What it does'),
                )),
                el('tbody', {},
                    el('tr', {},
                        el('td', { class: 'mono' }, 'ITI-67'),
                        el('td', {}, el('span', { class: 'method-badge get' }, 'GET')),
                        el('td', {}, urlChip('GET', '/DocumentReference?patient=p-001')),
                        el('td', {}, 'Find documents by patient. Returns a searchset of DocumentReferences with category + LOINC type + Binary URL.'),
                    ),
                    el('tr', {},
                        el('td', { class: 'mono' }, 'ITI-68'),
                        el('td', {}, el('span', { class: 'method-badge get' }, 'GET')),
                        el('td', {}, urlChip('GET', '/Binary/doc-p-001-patient-summary')),
                        el('td', {}, 'Retrieve a compiled FHIR Bundle document on demand from atomic resources in the patient compartment.'),
                    ),
                    el('tr', {},
                        el('td', { class: 'mono' }, 'ITI-105'),
                        el('td', {}, el('span', { class: 'method-badge post' }, 'POST')),
                        el('td', {}, urlChip('POST', '/', { noLink: true })),
                        el('td', {}, 'Submit a Bundle.type=transaction containing one or more DocumentReferences (+ optional Binary), which the server validates and persists.'),
                    ),
                ),
            ),
        );

        const cats = el('section', { class: 'doc-block' },
            el('h3', {}, 'Four priority categories'),
            el('div', { class: 'capability-grid' },
                ...Object.entries(CATEGORY_LABELS).map(([cat, meta]) =>
                    el('a', { class: 'cap-card', href: `#/p/p-001/doc/${cat}` },
                        el('div', { class: 'cap-head' },
                            el('span', { class: 'cap-icon' }, meta.icon),
                            el('span', { class: 'cap-title' }, meta.label),
                        ),
                        el('div', { class: 'cap-desc' },
                            'Profile: ',
                            el('code', { style: 'font-size:10px;' }, meta.profile),
                        ),
                        el('div', { class: 'cap-go' }, 'open Bundle for p-001 →'),
                    ),
                ),
            ),
        );

        const submit = buildSubmissionDemo(token.access_token);

        app.innerHTML = '';
        app.append(head, ti, cats, submit);
    } catch (e) {
        renderError(e.message);
    }
}

// ---------- resource exchange page ----------
async function renderResourcesPage() {
    setLoading();
    try {
        const [token, cap] = await Promise.all([
            fetch('/ui/api/dev-token', { method: 'POST' }).then(r => r.json()),
            api('/metadata'),
        ]);
        const head = el('div', { class: 'page-head' },
            el('h1', {}, 'Resource exchange'),
            el('div', { class: 'meta' },
                'IPA (International Patient Access) + PDQm — direct REST access to atomic FHIR resources in a patient compartment.',
            ),
        );

        const direct = el('section', { class: 'doc-block' },
            el('h3', {}, 'Direct REST access'),
            el('table', { class: 'endpoints-table' },
                el('thead', {}, el('tr', {},
                    el('th', {}, 'Pattern'),
                    el('th', {}, 'Example'),
                    el('th', {}, 'Notes'),
                )),
                el('tbody', {},
                    el('tr', {},
                        el('td', { class: 'mono' }, 'Read'),
                        el('td', {}, urlChip('GET', '/Patient/p-001')),
                        el('td', {}, 'Single resource read.'),
                    ),
                    el('tr', {},
                        el('td', { class: 'mono' }, 'Compartment'),
                        el('td', {}, urlChip('GET', '/Observation?patient=p-001')),
                        el('td', {}, 'All resources of a type for a patient.'),
                    ),
                    el('tr', {},
                        el('td', { class: 'mono' }, '$everything'),
                        el('td', {}, urlChip('GET', '/Patient/p-001/$everything')),
                        el('td', {}, 'Bundle of every resource referencing this patient.'),
                    ),
                    el('tr', {},
                        el('td', { class: 'mono' }, 'PDQm search'),
                        el('td', {}, urlChip('GET', '/Patient?family=Rossi&birthdate=1981-11-02')),
                        el('td', {}, 'Demographic search across the panel.'),
                    ),
                    el('tr', {},
                        el('td', { class: 'mono' }, 'PDQm $match'),
                        el('td', {}, urlChip('POST', '/Patient/$match', { noLink: true })),
                        el('td', {}, 'Weighted scoring; returns match-grade per candidate.'),
                    ),
                ),
            ),
        );

        const support = el('section', { class: 'doc-block' },
            el('h3', {}, 'Supported resource types'),
            el('table', { class: 'endpoints-table' },
                el('thead', {}, el('tr', {},
                    el('th', {}, 'Type'),
                    el('th', {}, 'Interactions'),
                    el('th', {}, 'Search params'),
                )),
                el('tbody', {}, ...(cap.rest?.[0]?.resource || []).map((r) => el('tr', {},
                    el('td', {}, el('code', {}, r.type)),
                    el('td', { style: 'font-size:11px;color:var(--text-muted);' }, (r.interaction || []).map((i) => i.code).join(', ')),
                    el('td', { style: 'font-size:11px;color:var(--text-faint);' }, (r.searchParam || []).map((p) => p.name).join(', ')),
                ))),
            ),
        );

        const match = buildMatchPlayground(token.access_token);

        app.innerHTML = '';
        app.append(head, direct, support, match);
    } catch (e) {
        renderError(e.message);
    }
}

// ---------- demo page (end-to-end walkthrough) ----------
async function renderDemoPage() {
    setLoading();
    try {
        const head = el('div', { class: 'page-head' },
            el('h1', {}, 'Consumer demo · end-to-end'),
            el('div', { class: 'meta' },
                'Walk through the API as a real client would: authorize → discover patient → read compartment → fetch compiled document. Each step shows the actual HTTP request and response.',
            ),
        );
        const steps = el('div', { class: 'demo-steps', id: 'demo-steps' });
        app.innerHTML = '';
        app.append(head, steps);
        const state = { token: null, pid: null };
        steps.appendChild(demoStep(1, 'Mint a bearer token',
            'In production the client signs a JWT with its private key and exchanges it at the token endpoint. For this demo we use the server-side dev shortcut, which returns the same shape of bearer.',
            'POST /ui/api/dev-token',
            async (out) => {
                const r = await fetch('/ui/api/dev-token', { method: 'POST' });
                const body = await r.json();
                state.token = body.access_token;
                return { request: 'POST /ui/api/dev-token', status: r.status, response: body };
            },
        ));
        steps.appendChild(demoStep(2, 'Discover a patient (PDQm)',
            'Search the patient registry by demographics. Returns a searchset Bundle.',
            "GET /Patient?family=Rossi&birthdate=1981-11-02",
            async () => {
                if (!state.token) throw new Error('run step 1 first');
                const r = await fetch('/Patient?family=Rossi&birthdate=1981-11-02', {
                    headers: { 'Authorization': `Bearer ${state.token}` },
                });
                const body = await r.json();
                state.pid = body?.entry?.[0]?.resource?.id || null;
                return { request: 'GET /Patient?family=Rossi&birthdate=1981-11-02 (Authorization: Bearer …)', status: r.status, response: body };
            },
        ));
        steps.appendChild(demoStep(3, 'Read the patient resource',
            'A full read by FHIR id. Uses the id discovered in step 2.',
            'GET /Patient/{id}',
            async () => {
                if (!state.token || !state.pid) throw new Error('run steps 1-2 first');
                const r = await fetch(`/Patient/${state.pid}`, { headers: { 'Authorization': `Bearer ${state.token}` } });
                return { request: `GET /Patient/${state.pid}`, status: r.status, response: await r.json() };
            },
        ));
        steps.appendChild(demoStep(4, 'List documents for the patient (ITI-67)',
            'DocumentReference search filtered to a patient. Each entry includes the priority category, LOINC type, and a Binary URL for retrieval.',
            'GET /DocumentReference?patient={id}',
            async () => {
                if (!state.token || !state.pid) throw new Error('run steps 1-2 first');
                const r = await fetch(`/DocumentReference?patient=${state.pid}`, { headers: { 'Authorization': `Bearer ${state.token}` } });
                return { request: `GET /DocumentReference?patient=${state.pid}`, status: r.status, response: await r.json() };
            },
        ));
        steps.appendChild(demoStep(5, 'Retrieve a compiled Patient Summary (ITI-68)',
            'The Binary endpoint compiles a FHIR Bundle.type=document on demand from atomic resources in the patient compartment. Bundle entries use absolute fullUrls so references resolve.',
            'GET /Binary/doc-{id}-patient-summary',
            async () => {
                if (!state.token || !state.pid) throw new Error('run steps 1-2 first');
                const r = await fetch(`/Binary/doc-${state.pid}-patient-summary`, { headers: { 'Authorization': `Bearer ${state.token}` } });
                const body = await r.json();
                return {
                    request: `GET /Binary/doc-${state.pid}-patient-summary`,
                    status: r.status,
                    response: body,
                    summary: `${body.entry?.length || 0} entries · type=${body.type} · profile=${body.meta?.profile?.[0] || '?'}`,
                };
            },
        ));
        steps.appendChild(el('div', { class: 'demo-actions' },
            el('button', { class: 'btn-primary', onclick: () => runAllSteps() }, 'Run all steps →'),
        ));
        async function runAllSteps() {
            const btns = steps.querySelectorAll('.demo-run-btn');
            for (const b of btns) {
                b.click();
                // small pause so the user can see each step happen
                await new Promise(r => setTimeout(r, 400));
            }
        }
    } catch (e) {
        renderError(e.message);
    }
}

function demoStep(n, title, narrative, url, run) {
    const out = el('div', { class: 'demo-out' });
    const card = el('section', { class: 'demo-step' },
        el('div', { class: 'demo-head' },
            el('span', { class: 'demo-n' }, String(n)),
            el('h3', {}, title),
            el('button', {
                class: 'btn-primary demo-run-btn',
                onclick: async () => {
                    out.innerHTML = '';
                    out.appendChild(el('div', { class: 'meta' }, 'running…'));
                    try {
                        const res = await run(out);
                        out.innerHTML = '';
                        const statusOk = res.status >= 200 && res.status < 300;
                        const head = el('div', { class: 'demo-result-head' },
                            el('span', { class: 'mono' }, res.request),
                            el('span', { style: `font-weight:600;color:${statusOk ? 'var(--accent)' : 'var(--danger)'};` }, `HTTP ${res.status}`),
                        );
                        out.appendChild(head);
                        if (res.summary) out.appendChild(el('div', { class: 'meta' }, res.summary));
                        out.appendChild(el('pre', { class: 'json-dump', html: colorizeJson(JSON.stringify(res.response, null, 2).slice(0, 6000)) }));
                    } catch (e) {
                        out.innerHTML = '';
                        out.appendChild(el('div', { class: 'error' }, e.message));
                    }
                },
            }, 'Run ▶'),
        ),
        el('div', { class: 'demo-narrative' }, narrative),
        el('div', { class: 'demo-url mono' }, url),
        out,
    );
    return card;
}

// ---------- QR page ----------
async function renderQrPage() {
    setLoading();
    try {
        const info = await api('/ui/api/server-info');
        const base = info.base_url;
        const urls = [
            { label: 'Demo home',                     path: '/ui/' },
            { label: 'Patient list',                  path: '/ui/#/patients' },
            { label: 'End-to-end demo',               path: '/ui/#/demo' },
            { label: 'Authorization',                 path: '/ui/#/authorization' },
            { label: 'Documents capability',          path: '/ui/#/documents' },
            { label: 'Resources capability',          path: '/ui/#/resources' },
            { label: 'CapabilityStatement',           path: '/metadata' },
            { label: 'SMART configuration',           path: '/.well-known/smart-configuration' },
            { label: 'Server JWKS',                   path: '/.well-known/jwks.json' },
        ];
        const head = el('div', { class: 'page-head' },
            el('h1', {}, 'QR codes'),
            el('div', { class: 'meta' },
                `Scan any of these to open the URL on a phone or tablet. Base: `,
                el('span', { class: 'mono' }, base),
            ),
        );
        const grid = el('div', { class: 'qr-grid' });
        for (const u of urls) {
            const full = base + u.path;
            grid.appendChild(el('div', { class: 'qr-card' },
                el('div', { class: 'qr-title' }, u.label),
                el('img', {
                    class: 'qr-img',
                    src: `/ui/api/qr?text=${encodeURIComponent(full)}`,
                    alt: `QR for ${u.label}`,
                    loading: 'lazy',
                }),
                el('div', { class: 'qr-url mono' },
                    el('a', { href: full, target: '_blank', rel: 'noopener' }, full),
                ),
                el('button', { class: 'btn-ghost', onclick: () => copyText(full) }, 'Copy URL'),
            ));
        }
        app.innerHTML = '';
        app.append(head, grid);
    } catch (e) {
        renderError(e.message);
    }
}

// ---------- router ----------
function highlightNav(hash) {
    // map sub-routes to their canonical nav item
    let active = hash;
    if (hash.startsWith('#/p/')) active = '#/patients';
    document.querySelectorAll('[data-nav]').forEach((a) => {
        a.classList.toggle('active', a.getAttribute('href') === active);
    });
}

async function route() {
    closeModal();
    const hash = location.hash || '#/';
    highlightNav(hash);
    const m_doc = hash.match(/^#\/p\/([^/]+)\/doc\/([^/]+)$/);
    const m_pat = hash.match(/^#\/p\/([^/]+)$/);
    if (m_doc) return renderDocument(m_doc[1], m_doc[2]);
    if (m_pat) return renderPatientDetail(m_pat[1]);
    if (hash === '#/patients') return renderPatientList();
    if (hash === '#/server') return renderServerPage();
    if (hash === '#/endpoints') return renderEndpointsPage();
    if (hash === '#/demo') return renderDemoPage();
    if (hash === '#/authorization') return renderAuthorizationPage();
    if (hash === '#/documents') return renderDocumentsPage();
    if (hash === '#/resources') return renderResourcesPage();
    if (hash === '#/qr') return renderQrPage();
    return renderHomePage();
}

// ---------- boot ----------
(async () => {
    try {
        const [info, build] = await Promise.all([
            api('/ui/api/server-info'),
            api('/ui/api/build-info'),
        ]);
        footerTags.innerHTML = '';
        footerTags.appendChild(el('span', { class: 'pill' }, `env: ${info.env}`));
        if (build.git_sha) footerTags.appendChild(el('span', { class: 'pill' }, `git: ${build.git_sha}`));
        footerTags.appendChild(el('span', { class: 'pill' }, `FHIR R4`));
    } catch {
        footerTags.innerHTML = '';
        footerTags.appendChild(el('span', { class: 'pill' }, 'env: ?'));
    }
})();

window.addEventListener('hashchange', route);
window.addEventListener('load', route);
route();
