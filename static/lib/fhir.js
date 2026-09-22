// FHIR-shape helpers: pull a human label, date and primary coding out of any
// resource; origin tags; category metadata; reference resolution.

export const ORIGIN_TAG_SYSTEM = 'urn:ehds-demo:origin';
export const SUBMISSION_TAG_SYSTEM = 'urn:ehds-demo:submission';
export const SLOT_SYSTEM = 'urn:ehds-demo:slot';
export const SOURCE_ID_SYSTEM = 'urn:ehds-demo:source-id';

export const CATEGORIES = ['patient-summary', 'laboratory-report', 'discharge-report', 'imaging-report', 'prescription'];

export const CATEGORY_META = {
  'patient-summary':   { label: 'Patient Summary',  short: 'Summary', icon: 'documents', loinc: '60591-5',
    blurb: 'Allergies, problems, medications, immunisations, procedures, recent results.',
    ig: 'https://build.fhir.org/ig/hl7-eu/eps/', standard: 'HL7 Europe Patient Summary (EPS)' },
  'laboratory-report': { label: 'Laboratory Report', short: 'Lab', icon: 'flask', loinc: '11502-2',
    blurb: 'A diagnostic report grouping the observations from a specimen.',
    ig: 'https://build.fhir.org/ig/hl7-eu/laboratory/', standard: 'HL7 Europe Laboratory Report' },
  'discharge-report':  { label: 'Hospital Discharge Report', short: 'Discharge', icon: 'hospital', loinc: '18842-5',
    blurb: 'Admission, course, outcome and follow-up of a hospital stay.',
    ig: 'https://build.fhir.org/ig/hl7-eu/hdr/', standard: 'HL7 Europe Hospital Discharge Report (HDR)' },
  'imaging-report':    { label: 'Imaging Report', short: 'Imaging', icon: 'scan', loinc: '18748-4',
    blurb: 'A radiology report with its imaging study metadata.',
    ig: 'https://build.fhir.org/ig/hl7-eu/imaging-r4/', standard: 'HL7 Europe Imaging Report' },
  'prescription':      { label: 'ePrescription', short: 'Prescription', icon: 'pill', loinc: '57833-6',
    blurb: 'The prescription order itself: one or more MedicationRequests.',
    ig: 'https://build.fhir.org/ig/hl7-eu/mpd/', standard: 'HL7 Europe Medication Prescription and Dispense (MPD)' },
};

const CATEGORY_BY_LOINC = {
  '60591-5': 'patient-summary', '11502-2': 'laboratory-report', '18842-5': 'discharge-report',
  '34105-7': 'discharge-report', '18748-4': 'imaging-report', '57833-6': 'prescription',
};

export const COUNTRY_NAMES = {
  AL: 'Albania', AD: 'Andorra', AM: 'Armenia', AT: 'Austria', AZ: 'Azerbaijan', BY: 'Belarus', BE: 'Belgium',
  BA: 'Bosnia and Herzegovina', BG: 'Bulgaria', HR: 'Croatia', CY: 'Cyprus', CZ: 'Czechia', DK: 'Denmark',
  EE: 'Estonia', FI: 'Finland', FR: 'France', GE: 'Georgia', DE: 'Germany', GR: 'Greece', HU: 'Hungary',
  IS: 'Iceland', IE: 'Ireland', IT: 'Italy', XK: 'Kosovo', LV: 'Latvia', LI: 'Liechtenstein', LT: 'Lithuania',
  LU: 'Luxembourg', MT: 'Malta', MD: 'Moldova', MC: 'Monaco', ME: 'Montenegro', NL: 'Netherlands',
  MK: 'North Macedonia', NO: 'Norway', PL: 'Poland', PT: 'Portugal', RO: 'Romania', RU: 'Russia',
  SM: 'San Marino', RS: 'Serbia', SK: 'Slovakia', SI: 'Slovenia', ES: 'Spain', SE: 'Sweden', CH: 'Switzerland',
  TR: 'Türkiye', UA: 'Ukraine', GB: 'United Kingdom', VA: 'Vatican City',
  US: 'United States', CA: 'Canada', AU: 'Australia', JP: 'Japan', BR: 'Brazil', IN: 'India', CN: 'China',
  IL: 'Israel', NZ: 'New Zealand', ZA: 'South Africa',
};

export const countryName = (code) => (code && COUNTRY_NAMES[code]) || code || '';

// ---------- origin ----------

export function originOf(res) {
  const tags = res?.meta?.tag || [];
  let kind = 'unknown';
  let submission = null;
  for (const t of tags) {
    if (t.system === ORIGIN_TAG_SYSTEM && (t.code === 'reference' || t.code === 'community')) kind = t.code;
    if (t.system === SUBMISSION_TAG_SYSTEM && t.code) submission = t.code;
  }
  return { kind, submission, source: res?.meta?.source || null };
}

export function slotOf(patient) {
  return (patient?.identifier || []).find((i) => i.system === SLOT_SYSTEM)?.value || null;
}

