"""Where a resource came from, and which country it represents.

Two orthogonal facts the UI and the coverage map need, both derived from the
data itself rather than from the calling client (no client data is collected,
see docs/ui-design.md "Coverage"):

* **origin** — a ``meta.tag`` with system :data:`ORIGIN_TAG_SYSTEM`. Seeded
  resources are tagged ``reference`` by ``scripts/seed.py``; anything that
  arrives through an ingest boundary (ITI-105 submit, Epic import) is tagged
  ``community`` by :func:`app.fhir.naturalize.naturalize_bundle`. A second tag
  with system :data:`SUBMISSION_TAG_SYSTEM` links a community resource back to
  the inbox bundle it arrived in.
* **country** — ISO 3166-1 alpha-2, read from ``Patient.address.country`` and
  normalised. When a bundle's Patient has no country we fall back to the
  Composition custodian's address, then any Organization in the bundle, then
  give up. We never guess.
"""
from __future__ import annotations

from typing import Any

ORIGIN_TAG_SYSTEM = "urn:ehds-demo:origin"
SUBMISSION_TAG_SYSTEM = "urn:ehds-demo:submission"

REFERENCE = "reference"
COMMUNITY = "community"

_ORIGIN_DISPLAY = {REFERENCE: "Reference example", COMMUNITY: "Community submission"}


# ---------- origin tags ----------

def tag_origin(res: dict[str, Any], kind: str, *, submission_id: str | None = None) -> None:
    """Stamp ``res.meta.tag`` with the origin kind (replacing any previous origin
    tag) and, for community resources, the submission it belongs to. Idempotent."""
    if kind not in _ORIGIN_DISPLAY:
        raise ValueError(f"unknown origin kind: {kind!r}")
    meta = res.setdefault("meta", {})
    tags = [t for t in (meta.get("tag") or [])
            if t.get("system") not in (ORIGIN_TAG_SYSTEM, SUBMISSION_TAG_SYSTEM)]
    tags.append({"system": ORIGIN_TAG_SYSTEM, "code": kind, "display": _ORIGIN_DISPLAY[kind]})
    if submission_id:
        tags.append({"system": SUBMISSION_TAG_SYSTEM, "code": submission_id})
    meta["tag"] = tags


def origin_of(res: dict[str, Any]) -> dict[str, Any]:
    """``{"kind": reference|community|unknown, "submission": id|None, "source": url|None}``."""
    meta = res.get("meta") or {}
    kind = "unknown"
    submission = None
    for t in meta.get("tag") or []:
        if t.get("system") == ORIGIN_TAG_SYSTEM and t.get("code") in _ORIGIN_DISPLAY:
            kind = t["code"]
        elif t.get("system") == SUBMISSION_TAG_SYSTEM and t.get("code"):
            submission = t["code"]
    return {"kind": kind, "submission": submission, "source": meta.get("source")}


# ---------- countries ----------

# Every country drawn on the coverage map (static/assets/europe.svg), keyed by
# alpha-2. Non-European codes normalise fine but are listed beside the map.
COUNTRY_NAMES: dict[str, str] = {
    "AL": "Albania", "AD": "Andorra", "AM": "Armenia", "AT": "Austria", "AZ": "Azerbaijan",
    "BY": "Belarus", "BE": "Belgium", "BA": "Bosnia and Herzegovina", "BG": "Bulgaria",
    "HR": "Croatia", "CY": "Cyprus", "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia",
    "FI": "Finland", "FR": "France", "GE": "Georgia", "DE": "Germany", "GR": "Greece",
    "HU": "Hungary", "IS": "Iceland", "IE": "Ireland", "IT": "Italy", "XK": "Kosovo",
    "LV": "Latvia", "LI": "Liechtenstein", "LT": "Lithuania", "LU": "Luxembourg",
    "MT": "Malta", "MD": "Moldova", "MC": "Monaco", "ME": "Montenegro", "NL": "Netherlands",
    "MK": "North Macedonia", "NO": "Norway", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "RU": "Russia", "SM": "San Marino", "RS": "Serbia", "SK": "Slovakia", "SI": "Slovenia",
    "ES": "Spain", "SE": "Sweden", "CH": "Switzerland", "TR": "Türkiye", "UA": "Ukraine",
    "GB": "United Kingdom", "VA": "Vatican City",
    # frequent non-European origins, so the "beside the map" list has names too
    "US": "United States", "CA": "Canada", "AU": "Australia", "JP": "Japan", "BR": "Brazil",
    "IN": "India", "CN": "China", "IL": "Israel", "NZ": "New Zealand", "ZA": "South Africa",
}

