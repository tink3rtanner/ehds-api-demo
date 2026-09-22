#!/usr/bin/env python3
"""Build ``static/assets/europe.svg`` from the world-atlas TopoJSON.

Standard library only. Downloads (and caches) ``countries-50m.json`` from
world-atlas, decodes the TopoJSON by hand, projects with a Lambert Azimuthal
Equal-Area projection centred on 10E/52N (the ETRS89-LAEA / EPSG:3035
parameters), lightly simplifies with Douglas-Peucker and writes one
``<path id="XX">`` per country keyed by ISO 3166-1 alpha-2.

Usage::

    python -m scripts.build_europe_map            # download + cache + write
    python -m scripts.build_europe_map --source /path/to/countries-50m.json
    python -m scripts.build_europe_map --out static/assets/europe.svg

Source: https://github.com/topojson/world-atlas (ISC licence), derived from
Natural Earth (public domain).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from pathlib import Path
from xml.sax.saxutils import quoteattr

SOURCE_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json"
# Session scratchpad; override with --cache-dir.
DEFAULT_CACHE_DIR = Path(
    "/tmp/claude-1000/-srv-ehds-api/dee82d23-5eb7-4d68-88ed-37b411763bed/scratchpad"  # noqa: S108
)
CACHE_NAME = "countries-50m.json"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "static" / "assets" / "europe.svg"

# --------------------------------------------------------------------------
# ISO 3166-1 numeric -> alpha-2, for the countries we emit.
# --------------------------------------------------------------------------

EUROPE: dict[str, str] = {
    "008": "AL", "020": "AD", "040": "AT", "112": "BY", "056": "BE",
    "070": "BA", "100": "BG", "191": "HR", "196": "CY", "203": "CZ",
    "208": "DK", "233": "EE", "246": "FI", "250": "FR", "276": "DE",
    "300": "GR", "348": "HU", "352": "IS", "372": "IE", "380": "IT",
    "428": "LV", "438": "LI", "440": "LT", "442": "LU", "470": "MT",
    "498": "MD", "492": "MC", "499": "ME", "528": "NL", "807": "MK",
    "578": "NO", "616": "PL", "620": "PT", "642": "RO", "643": "RU",
    "674": "SM", "688": "RS", "703": "SK", "705": "SI", "724": "ES",
    "752": "SE", "756": "CH", "792": "TR", "804": "UA", "826": "GB",
    "336": "VA", "268": "GE", "051": "AM", "031": "AZ",
}

NEIGHBOURS: dict[str, str] = {
    "504": "MA", "012": "DZ", "788": "TN", "434": "LY", "818": "EG",
    "760": "SY", "422": "LB", "376": "IL", "400": "JO", "368": "IQ",
    "364": "IR", "398": "KZ", "795": "TM", "860": "UZ", "304": "GL",
}

# Some geometries in world-atlas carry no numeric id (disputed territories).
# Match those by ``properties.name`` instead.
BY_NAME_EUROPE: dict[str, str] = {
    "Kosovo": "XK",
}

# Which alpha-2 codes are "European" (drives the viewBox) vs context.
EUROPE_CODES = set(EUROPE.values()) | set(BY_NAME_EUROPE.values())
NEIGHBOUR_CODES = set(NEIGHBOURS.values())

# Geographic clip window applied to rings (by centroid) before projection.
LON_MIN, LON_MAX = -25.0, 45.0
LAT_MIN, LAT_MAX = 33.0, 72.0

# Wider context window. Rings of neighbours and of the big straddlers are kept
# when their bbox intersects this box even if their centroid is outside the
# clip window; the SVG viewBox (fitted to the European set) crops them.
CTX_LON_MIN, CTX_LON_MAX = -30.0, 60.0
CTX_LAT_MIN, CTX_LAT_MAX = 20.0, 85.0

# European countries that straddle the clip window edge.
STRADDLERS = {"RU", "TR", "GE", "AM", "AZ"}

# Projection centre (EPSG:3035).
LON0 = 10.0
LAT0 = 52.0

# --------------------------------------------------------------------------
# TopoJSON decoding
# --------------------------------------------------------------------------

Point = tuple[float, float]
Ring = list[Point]


def decode_arcs(topo: dict) -> list[Ring]:
    """Decode every arc: delta-decode then apply the quantization transform."""
    transform = topo.get("transform")
    if transform:
        sx, sy = transform["scale"]
        tx, ty = transform["translate"]
    else:
        sx = sy = 1.0
        tx = ty = 0.0

    out: list[Ring] = []
    for arc in topo["arcs"]:
        x = 0
        y = 0
        pts: Ring = []
        for dx, dy in arc:
            if transform:
                x += dx
                y += dy
                pts.append((x * sx + tx, y * sy + ty))
            else:
                pts.append((float(dx), float(dy)))
        out.append(pts)
    return out


def ring_from_arc_indices(indices: list[int], arcs: list[Ring]) -> Ring:
    """Stitch arcs into one ring. Negative index ``i`` means ``arcs[~i]`` reversed.

    Consecutive arcs share their end/start vertex; drop the duplicate join.
    """
    ring: Ring = []
    for idx in indices:
        if idx < 0:
            seg = arcs[~idx][::-1]
        else:
            seg = arcs[idx]
        if ring and seg and ring[-1] == seg[0]:
            ring.extend(seg[1:])
        else:
            ring.extend(seg)
    return ring


def geometry_polygons(geom: dict, arcs: list[Ring]) -> list[list[Ring]]:
    """Return a list of polygons, each a list of rings (outer first)."""
    gtype = geom["type"]
    if gtype == "Polygon":
        polys = [geom["arcs"]]
    elif gtype == "MultiPolygon":
        polys = geom["arcs"]
    elif gtype == "GeometryCollection":
        result: list[list[Ring]] = []
        for g in geom["geometries"]:
            result.extend(geometry_polygons(g, arcs))
        return result
    else:
        return []
    return [[ring_from_arc_indices(r, arcs) for r in poly] for poly in polys]


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------


def ring_centroid(ring: Ring) -> Point:
    """Area-weighted centroid (shoelace); falls back to vertex mean."""
    a = 0.0
    cx = 0.0
    cy = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-12:
        return (sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n)
    a *= 0.5
    return (cx / (6 * a), cy / (6 * a))


def ring_bbox(ring: Ring) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def in_window(p: Point) -> bool:
    return LON_MIN <= p[0] <= LON_MAX and LAT_MIN <= p[1] <= LAT_MAX


def bbox_intersects_context(ring: Ring) -> bool:
    x0, y0, x1, y1 = ring_bbox(ring)
    return not (x1 < CTX_LON_MIN or x0 > CTX_LON_MAX or y1 < CTX_LAT_MIN or y0 > CTX_LAT_MAX)


def classify_polygon(poly: list[Ring], code: str) -> str | None:
    """Return "fit" (centroid inside clip window, drives the viewBox),
    "overflow" (kept for context, cropped by viewBox) or None (dropped)."""
    outer = poly[0]
    if not outer:
        return None
    x0, _, x1, _ = ring_bbox(outer)
    if x1 - x0 > 180.0:
        # Crosses the antimeridian (e.g. Wrangel Island); a lon/lat centroid
        # is meaningless and it is nowhere near Europe anyway.
        return None
    if in_window(ring_centroid(outer)):
        return "fit"
    if (code in STRADDLERS or code in NEIGHBOUR_CODES) and bbox_intersects_context(outer):
        return "overflow"
    return None


# --------------------------------------------------------------------------
# Projection: Lambert Azimuthal Equal-Area (spherical form)
# --------------------------------------------------------------------------

_LAT0 = math.radians(LAT0)
_LON0 = math.radians(LON0)
_SIN0 = math.sin(_LAT0)
_COS0 = math.cos(_LAT0)


def project(lon: float, lat: float) -> Point | None:
    """Return (x, y) in unit-sphere units with y up; None for the antipode."""
    lam = math.radians(lon) - _LON0
    phi = math.radians(lat)
    sinp = math.sin(phi)
    cosp = math.cos(phi)
    cosl = math.cos(lam)
    denom = 1.0 + _SIN0 * sinp + _COS0 * cosp * cosl
    if denom <= 1e-9:
        return None
    k = math.sqrt(2.0 / denom)
    x = k * cosp * math.sin(lam)
    y = k * (_COS0 * sinp - _SIN0 * cosp * cosl)
    return (x, y)


def project_ring(ring: Ring) -> Ring:
    out: Ring = []
    for lon, lat in ring:
        p = project(lon, lat)
        if p is not None:
            out.append(p)
    return out


# --------------------------------------------------------------------------
# Douglas-Peucker simplification
# --------------------------------------------------------------------------


def _perp_dist(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx = bx - ax
    dy = by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg2
    t = max(0.0, min(1.0, t))
    qx = ax + t * dx
    qy = ay + t * dy
    return math.hypot(px - qx, py - qy)


def douglas_peucker(points: Ring, tol: float) -> Ring:
    """Iterative DP (avoids recursion limits on long coastlines)."""
    n = len(points)
    if n < 3:
        return list(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        s, e = stack.pop()
        if e <= s + 1:
            continue
        best = -1.0
        best_i = -1
        a = points[s]
        b = points[e]
        for i in range(s + 1, e):
            d = _perp_dist(points[i], a, b)
            if d > best:
                best = d
                best_i = i
        if best > tol:
            keep[best_i] = True
            stack.append((s, best_i))
            stack.append((best_i, e))
    return [p for p, k in zip(points, keep, strict=True) if k]


def simplify_ring(ring: Ring, tol: float) -> Ring:
    """Simplify a closed ring. Drop rings that collapse below a triangle."""
    if not ring:
        return ring
    closed = ring[0] == ring[-1]
    pts = ring if closed else [*ring, ring[0]]
    # Split at the vertex farthest from the start so DP has two real anchors.
    if len(pts) > 3:
        far = max(range(1, len(pts) - 1), key=lambda i: math.dist(pts[0], pts[i]))
        a = douglas_peucker(pts[: far + 1], tol)
        b = douglas_peucker(pts[far:], tol)
        pts = a + b[1:]
    else:
        pts = douglas_peucker(pts, tol)
    if pts and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return []
    return pts


# --------------------------------------------------------------------------
# Main pipeline
# --------------------------------------------------------------------------


def load_topology(source: str | None, cache_dir: Path) -> dict:
    if source:
        with open(source, encoding="utf-8") as fh:
            return json.load(fh)
    cache_file = cache_dir / CACHE_NAME
    if not cache_file.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        print(f"downloading {SOURCE_URL} -> {cache_file}", file=sys.stderr)
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "ehds-api/build_europe_map"})
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            data = resp.read()
        cache_file.write_bytes(data)
    with cache_file.open(encoding="utf-8") as fh:
        return json.load(fh)


def fmt(v: float) -> str:
    s = f"{v:.1f}"
    if s == "-0.0":
        s = "0.0"
    return s


def rings_to_path(rings: list[Ring]) -> str:
    parts: list[str] = []
    for ring in rings:
        first = True
        for x, y in ring:
            parts.append(("M" if first else "L") + fmt(x) + " " + fmt(y))
            first = False
        parts.append("Z")
    return "".join(parts)


def build(topo: dict, tolerance_px: float, target_w: float, target_h: float):
    arcs = decode_arcs(topo)
    geoms = topo["objects"]["countries"]["geometries"]

    # Resolve geometry -> (code, name, class)
    resolved: dict[str, tuple[str, dict]] = {}
    for g in geoms:
        gid = g.get("id")
        name = g.get("properties", {}).get("name", "")
        code = None
        if gid is not None:
            gid = str(gid).zfill(3)
            code = EUROPE.get(gid) or NEIGHBOURS.get(gid)
        if code is None and name in BY_NAME_EUROPE:
            code = BY_NAME_EUROPE[name]
        if code is None:
            continue
        if code in resolved:
            print(f"warning: duplicate geometry for {code} ({name})", file=sys.stderr)
            continue
        resolved[code] = (name, g)

    wanted = EUROPE_CODES | NEIGHBOUR_CODES
    missing = sorted(wanted - set(resolved))

    # Decode, clip by ring centroid, project (still in sphere units, y up).
    # The fit box is sampled from European vertices that lie inside the
    # geographic window, so straddlers (Russia) cannot inflate it.
    projected: dict[str, tuple[str, list[Ring]]] = {}
    xs: list[float] = []
    ys: list[float] = []
    for code, (name, g) in resolved.items():
        polys = geometry_polygons(g, arcs)
        rings: list[Ring] = []
        for poly in polys:
            if classify_polygon(poly, code) is None:
                continue
            for ring in poly:
                pr = project_ring(ring)
                if len(pr) < 3:
                    continue
                rings.append(pr)
                if code in EUROPE_CODES:
                    for (lon, lat), (px, py) in zip(ring, pr, strict=False):
                        if LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX:
                            xs.append(px)
                            ys.append(py)
        if rings:
            projected[code] = (name, rings)
        else:
            print(f"warning: {code} ({name}) has no rings inside window", file=sys.stderr)

    # Fit: bounding box of the European set (window-interior vertices only).
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    span_x = maxx - minx
    span_y = maxy - miny
    scale = min(target_w / span_x, target_h / span_y)
    width = span_x * scale
    height = span_y * scale

    def to_px(p: Point) -> Point:
        return ((p[0] - minx) * scale, (maxy - p[1]) * scale)

    out: dict[str, tuple[str, list[Ring], int]] = {}
    for code, (name, rings) in projected.items():
        px_rings: list[Ring] = []
        for r in rings:
            pr = simplify_ring([to_px(p) for p in r], tolerance_px)
            if pr:
                px_rings.append(pr)
        if px_rings:
            out[code] = (name, px_rings, sum(len(r) for r in px_rings))

    view = (0.0, 0.0, width, height)
    return out, view, missing


def write_svg(out_path: Path, countries, view, cmd: str) -> int:
    vb = " ".join(fmt(v) for v in view)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!--",
        "  Europe base map, one <path> per country keyed by ISO 3166-1 alpha-2.",
        "  Source: world-atlas countries-50m.json (https://github.com/topojson/world-atlas,",
        "  ISC licence), derived from Natural Earth 1:50m (public domain).",
        "  Projection: Lambert Azimuthal Equal-Area, centre 10E 52N (ETRS89-LAEA / EPSG:3035 parameters).",
        f"  Generated by: {cmd}",
        "  Do not edit by hand; rerun the generator instead.",
        "-->",
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}">',
        '<g id="neighbours">',
    ]
    for code in sorted(c for c in countries if c in NEIGHBOUR_CODES):
        name, rings, _ = countries[code]
        lines.append(
            f'<path id="{code}" class="neighbour" data-name={quoteattr(name)} d="{rings_to_path(rings)}"/>'
        )
    lines.append("</g>")
    lines.append('<g id="countries">')
    for code in sorted(c for c in countries if c in EUROPE_CODES):
        name, rings, _ = countries[code]
        lines.append(
            f'<path id="{code}" class="country" data-name={quoteattr(name)} d="{rings_to_path(rings)}"/>'
        )
    lines.append("</g>")
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = "\n".join(lines) + "\n"
    out_path.write_text(data, encoding="utf-8")
    return len(data.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="local countries-50m.json instead of download/cache")
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="where to cache the download")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"output SVG path (default {DEFAULT_OUT})")
    ap.add_argument("--tolerance", type=float, default=0.6, help="Douglas-Peucker tolerance in px (default 0.6)")
    ap.add_argument("--width", type=float, default=1000.0, help="target viewBox width (default 1000)")
    ap.add_argument("--height", type=float, default=900.0, help="target viewBox height (default 900)")
    args = ap.parse_args(argv)

    topo = load_topology(args.source, Path(args.cache_dir))
    countries, view, missing = build(topo, args.tolerance, args.width, args.height)
    cmd = "python -m scripts.build_europe_map" + (f" --tolerance {args.tolerance}" if args.tolerance != 0.6 else "")
    size = write_svg(Path(args.out), countries, view, cmd)

    n_eu = sum(1 for c in countries if c in EUROPE_CODES)
    n_nb = sum(1 for c in countries if c in NEIGHBOUR_CODES)
    n_pts = sum(v[2] for v in countries.values())
    print(f"wrote {args.out}")
    print(f"viewBox: {' '.join(fmt(v) for v in view)}")
    print(f"countries emitted: {n_eu} european + {n_nb} neighbours = {len(countries)}")
    print(f"vertices: {n_pts}")
    print(f"missing ids: {', '.join(missing) if missing else 'none'}")
    print(f"file size: {size} bytes ({size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
