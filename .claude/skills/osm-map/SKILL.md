---
name: osm-map
description: >-
  Build a layered, hand-editable SVG map from OpenStreetMap using this repo's
  osm_to_svg.py + style.py pipeline. Use this whenever the user wants to fetch,
  build, or render a map of a street, avenue, corridor, neighborhood, or area —
  e.g. "make a map of X", "fetch the map for these corners", "render St. Nicholas
  Ave", "build an OSM map", "I want a figure-ground plate of this block". Trigger
  even if the user only gives coordinates or a place name without saying "map
  pipeline". Handles the rotated-frame model (two corners + an axis street made
  vertical), scale bar, north arrow, halftone/hatch textures, in-chat SVG
  preview, and per-step commits.
---

# OSM → editable SVG map

This repo turns OpenStreetMap geometry into a **layered, hand-editable SVG** for
landscape-architecture / figure-ground cartography. Every visual layer is its
own semantic `<g>`; nothing is rasterized. The entry point is
[`osm_to_svg.py`](../../../osm_to_svg.py); the palette and OSM tag filters live in
[`style.py`](../../../style.py). See [`BUILD_SPEC.md`](../../../BUILD_SPEC.md) for
the design rationale and feature reference.

Your job with this skill is to drive that pipeline *with* the user: pin down the
map frame, run it, add map furniture, preview the result in-chat, and commit.

## 0. Environment

Use the project venv. On Windows run the interpreter directly:
`.venv\Scripts\python.exe osm_to_svg.py ...`. If `.venv` is missing, create it
(`py -m venv .venv`) and install `osmnx geopandas shapely` (matplotlib only if
you need a quick non-SVG preview). The pipeline targets osmnx 2.x.

## 1. Define the map frame — the key decision

Before running anything, settle **how the map area is defined**. There are two
modes; prefer the first for any street/avenue/corridor.

### Rotated-frame mode (preferred for corridors)

The user gives **two opposite corners** of a box plus a **street to align
vertical**. The pipeline rotates the world so that street runs straight up the
page, then takes the axis-aligned bounding box of the two corners *in that
rotated frame* and clips to it. This is what makes a diagonal avenue read
cleanly as a vertical strip.

Ask for, or extract from the user:
- **Two corners** as lat/lon. Users often give DMS (e.g. `40°48'37.84"N
  73°57'22.75"W`). Convert to signed decimal before passing — the script's
  `--corner1/--corner2` expect `"lat,lon"` decimal (W and S are negative).
- **The axis street name** exactly as OSM names it (e.g. `"Saint Nicholas
  Avenue"`, not "St. Nicholas Ave"). If unsure of the exact OSM name, fetch the
  area once and check, or ask the user.

Run:
```
.venv\Scripts\python.exe osm_to_svg.py \
  --corner1 "<lat>,<lon>" --corner2 "<lat>,<lon>" \
  --rotate auto --align "<Exact OSM Street Name>" -o <out>.svg
```
`--rotate auto` derives the angle from the street's geometry *measured within
the frame* (iterated for accuracy), so it aligns to the segment you're actually
showing — not where the street curves elsewhere. To override, pass
`--rotate <degrees>` (CCW) instead of `auto`. Scale follows from
`--long-side <px>` (default 1400) or pin it with `--scale <px_per_meter>`.

### Place + dist mode (fallback, north-up)

For a quick neighborhood plate with north up, no rotation:
```
.venv\Scripts\python.exe osm_to_svg.py "Harlem, New York, NY" --dist 900 -o <out>.svg
```
`--dist` is the half-extent in meters around the geocoded center.

### If the user hasn't said how to frame it

Don't guess silently. Briefly offer the choice: rotated-frame (give two corners +
an axis street) vs. place+dist (name a place + radius). For a named street/
corridor, recommend rotated-frame. Confirm corner handedness (top-left = NW,
bottom-right = SE) so the box isn't mirrored.

## 2. Map furniture

The **scale bar** (bottom-left, round-number length from the px/m scale) and a
**north arrow** (tilted to true north, beside the scale bar) are emitted
automatically in rotated-frame mode. Each is its own `<g>` (`scalebar`,
`north`). Nothing to do unless the user wants them moved or off.

**Hatch / halftone textures** are config-driven in `style.py`:
- `HALFTONE` — clipped dot grids per layer (default: green/parks).
- `HATCH` — clipped diagonal lines per layer (default: water; angle/spacing
  configurable). Water is often a tiny sliver in tight corridor frames — if the
  user wants visible hatch, point it at a bigger zone (parks) or build an avenue
  corridor band. Ask where they want it rather than assuming.

Colors, stroke weights, dot/line density all live in `style.py` — edit there,
never hardcode in the emit functions. To offer alternate palettes, copy
`style.py` and switch the import in `osm_to_svg.py`.

## 3. Preview the SVG in-chat

The user wants to see the result *here*, not open it elsewhere. The SVG styles
geometry via a `<style>` block (CSS classes), so use a **browser-grade**
renderer. `cairosvg` does NOT work on Windows (no cairo DLL) — don't rely on it.

Use the Claude_Preview MCP browser route:
1. Ensure `index.html` (wraps the SVG in an `<img>`) and `.claude/launch.json`
   (a static `python -m http.server` config) exist — they're committed in this
   repo. SVGs are written to `output/` (auto-created, git-ignored). If the
   output filename differs from `stnicholas.svg`, point the `<img src>` in
   `index.html` at `output/<file>.svg`.
2. `preview_start` the `static` server, then `preview_screenshot`.
3. To reload after regenerating, bump the img src cache-buster via
   `preview_eval`: `document.querySelector('img').src='<file>.svg?'+Date.now()`.
4. To inspect detail, scale the img up and `window.scrollTo(...)` the region,
   then screenshot. Reset afterward.

A matplotlib render of the transformed GeoDataFrames is a fast *geometry* sanity
check, but it does not reflect the real CSS styling — only use it to verify
orientation/clipping, not look.

## 4. Verify, then commit

- Confirm layers aren't empty (grep group ids / count `<path>`/`<circle>`/
  `<line>`), and in rotated-frame mode that the axis street is actually vertical
  (its post-transform principal-axis bearing should be ~0°).
- Commit after every change that yields a valid SVG, following the repo's
  one-commit-per-step rhythm. SVGs and `.venv/` are gitignored; commit the
  source and `style.py`.

## Gotchas worth remembering

- **Exact OSM street names** matter for `--align`. Partial/fuzzy names miss or
  grab the wrong way (e.g. "Saint Nicholas Terrace" vs "Saint Nicholas Avenue").
- **Projection is UTM** for true meters (not Web-Mercator); the local zone is
  auto-selected per map from longitude (`utm_crs_for`), so any location works.
- **DMS → decimal**: West longitude and South latitude are negative.
- **osmnx 2.x `bbox_from_point`** tuple order: a historical snag noted in
  BUILD_SPEC; not present in osmnx 2.1.0 but check if geometry comes out empty
  or mirrored.