EUROPEAN_COUNTRIES: frozenset[str] = frozenset({
    "AL", "AD", "AM", "AT", "AZ", "BY", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE", "FI",
    "FR", "GE", "DE", "GR", "HU", "IS", "IE", "IT", "XK", "LV", "LI", "LT", "LU", "MT", "MD",
    "MC", "ME", "NL", "MK", "NO", "PL", "PT", "RO", "RU", "SM", "RS", "SK", "SI", "ES", "SE",
    "CH", "TR", "UA", "GB", "VA",
})

_ALPHA3 = {
    "ALB": "AL", "AND": "AD", "ARM": "AM", "AUT": "AT", "AZE": "AZ", "BLR": "BY", "BEL": "BE",
    "BIH": "BA", "BGR": "BG", "HRV": "HR", "CYP": "CY", "CZE": "CZ", "DNK": "DK", "EST": "EE",
    "FIN": "FI", "FRA": "FR", "GEO": "GE", "DEU": "DE", "GRC": "GR", "HUN": "HU", "ISL": "IS",
    "IRL": "IE", "ITA": "IT", "XKX": "XK", "LVA": "LV", "LIE": "LI", "LTU": "LT", "LUX": "LU",
    "MLT": "MT", "MDA": "MD", "MCO": "MC", "MNE": "ME", "NLD": "NL", "MKD": "MK", "NOR": "NO",
    "POL": "PL", "PRT": "PT", "ROU": "RO", "RUS": "RU", "SMR": "SM", "SRB": "RS", "SVK": "SK",
    "SVN": "SI", "ESP": "ES", "SWE": "SE", "CHE": "CH", "TUR": "TR", "UKR": "UA", "GBR": "GB",
    "VAT": "VA", "USA": "US", "CAN": "CA", "AUS": "AU", "JPN": "JP", "BRA": "BR", "IND": "IN",
    "CHN": "CN", "ISR": "IL", "NZL": "NZ", "ZAF": "ZA",
}

# Names in English plus the local-language forms a Patient.address.country is
# likely to carry in practice. Keys are lower-cased and stripped.
_NAME_ALIASES: dict[str, str] = {
    "uk": "GB", "great britain": "GB", "britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "united states": "US", "united states of america": "US", "u.s.": "US", "u.s.a.": "US", "america": "US",
    "österreich": "AT", "oesterreich": "AT",
    "deutschland": "DE",
    "the netherlands": "NL", "nederland": "NL", "holland": "NL",
    "españa": "ES", "espana": "ES",
    "suomi": "FI",
    "sverige": "SE",
    "norge": "NO", "noreg": "NO",
    "danmark": "DK",
    "polska": "PL",
    "italia": "IT",
    "belgië": "BE", "belgique": "BE", "belgien": "BE",
    "schweiz": "CH", "suisse": "CH", "svizzera": "CH",
    "éire": "IE", "eire": "IE",
    "česko": "CZ", "cesko": "CZ", "czech republic": "CZ",
    "magyarország": "HU",
    "hrvatska": "HR",
    "ελλάδα": "GR", "hellas": "GR",
    "türkiye": "TR", "turkey": "TR",
    "россия": "RU", "russian federation": "RU",
    "україна": "UA",
    "slovensko": "SK", "slovenija": "SI",
    "românia": "RO", "romania": "RO",
    "българия": "BG", "eesti": "EE", "latvija": "LV", "lietuva": "LT",
    "moldova": "MD", "republic of moldova": "MD",
    "north macedonia": "MK", "macedonia": "MK",
    "bosnia": "BA", "kosovo": "XK",
    "vatican": "VA", "holy see": "VA",
    "luxemburg": "LU", "lëtzebuerg": "LU",
    "ísland": "IS",
}
_NAME_ALIASES.update({name.lower(): code for code, name in COUNTRY_NAMES.items()})


