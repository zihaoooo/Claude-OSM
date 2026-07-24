#!/usr/bin/env python3
"""
Isolate NYC's named triangle spaces (e.g. Samuel Marx Triangle) -- the leftover
public slivers thrown off where diagonal avenues cut the street grid.

Unlike triangle_blocks.py (which *infers* triangles by polygonizing streets),
this pulls the authoritative catalog: NYC Parks Properties, filtered to
typecategory = "Triangle/Plaza". That dataset names and delineates each space,
so tiny planted islands the street-polygonization misses are captured exactly.

  Source: NYC Open Data "Parks Properties" (Socrata id enfh-gkve), geometry in
  the `multipolygon` column, selected via the SODA API.

The OSM drive network for the same window is fetched only as a *context base*
(streets + ghost blocks) so each triangle sits in the grid that created it.

Pipeline:
  1. Geocode the study area; fetch the OSM drive network for a dist-m window.
  2. Query Socrata for Triangle/Plaza polygons within that window's lat/lon box.
  3. Project everything to the local UTM; polygonize streets into ghost blocks.
  4. Emit a layered SVG: background / blocks / roads / triangles / labels.

Run through the repo venv:
  Windows:  .venv\\Scripts\\python.exe scripts\\parks_triangles.py
  *nix:     .venv/bin/python scripts/parks_triangles.py
"""

import argparse
import json
import math
import os
import urllib.parse
import urllib.request

import osmnx as ox
import geopandas as gpd
from shapely.geometry import shape, MultiLineString
from shapely.ops import polygonize, unary_union

# ----------------------------------------------------------------------------
# Style tokens (local, decoupled from style.py and triangle_blocks.py)
# ----------------------------------------------------------------------------

STYLE = {
    "background": {"fill": "#ffffff"},
    "blocks":     {"fill": "#f1f1f1", "stroke": "#e2e2e2", "stroke_width": 0.4},
    "roads":      {"fill": "none", "stroke": "#b0b0b0", "stroke_width": 0.5},
    "triangles":  {"fill": "#e8603c", "fill_opacity": "0.9",
                   "stroke": "#7a2a15", "stroke_width": 0.9},
    "label":      {"fill": "#5a1f0e", "stroke": "none",
                   "font_family": "sans-serif", "font_size": "8"},
}

PAGE_LONG_SIDE = 1400
MARGIN = 40

SOCRATA = "https://data.cityofnewyork.us/resource/enfh-gkve.geojson"


def utm_crs_for(lon, lat):
    """Local true-meter UTM CRS for a point (same rule as osm_to_svg.py)."""
    zone = int(math.floor((lon + 180) / 6) % 60) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


# ----------------------------------------------------------------------------
# 1. OSM CONTEXT  (street network -> edges + ghost block faces)
# ----------------------------------------------------------------------------

def fetch_context(place, dist, network_type="drive"):
    """Fetch the OSM street network; return (edges_utm, crs, lonlat_bounds)."""
    print(f"Geocoding '{place}' ...")
    lat, lon = ox.geocode(place)
    crs = utm_crs_for(lon, lat)
    print(f"  center = {lat:.5f}, {lon:.5f}   UTM = {crs}")
    print(f"Fetching street network (dist={dist} m, network={network_type}) ...")
    G = ox.graph_from_point((lat, lon), dist=dist, network_type=network_type,
                            truncate_by_edge=True)
    edges = ox.graph_to_gdfs(G, nodes=False)
    w, s, e, n = edges.total_bounds          # lon/lat (EPSG:4326 from osmnx)
    print(f"  {len(edges)} road segments; bbox W{w:.4f} S{s:.4f} E{e:.4f} N{n:.4f}")
    return edges.to_crs(crs), crs, (w, s, e, n)


def block_faces(edges):
    """Polygonize road centerlines into enclosed block faces (context only)."""
    merged = unary_union(list(edges.geometry))
    if merged.geom_type == "LineString":
        merged = MultiLineString([merged])
    faces = list(polygonize(merged))
    print(f"  polygonized into {len(faces)} ghost block faces")
    return faces


# ----------------------------------------------------------------------------
# 2. NYC PARKS  (Triangle/Plaza polygons from Socrata within the window)
# ----------------------------------------------------------------------------

