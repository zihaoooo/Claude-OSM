#!/usr/bin/env python3
"""
Isolate triangular city blocks created by diagonal streets (e.g. St. Nicholas
Ave slicing the Manhattan grid). Harlem is the default test site.

Standalone from osm_to_svg.py by design -- this is an *analysis* script, not the
figure-ground pipeline. It shares the same conventions (local UTM projection,
y-flipped SVG page, one semantic <g> per layer) but keeps its own geometry and
style so the two never entangle.

Pipeline:
  1. Fetch the street network for the study area (osmnx 2.x).
  2. Polygonize the road centerlines -> the enclosed block faces.
  3. Score each face for "triangularity" (3 dominant corners after
     simplification, within a sane area band) and keep the triangles.
  4. Emit a layered, hand-editable SVG:
       background / all blocks (ghost) / triangle blocks (highlight) / roads.

Run through the repo venv:
  Windows:  .venv\\Scripts\\python.exe scripts\\triangle_blocks.py
  *nix:     .venv/bin/python scripts/triangle_blocks.py
"""

import argparse
import math
import os

import numpy as np
import osmnx as ox
import geopandas as gpd
from shapely.geometry import Polygon, MultiLineString
from shapely.ops import polygonize, unary_union

# ----------------------------------------------------------------------------
# Style tokens (kept local so this script owns its look, decoupled from style.py)
# ----------------------------------------------------------------------------

STYLE = {
    "background":  {"fill": "#ffffff"},
    "blocks":      {"fill": "#eeeeee", "stroke": "#dcdcdc", "stroke_width": 0.4},
    "triangles":   {"fill": "#e8603c", "fill_opacity": "0.85",
                    "stroke": "#7a2a15", "stroke_width": 0.8},
    "roads":       {"fill": "none", "stroke": "#9a9a9a", "stroke_width": 0.5},
    "label":       {"fill": "#7a2a15", "stroke": "none",
                    "font_family": "sans-serif", "font_size": "9"},
}

PAGE_LONG_SIDE = 1400   # fit the longer page dimension to this many px
MARGIN = 40

# Triangle-detection defaults (metric, in the projected UTM meters).
DEFAULTS = dict(
    simplify_m=6.0,     # drop jogs/curvature smaller than this before counting
    corner_deg=24.0,    # a vertex counts as a "corner" if it turns >= this many deg
    min_area=120.0,     # m^2 -- ignore slivers below this
    max_area=45000.0,   # m^2 -- ignore full-size rectangular blocks above this
    max_fill=0.62,      # area / min-rotated-rect area; triangles fill ~0.5
)


def utm_crs_for(lon, lat):
    """Local true-meter UTM CRS for a point (same rule as osm_to_svg.py)."""
    zone = int(math.floor((lon + 180) / 6) % 60) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


# ----------------------------------------------------------------------------
# 1. FETCH  -> road centerlines as one metric GeoDataFrame
# ----------------------------------------------------------------------------

def fetch_roads(place, dist, network_type="drive"):
    """Fetch the street network around a geocoded place.

    Returns (edges_utm GeoDataFrame, crs). Default network_type='drive' keeps the
    real vehicular block structure -- 'all' fractures blocks with footpaths and
    driveways, flooding the result with incidental sidewalk-corner triangles.
    """
    print(f"Geocoding '{place}' ...")
    lat, lon = ox.geocode(place)
    crs = utm_crs_for(lon, lat)
    print(f"  center = {lat:.5f}, {lon:.5f}   UTM = {crs}")
    print(f"Fetching street network (dist={dist} m, network={network_type}) ...")
    G = ox.graph_from_point((lat, lon), dist=dist, network_type=network_type,
                            truncate_by_edge=True)
    edges = ox.graph_to_gdfs(G, nodes=False).to_crs(crs)
    print(f"  {len(edges)} road segments")
    return edges, crs


# ----------------------------------------------------------------------------
# 2. POLYGONIZE  -> the enclosed block faces
# ----------------------------------------------------------------------------

