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
import math
import os
import numpy as np
import osmnx as ox
import geopandas as gpd
from shapely.geometry import (
    Polygon, MultiPolygon, LineString, MultiLineString, Point, MultiPoint, box
)
from shapely.affinity import rotate as shp_rotate

# ----------------------------------------------------------------------------
# 1. CONFIG
#   Design tokens (palette, OSM tags, textures) live in style.py so palettes
#   can be swapped. Pipeline geometry stays here.
# ----------------------------------------------------------------------------

from style import STYLE, TAGS, MAJOR_ROADS, HALFTONE, HATCH

PAGE_W = 1200          # SVG width in px (fallback place+dist mode)
LONG_SIDE_PX = 1400    # frame mode: fit the longer page dimension to this
MARGIN = 40            # inner margin in px
CRS_METRIC = "EPSG:32618"   # UTM zone 18N -- true meters for NYC


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
# 2b. ROTATED-FRAME MODE
#   Define the map by two lat/lon corners of a box that is *upright in the
#   rotated view*. We derive a rotation that makes a chosen street vertical,
#   rotate the world by it, take the axis-aligned bbox of the two corners in
#   that rotated frame, then fetch / clip to it.
# ----------------------------------------------------------------------------

def parse_latlon(s):
    """'40.81, -73.95' -> (lat, lon) floats."""
    lat, lon = (float(t) for t in s.replace(" ", "").split(","))
    return lat, lon


def _exact_name(value, target):
    """OSM 'name' may be a str or a list; True if any equals target."""
    t = target.strip().lower()
    vals = value if isinstance(value, list) else [value]
    return any(str(v).strip().lower() == t for v in vals)


def _principal_bearing(geoms):
    """SVD principal-axis bearing (deg, +CW from vertical) of metric line geoms."""
    xs, ys = [], []
    for g in geoms:
        sub = g.geoms if g.geom_type in ("MultiLineString", "GeometryCollection") else [g]
        for ln in sub:
            if ln.geom_type != "LineString":
                continue
            for x, y in ln.coords:
                xs.append(x); ys.append(y)
    P = np.column_stack([np.array(xs) - np.mean(xs), np.array(ys) - np.mean(ys)])
    _, _, Vt = np.linalg.svd(P, full_matrices=False)
    vx, vy = Vt[0]
    if vy < 0:
        vx, vy = -vx, -vy
    return math.degrees(math.atan2(vx, vy))


def derive_bearing(poly4326, name):
    """Bearing of a named street *within poly4326*, deg clockwise from vertical.

    Fetches the street graph clipped to the polygon, keeps edges whose name
    matches, projects to true UTM meters, and fits the dominant axis. Measuring
    only inside the frame avoids bias from where the avenue curves outside it.
    """
    G = ox.graph_from_polygon(poly4326, network_type="all", truncate_by_edge=True)
    edges = ox.graph_to_gdfs(G, nodes=False)
    sel = edges[edges["name"].apply(lambda v: _exact_name(v, name))]
    if not len(sel):
        raise RuntimeError(f"No street named '{name}' found to align to.")
    return _principal_bearing(sel.to_crs(CRS_METRIC).geometry)


def build_frame(corner1, corner2, rotate_arg, align_name):
    """Compute the rotated-frame geometry.

    Returns a dict with:
      angle   -- CCW degrees to rotate the world (so the street goes vertical)
      center  -- (cx, cy) metric rotation origin
      rbbox   -- (minx, miny, maxx, maxy) in the rotated metric frame
      poly4326-- shapely Polygon (lat/lon) covering the frame, for OSM queries
    """
    # Project both corners to metric.
    pts = gpd.GeoSeries(
        [Point(lon, lat) for lat, lon in (corner1, corner2)], crs="EPSG:4326"
    ).to_crs(CRS_METRIC)
    (x1, y1), (x2, y2) = ((p.x, p.y) for p in pts)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

    def frame_for(angle):
        """rbbox + tilted lat/lon fetch polygon for a given CCW rotation."""
        r1 = shp_rotate(Point(x1, y1), angle, origin=(cx, cy))
        r2 = shp_rotate(Point(x2, y2), angle, origin=(cx, cy))
        minx, maxx = sorted([r1.x, r2.x])
        miny, maxy = sorted([r1.y, r2.y])
        rbbox = (minx, miny, maxx, maxy)
        rcorners = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
        orig = [shp_rotate(Point(px, py), -angle, origin=(cx, cy))
                for px, py in rcorners]
        poly = gpd.GeoSeries(
            [Polygon([(p.x, p.y) for p in orig])], crs=CRS_METRIC
        ).to_crs("EPSG:4326").iloc[0]
        return rbbox, poly

    if str(rotate_arg).lower() == "auto":
        # Start from the corners' axis-aligned bbox, then refine against the
        # tilted frame so we align to the avenue *as displayed*.
        _, poly = frame_for(0.0)
        angle = derive_bearing(poly, align_name)
        for _ in range(2):
            _, poly = frame_for(angle)
            angle = derive_bearing(poly, align_name)
        print(f"  aligned '{align_name}' to vertical; rotating {angle:+.2f} deg")
    else:
        angle = float(rotate_arg)

    rbbox, poly4326 = frame_for(angle)
    return {"angle": angle, "center": (cx, cy), "rbbox": rbbox, "poly4326": poly4326}


