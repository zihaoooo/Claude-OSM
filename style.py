"""Design tokens for osm_to_svg -- the swappable palette / cartographic style.

Import these into osm_to_svg.py. Keep a palette self-contained here so you can
maintain several (e.g. style_mono.py, style_blueprint.py) and switch by editing
the import. Pipeline geometry (page size, margins, CRS) lives in osm_to_svg.py;
only look-and-feel and OSM selection live here.
"""

# Per-layer style. Each key becomes a CSS class in the output <style> block.
# Stroke widths are in px (page space), so they stay crisp at any map scale.
STYLE = {
    "background": {"fill": "#ffffff"},
    "water":      {"fill": "#cfe0ea", "stroke": "none"},
    "green":      {"fill": "#eaf2e6", "stroke": "none"},
    "dots_green": {"fill": "#5b8a5b", "stroke": "none"},
    "hatch_water":{"fill": "none", "stroke": "#5a9bc4", "stroke_width": 0.6},
    "buildings":  {"fill": "#111111", "stroke": "none"},
    "roads_major":{"fill": "none", "stroke": "#111111", "stroke_width": 1.4},
    "roads_minor":{"fill": "none", "stroke": "#999999", "stroke_width": 0.5},
    "rail":       {"fill": "none", "stroke": "#111111", "stroke_width": 0.8,
                   "stroke_dasharray": "4 3"},
    "north":      {"fill": "#111111", "stroke": "#111111", "stroke_width": 1.5},
    "scalebar":   {"fill": "#111111", "stroke": "none",
                   "font_family": "sans-serif"},
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

# Halftone dot-fill per layer. spacing/radius are in px (page space), so the
# texture density stays constant regardless of map scale.
HALFTONE = {
    "green": {"spacing": 7, "radius": 1.0},
}

# Diagonal-hatch per layer. angle in degrees (from horizontal), spacing in px.
HATCH = {
    "water": {"angle": 45, "spacing": 5},
}