export function sourceIdOf(res) {
  return (res?.identifier || []).find((i) => i.system === SOURCE_ID_SYSTEM)?.value || null;
}

export function sourceHost(url) {
  try { return new URL(url).host; } catch { return url || ''; }
}

// ---------- names, dates, codes ----------

export function humanName(p) {
  const n = (p?.name || [])[0] || {};
  const given = (n.given || []).join(' ');
  const s = `${given} ${n.family || ''}`.trim();
  return s || n.text || '';
}

export function patientCountry(p) {
  const addr = (p?.address || []).find((a) => a.use === 'home') || (p?.address || [])[0] || {};
  return (addr.country || '').toUpperCase().slice(0, 2) || null;
}

export function patientCity(p) {
  return ((p?.address || [])[0] || {}).city || '';
}

export function ccText(cc) {
  if (!cc) return '';
  if (typeof cc === 'string') return cc;
  return cc.text || (cc.coding || [])[0]?.display || (cc.coding || [])[0]?.code || '';
}

export function pickCoding(res) {
  const cc = res?.code || res?.vaccineCode || res?.medicationCodeableConcept || res?.type || res?.class
    || res?.category?.[0];
  return (cc?.coding || [])[0] || null;
}

export function medicationLabel(res) {
  if (res?.medicationCodeableConcept) return ccText(res.medicationCodeableConcept);
  const ref = res?.medicationReference;
  if (ref?.display) return ref.display;
  return ref?.reference ? `Medication ${String(ref.reference).split('/').pop().slice(0, 8)}…` : '';
}

/**
 * Fill in medicationReference.display from Medication resources found in the
 * same list (a document bundle, a compartment). Returns the same objects,
 * mutated, so rows render a drug name instead of a bare reference.
 */
export function enrichMedicationDisplays(resources) {
  const meds = new Map();
  for (const r of resources) if (r?.resourceType === 'Medication') meds.set(r.id, r);
  if (!meds.size) return resources;
  for (const r of resources) {
    const ref = r?.medicationReference;
    if (ref && !ref.display && ref.reference) {
      const m = meds.get(String(ref.reference).split('/').pop());
      if (m) ref.display = ccText(m.code);
    }
  }
  return resources;
}

export function pickName(res) {
  if (!res) return '';
  switch (res.resourceType) {
    case 'Patient': case 'Practitioner': case 'RelatedPerson': return humanName(res);
    case 'PractitionerRole': return res.practitioner?.display || ccText(res.code?.[0]) || 'Practitioner role';
    case 'Organization': case 'Location': case 'Device': return (typeof res.name === 'string' ? res.name : '') || '';
    case 'Medication': return ccText(res.code);
    case 'MedicationStatement': case 'MedicationRequest': case 'MedicationDispense':
      return medicationLabel(res) || ccText(res.medicationCodeableConcept);
    case 'Immunization': return ccText(res.vaccineCode);
    case 'Encounter': return ccText(res.type?.[0]) || res.class?.display || res.class?.code || 'Encounter';
    case 'Composition': return res.title || ccText(res.type);
    case 'DocumentReference': return res.description || ccText(res.type);
    case 'ImagingStudy': return res.description || (res.modality || [])[0]?.display || 'Imaging study';
    case 'Specimen': return ccText(res.type) || 'Specimen';
    case 'Observation': {
      const label = ccText(res.code);
      const v = valueText(res);
      return v ? `${label}: ${v}` : label;
    }
    default: return ccText(res.code) || ccText(res.type) || res.title || res.description || res.status || '';
  }
}

export function valueText(res) {
  if (!res) return '';
  if (res.valueQuantity) return `${res.valueQuantity.value ?? ''} ${res.valueQuantity.unit || res.valueQuantity.code || ''}`.trim();
  if (res.valueCodeableConcept) return ccText(res.valueCodeableConcept);
  if (res.valueString) return res.valueString;
  if (res.valueBoolean != null) return String(res.valueBoolean);
  if (res.valueInteger != null) return String(res.valueInteger);
  if (res.component?.length) {
    return res.component.map((c) => `${ccText(c.code).split(' ')[0]} ${valueText(c)}`).join(' / ');
  }
  return '';
}

export function pickDate(res) {
  if (!res) return '';
  return res.effectiveDateTime || res.effectivePeriod?.start || res.issued || res.recordedDate || res.onsetDateTime
    || res.occurrenceDateTime || res.performedDateTime || res.performedPeriod?.start || res.authoredOn
    || res.whenHandedOver || res.dateAsserted || res.period?.start || res.started || res.date || res.meta?.lastUpdated || '';
}

export function statusOf(res) {
  return res?.clinicalStatus?.coding?.[0]?.code || res?.status || '';
}

// ---------- documents ----------

export function categoryOfDocRef(dr) {
  for (const c of dr?.type?.coding || []) {
    if (CATEGORY_BY_LOINC[c.code]) return CATEGORY_BY_LOINC[c.code];
  }
  for (const cat of dr?.category || []) {
    for (const c of cat.coding || []) if (CATEGORY_BY_LOINC[c.code]) return CATEGORY_BY_LOINC[c.code];
  }
  return null;
}