def fetch_layers_frame(frame):
    """Fetch road graph + feature layers within the frame's lat/lon polygon."""
    poly = frame["poly4326"]
    layers = {}
    print("Fetching street network ...")
    G = ox.graph_from_polygon(poly, network_type="all", truncate_by_edge=True)
    layers["roads"] = ox.graph_to_gdfs(G, nodes=False)
    for name, tag in TAGS.items():
        print(f"Fetching {name} ...")
        layers[name] = fetch_features(poly, tag)
    return layers


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
        self.page_w = PAGE_W
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


def transform_layers(layers, frame):
    """Reproject -> rotate about frame center -> clip to the rotated bbox."""
    cx, cy = frame["center"]
    angle = frame["angle"]
    clip = box(*frame["rbbox"])
    out = {}
    for k, v in layers.items():
        if v is None or not len(v):
            out[k] = v
            continue
        g = v.to_crs(CRS_METRIC)
        g = g.set_geometry(g.rotate(angle, origin=(cx, cy)))
        try:
            g = gpd.clip(g, clip)
        except Exception:
            g = g[g.intersects(clip)]
        out[k] = g if len(g) else None
    return out


class FrameProjector:
    """Map an already-rotated metric frame (fixed bbox) onto the SVG page."""

    def __init__(self, layers, rbbox, long_side=LONG_SIDE_PX, scale=None):
        self.layers = layers
        self.minx, self.miny, self.maxx, self.maxy = rbbox
        w_m, h_m = self.maxx - self.minx, self.maxy - self.miny
        self.scale = scale if scale else (long_side - 2 * MARGIN) / max(w_m, h_m)
        self.page_w = w_m * self.scale + 2 * MARGIN
        self.page_h = h_m * self.scale + 2 * MARGIN

    def xy(self, x, y):
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
    elif gt == "GeometryCollection":   # clipping can yield mixed collections
        for g in geom.geoms:
            paths.extend(geom_to_paths(g, proj))
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


def _page_polygon(poly, proj):
    """Shapely Polygon (with holes) reprojected into SVG page coordinates."""
    shell = [proj.xy(x, y) for x, y in poly.exterior.coords]
    holes = [[proj.xy(x, y) for x, y in r.coords] for r in poly.interiors]
    return Polygon(shell, holes)


def halftone_group(name, gdf, proj, spacing, radius):
    """Fill each polygon with a clipped grid of <circle>s (a halftone texture).

    The grid is sampled in page space and anchored to a global origin so dots
    line up across separate polygons; points outside the polygon are dropped.
    """
    from shapely.prepared import prep
    if gdf is None or not len(gdf):
        return f'  <g id="dots_{name}" class="dots_{name}"><!-- empty --></g>'
    out = [f'  <g id="dots_{name}" class="dots_{name}">']
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        polys = (geom.geoms if geom.geom_type in ("MultiPolygon", "GeometryCollection")
                 else [geom])
        for poly in polys:
            if poly.geom_type != "Polygon":
                continue
            pp = _page_polygon(poly, proj)
            if pp.is_empty:
                continue
            pr = prep(pp)
            minx, miny, maxx, maxy = pp.bounds
            y = math.floor(miny / spacing) * spacing
            while y <= maxy:
                x = math.floor(minx / spacing) * spacing
                while x <= maxx:
                    if pr.contains(Point(x, y)):
                        out.append(f'    <circle cx="{x:.2f}" cy="{y:.2f}" '
                                   f'r="{radius}"/>')
                    x += spacing
                y += spacing
    out.append("  </g>")
    return "\n".join(out)


