#!/usr/bin/env python3
"""
Print-to-scale base plan for hand drawing: a light-gray site plan on a fixed sheet
at an architectural scale (default 11x17 landscape at 1" = 20'), with scale bar,
north arrow, and a small title strip. Students print it at 100% and draw on trace
over it, so every site element is thin and pale.

OSM alone has no curb lines or street trees, which a 1"=20' plan needs, so this
combines three sources:
  - OSM (API 0.6 map call, cached .osm)  -> building footprints, bus stops
  - NYC Planimetric Database (Socrata)    -> curbs 5xvt-8cbk, medians ees7-4ufv
  - NYC Forestry Tree Points (Socrata)    -> hn5i-inap (living trees, DBH)

Geometry is projected to NY State Plane (EPSG:2263, US feet), rotated about the
sheet center, and drawn with matplotlib in true page inches, so the PDF prints
at exact scale.

Example (A. Philip Randolph Square, Harlem):
  .venv\\Scripts\\python.exe scripts\\base_plan.py --center 40.80385,-73.95246 \\
      --align-bearing 29 --name "A. Philip Randolph Square" -o randolph_base
"""

import argparse
import json
import math
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon as MplPolygon, FancyArrow
from shapely import affinity
from shapely.geometry import shape, Point, Polygon, LineString, box

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "cache")
OUT = os.path.join(HERE, "output")
UA = "Claude-OSM base_plan (github.com/zihaoooo/Claude-OSM)"

# --- look: everything pale so pen reads on top --------------------------------
GRAY = "#9a9a9a"      # curbs, building edges
LIGHT = "#c4c4c4"     # canopies, medians, labels
FILL = "#f3f3f3"      # building poke
SITE = "#7a7a7a"      # the site outline, one step darker
INK = "#333333"       # scale bar, north arrow, title
FONT = "DejaVu Sans"


def fetch(url, path):
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
            f.write(r.read())
    return path


def socrata(dataset, col, bbox, tag, extra=""):
    s, w, n, e = bbox
    where = f"within_box({col},{n},{w},{s},{e})" + (f" AND {extra}" if extra else "")
    q = urllib.parse.urlencode({"$where": where, "$limit": 50000})
    path = fetch(f"https://data.cityofnewyork.us/resource/{dataset}.geojson?{q}",
                 os.path.join(CACHE, f"{tag}_{dataset}.geojson"))
    with open(path, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    rows = [{**(ft["properties"] or {}), "geometry": shape(ft["geometry"])} for ft in feats if ft["geometry"]]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326) if rows else gpd.GeoDataFrame(geometry=[], crs=4326)


def osm_layers(bbox, tag):
    s, w, n, e = bbox
    path = fetch(f"https://api.openstreetmap.org/api/0.6/map?bbox={w},{s},{e},{n}",
                 os.path.join(CACHE, f"{tag}.osm"))
    root = ET.parse(path).getroot()
    nodes, bldg, bus, streets = {}, [], [], {}
    for el in root.iter("node"):
        nodes[el.get("id")] = (float(el.get("lon")), float(el.get("lat")))
        t = {x.get("k"): x.get("v") for x in el.findall("tag")}
        if t.get("highway") == "bus_stop":
            bus.append(Point(nodes[el.get("id")]))
    for el in root.iter("way"):
        t = {x.get("k"): x.get("v") for x in el.findall("tag")}
        refs = [nd.get("ref") for nd in el.findall("nd")]
        if "building" in t and len(refs) > 3 and refs[0] == refs[-1] and all(r in nodes for r in refs):
            bldg.append(Polygon([nodes[r] for r in refs]))
        elif t.get("highway") in ROAD_CLASSES and t.get("name") and all(r in nodes for r in refs):
            streets.setdefault(t["name"], []).append(LineString([nodes[r] for r in refs]))
    g = lambda geoms: gpd.GeoDataFrame(geometry=geoms, crs=4326)
    names = [n for n in streets for _ in streets[n]]
    lines = [l for n in streets for l in streets[n]]
    return g(bldg), g(bus), gpd.GeoDataFrame({"name": names}, geometry=lines, crs=4326)


ROAD_CLASSES = {"primary", "secondary", "tertiary", "residential", "unclassified"}
ABBR = [("Saint ", "St. "), ("Avenue", "Ave"), ("Boulevard", "Blvd"), ("Street", "St"), ("West ", "W ")]