export function categoryOfComposition(comp) {
  for (const c of comp?.type?.coding || []) if (CATEGORY_BY_LOINC[c.code]) return CATEGORY_BY_LOINC[c.code];
  return null;
}

/** 'Bundle/<id>' | 'https://host/Bundle/<id>' | 'Binary/<id>' -> id */
export function bundleIdFromAttachment(url) {
  if (!url) return null;
  const m = String(url).match(/(?:Bundle|Binary)\/([A-Za-z0-9._-]+)(?:[/?#]|$)/);
  return m ? m[1] : null;
}

export function attachmentUrl(dr) {
  return (dr?.content || [])[0]?.attachment?.url || null;
}

export function refId(ref) {
  if (!ref) return null;
  const s = typeof ref === 'string' ? ref : ref.reference;
  return s ? s.split('/').pop() : null;
}

export function refType(ref) {
  const s = typeof ref === 'string' ? ref : ref?.reference;
  if (!s) return null;
  const parts = s.split('/');
  return parts.length >= 2 ? parts[parts.length - 2] : null;
}

/** Resolve a Composition section entry reference inside a document bundle. */
export function resolveInBundle(bundle, reference) {
  if (!reference) return null;
  for (const e of bundle?.entry || []) {
    if (e.fullUrl === reference) return e.resource;
    const r = e.resource;
    if (r && `${r.resourceType}/${r.id}` === reference) return r;
    if (r && reference.endsWith(`/${r.resourceType}/${r.id}`)) return r;
  }
  return null;
}

// ---------- terminology links ----------

const SYSTEM_LINKS = {
  'http://loinc.org': (code) => `https://loinc.org/${encodeURIComponent(code)}/`,
  'http://snomed.info/sct': (code) => `https://browser.ihtsdotools.org/?perspective=full&conceptId1=${encodeURIComponent(code)}`,
  'http://hl7.org/fhir/sid/icd-10': (code) => `https://icd.who.int/browse10/2019/en#/${encodeURIComponent(code)}`,
  'http://www.whocc.no/atc': (code) => `https://www.whocc.no/atc_ddd_index/?code=${encodeURIComponent(code)}`,
  'http://unitsofmeasure.org': () => 'https://ucum.org/ucum',
};

export function codingLink(coding) {
  const fn = coding?.system && SYSTEM_LINKS[coding.system];
  return fn && coding.code ? fn(coding.code) : null;
}

export function shortSystem(system) {
  if (!system) return '';
  const map = {
    'http://loinc.org': 'LOINC', 'http://snomed.info/sct': 'SNOMED', 'http://hl7.org/fhir/sid/icd-10': 'ICD-10',
    'http://www.whocc.no/atc': 'ATC', 'http://unitsofmeasure.org': 'UCUM', 'urn:ietf:bcp:47': 'BCP-47',
    'http://hl7.org/fhir/sid/cvx': 'CVX', 'http://dicom.nema.org/resources/ontology/DCM': 'DICOM',
  };
  if (map[system]) return map[system];
  try { return new URL(system).host.replace(/^www\./, ''); } catch { return system.replace(/^urn:(oid:)?/, ''); }
}

/** Group compartment resources into clinical buckets for the patient page. */
export function bucketize(resources) {
  const buckets = {};
  for (const r of resources) {
    if (!r?.resourceType || r.resourceType === 'Patient') continue;
    (buckets[r.resourceType] ||= []).push(r);
  }
  for (const list of Object.values(buckets)) {
    list.sort((a, b) => String(pickDate(b)).localeCompare(String(pickDate(a))));
  }
  return buckets;
}

const TIMELINE_KIND = {
  Condition: ['Condition', 'alert'], Encounter: ['Encounter', 'hospital'], Procedure: ['Procedure', 'spark'],
  Observation: ['Result', 'flask'], Immunization: ['Vaccination', 'syringe'], MedicationRequest: ['Prescription', 'pill'],
  MedicationDispense: ['Dispensed', 'pill'], MedicationStatement: ['Medication', 'pill'],
  AllergyIntolerance: ['Allergy', 'alert'], DiagnosticReport: ['Report', 'documents'],
  ImagingStudy: ['Imaging', 'scan'], DocumentReference: ['Document', 'documents'], Composition: ['Document', 'documents'],
};

export function timelineEvents(resources, { limit = 60 } = {}) {
  const events = [];
  for (const r of resources) {
    const kind = TIMELINE_KIND[r.resourceType];
    if (!kind) continue;
    const date = pickDate(r);
    if (!date) continue;
    events.push({ date: String(date).slice(0, 19), label: kind[0], icon: kind[1], resource: r,
      detail: pickName(r), status: statusOf(r) });
  }
  events.sort((a, b) => b.date.localeCompare(a.date));
  return events.slice(0, limit);
}
