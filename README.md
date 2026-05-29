# Claude-OSM

Turn OpenStreetMap geometry into a **layered, hand-editable SVG** for
landscape-architecture / figure-ground cartography. Every map layer is its own
selectable `<g>` — open the output in Illustrator (or any editor) and edit fills,
strokes, and textures by hand. Nothing is rasterized.

The pipeline can **rotate** the map so a chosen street runs vertical, clip to a
two-corner frame, and add a scale bar, north arrow, halftone dot-fills, and
diagonal hatching — all as editable vector layers.

## Quick start

Requires **Python 3.10+**.

```bash
git clone https://github.com/zihaoooo/Claude-OSM.git
cd Claude-OSM
```

Create a virtual environment and install dependencies:

- **Windows**
  ```powershell
  py -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  ```
- **macOS / Linux**
  ```bash
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  ```

Then build a map (use your platform's interpreter path — `.venv\Scripts\python.exe`
on Windows, `.venv/bin/python` on macOS/Linux):

```bash
.venv/bin/python osm_to_svg.py \
  --corner1 "40.810511,-73.956319" --corner2 "40.796861,-73.948272" \
  --rotate auto --align "Saint Nicholas Avenue" -o stnicholas.svg
```

Open `stnicholas.svg` in any web browser or vector editor.

## Two ways to frame a map

**Rotated-frame (preferred for a street/avenue/corridor)** — give two opposite
corners of a box plus a street to make vertical. The pipeline rotates the scene so
that street runs straight up the page, then clips to the box:

```bash
python osm_to_svg.py --corner1 "lat,lon" --corner2 "lat,lon" \
  --rotate auto --align "<Exact OSM Street Name>" -o out.svg
```
- Corners accept signed **decimal** lat/lon (West longitude / South latitude are
  negative). Convert from DMS first.
- `--align` needs the **exact OSM street name** (e.g. `"Saint Nicholas Avenue"`).
- `--rotate auto` derives the angle from the street; override with `--rotate <deg>`.
- Size with `--long-side <px>` (default 1400) or pin `--scale <px_per_meter>`.

**Place + dist (north-up neighborhood plate)**:
```bash
python osm_to_svg.py "Harlem, New York, NY" --dist 900 -o out.svg
```

## Using it with Claude Code

This repo ships a project skill (`.claude/skills/osm-map/`) and a `CLAUDE.md`, so
the whole workflow is automated: open the folder in
[Claude Code](https://claude.com/claude-code) and just ask, e.g. *"make a map of
St. Nicholas Avenue from 110th to 125th."* Claude sets up the environment on first
run, asks how to frame the map, runs the pipeline, adds the furniture/textures,
and previews the result. See [`BUILD_SPEC.md`](BUILD_SPEC.md) for the design doc.

## Customizing the look

All design tokens live in [`style.py`](style.py): per-layer colors and stroke
weights (`STYLE`), OSM tag filters (`TAGS`), road classes (`MAJOR_ROADS`),
halftone dot density (`HALFTONE`), and diagonal-hatch angle/spacing (`HATCH`).
Edit there — never hardcode style in the pipeline. Copy `style.py` for alternate
palettes and switch the import in `osm_to_svg.py`.

## Repo layout

| Path | Purpose |
|---|---|
| `osm_to_svg.py` | The pipeline + CLI (fetch → project/rotate/clip → emit SVG) |
| `style.py` | Design tokens (palette, OSM tags, textures) |
| `requirements.txt` | Python dependencies |
| `CLAUDE.md` | Onboarding for Claude Code |
| `BUILD_SPEC.md` | Design doc / feature reference |
| `.claude/skills/osm-map/` | The interactive workflow skill |
| `index.html`, `.claude/launch.json` | In-chat SVG preview scaffolding |

## Notes

- Projection is **UTM** (EPSG:32618, NYC zone 18N). For maps far from that zone,
  set the correct UTM zone in `CRS_METRIC` or the map will be distorted.
- Data © OpenStreetMap contributors (ODbL), fetched via
  [OSMnx](https://github.com/gboeing/osmnx).
