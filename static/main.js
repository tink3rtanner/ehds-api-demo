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
    let shown = 0;
    document.querySelectorAll('.patient-row').forEach((row) => {
        const visible = !needle || (row.dataset.search || '').includes(needle);
        row.style.display = visible ? '' : 'none';
        if (visible) shown++;
    });
    // empty-state message
    let empty = document.getElementById('patient-empty');
    if (shown === 0 && needle) {
        if (!empty) {
            empty = el('div', { id: 'patient-empty', class: 'empty-state' },
                el('div', { class: 'empty-icon' }, '∅'),
                el('div', { class: 'empty-title' }, 'No patients match this filter'),
                el('div', { class: 'empty-sub' }, `Searched ${document.querySelectorAll('.patient-row').length} patients for `,
                    el('code', { id: 'patient-empty-q' }, needle),
                    '. Try clearing the filter or matching by family name, country (ISO-2), city, or FHIR id.'),
            );
            const host = document.querySelector('.patients-table') || document.getElementById('patient-grid');
            host?.parentNode?.appendChild(empty);
        } else {
            document.getElementById('patient-empty-q').textContent = needle;
            empty.style.display = '';
        }
    } else if (empty) {
        empty.style.display = 'none';
    }
}

// ---------- patient detail ----------
async function renderPatientDetail(pid) {
    setLoading();
    let data;
    try {
        data = await api(`/ui/api/patients/${pid}`);
    } catch (e) {
        // friendly 404 vs raw API error
        const is404 = /:\s*404\b/.test(e.message);
        app.innerHTML = '';
        app.appendChild(el('section', { class: 'not-found' },
            el('h1', {}, is404 ? `Patient/${pid} not found` : 'Could not load patient'),
            el('p', {}, is404
                ? `No synthetic patient with id ${pid}. The demo panel has 10 patients (p-001 … p-010).`
                : el('span', {}, e.message)),
            el('div', { class: 'btn-group', style: 'margin-top:14px;' },
                el('a', { class: 'btn-primary', href: '#/patients' }, '← Back to patient list'),
                el('a', { class: 'btn', href: '#/' }, 'Home'),
            ),
        ));
        return;
    }
    try {
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

        // IGs cloned — only show when there's something to show (no point
        // leaking an admin-side instruction to public-demo viewers)
        const igBlock = build.ig_packages.length
            ? el('section', { class: 'tech-block' },
                el('h3', {}, 'HL7 Europe IG packages on disk'),
                ...build.ig_packages.map(ig => techRow(ig.name, el('span', { class: 'mono' }, ig.path))),
              )
            : el('div');

        // supported resources table
        const supportedH = el('section', { class: 'section-title' }, 'Supported resources');
        const tbl = el('table', { class: 'endpoints-table' },
            el('thead', {}, el('tr', {},
                el('th', {}, 'Type'),
                el('th', {}, 'Interactions'),
                el('th', {}, 'Search params'),
                el('th', { style: 'text-align:right;' }, 'Stored'),
            )),
            el('tbody', {}, ...(cap.rest?.[0]?.resource || []).map((r) => {
                const stored = info.by_type[r.type] || 0;
                return el('tr', { class: stored === 0 ? 'empty-row' : '' },
                    el('td', {}, el('code', {}, r.type)),
                    el('td', { style: 'font-size:11px;color:var(--text-muted);' }, (r.interaction || []).map((i) => i.code).join(', ')),
                    el('td', { style: 'font-size:11px;color:var(--text-faint);' }, (r.searchParam || []).map((p) => p.name).join(', ')),
                    el('td', { style: 'text-align:right;font-family:ui-monospace,monospace;' },
                        stored === 0
                            ? el('span', { style: 'color:var(--text-faint);' }, '0')
                            : String(stored),
                    ),
                );
            })),
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
    // sample identities the form rotates through. the form starts populated
    // with the first sample so clicking "Run $match" with no edits returns
    // a real result (was: empty Patient → 0 candidates).
    const SAMPLES = [
        { family: 'Müller',   given: 'Anna',    birthdate: '1968-03-14', gender: 'female', identifier: '' },
        { family: 'Rossi',    given: 'Giulia',  birthdate: '1981-11-02', gender: 'female', identifier: '' },
        { family: 'Schmidt',  given: 'Hans',    birthdate: '1955-07-29', gender: 'male',   identifier: '' },
        { family: 'García',   given: 'Sofía',   birthdate: '1990-09-21', gender: 'female', identifier: '' },
    ];
    let sampleIdx = 0;
    const inputs = {};
    const FIELDS = [
        ['family',     'Family name',  'e.g. Müller'],
        ['given',      'Given name',   'e.g. Anna'],
        ['birthdate',  'Birthdate',    'YYYY-MM-DD'],
        ['identifier', 'Identifier',   '(optional)'],
        ['gender',     'Gender',       'male|female|other'],
    ];
    for (const f of FIELDS) {
        const id = `match-${f[0]}`;
        const input = el('input', {
            id,
            value: SAMPLES[0][f[0]] || '',
            placeholder: f[2] || '',
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
        sampleIdx = (sampleIdx + 1) % SAMPLES.length;
        const s = SAMPLES[sampleIdx];
        for (const [k] of FIELDS) inputs[k].value = s[k] || '';
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
        'POST to the FHIR base (',
        el('code', {}, 'POST /'),
        ') with a Bundle.type=transaction containing a DocumentReference for Patient/p-001. Server validates structurally, persists into data/inbox/, mirrors into the store so subsequent searches see it.',
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
            capCard('🤖', 'Implement a client', 'Point your agent here. Quickstart + Python sample + curl + AI-native CLI.',  '#/implement'),
            capCard('🔑', 'Register a client',  'Generate a keypair in the browser or paste your public PEM/JWK.',            '#/register'),
            capCard('▶️', 'Consumer demo',      'Live end-to-end walkthrough of the API as a real client.',                    '#/demo'),
            capCard('🔐', 'Authorization',      'SMART backend services — JWT client assertion → bearer token. RS256/ES256.',  '#/authorization'),
            capCard('🩺', 'Resource exchange',  'IPA + PDQm $match — read patients & dependent resources.',                    '#/resources'),
            capCard('📄', 'Document exchange',  'ITI-67/68/105 — search, retrieve, submit Bundle documents.',                  '#/documents'),
            capCard('👥', 'Patient browser',    'Browse the 10 EU synthetic patients and their compartments.',                 '#/patients'),
            capCard('📐', 'Endpoints',          'Every URL the server exposes, with copy-paste cURL.',                         '#/endpoints'),
            capCard('📡', 'Server info',        'Capability statement, SMART config, build details.',                          '#/server'),
            capCard('📱', 'Share via QR',       'Scan QR codes for demo URLs from a phone or tablet.',                         '#/qr'),
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

// ---------- implementation guide ----------
async function renderImplementPage() {
    setLoading();
    try {
        const [info, smart] = await Promise.all([
            api('/ui/api/server-info'),
            api('/.well-known/smart-configuration'),
        ]);
        const base = info.base_url;
        const head = el('div', { class: 'page-head' },
            el('h1', {}, 'Implementation guide'),
            el('div', { class: 'meta' },
                'Quickstart for an implementer (or an AI agent) building a consumer client against this server. ',
                'Point an agent at this page and it has everything it needs to register a client, mint a bearer, and start reading FHIR resources.',
            ),
        );

        const machine = el('section', { class: 'doc-block', id: 'agent-spec' },
            el('h3', {}, 'Server facts (machine-readable)'),
            el('p', { class: 'meta' }, 'Copy this block to give an agent the exact URLs and parameters to target.'),
            el('pre', { class: 'code-snippet' }, JSON.stringify({
                fhir_base_url: base,
                fhir_version: '4.0.1',
                issuer: smart.issuer,
                token_endpoint: smart.token_endpoint,
                jwks_uri: smart.jwks_uri,
                metadata: base + '/metadata',
                smart_configuration: base + '/.well-known/smart-configuration',
                client_registration: { ui: base + '/ui/#/register', rest: base + '/ui/api/register-client', cli: 'python -m app.tools.register_client' },
                supported_scopes: smart.scopes_supported,
                allowed_algs: smart.token_endpoint_auth_signing_alg_values_supported,
                example_patient_ids: ['p-001', 'p-002', 'p-003', 'p-004', 'p-005'],
                example_endpoints: {
                    read_patient: base + '/Patient/p-001',
                    search_patient: base + '/Patient?family=Rossi&birthdate=1981-11-02',
                    patient_match: base + '/Patient/$match (POST Parameters)',
                    everything: base + '/Patient/p-001/$everything',
                    document_search: base + '/DocumentReference?patient=p-001',
                    compiled_document: base + '/Binary/doc-p-001-patient-summary',
                    submit: base + '/ (POST Bundle.type=transaction)',
                },
            }, null, 2)),
        );

        const path = el('section', { class: 'doc-block' },
            el('h3', {}, 'Three steps'),
            el('ol', { class: 'flow-list' },
                el('li', {},
                    el('strong', {}, 'Register a client. '),
                    'Generate an RSA keypair and submit the public half. Three options: ',
                    el('a', { href: '#/register' }, 'web UI'),
                    ', the REST endpoint ',
                    urlChip('POST', '/ui/api/register-client', { noLink: true }),
                    ', or the CLI: ',
                    el('code', {}, 'python -m app.tools.register_client --client-id my-app --generate --scope "system/*.read"'),
                    '.',
                ),
                el('li', {},
                    el('strong', {}, 'Mint a bearer token. '),
                    'Sign a short-lived JWT with your private key (',
                    el('code', {}, 'iss = sub = client_id'),
                    ', ',
                    el('code', {}, `aud = ${smart.token_endpoint}`),
                    ', ',
                    el('code', {}, 'exp = now+60s'),
                    ', ',
                    el('code', {}, 'jti = uuid'),
                    ') and POST it as the ',
                    el('code', {}, 'client_assertion'),
                    ' to ',
                    urlChip('POST', new URL(smart.token_endpoint).pathname, { noLink: true }),
                    '.',
                ),
                el('li', {},
                    el('strong', {}, 'Call FHIR endpoints. '),
                    'Send ',
                    el('code', {}, 'Authorization: Bearer <token>'),
                    ' on every request. Tokens last 300s; mint a new one when they expire.',
                ),
            ),
        );

        const py = el('section', { class: 'doc-block' },
            el('h3', {}, 'Full Python client (≈30 lines)'),
            el('p', { class: 'meta' },
                'Drop into any script. Requires ',
                el('code', {}, 'pip install requests pyjwt cryptography'),
                '. Substitute your client id and private-key path.',
            ),
            el('pre', { class: 'code-snippet' },
                `# ehds_client.py
import time, uuid, jwt, requests
from cryptography.hazmat.primitives import serialization

CLIENT_ID    = "my-app"
PRIVATE_KEY  = open("client-my-app.pem", "rb").read()
TOKEN_URL    = "${smart.token_endpoint}"
FHIR_BASE    = "${base}"

priv = serialization.load_pem_private_key(PRIVATE_KEY, password=None)
now  = int(time.time())
assertion = jwt.encode({
    "iss": CLIENT_ID, "sub": CLIENT_ID, "aud": TOKEN_URL,
    "iat": now, "exp": now + 60, "jti": str(uuid.uuid4()),
}, priv, algorithm="RS256", headers={"kid": f"{CLIENT_ID}-key-1"})

tok = requests.post(TOKEN_URL, data={
    "grant_type": "client_credentials",
    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
    "client_assertion": assertion,
    "scope": "system/*.read",
}).json()["access_token"]

r = requests.get(f"{FHIR_BASE}/Patient/p-001",
                 headers={"Authorization": f"Bearer {tok}"})
print(r.status_code, r.json()["name"][0]["family"])
`,
            ),
        );

        const curl = el('section', { class: 'doc-block' },
            el('h3', {}, 'Curl one-liner (after registration)'),
            el('pre', { class: 'code-snippet' },
                `# generate a private_key_jwt assertion in Python, then:
TOKEN=$(curl -s -X POST ${smart.token_endpoint} \\
  -d grant_type=client_credentials \\
  -d client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer \\
  -d client_assertion=$ASSERTION \\
  -d 'scope=system/*.read' | jq -r .access_token)

curl -s -H "Authorization: Bearer $TOKEN" ${base}/Patient/p-001 | jq`,
            ),
        );

        const cli = el('section', { class: 'doc-block' },
            el('h3', {}, 'AI-native CLI'),
            el('p', { class: 'meta' },
                'The repo ships an ',
                el('code', {}, 'app.tools.register_client'),
                ' module that does the whole keypair + registration dance in one command. Use ',
                el('code', {}, '--out json'),
                ' for machine-readable output an agent can parse:',
            ),
            el('pre', { class: 'code-snippet' },
                `# generate, register, and print json:
python -m app.tools.register_client \\
  --client-id my-agent --generate \\
  --scope "system/*.read" \\
  --base-url ${base} \\
  --out json`,
            ),
            el('p', { class: 'meta' },
                'Or, for a fully remote AI-native flow (no local files needed), use the REST endpoint with a pre-generated JWK:',
            ),
            el('pre', { class: 'code-snippet' },
                `curl -X POST ${base}/ui/api/register-client \\
  -H 'Content-Type: application/json' \\
  -d '{
    "client_id": "my-agent",
    "scopes": ["system/*.read"],
    "public_key_pem": "-----BEGIN PUBLIC KEY-----\\n..."
  }'`,
            ),
        );

        const sandbox = el('section', { class: 'doc-block' },
            el('h3', {}, 'Try it now (no install)'),
            el('p', { class: 'meta' },
                '→ ', el('a', { href: '#/register', class: 'btn-primary', style: 'text-decoration:none;' }, 'Open client registration UI'),
                ' ',
                el('a', { href: '#/demo', class: 'btn', style: 'text-decoration:none;' }, 'Run the live consumer demo'),
            ),
        );

        app.innerHTML = '';
        app.append(head, sandbox, path, machine, py, curl, cli);
    } catch (e) {
        renderError(e.message);
    }
}

// ---------- client registration UI ----------
async function renderRegisterPage() {
    setLoading();
    try {
        const [info, smart, existing] = await Promise.all([
            api('/ui/api/server-info'),
            api('/.well-known/smart-configuration'),
            api('/ui/api/clients'),
        ]);
        const head = el('div', { class: 'page-head' },
            el('h1', {}, 'Client registration'),
            el('div', { class: 'meta' },
                'Register a SMART backend services client. Either generate a keypair right here in your browser (private key never leaves your machine), or paste your existing PEM/JWK.',
            ),
        );

        const out = el('div', { id: 'register-result', class: 'register-result' });

        // form
        const cid = el('input', { value: '', placeholder: 'e.g. my-agent', style: 'width:100%;' });
        const scope = el('select', { style: 'width:100%;' },
            el('option', { value: 'system/*.read' }, 'system/*.read (all reads)'),
            el('option', { value: 'system/Patient.read' }, 'system/Patient.read'),
            el('option', { value: 'system/DocumentReference.read' }, 'system/DocumentReference.read'),
            el('option', { value: 'system/Binary.read' }, 'system/Binary.read'),
        );
        const pem = el('textarea', { rows: 6, placeholder: '-----BEGIN PUBLIC KEY-----\\n…\\n-----END PUBLIC KEY-----', style: 'width:100%;font-family:ui-monospace,monospace;font-size:12px;' });

        const form = el('section', { class: 'doc-block register-form' },
            el('h3', {}, 'Register a client'),
            fieldRow('Client id', cid, 'lowercase letters, numbers, hyphens. unique per client.'),
            fieldRow('Scope', scope, 'public-UI registration is limited to read scopes. use the CLI for Bundle.write.'),
            fieldRow('Public key (PEM)', pem,
                el('span', {},
                    'Or ',
                    el('button', { class: 'btn-ghost', onclick: () => generateKeypair() }, 'generate a keypair in this browser'),
                    ' (we never see the private half).',
                ),
            ),
            el('div', { class: 'btn-group', style: 'margin-top:14px;' },
                el('button', { class: 'btn-primary', onclick: () => submit() }, 'Register →'),
                el('button', { class: 'btn-ghost', onclick: () => { cid.value=''; pem.value=''; out.innerHTML=''; } }, 'Reset'),
            ),
        );

        async function generateKeypair() {
            out.innerHTML = '';
            out.appendChild(el('div', { class: 'meta' }, 'generating RSA-2048 in browser…'));
            const kp = await crypto.subtle.generateKey(
                { name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1,0,1]), hash: 'SHA-256' },
                true,
                ['sign', 'verify'],
            );
            const pubSpki = await crypto.subtle.exportKey('spki', kp.publicKey);
            const privPkcs8 = await crypto.subtle.exportKey('pkcs8', kp.privateKey);
            pem.value = derToPem(pubSpki, 'PUBLIC KEY');
            const privPem = derToPem(privPkcs8, 'PRIVATE KEY');
            // offer the private key as a download immediately so the user has it
            const filename = `client-${(cid.value || 'agent').replace(/[^a-z0-9-]/gi,'-')}.pem`;
            downloadText(filename, privPem);
            out.innerHTML = '';
            out.appendChild(el('section', { class: 'demo-step' },
                el('h3', {}, 'Keypair generated in your browser'),
                el('p', { class: 'meta' },
                    'Private key downloaded as ',
                    el('code', {}, filename),
                    '. Keep it on your client machine — the server only ever sees the public half. The textarea above has been filled with the public PEM.',
                ),
                el('pre', { class: 'code-snippet' }, privPem),
            ));
        }

        async function submit() {
            const body = {
                client_id: cid.value.trim(),
                scopes: [scope.value],
                public_key_pem: pem.value.trim(),
            };
            if (!body.client_id) return out.replaceChildren(el('div', { class: 'error' }, 'client_id is required'));
            if (!body.public_key_pem) return out.replaceChildren(el('div', { class: 'error' }, 'public_key_pem is required (generate one above or paste yours)'));
            out.innerHTML = '';
            out.appendChild(el('div', { class: 'meta' }, 'POSTing /ui/api/register-client…'));
            try {
                const r = await fetch('/ui/api/register-client', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const reply = await r.json();
                if (!r.ok) {
                    out.replaceChildren(el('div', { class: 'error' }, `${r.status}: ${reply.detail || JSON.stringify(reply)}`));
                    return;
                }
                renderRegisterResult(out, reply, info.base_url, smart.token_endpoint);
                // refresh the registered-list table without losing the result panel
                existingBlock.innerHTML = '';
                existingBlock.appendChild(el('h3', {}, 'Refresh the page to see the updated client list.'));
            } catch (e) {
                out.replaceChildren(el('div', { class: 'error' }, e.message));
            }
        }

        const existingBlock = el('section', { class: 'doc-block' },
            el('h3', {}, `Already registered (${existing.clients.length})`),
            existing.clients.length
                ? el('table', { class: 'endpoints-table' },
                    el('thead', {}, el('tr', {},
                        el('th', {}, 'client_id'),
                        el('th', {}, 'scopes'),
                        el('th', {}, 'kids'),
                    )),
                    el('tbody', {}, ...existing.clients.map(c => el('tr', {},
                        el('td', { class: 'mono' }, c.client_id),
                        el('td', { class: 'mono', style: 'font-size:11px;' }, c.scopes.join(' ')),
                        el('td', { class: 'mono', style: 'font-size:11px;color:var(--text-faint);' }, (c.kids || []).join(', ') || `(${c.key_count} keys)`),
                    ))),
                )
                : el('div', { class: 'meta' }, '(no clients yet)'),
        );

        const cli = el('section', { class: 'doc-block' },
            el('h3', {}, 'Or via CLI / REST'),
            el('p', { class: 'meta' }, 'Local Python:'),
            el('pre', { class: 'code-snippet' },
                'python -m app.tools.register_client \\\n' +
                '  --client-id my-agent --generate \\\n' +
                '  --scope "system/*.read" --out json',
            ),
            el('p', { class: 'meta' }, 'Or with a remote curl (pass a PEM you generated locally):'),
            el('pre', { class: 'code-snippet' },
                `curl -X POST ${info.base_url}/ui/api/register-client \\\n  -H 'Content-Type: application/json' \\\n  -d @reg.json`,
            ),
        );

        app.innerHTML = '';
        app.append(head, form, out, existingBlock, cli);
    } catch (e) {
        renderError(e.message);
    }
}

function fieldRow(label, input, hint) {
    return el('div', { style: 'margin-bottom:12px;' },
        el('div', { class: 'field-label' }, label),
        input,
        hint ? el('div', { class: 'meta', style: 'margin-top:4px;font-size:11px;' }, hint) : null,
    );
}

function renderRegisterResult(out, reply, base, tokenEndpoint) {
    out.innerHTML = '';
    out.appendChild(el('section', { class: 'demo-step' },
        el('div', { class: 'demo-head' },
            el('span', { class: 'demo-n' }, '✓'),
            el('h3', {}, `Client ${reply.client_id} registered`),
        ),
        el('p', { class: 'meta' },
            'Effective immediately — the server reads the registry on every /token call. ',
            `Next step: sign a JWT with kid="${reply.next_steps.client_assertion_kid}", aud="${reply.next_steps.audience}", and POST it as client_assertion.`,
        ),
        el('pre', { class: 'code-snippet' }, JSON.stringify(reply, null, 2)),
    ));
}

function derToPem(buffer, label) {
    const bytes = new Uint8Array(buffer);
    let b64 = btoa(String.fromCharCode(...bytes));
    const lines = b64.match(/.{1,64}/g).join('\n');
    return `-----BEGIN ${label}-----\n${lines}\n-----END ${label}-----\n`;
}

function downloadText(filename, text) {
    const blob = new Blob([text], { type: 'application/x-pem-file' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
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
    // detail-view routes (also accept #/patients/{id} as an alias so the
    // URL-shape on the patient card looks shareable)
    const m_doc = hash.match(/^#\/(?:p|patients)\/([^/]+)\/doc\/([^/]+)$/);
    const m_pat = hash.match(/^#\/(?:p|patients)\/([^/]+)$/);
    if (m_doc) return renderDocument(m_doc[1], m_doc[2]);
    if (m_pat) return renderPatientDetail(m_pat[1]);
    if (hash === '#/' || hash === '#') return renderHomePage();
    if (hash === '#/patients') return renderPatientList();
    if (hash === '#/server') return renderServerPage();
    if (hash === '#/endpoints') return renderEndpointsPage();
    if (hash === '#/demo') return renderDemoPage();
    if (hash === '#/authorization') return renderAuthorizationPage();
    if (hash === '#/documents') return renderDocumentsPage();
    if (hash === '#/resources') return renderResourcesPage();
    if (hash === '#/implement') return renderImplementPage();
    if (hash === '#/register') return renderRegisterPage();
    if (hash === '#/qr') return renderQrPage();
    return renderNotFound(hash);
}

function renderNotFound(hash) {
    app.innerHTML = '';
    app.appendChild(el('section', { class: 'not-found' },
        el('h1', {}, '404 · view not found'),
        el('p', {}, 'No view matched ',
            el('code', {}, hash),
            '. Either the URL is mistyped or the page was removed.'),
        el('div', { class: 'btn-group', style: 'margin-top:14px;' },
            el('a', { class: 'btn-primary', href: '#/' }, 'Go to Home'),
            el('a', { class: 'btn', href: '#/patients' }, 'Browse patients'),
            el('a', { class: 'btn', href: '#/demo' }, 'Run the consumer demo'),
        ),
    ));
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