def fetch_triangles(bounds_lonlat, types, crs):
    """Fetch Parks Properties of the given typecategories within the lat/lon box.

    Returns a GeoDataFrame in `crs` with a `signname` column for labeling.
    within_box takes (lat_north, lon_west, lat_south, lon_east).
    """
    w, s, e, n = bounds_lonlat
    type_list = ",".join(f"'{t}'" for t in types)
    where = (f"typecategory in ({type_list}) "
             f"and within_box(multipolygon, {n}, {w}, {s}, {e})")
    params = {"$select": "signname,typecategory,acres,multipolygon",
              "$where": where, "$limit": "5000"}
    url = SOCRATA + "?" + urllib.parse.urlencode(params)
    print(f"Querying NYC Parks Properties for {types} ...")
    with urllib.request.urlopen(url, timeout=60) as resp:
        fc = json.loads(resp.read().decode())
    feats = fc.get("features", [])
    print(f"  {len(feats)} triangle spaces in window")
    geoms, names = [], []
    for f in feats:
        g = f.get("geometry")
        if not g:
            continue
        geoms.append(shape(g))
        names.append((f.get("properties") or {}).get("signname") or "")
    if not geoms:
        return gpd.GeoDataFrame({"signname": []}, geometry=[], crs="EPSG:4326")
    gdf = gpd.GeoDataFrame({"signname": names}, geometry=geoms, crs="EPSG:4326")
    return gdf.to_crs(crs)


# ----------------------------------------------------------------------------
# 3. PROJECT + EMIT SVG
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
        if p is None or p.is_empty:
            continue
        sub = p.geoms if p.geom_type == "MultiPolygon" else [p]
        for g in sub:
            if g.geom_type != "Polygon":
                continue
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


def label_group(tri_gdf, proj):
    """Name each triangle at its centroid (signname, falling back to index)."""
    out = ['  <g id="labels" class="label">']
    for i, (name, geom) in enumerate(zip(tri_gdf["signname"], tri_gdf.geometry), 1):
        if geom is None or geom.is_empty:
            continue
        c = geom.representative_point()
        px, py = proj.xy(c.x, c.y)
        txt = name if name else f"#{i}"
        out.append(f'    <text x="{px:.2f}" y="{py:.2f}" '
                   f'text-anchor="middle">{_xml(txt)}</text>')
    out.append("  </g>")
    return "\n".join(out)


def _xml(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_svg(edges, blocks, tri_gdf, proj):
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'width="{proj.page_w:.0f}" height="{proj.page_h:.0f}" '
           f'viewBox="0 0 {proj.page_w:.0f} {proj.page_h:.0f}">']
    svg.append(css_block())
    svg.append(f'  <rect class="background" x="0" y="0" '
               f'width="{proj.page_w:.0f}" height="{proj.page_h:.0f}"/>')
    svg.append(poly_group("blocks", blocks, proj))
    svg.append(road_group(edges, proj))
    svg.append(poly_group("triangles", list(tri_gdf.geometry), proj))
    svg.append(label_group(tri_gdf, proj))
    svg.append("</svg>")
    return "\n".join(svg)


# ----------------------------------------------------------------------------
# 4. CLI
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Isolate NYC named triangle spaces from NYC Parks data.")
    ap.add_argument("place", nargs="?", default="Harlem, New York, NY",
                    help='study-area center (default "Harlem, New York, NY")')
    ap.add_argument("--dist", type=int, default=1500,
                    help="half-extent in meters (default 1500)")
    ap.add_argument("--types", default="Triangle/Plaza",
                    help='comma-separated Parks typecategories '
                         '(default "Triangle/Plaza"; e.g. add "Mall,Strip")')
    ap.add_argument("--network", default="drive",
                    choices=["drive", "all", "walk", "bike"],
                    help="OSM context network type (default drive)")
    ap.add_argument("--long-side", type=int, default=PAGE_LONG_SIDE)
    ap.add_argument("-o", "--out", default="parks_triangles.svg",
                    help="output SVG (bare names go in output/)")
    args = ap.parse_args()

    types = [t.strip() for t in args.types.split(",") if t.strip()]

    edges, crs, bounds_lonlat = fetch_context(args.place, args.dist, args.network)
    blocks = block_faces(edges)
    tri_gdf = fetch_triangles(bounds_lonlat, types, crs)

    proj = Projector(edges.total_bounds, long_side=args.long_side)
    svg = build_svg(edges, blocks, tri_gdf, proj)

    out_path = args.out
    if not os.path.dirname(out_path):
        out_path = os.path.join("output", out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"Wrote {out_path}  ({proj.page_w:.0f} x {proj.page_h:.0f} px, "
          f"{len(tri_gdf)} triangle spaces)")


if __name__ == "__main__":
    main()