def normalize_country(value: Any) -> str | None:
    """Return an alpha-2 code for ``value`` (alpha-2, alpha-3 or a country name),
    or None when it cannot be recognised. Never guesses."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    up = v.upper()
    if len(up) == 2 and up.isalpha() and up in COUNTRY_NAMES:
        return up
    if len(up) == 3 and up.isalpha() and up in _ALPHA3:
        return _ALPHA3[up]
    return _NAME_ALIASES.get(v.lower())


def is_european(code: Any) -> bool:
    return isinstance(code, str) and code in EUROPEAN_COUNTRIES


def _country_from_addresses(addresses: Any) -> str | None:
    if not isinstance(addresses, list):
        return None
    # home address first, then whatever else carries a recognisable country
    ordered = sorted(addresses, key=lambda a: 0 if isinstance(a, dict) and a.get("use") == "home" else 1)
    for a in ordered:
        if isinstance(a, dict):
            code = normalize_country(a.get("country"))
            if code:
                return code
    return None


def country_of_patient(patient: dict[str, Any] | None) -> str | None:
    if not isinstance(patient, dict):
        return None
    return _country_from_addresses(patient.get("address"))


def _resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for e in bundle.get("entry") or []:
        r = e.get("resource") if isinstance(e, dict) else None
        if isinstance(r, dict):
            out.append(r)
    return out


def country_of_bundle(bundle: dict[str, Any]) -> tuple[str | None, str | None]:
    """``(alpha2, how)`` where ``how`` is ``patient`` | ``custodian`` |
    ``organization``; ``(None, None)`` when nothing in the bundle says."""
    resources = _resources(bundle)
    for r in resources:
        if r.get("resourceType") == "Patient":
            code = country_of_patient(r)
            if code:
                return code, "patient"
    orgs = {r.get("id"): r for r in resources if r.get("resourceType") == "Organization"}
    for r in resources:
        if r.get("resourceType") == "Composition":
            ref = ((r.get("custodian") or {}).get("reference") or "")
            org = orgs.get(ref.rsplit("/", 1)[-1]) if ref else None
            code = _country_from_addresses(org.get("address")) if org else None
            if code:
                return code, "custodian"
    for org in orgs.values():
        code = _country_from_addresses(org.get("address"))
        if code:
            return code, "organization"
    return None, None


# ---------- priority category from the document type ----------

# LOINC document-type codes → priority-category slug. The first code per
# category is what our compiler emits (app.fhir.document.CATEGORY_TO_DOC_TYPE);
# the rest are codes the EU IGs fix or that partner pipelines emit.
CATEGORY_BY_LOINC: dict[str, str] = {
    "60591-5": "patient-summary",
    "11502-2": "laboratory-report",
    "18842-5": "discharge-report",
    "34105-7": "discharge-report",   # HDR Composition.type is fixed to this code
    "18748-4": "imaging-report",
    "57833-6": "prescription",
}


def _loinc_codes(cc: Any) -> list[str]:
    if not isinstance(cc, dict):
        return []
    return [c.get("code") for c in cc.get("coding") or []
            if isinstance(c, dict) and c.get("system") == "http://loinc.org" and c.get("code")]


def category_of_bundle(bundle: dict[str, Any]) -> str | None:
    """Priority category of a submitted bundle, from the DocumentReference type
    (MHD registry entry) or, failing that, the Composition type."""
    resources = _resources(bundle)
    for rtype in ("DocumentReference", "Composition"):
        for r in resources:
            if r.get("resourceType") != rtype:
                continue
            for code in _loinc_codes(r.get("type")):
                if code in CATEGORY_BY_LOINC:
                    return CATEGORY_BY_LOINC[code]
    return None