def block_faces(edges):
    """Turn the road-line soup into enclosed polygons (city blocks).

    unary_union nodes the network at every crossing (required for polygonize to
    find closed faces), then polygonize walks the minimal cycles.
    """
    merged = unary_union(list(edges.geometry))
    if merged.geom_type == "LineString":
        merged = MultiLineString([merged])
    faces = [g for g in polygonize(merged)]
    print(f"  polygonized into {len(faces)} block faces")
    return faces


# ----------------------------------------------------------------------------
# 3. TRIANGLE DETECTION
# ----------------------------------------------------------------------------

def _corner_count(poly, corner_deg):
    """Number of vertices whose turn angle exceeds corner_deg (a real corner)."""
    ring = list(poly.exterior.coords)[:-1]      # drop the repeated closing pt
    n = len(ring)
    if n < 3:
        return 0
    pts = np.array(ring)
    corners = 0
    for i in range(n):
        a = pts[(i - 1) % n]
        b = pts[i]
        c = pts[(i + 1) % n]
        v1 = a - b
        v2 = c - b
        n1 = np.hypot(*v1)
        n2 = np.hypot(*v2)
        if n1 < 1e-9 or n2 < 1e-9:
            continue
        cosang = np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0)
        interior = math.degrees(math.acos(cosang))
        turn = 180.0 - interior                 # deviation from a straight line
        if turn >= corner_deg:
            corners += 1
    return corners


def is_triangle(poly, cfg):
    """True if a block face reads as a triangle under the given thresholds."""
    area = poly.area
    if area < cfg["min_area"] or area > cfg["max_area"]:
        return False
    simp = poly.simplify(cfg["simplify_m"], preserve_topology=True)
    if simp.is_empty or simp.geom_type != "Polygon":
        return False
    if _corner_count(simp, cfg["corner_deg"]) != 3:
        return False
    # Secondary check: a true triangle fills ~half its bounding rectangle.
    mrr = simp.minimum_rotated_rectangle
    if mrr.area <= 0:
        return False
    return (simp.area / mrr.area) <= cfg["max_fill"]


def classify(faces, cfg):
    """Split faces into (triangle_polys, all_block_polys)."""
    tris = [p for p in faces if is_triangle(p, cfg)]
    print(f"  {len(tris)} triangular blocks isolated")
    return tris, faces


# ----------------------------------------------------------------------------
# 4. PROJECT + EMIT SVG
# ----------------------------------------------------------------------------

class Projector:
    """Map metric bounds onto a y-flipped SVG page."""

    def __init__(self, bounds, long_side=PAGE_LONG_SIDE):
        self.minx, self.miny, self.maxx, self.maxy = bounds
        w_m, h_m = self.maxx - self.minx, self.maxy - self.miny
        self.scale = (long_side - 2 * MARGIN) / max(w_m, h_m)
        self.page_w = w_m * self.scale + 2 * MARGIN
        self.page_h = h_m * self.scale + 2 * MARGIN

    def xy(self, x, y):
        return (MARGIN + (x - self.minx) * self.scale,
                MARGIN + (self.maxy - y) * self.scale)


def _ring_path(coords, proj):
    pts = [proj.xy(x, y) for x, y in coords]
    return "M" + " L".join(f"{px:.2f},{py:.2f}" for px, py in pts) + " Z"


def _poly_path(poly, proj):
    parts = [_ring_path(poly.exterior.coords, proj)]
    for r in poly.interiors:
        parts.append(_ring_path(r.coords, proj))
    return " ".join(parts)


def _line_path(coords, proj):
    pts = [proj.xy(x, y) for x, y in coords]
    return "M" + " L".join(f"{px:.2f},{py:.2f}" for px, py in pts)


def css_block():
    lines = ["  <style>"]
    for cls, props in STYLE.items():
        decls = "; ".join(f"{k.replace('_', '-')}:{v}" for k, v in props.items())
        lines.append(f"    .{cls} {{ {decls} }}")
    lines.append("  </style>")
    return "\n".join(lines)


