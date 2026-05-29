# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A pure-Python pipeline that turns OpenStreetMap geometry into a **layered,
hand-editable SVG** for landscape-architecture / figure-ground cartography. Every
visual layer is its own semantic `<g>`; nothing is rasterized. Full design doc:
[`BUILD_SPEC.md`](BUILD_SPEC.md).

## First-run setup (do this before running anything)

There is intentionally **no committed virtualenv** — it's platform-specific.
If `.venv/` is missing, create it and install dependencies first:

- **Windows:** `py -m venv .venv` then
  `.venv\Scripts\python.exe -m pip install -r requirements.txt`
- **macOS / Linux:** `python3 -m venv .venv` then
  `.venv/bin/python -m pip install -r requirements.txt`

Dependencies are pinned in `requirements.txt` (osmnx 2.x, geopandas, shapely,
numpy; Python 3.10+). `matplotlib` is optional, only for quick geometry-only
previews.

Always run the pipeline through the venv interpreter:
- Windows: `.venv\Scripts\python.exe osm_to_svg.py ...`
- macOS / Linux: `.venv/bin/python osm_to_svg.py ...`

## How to build a map

There is a project skill, **`osm-map`** (in `.claude/skills/osm-map/`), that
encodes the full interactive workflow — choosing how to define the map frame,
running the pipeline, adding the scale bar / north arrow / textures, previewing
the SVG in-chat, and committing. **Follow that skill whenever the user wants to
fetch, build, or render a map.** It is the source of truth for the workflow; this
file only covers environment + conventions.

Quick reference — the two ways to frame a map:
- **Rotated-frame (preferred for a street/corridor):**
  `osm_to_svg.py --corner1 "lat,lon" --corner2 "lat,lon" --rotate auto
  --align "<Exact OSM Street Name>" -o out.svg`
- **Place + dist (north-up neighborhood):**
  `osm_to_svg.py "Place, Region" --dist <meters> -o out.svg`

## Module map

- `osm_to_svg.py` — the pipeline + CLI (fetch → project/rotate/clip → emit SVG).
- `style.py` — all design tokens (`STYLE`, `TAGS`, `MAJOR_ROADS`, `HALFTONE`,
  `HATCH`). Edit look-and-feel here; copy the file for alternate palettes.
- `.claude/skills/osm-map/SKILL.md` — the workflow skill.
- `index.html` + `.claude/launch.json` — in-chat SVG preview scaffolding.

## Conventions

- **Edit style in `style.py`**, never hardcode colors/weights/density in emit
  functions.
- **Projection is UTM** (`CRS_METRIC`, EPSG:32618 = NYC zone 18N). For other
  regions, set the correct UTM zone or the map will be distorted/offset.
- `--align` needs the **exact OSM street name** (beware near-matches).
- **DMS → decimal**: West longitude and South latitude are negative.
- **Commit after every change that yields a valid SVG.** `.venv/`, `*.svg`,
  `cache/`, `__pycache__/`, preview PNGs are gitignored — commit source +
  `style.py` + docs.

## Previewing the SVG

The output uses a `<style>` block (CSS classes), so render it with a browser, not
`cairosvg` (which lacks the cairo DLL on Windows). The skill describes the
Claude_Preview MCP route; if that MCP isn't available, just open the `.svg` in any
web browser.