def short(name):
    for a, b in ABBR:
        name = name.replace(a, b)
    return name.upper()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--center", required=True, help='"lat,lon" of the sheet center')
    ap.add_argument("--align-bearing", type=float, default=0,
                    help="compass bearing (deg) of the street to run left-to-right; 0 keeps north up")
    ap.add_argument("--scale-ft", type=float, default=20, help="feet per inch (20 -> 1\"=20')")
    ap.add_argument("--sheet", default="17x11", help="WxH inches, e.g. 17x11")
    ap.add_argument("--name", default="Site")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--credit", default="")
    ap.add_argument("--label", action="append", default=[], metavar='"OSM Name=TEXT"',
                    help="rename a street label; once any is given, only listed streets are labeled")
    ap.add_argument("-o", "--out", default="base_plan")
    a = ap.parse_args()

    lat, lon = map(float, a.center.split(","))
    W, H = map(float, a.sheet.lower().split("x"))
    k = a.scale_ft                      # ft per page inch
    m = 0.4                             # page margin, in
    strip = 0.55                        # title strip height, in
    map_w, map_h = W - 2 * m, H - 2 * m - strip
    tag = f"base_{lat:.5f}_{lon:.5f}"
    os.makedirs(CACHE, exist_ok=True); os.makedirs(OUT, exist_ok=True)

    # fetch a lat/lon box comfortably larger than the rotated sheet
    half_ft = math.hypot(map_w, map_h) * k / 2 + 60
    dlat = half_ft / 364000; dlon = half_ft / (364000 * math.cos(math.radians(lat)))
    bbox = (lat - dlat, lon - dlon, lat + dlat, lon + dlon)

    curbs = socrata("5xvt-8cbk", "the_geom", bbox, tag)
    medians = socrata("ees7-4ufv", "the_geom", bbox, tag)
    trees = socrata("hn5i-inap", "location", bbox, tag, "tpstructure='Full'")
    bldg, bus, streets = osm_layers(bbox, tag)

    c = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(2263).iloc[0]
    rot = a.align_bearing - 90          # CCW degrees that turn that bearing to page-right
    frame_ft = box(-map_w * k / 2, -map_h * k / 2, map_w * k / 2, map_h * k / 2)

    def prep(gdf):
        """Project, rotate and clip; keeps row order, None where a feature falls off the sheet."""
        if gdf.empty:
            return []
        out = []
        for g in gdf.to_crs(2263).geometry:
            g = affinity.translate(g, -c.x, -c.y)
            g = affinity.rotate(g, rot, origin=(0, 0))
            g = g.intersection(frame_ft)
            out.append(None if g.is_empty else g)
        return out

    def on_sheet(gdf):
        return [g for g in prep(gdf) if g is not None]

    fig = plt.figure(figsize=(W, H))
    ax = fig.add_axes([m / W, (m + strip) / H, map_w / W, map_h / H])
    ax.set_xlim(-map_w * k / 2, map_w * k / 2); ax.set_ylim(-map_h * k / 2, map_h * k / 2)
    ax.set_aspect("equal"); ax.axis("off")
    pt = 72 / k  # points per foot, for sizing strokes in ground units

    def draw_line(g, **kw):
        for part in getattr(g, "geoms", [g]):
            if isinstance(part, Polygon):
                xs, ys = part.exterior.xy; ax.plot(xs, ys, **kw)
            elif isinstance(part, LineString):
                xs, ys = part.xy; ax.plot(xs, ys, **kw)

    for g in on_sheet(bldg):
        for part in getattr(g, "geoms", [g]):
            if isinstance(part, Polygon):
                ax.add_patch(MplPolygon(list(part.exterior.coords), closed=True, fc=FILL, ec=GRAY, lw=0.5))
    for g in on_sheet(medians):
        draw_line(g, color=LIGHT, lw=0.5)
    for g in on_sheet(curbs):
        draw_line(g, color=GRAY, lw=0.7, solid_capstyle="round")

    # trees: trunk dot + canopy circle sized from DBH (crown diameter ~ 1.5 ft per inch DBH, 8-40 ft)
    for (_, r), g in zip(trees.iterrows(), prep(trees)):
        if g is None:
            continue
        try:
            dbh = float(r.get("dbh") or 0)
        except ValueError:
            dbh = 0
        rad = max(8, min(40, 1.5 * dbh)) / 2
        ax.add_patch(Circle((g.x, g.y), rad, fill=False, ec=LIGHT, lw=0.5, ls=(0, (2, 2))))
        ax.add_patch(Circle((g.x, g.y), 0.6, fc=GRAY, ec="none"))
    # street names: one label per name, on its longest visible centerline piece
    best = {}
    for (_, r), g in zip(streets.iterrows(), prep(streets)):
        if g is None:
            continue
        for part in getattr(g, "geoms", [g]):
            if isinstance(part, LineString) and part.length > best.get(r["name"], (0,))[0]:
                best[r["name"]] = (part.length, part)
    relabel = dict(x.split("=", 1) for x in a.label)
    for name, (length, line) in best.items():
        if length < 40 or (relabel and name not in relabel):
            continue
        p = line.interpolate(0.5, normalized=True)
        q = line.interpolate(min(1, 0.5 + 10 / length), normalized=True)
        ang = math.degrees(math.atan2(q.y - p.y, q.x - p.x))
        ang = ang - 180 if ang > 90 else ang + 180 if ang < -90 else ang
        ax.text(p.x, p.y, relabel.get(name, short(name)), fontsize=6.5, color=LIGHT, ha="center", va="center",
                rotation=ang, rotation_mode="anchor", family=FONT, weight="bold")
    for g in on_sheet(bus):
        ax.text(g.x, g.y, "BUS", fontsize=5, color=GRAY, ha="center", va="center", family=FONT,
                bbox=dict(boxstyle="square,pad=0.15", fc="white", ec=LIGHT, lw=0.4))

    # page-level furniture (in page inches)
    pg = fig.add_axes([0, 0, 1, 1]); pg.set_xlim(0, W); pg.set_ylim(0, H); pg.axis("off"); pg.patch.set_alpha(0)
    pg.add_patch(plt.Rectangle((m, m + strip), map_w, map_h, fill=False, ec=LIGHT, lw=0.5))
    pg.plot([m, W - m], [m + strip - 0.08] * 2, color=INK, lw=0.6)

    # scale bar 0-10-20-40 ft (or scaled to k)
    unit = k / 2
    x0, y0 = m + 0.15, m + 0.22
    for i, (a0, a1) in enumerate([(0, 0.5), (0.5, 1), (1, 2)]):
        pg.add_patch(plt.Rectangle((x0 + a0, y0), a1 - a0, 0.07, fc=INK if i % 2 == 0 else "white", ec=INK, lw=0.5))
    for v in (0, 0.5, 1, 2):
        pg.text(x0 + v, y0 + 0.11, f"{v * k:g}", fontsize=6, ha="center", va="bottom", color=INK, family=FONT)
    pg.text(x0 + 2.12, y0 + 0.035, "FT", fontsize=6, va="center", color=INK, family=FONT)
    pg.text(x0, y0 - 0.12, f'SCALE 1" = {k:g}\'-0"   PRINT AT 100% ON 11x17', fontsize=6, color=INK, family=FONT, va="center")

    # north arrow: north's page angle = 90 deg + rot (CCW from page-right)
    nx, ny = x0 + 3.0, y0 + 0.08
    ang = math.radians(90 + rot)
    L = 0.32
    pg.add_artist(FancyArrow(nx - math.cos(ang) * L / 2, ny - math.sin(ang) * L / 2,
                             math.cos(ang) * L, math.sin(ang) * L, width=0.012, head_width=0.09,
                             head_length=0.11, length_includes_head=True, color=INK))
    pg.text(nx + math.cos(ang) * (L / 2 + 0.1), ny + math.sin(ang) * (L / 2 + 0.1), "N",
            fontsize=7, ha="center", va="center", color=INK, family=FONT, weight="bold")

    pg.text(W - m, y0 + 0.13, a.name.upper(), fontsize=10, ha="right", color=INK, family=FONT, weight="bold")
    pg.text(W - m, y0 - 0.02, a.subtitle, fontsize=6.5, ha="right", color=INK, family=FONT)
    pg.text(W - m, y0 - 0.15, a.credit, fontsize=5, ha="right", color=GRAY, family=FONT)

    base = a.out if os.path.isabs(a.out) else os.path.join(OUT, a.out)
    fig.savefig(base + ".pdf"); fig.savefig(base + ".svg"); fig.savefig(base + ".png", dpi=110)
    print("wrote", base + ".{pdf,svg,png}", f"| curbs {len(curbs)} medians {len(medians)} trees {len(trees)} buildings {len(bldg)} bus {len(bus)}")
    return ax


if __name__ == "__main__":
    main()