def _emit_clipped_segments(geom, out):
    """Append <line>s for each straight piece of a clipped hatch intersection."""
    gt = geom.geom_type
    if gt == "LineString":
        if geom.is_empty:
            return
        (x1, y1), (x2, y2) = geom.coords[0], geom.coords[-1]
        out.append(f'    <line x1="{x1:.2f}" y1="{y1:.2f}" '
                   f'x2="{x2:.2f}" y2="{y2:.2f}"/>')
    elif gt in ("MultiLineString", "GeometryCollection"):
        for g in geom.geoms:
            _emit_clipped_segments(g, out)


def hatch_group(name, gdf, proj, angle, spacing):
    """Fill each polygon with parallel lines at `angle`, clipped to the polygon.

    Lines are generated in page space and clipped via shapely intersection (no
    SVG <pattern> defs), so every stroke is a flat, editable <line>. Offsets are
    anchored to a global origin so hatching aligns across polygons.
    """
    if gdf is None or not len(gdf):
        return f'  <g id="hatch_{name}" class="hatch_{name}"><!-- empty --></g>'
    th = math.radians(angle)
    dx, dy = math.cos(th), math.sin(th)        # line direction
    nx, ny = -math.sin(th), math.cos(th)       # normal (offset axis)
    out = [f'  <g id="hatch_{name}" class="hatch_{name}">']
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        polys = (geom.geoms if geom.geom_type in ("MultiPolygon", "GeometryCollection")
                 else [geom])
        for poly in polys:
            if poly.geom_type != "Polygon":
                continue
            pp = _page_polygon(poly, proj)
            if pp.is_empty:
                continue
            minx, miny, maxx, maxy = pp.bounds
            cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
            corners = [(minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)]
            offs = [nx * x + ny * y for x, y in corners]
            D = math.hypot(maxx - minx, maxy - miny)
            c = math.floor(min(offs) / spacing) * spacing
            while c <= max(offs):
                t0 = c - (nx * cx + ny * cy)       # shift center onto this line
                x0, y0 = cx + t0 * nx, cy + t0 * ny
                seg = LineString([(x0 - D * dx, y0 - D * dy),
                                  (x0 + D * dx, y0 + D * dy)])
                _emit_clipped_segments(pp.intersection(seg), out)
                c += spacing
    out.append("  </g>")
    return "\n".join(out)


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


def north_arrow_group(proj, angle):
    """A small north indicator, tilted to true north, in the bottom-left corner.

    The world was rotated CCW by `angle`, so true north (metric +y) now points
    at the unit page vector (-sin a, -cos a) (page y points down).
    """
    a = math.radians(angle)
    ux, uy = -math.sin(a), -math.cos(a)            # page-space north unit
    px_, py_ = -uy, ux                             # perpendicular
    L = 38
    bx, by = MARGIN + 12, proj.page_h - MARGIN - 14   # base, bottom-left
    tx, ty = bx + L * ux, by + L * uy                 # tip
    # Arrowhead triangle.
    h, w = 11, 4.5
    a1 = (tx - h * ux + w * px_, ty - h * uy + w * py_)
    a2 = (tx - h * ux - w * px_, ty - h * uy - w * py_)
    lx, ly = tx + 11 * ux, ty + 11 * uy + 4        # label just past the tip
    out = ['  <g id="north" class="north">']
    out.append(f'    <line x1="{bx:.2f}" y1="{by:.2f}" '
               f'x2="{tx:.2f}" y2="{ty:.2f}"/>')
    out.append(f'    <polygon points="{tx:.2f},{ty:.2f} '
               f'{a1[0]:.2f},{a1[1]:.2f} {a2[0]:.2f},{a2[1]:.2f}"/>')
    out.append(f'    <text x="{lx:.2f}" y="{ly:.2f}" '
               f'text-anchor="middle" font-size="13" stroke="none">N</text>')
    out.append("  </g>")
    return "\n".join(out)


def _nice_length(scale, target_px=150):
    """Pick a round distance (m) whose pixel length is near target_px."""
    raw = target_px / scale                          # meters at target width
    mag = 10 ** math.floor(math.log10(raw))
    for f in (1, 2, 5, 10):
        if f * mag >= raw:
            return f * mag
    return 10 * mag


