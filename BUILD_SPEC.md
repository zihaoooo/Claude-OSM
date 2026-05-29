# OSM → Editable SVG: Cartographic Pipeline

## Goal
A pure-Python pipeline that pulls urban geometry from OpenStreetMap and emits a
**layered, hand-editable SVG** for contemporary landscape-architecture mapping.
Output opens in Illustrator with each map layer as its own selectable `<g>`.
Nothing is ever rasterized. The aesthetic target: figure-ground building masses,
halftone-dot land-use zones, diagonal-hatch corridors, clean road hierarchy.

## Environment
- Python 3.10+, osmnx **2.x** (API differs from 1.x), geopandas, shapely.
  (matplotlib optional, only for quick geometry sanity previews.)
- Windows: run the venv interpreter directly — `.venv\Scripts\python.exe ...`.
  (No `mkdir`/activate needed for tooling; create with `py -m venv .venv` and
  `pip install osmnx geopandas shapely`.)
- Projection is **UTM** (EPSG:32618, zone 18N for NYC) for true meters — change
  `CRS_METRIC` for other regions.

## Module layout
- [`osm_to_svg.py`](osm_to_svg.py) — the pipeline (fetch → project/rotate/clip →
  emit). CLI entry point.
- [`style.py`](style.py) — all design tokens: `STYLE` (CSS classes), `TAGS` (OSM
  filters), `MAJOR_ROADS`, `HALFTONE`, `HATCH`. Swap palettes by copying this
  file and changing the import in `osm_to_svg.py`.
- [`.claude/skills/osm-map/`](.claude/skills/osm-map/SKILL.md) — skill that
  drives the interactive build workflow (framing, furniture, preview, commit).
- `index.html` + `.claude/launch.json` — in-chat SVG preview scaffolding.

## Pipeline (how it works)
1. **Fetch** — geometry from OSM. Roads come from the street-network graph
   (clean topology); water/green/buildings from `TAGS` feature filters.
2. **Project / rotate / clip** — reproject to UTM meters; in rotated-frame mode,
   rotate about the frame center so a chosen street is vertical, then clip to the
   frame box. `FrameProjector` maps metric → page coords (y-flipped) and reports
   the px/m scale.
3. **Emit** — a `<style>` block from `STYLE`, then one semantic `<g>` per layer
   in draw order, plus texture groups (`dots_*`, `hatch_*`) and furniture
   (`scalebar`, `north`). Coords rounded to 2 decimals; stroke widths in px.

## Two ways to define the map area
- **Rotated-frame mode (preferred for corridors):** `--corner1 "lat,lon"
  --corner2 "lat,lon" --rotate auto --align "<Exact OSM Street Name>"`. The two
  corners define a box that is upright *in the rotated view*; `--align` derives
  the rotation that makes the named street vertical, measured within the frame
  (iterated). Override rotation with `--rotate <deg>` (CCW). Scale via
  `--long-side <px>` (default 1400) or `--scale <px_per_meter>`.
- **Place + dist mode (fallback, north-up):** `osm_to_svg.py "Place, Region"
  --dist <meters>` — square window of ±dist around the geocoded center.

## Map furniture
- **Scale bar** — bottom-left, round-number length derived from px/m scale.
- **North arrow** — beside the scale bar, tilted to true north after rotation.
- Both auto-emit in rotated-frame mode, each as its own `<g>`.

## Usage example
```
.venv\Scripts\python.exe osm_to_svg.py \
  --corner1 "40.810511,-73.956319" --corner2 "40.796861,-73.948272" \
  --rotate auto --align "Saint Nicholas Avenue" -o stnicholas.svg
```
Then preview in-chat via the Claude_Preview browser route (serve the folder,
screenshot `index.html`). `cairosvg` does NOT work on Windows — use the browser.

## Build status
Work in tight loops: make one change, run the script, preview the SVG, commit.

- **Step 1 — Verify baseline** ✅ done. Pipeline confirmed on Harlem.
- **Rotated-frame mode + furniture** ✅ done (added beyond the original plan):
  two-corner box, auto street-alignment, UTM projection, scale bar, north arrow.
- **Step 2 — Halftone dot-fill** ✅ done. `halftone_group()` samples a clipped
  page-space grid of `<circle>`s per polygon; density in `HALFTONE` (px). Applied
  to green/land-use. Own `<g id="dots_<layer>">`.
- **Step 3 — Diagonal hatch** ✅ done. `hatch_group()` clips angled parallel
  lines to polygons via shapely intersection (no `<pattern>` defs — flat,
  editable `<line>`s). Config in `HATCH` (angle/spacing). Currently on water;
  **deferred**: point it at the avenue corridor or parks for visible impact in
  tight frames (water is often just a sliver).
- **Step 4 — Externalize style** ✅ done. Tokens live in `style.py`.
- **Step 5 — Node markers** ⬜ optional, not started. Support an optional points
  layer for symbols (e.g. `+` development markers). Place by hand is fine; lowest
  priority. Add as its own `<g>`.

## Constraints / house style
- Pure SVG output. No raster, no base64 images, no external fonts baked in.
- Every visual layer = one `<g id=... class=...>`. Semantic ids.
- Stroke widths in px (page space) so they stay crisp regardless of map scale.
- Keep the tokens-as-config pattern: all color/weight/density decisions live in
  `style.py`, never hardcoded in the emit functions.
- Coordinates rounded to 2 decimals in path strings to keep files lean.

## Gotchas
- **Exact OSM street names** for `--align` (e.g. "Saint Nicholas Avenue", and
  beware near-matches like "Saint Nicholas Terrace").
- **DMS → decimal**: West longitude and South latitude are negative.
- **osmnx 2.x `bbox_from_point`** return-tuple order shifted across releases;
  `_bbox_to_polygon()` assumes `(left, bottom, right, top)`. Not biting in
  osmnx 2.1.0, but if geometry comes out empty/mirrored, print the raw bbox and
  correct it.

## Git
Commit after every step that produces a valid SVG. `.venv/`, `*.svg`, `cache/`,
`__pycache__/`, preview PNGs are gitignored; commit the source + `style.py`.