def poly_group(gid, polys, proj):
    out = [f'  <g id="{gid}" class="{gid}">']
    for p in polys:
        if p.is_empty:
            continue
        sub = p.geoms if p.geom_type == "MultiPolygon" else [p]
        for g in sub:
            out.append(f'    <path d="{_poly_path(g, proj)}"/>')
    out.append("  </g>")
    return "\n".join(out)


def road_group(edges, proj):
    out = ['  <g id="roads" class="roads">']
    for geom in edges.geometry:
        if geom is None or geom.is_empty:
            continue
        sub = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
        for ln in sub:
            out.append(f'    <path d="{_line_path(ln.coords, proj)}"/>')
    out.append("  </g>")
    return "\n".join(out)


def label_group(tris, proj):
    """Number each triangle at its centroid, for referencing in the analysis."""
    out = ['  <g id="labels" class="label">']
    for i, p in enumerate(tris, 1):
        c = p.representative_point()
        px, py = proj.xy(c.x, c.y)
        out.append(f'    <text x="{px:.2f}" y="{py:.2f}" '
                   f'text-anchor="middle">{i}</text>')
    out.append("  </g>")
    return "\n".join(out)


def build_svg(edges, blocks, tris, proj):
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'width="{proj.page_w:.0f}" height="{proj.page_h:.0f}" '
           f'viewBox="0 0 {proj.page_w:.0f} {proj.page_h:.0f}">']
    svg.append(css_block())
    svg.append(f'  <rect class="background" x="0" y="0" '
               f'width="{proj.page_w:.0f}" height="{proj.page_h:.0f}"/>')
    svg.append(poly_group("blocks", blocks, proj))
    svg.append(poly_group("triangles", tris, proj))
    svg.append(road_group(edges, proj))
    svg.append(label_group(tris, proj))
    svg.append("</svg>")
    return "\n".join(svg)


# ----------------------------------------------------------------------------
# 5. CLI
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Isolate triangular blocks made by diagonal streets.")
    ap.add_argument("place", nargs="?", default="Harlem, New York, NY",
                    help='study-area center (default "Harlem, New York, NY")')
    ap.add_argument("--dist", type=int, default=1500,
                    help="half-extent in meters (default 1500)")
    ap.add_argument("--network", default="drive",
                    choices=["drive", "all", "walk", "bike"],
                    help="OSM network type (default drive = real block structure)")
    ap.add_argument("--simplify", type=float, default=DEFAULTS["simplify_m"],
                    help="pre-count simplify tolerance, m")
    ap.add_argument("--corner-deg", type=float, default=DEFAULTS["corner_deg"],
                    help="min turn angle to count a vertex as a corner")
    ap.add_argument("--min-area", type=float, default=DEFAULTS["min_area"])
    ap.add_argument("--max-area", type=float, default=DEFAULTS["max_area"])
    ap.add_argument("--max-fill", type=float, default=DEFAULTS["max_fill"],
                    help="max area/bounding-rect ratio (triangles ~0.5)")
    ap.add_argument("--long-side", type=int, default=PAGE_LONG_SIDE)
    ap.add_argument("-o", "--out", default="triangles.svg",
                    help="output SVG (bare names go in output/)")
    args = ap.parse_args()

    cfg = dict(simplify_m=args.simplify, corner_deg=args.corner_deg,
               min_area=args.min_area, max_area=args.max_area,
               max_fill=args.max_fill)

    edges, crs = fetch_roads(args.place, args.dist, network_type=args.network)
    faces = block_faces(edges)
    tris, blocks = classify(faces, cfg)

    bounds = edges.total_bounds
    proj = Projector(bounds, long_side=args.long_side)
    svg = build_svg(edges, blocks, tris, proj)

    out_path = args.out
    if not os.path.dirname(out_path):
        out_path = os.path.join("output", out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write(svg)
    print(f"Wrote {out_path}  ({proj.page_w:.0f} x {proj.page_h:.0f} px, "
          f"{len(tris)} triangles)")


if __name__ == "__main__":
    main()