def scalebar_group(proj):
    """A two-segment scale bar in the bottom-left, sitting beside the arrow."""
    nice = _nice_length(proj.scale)
    L = nice * proj.scale                            # bar length in px
    half = L / 2
    x0 = MARGIN + 46                                 # right of the north arrow
    y1 = proj.page_h - MARGIN - 18                   # bar top
    h = 6
    fmt = (lambda m: f"{m/1000:g} km") if nice >= 1000 else (lambda m: f"{m:g}")
    out = ['  <g id="scalebar" class="scalebar">']
    out.append(f'    <rect x="{x0:.2f}" y="{y1:.2f}" width="{L:.2f}" '
               f'height="{h}" fill="none" stroke="#111" stroke-width="1"/>')
    out.append(f'    <rect x="{x0:.2f}" y="{y1:.2f}" width="{half:.2f}" '
               f'height="{h}" fill="#111" stroke="none"/>')
    ty = y1 + h + 12
    for frac, txt in ((0.0, "0"), (0.5, fmt(nice / 2)), (1.0, f"{fmt(nice)} m")):
        out.append(f'    <text x="{x0 + L*frac:.2f}" y="{ty}" '
                   f'text-anchor="middle" font-size="11" stroke="none">{txt}</text>')
    out.append("  </g>")
    return "\n".join(out)


def build_svg(proj, north_angle=None):
    L = proj.layers
    page_w, page_h = proj.page_w, proj.page_h

    svg = []
    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{page_w:.0f}" height="{page_h:.0f}" '
        f'viewBox="0 0 {page_w:.0f} {page_h:.0f}">'
    )
    svg.append(css_block())
    svg.append(f'  <rect class="background" x="0" y="0" '
               f'width="{page_w:.0f}" height="{page_h:.0f}"/>')

    # Draw order: water -> green -> buildings -> minor roads -> major roads.
    svg.append(layer_group("water", L.get("water"), proj, "water"))
    if "water" in HATCH and L.get("water") is not None:
        svg.append(hatch_group("water", L.get("water"), proj, **HATCH["water"]))
    svg.append(layer_group("green", L.get("green"), proj, "green"))
    if "green" in HALFTONE and L.get("green") is not None:
        svg.append(halftone_group("green", L.get("green"), proj, **HALFTONE["green"]))
    svg.append(layer_group("buildings", L.get("buildings"), proj, "buildings"))

    # Split roads by class.
    roads = L.get("roads")
    if roads is not None and len(roads):
        major, minor = _split_roads(roads)
        svg.append(layer_group("roads_minor", minor, proj, "roads_minor"))
        svg.append(layer_group("roads_major", major, proj, "roads_major"))

    if north_angle is not None:
        svg.append(north_arrow_group(proj, north_angle))
        svg.append(scalebar_group(proj))

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
    ap.add_argument("place", nargs="?",
                    help='place name for fallback mode, e.g. "Brooklyn, NY"')
    ap.add_argument("--dist", type=int, default=1200,
                    help="half-extent in meters (place mode, default 1200)")
    # Rotated-frame mode:
    ap.add_argument("--corner1", help='"lat,lon" of one box corner')
    ap.add_argument("--corner2", help='"lat,lon" of the opposite box corner')
    ap.add_argument("--rotate", default="auto",
                    help='"auto" (align to --align) or degrees CCW')
    ap.add_argument("--align", default=None,
                    help="street name to make vertical when --rotate auto")
    ap.add_argument("--long-side", type=int, default=LONG_SIDE_PX,
                    help="fit the longer page dimension to this many px")
    ap.add_argument("--scale", type=float, default=None,
                    help="px per meter (overrides --long-side)")
    ap.add_argument("-o", "--out", default="output/map.svg",
                    help="output SVG path; bare filenames go in output/")
    args = ap.parse_args()

    if args.corner1 and args.corner2:
        frame = build_frame(parse_latlon(args.corner1), parse_latlon(args.corner2),
                            args.rotate, args.align)
        layers = fetch_layers_frame(frame)
        layers = transform_layers(layers, frame)
        proj = FrameProjector(layers, frame["rbbox"],
                              long_side=args.long_side, scale=args.scale)
        svg = build_svg(proj, north_angle=frame["angle"])
        print(f"  scale: {proj.scale:.4f} px/m  "
              f"(1px = {1/proj.scale:.3f} m)")
    else:
        if not args.place:
            ap.error("give a place name, or --corner1/--corner2 for frame mode")
        layers, _ = fetch_layers(args.place, args.dist)
        proj = Projector(layers)
        svg = build_svg(proj)

    # bare filenames (no directory component) default into output/
    out_path = args.out
    if not os.path.dirname(out_path):
        out_path = os.path.join("output", out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write(svg)
    args.out = out_path
    print(f"Wrote {args.out}  ({proj.page_w:.0f} x {proj.page_h:.0f} px)")


if __name__ == "__main__":
    main()
