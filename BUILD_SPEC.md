# OSM → Editable SVG: Cartographic Pipeline

## Goal
A pure-Python pipeline that pulls urban geometry from OpenStreetMap and emits a
**layered, hand-editable SVG** for contemporary landscape-architecture mapping.
Output opens in Illustrator with each map layer as its own selectable `<g>`.
Nothing is ever rasterized. The aesthetic target: figure-ground building masses,
halftone-dot land-use zones, diagonal-hatch corridors, clean road hierarchy.

## Environment
- Python 3.10+, osmnx **2.x** (API differs from 1.x — assume 2.x), geopandas, shapely.
- Activate the venv before running: `source .venv/bin/activate`
- Run example: `python3 osm_to_svg.py "Ås, Norway" --dist 1200 -o aas.svg`

## What already exists (`osm_to_svg.py`)
A working baseline. Read it first, then summarize it back before editing.
- `STYLE` dict at top = design tokens; each key becomes a CSS class in the SVG.
- `TAGS` dict = OSM tag filters per layer (water, green, buildings).
- `fetch_layers()` — geocodes place, builds bbox polygon, fetches features +
  street network graph. Roads come from the graph (clean topology), not features.
- `Projector` — reprojects to EPSG:3857 meters, then maps to SVG page coords,
  y-flipped. Computes page height from aspect ratio.
- `geom_to_paths()` — shapely geometry → SVG path d-strings. Polygons use
  even-odd fill for holes.
- `build_svg()` — assembles layers in draw order, writes `<style>` block + one
  `<g class=...>` per layer.

## Known snag (fix first if it errors)
osmnx 2.x `bbox_from_point` return-tuple order shifted across point releases.
`_bbox_to_polygon()` assumes `(left, bottom, right, top)`. If geometry comes out
empty or mirrored, print the raw bbox, confirm the order, and correct it.

## Build order
Work in tight loops: make one change, run the script, open the SVG, commit.

### Step 1 — Verify baseline
Run the example command. Confirm buildings + roads render. Commit as the
known-good starting point before any edits.

### Step 2 — Halftone dot-fill generator
New function: given a polygon layer + a density parameter, sample a point grid
clipped to each polygon and emit `<circle>` elements. Use it to fill land-use
zones (the dotted areas in the reference). Density should be a config value.
Add a `<g id="dots_<layer>">` group so it stays editable.

### Step 3 — Diagonal hatch generator
New function: clip angled parallel lines to corridor/zone polygons. Configurable
`angle` and `spacing`. Emit as its own `<g>`. This produces the repose-angle
hatching look. Prefer generated `<line>`s clipped via shapely intersection over
SVG `<pattern>` defs, so the output stays flat and editable in Illustrator.

### Step 4 — Externalize style
Move `STYLE` (and ideally `TAGS`) into `style.py` so multiple palettes can be
swapped. Keep `osm_to_svg.py` importing from it.

### Step 5 — Node markers (optional)
Support an optional points layer for symbols (e.g. the `+` development markers).
Place by hand is fine; this is lowest priority.

## Constraints / house style
- Pure SVG output. No raster, no base64 images, no external fonts baked in.
- Every visual layer = one `<g id=... class=...>`. Semantic ids.
- Stroke widths in px (page space) so they stay crisp regardless of map scale.
- Keep the `STYLE`-as-tokens pattern: all color/weight decisions live in config,
  never hardcoded in the emit functions.
- Coordinates rounded to 2 decimals in path strings to keep files lean.

## Git
`git init` now. Commit after every step that produces a valid SVG.
