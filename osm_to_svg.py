#!/usr/bin/env python3
"""
OSM -> layered, editable SVG for landscape/urban cartography.

Pipeline:
  1. Fetch raw geometry from OpenStreetMap (osmnx 2.x)
  2. Reproject to a metric CRS so coordinates are in meters
  3. Project meters -> SVG page coordinates (y-flipped)
  4. Emit a hand-editable SVG with one <g> per layer, semantic classes,
     and a <style> block you can tweak in code or in Illustrator.

Every layer is a labeled group. Open the result in Illustrator and each
group lands on its own selectable set; edit fills/strokes here in CSS or there
by hand. Nothing is rasterized.
"""

import argparse
import osmnx as ox
import geopandas as gpd
from shapely.geometry import (
    Polygon, MultiPolygon, LineString, MultiLineString, Point, MultiPoint
)

# ----------------------------------------------------------------------------
# 1. CONFIG -- the "design tokens" for the map. Edit freely.
# ----------------------------------------------------------------------------

PAGE_W = 1200          # SVG width in px
MARGIN = 40            # inner margin in px

# Per-layer style. These become CSS classes in the output <style> block.
# Stroke widths are in px (page space), so they stay crisp at any map scale.
STYLE = {
    "background": {"fill": "#ffffff"},
    "water":      {"fill": "#e8eef2", "stroke": "none"},
    "green":      {"fill": "#000000", "fill_opacity": 0.04, "stroke": "none"},
    "buildings":  {"fill": "#111111", "stroke": "none"},
    "roads_major":{"fill": "none", "stroke": "#111111", "stroke_width": 1.4},
    "roads_minor":{"fill": "none", "stroke": "#999999", "stroke_width": 0.5},
    "rail":       {"fill": "none", "stroke": "#111111", "stroke_width": 0.8,
                   "stroke_dasharray": "4 3"},
}

# OSM tag filters per layer. Add/remove tags to taste.
TAGS = {
    "water":     {"natural": ["water"], "waterway": True},
    "green":     {"leisure": ["park", "garden"], "landuse": ["forest", "grass",
                  "meadow", "recreation_ground"], "natural": ["wood"]},
    "buildings": {"building": True},
}

# Road classes split into major/minor by OSM highway value.
MAJOR_ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary",
               "motorway_link", "trunk_link", "primary_link"}


# ----------------------------------------------------------------------------
# 2. FETCH
# ----------------------------------------------------------------------------

def fetch_features(polygon, tags):
    """Return a GeoDataFrame for the given tag dict, or None if empty."""
    try:
        gdf = ox.features_from_polygon(polygon, tags)
        return gdf if len(gdf) else None
    except Exception:
        return None


def fetch_layers(place, dist):
    """Geocode the place, build a study-area polygon, fetch all layers."""
    print(f"Geocoding '{place}' ...")
    center = ox.geocode(place)                      # (lat, lon)
    # Build a square bbox of +/- dist meters around the center.
    bbox = ox.utils_geo.bbox_from_point(center, dist=dist)  # (N,S,E,W) or poly
    # osmnx 2.x: bbox_from_point returns (left, bottom, right, top) in 2.x>=2.0
    # Normalize to a polygon either way.
    poly = _bbox_to_polygon(bbox)

    layers = {}

    # Roads come from the street network graph (cleaner topology than features).
    print("Fetching street network ...")
    G = ox.graph_from_point(center, dist=dist, network_type="all",
                            truncate_by_edge=True)
    edges = ox.graph_to_gdfs(G, nodes=False)
    layers["roads"] = edges

    for name, tag in TAGS.items():
        print(f"Fetching {name} ...")
        layers[name] = fetch_features(poly, tag)

    return layers, poly


def _bbox_to_polygon(bbox):
    """Handle both old (N,S,E,W) and new (left,bottom,right,top) signatures."""
    vals = list(bbox)
    if len(vals) != 4:
        raise ValueError("Unexpected bbox shape")
    # osmnx 2.x returns (left, bottom, right, top) = (W, S, E, N)
    left, bottom, right, top = vals
    # Heuristic: if 'left' looks like a latitude (>|90| impossible) skip.
    return Polygon([(left, bottom), (right, bottom),
                    (right, top), (left, top)])


# ----------------------------------------------------------------------------
# 3. PROJECT  (lon/lat -> meters -> SVG page coords)
# ----------------------------------------------------------------------------

class Projector:
    """Reproject to metric CRS, then map metric bounds onto the SVG page."""

    def __init__(self, layers, crs_metric="EPSG:3857"):
        self.crs_metric = crs_metric
        # Reproject every non-empty layer.
        for k, v in layers.items():
            if v is not None and len(v):
                layers[k] = v.to_crs(crs_metric)
        self.layers = layers
        self.minx, self.miny, self.maxx, self.maxy = self._union_bounds()
        self.scale = (PAGE_W - 2 * MARGIN) / (self.maxx - self.minx)
        self.page_h = (self.maxy - self.miny) * self.scale + 2 * MARGIN

    def _union_bounds(self):
        bb = None
        for v in self.layers.values():
            if v is None or not len(v):
                continue
            b = v.total_bounds  # (minx, miny, maxx, maxy)
            if bb is None:
                bb = list(b)
            else:
                bb[0], bb[1] = min(bb[0], b[0]), min(bb[1], b[1])
                bb[2], bb[3] = max(bb[2], b[2]), max(bb[3], b[3])
        if bb is None:
            raise RuntimeError("No geometry fetched -- check place/tags.")
        return bb

    def xy(self, x, y):
        """Metric (x,y) -> SVG (px,py) with y flipped."""
        px = MARGIN + (x - self.minx) * self.scale
        py = MARGIN + (self.maxy - y) * self.scale
        return px, py


# ----------------------------------------------------------------------------
# 4. GEOMETRY -> SVG PATH STRINGS
# ----------------------------------------------------------------------------

def ring_to_path(coords, proj):
    pts = [proj.xy(x, y) for x, y in coords]
    d = "M" + " L".join(f"{px:.2f},{py:.2f}" for px, py in pts) + " Z"
    return d


def polygon_to_path(geom, proj):
    """Polygon (with holes) -> single path d using even-odd fill."""
    if geom.is_empty:
        return ""
    parts = [ring_to_path(geom.exterior.coords, proj)]
    for interior in geom.interiors:
        parts.append(ring_to_path(interior.coords, proj))
    return " ".join(parts)


def line_to_path(coords, proj):
    pts = [proj.xy(x, y) for x, y in coords]
    return "M" + " L".join(f"{px:.2f},{py:.2f}" for px, py in pts)


def geom_to_paths(geom, proj):
    """Dispatch any shapely geometry to a list of path d-strings."""
    paths = []
    gt = geom.geom_type
    if gt == "Polygon":
        d = polygon_to_path(geom, proj)
        if d:
            paths.append(("fill", d))
    elif gt == "MultiPolygon":
        for g in geom.geoms:
            d = polygon_to_path(g, proj)
            if d:
                paths.append(("fill", d))
    elif gt == "LineString":
        paths.append(("stroke", line_to_path(geom.coords, proj)))
    elif gt == "MultiLineString":
        for g in geom.geoms:
            paths.append(("stroke", line_to_path(g.coords, proj)))
    # Points ignored for now (markers are better placed by hand).
    return paths


# ----------------------------------------------------------------------------
# 5. EMIT SVG
# ----------------------------------------------------------------------------

def css_block():
    lines = ["  <style>"]
    for cls, props in STYLE.items():
        decls = []
        for k, v in props.items():
            decls.append(f"{k.replace('_', '-')}:{v}")
        lines.append(f"    .{cls} {{ {'; '.join(decls)} }}")
    lines.append("  </style>")
    return "\n".join(lines)


def layer_group(name, gdf, proj, css_class):
    """Render one GeoDataFrame as a <g class=...> of <path>s."""
    if gdf is None or not len(gdf):
        return f'  <g id="{name}" class="{css_class}"><!-- empty --></g>'
    out = [f'  <g id="{name}" class="{css_class}">']
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        for _, d in geom_to_paths(geom, proj):
            out.append(f'    <path d="{d}"/>')
    out.append("  </g>")
    return "\n".join(out)


def build_svg(proj):
    L = proj.layers
    page_h = proj.page_h

    svg = []
    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{PAGE_W}" height="{page_h:.0f}" '
        f'viewBox="0 0 {PAGE_W} {page_h:.0f}">'
    )
    svg.append(css_block())
    svg.append(f'  <rect class="background" x="0" y="0" '
               f'width="{PAGE_W}" height="{page_h:.0f}"/>')

    # Draw order: water -> green -> buildings -> minor roads -> major roads.
    svg.append(layer_group("water", L.get("water"), proj, "water"))
    svg.append(layer_group("green", L.get("green"), proj, "green"))
    svg.append(layer_group("buildings", L.get("buildings"), proj, "buildings"))

    # Split roads by class.
    roads = L.get("roads")
    if roads is not None and len(roads):
        major, minor = _split_roads(roads)
        svg.append(layer_group("roads_minor", minor, proj, "roads_minor"))
        svg.append(layer_group("roads_major", major, proj, "roads_major"))

    svg.append("</svg>")
    return "\n".join(svg)


def _split_roads(edges):
    def is_major(h):
        if isinstance(h, list):
            return any(x in MAJOR_ROADS for x in h)
        return h in MAJOR_ROADS
    mask = edges["highway"].apply(is_major)
    return edges[mask], edges[~mask]


# ----------------------------------------------------------------------------
# 6. CLI
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="OSM -> editable SVG map")
    ap.add_argument("place", help='e.g. "As, Norway" or "Brooklyn, NY"')
    ap.add_argument("--dist", type=int, default=1200,
                    help="half-extent in meters (default 1200)")
    ap.add_argument("-o", "--out", default="map.svg")
    args = ap.parse_args()

    layers, _ = fetch_layers(args.place, args.dist)
    proj = Projector(layers)
    svg = build_svg(proj)
    with open(args.out, "w") as f:
        f.write(svg)
    print(f"Wrote {args.out}  ({PAGE_W} x {proj.page_h:.0f} px)")


if __name__ == "__main__":
    main()
