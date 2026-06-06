# FSW Weld Surface Generator

Generate a parametric 3D CAD part of a **friction stir welded (FSW) butt joint**
from a JSON description, export it as STEP/STL, and produce a photorealistic
ISO render. The renders are intended as synthetic training data for a weld
quality monitoring vision system.

The geometry is: two identical plates butted together with a seam gap, then a
tilted cylinder is repeatedly subtracted along the weld line ("slice of apple"
indentations) to create the characteristic repeated tool-mark ripples.
Optionally, a **surface void** (weld defect) can be added on the advancing side
- either a continuous channel or intermittent isolated pits.

## Pipeline

```
part JSON  ->  CadQuery solid  ->  STEP + STL  ->  Blender Cycles  ->  ISO render PNG
```

- **CAD engine:** [CadQuery](https://cadquery.readthedocs.io/) (OpenCASCADE) - runs in this project's Python venv.
- **Renderer:** [Blender](https://www.blender.org/) Cycles - runs headless as a separate subprocess (its own bundled Python). The two communicate only through files.

## Coordinate system

- **X** = weld / travel direction (indentations advance along X by `pitch`).
- **Y** = transverse direction (the two plates sit side by side, seam at Y=0).
- **Z** = thickness (top surface at Z = `thickness`, bottom at Z = 0).

## Setup

### 1. Python environment (CadQuery)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### 2. Blender (renderer)

Any Blender 4.x install works. Either of:

```bash
# System install (needs root)
sudo snap install blender --classic

# OR a portable build (no root) - extract anywhere and put it on PATH
# https://www.blender.org/download/
```

The renderer locates Blender automatically in this order: `--blender` flag ->
`blender` on `PATH` -> `~/.local/bin/blender` -> `~/.local/opt/blender-*/blender`
-> `/snap/bin/blender`.

> Note: on a headless machine a portable Blender build may be missing the X11
> libraries `libSM.so.6` / `libICE.so.6`. Place them in a directory and point
> the env var `BLENDER_EXTRA_LIB_DIR` at it (default
> `~/.local/opt/blender-extralibs`); the renderer prepends it to
> `LD_LIBRARY_PATH`. You can obtain them without root via
> `apt-get download libsm6 libice6` and `dpkg-deb -x`.

## Usage

```bash
. .venv/bin/activate

# Full pipeline: build CAD, export STEP/STL, render PNG
python main.py config/example_part.json

# CAD only (skip rendering / Blender)
python main.py config/example_part.json --no-render

# Custom output directory and explicit Blender path
python main.py config/example_part.json --out-dir outputs --blender /path/to/blender
```

Outputs are written to `outputs/` (configurable):

- `<basename>.step`, `<basename>.stl` - the CAD geometry.
- `<basename>.png` - the ISO perspective render of the whole part.
- `<basename>_toolview.png` - a tilted top-down "tool-mounted" close-up of the
  weld centre (see below). Enabled by default; disable with `render.tool_view`.
- `<basename>_render.json` - the render settings passed to Blender.

## Configuration (JSON)

See [config/example_part.json](config/example_part.json). All dimensions are in
millimetres; angles in degrees.

| Section | Field | Meaning |
| --- | --- | --- |
| `plate` | `length` | Plate extent along X (weld direction). |
| | `width` | Width of each plate along Y. |
| | `thickness` | Plate thickness along Z. |
| | `gap` | Transverse seam gap between the two plates. |
| `indentation` | `radius` | Cutter cylinder radius. |
| | `length` | Cutter cylinder length. |
| | `tilt_angle_deg` | Tilt of the cylinder axis in the XZ plane. |
| | `depth` | Penetration below the top surface (must be `< thickness`). |
| | `pitch` | Spacing between successive indentations along X. |
| | `count` | Number of indentations. |
| | `x_start` | X position of the first indentation. |
| `void` (optional) | `mode` | `none` (default, disabled), `continuous`, or `intermittent`. |
| | `x_start`, `x_end` | X extent of the void along the weld. |
| | `r_min`, `r_max` | Random cutter radius range (each cylinder is randomized). |
| | `pitch` | Spacing between void cutter cylinders along X. |
| | `depth` | Void penetration below the top surface (must be `< thickness`). |
| | `y_offset` | Transverse offset toward the advancing side (signed; +Y by default). |
| | `y_scatter` | Uniform +/- jitter on each cutter's Y position. |
| | `seed` | RNG seed for reproducible radii/scatter. |
| `flash` (optional) | `enabled` | Add weld flash (excess material) on the advancing side. |
| | `x_start`, `x_stop` | X extent of the flash bead. |
| | `flash_width` | Maximum bead radius (lateral half-width); ramps to this and back to 0. |
| | `height` | Maximum protrusion above the top surface. |
| | `pitch` | Spacing of the bead cylinders along X (smaller = smoother). |
| | `tilt_angle_deg` | Tilt of the bead cylinders in the XZ plane. |
| | `y_offset` | Advancing-side Y position (0 => auto = `indentation.radius`). |
| | `ramp_fraction` | Fraction of the span used to ramp up (and down); 0.5 = triangular. |
| `render` | `width`, `height` | Output image resolution in pixels. |
| | `samples` | Cycles render samples. |
| | `camera_azimuth_deg` | Camera azimuth around Z (ISO view). |
| | `camera_elevation_deg` | Camera elevation above the XY plane. |
| | `camera_margin` | Framing margin (>1 zooms out). |
| | `background_color` | `[r, g, b]` studio floor / backdrop colour. |
| | `material` | Principled BSDF metal: `base_color`, `metallic`, `roughness`, `anisotropic`. |
| | `tool_view` | If true (default), also render the tool-mounted close-up. |
| | `tool_view_window_factor` | Close-up window size = `indentation.radius * factor` (default 2.0). |
| `output` | `basename` | Base filename for all outputs. |
| | `export_step`, `export_stl` | Toggle each CAD export. |
| | `stl_tolerance`, `stl_angular_tolerance` | STL mesh fineness (smaller = finer). |

### Surface voids (weld defects)

A void is a row of secondary cylinders subtracted from the top surface on the
advancing side. Each cylinder gets a random radius in `[r_min, r_max]` and a Y
position of `y_offset` +/- `y_scatter`, mimicking the irregular path of a real
void. Whether the void is continuous or intermittent is governed by the
radius/pitch relationship:

- **Continuous** - neighbouring cutters always overlap into one channel. Keep
  `pitch <= 2 * r_min` (allowing for `y_scatter`); i.e. the minimum radius is on
  the order of the void pitch. See [config/void_continuous.json](config/void_continuous.json).
- **Intermittent** - neighbouring cutters never touch, leaving isolated pits.
  Keep `2 * r_max < pitch`. See [config/void_intermittent.json](config/void_intermittent.json).

```bash
python main.py config/void_continuous.json
python main.py config/void_intermittent.json
```

Because real voids are sub-millimetre, they appear small when the whole part is
framed; reduce `render.camera_margin` (or crop) to inspect them closely. The
`seed` makes a given void layout reproducible for labelled datasets.

### Weld flash (excess material)

Flash is excess metal squeezed out on the advancing side, forming a continuous
raised layer. It is built by unioning a row of tilted cylinders along X (at
`y = indentation.radius` by default), whose radius and height **ramp from zero
up to `flash_width`/`height` and back to zero** across `[x_start, x_stop]`. The
`pitch` is kept small enough that neighbouring cylinders always overlap, so the
bead is a single continuous solid - it is never intermittent. See
[config/weld_flash.json](config/weld_flash.json).

```bash
python main.py config/weld_flash.json
```

### Tool-mounted close-up view

In addition to the ISO render, each run produces `<basename>_toolview.png`: an
**orthographic, tilted top-down** view as if a camera were mounted on the FSW
tool looking down at the surface. It is centered on the plate (`X = length/2`,
`Y = 0`), tilted about Y by `indentation.tilt_angle_deg` to mimic the tool tilt,
and zoomed to a window of `indentation.radius * tool_view_window_factor` mm
(default `radius * 2`). This gives a clear, repeatable close-up of the tool
marks and any surface void - ideal as labelled training tiles. Disable it with
`"tool_view": false` in the `render` section.

### Tuning the tool-mark appearance

The repeated ridges read most clearly when the `pitch` is large enough that each
"slice of apple" cut leaves a distinct curved ridge rather than being smoothed
away by overlap. Larger `tilt_angle_deg`, `radius`, or `depth` make each mark
more pronounced. A low-elevation key light (set in `scripts/blender_render.py`)
grazes across the ridges so the shallow relief casts highlights and shadows.

## Project layout

```
main.py                     CLI entry point (orchestrates the pipeline)
requirements.txt            CadQuery + numpy
config/example_part.json    Example parametric part description (with a void)
config/void_continuous.json   Example: continuous surface void
config/void_intermittent.json Example: intermittent surface void
config/weld_flash.json        Example: advancing-side weld flash
src/weldgen/
  config.py                 Dataclasses + JSON load/validation
  geometry.py               CadQuery solid construction
  export.py                 STEP / STL export
  render.py                 Locates Blender, writes render config, runs subprocess
scripts/blender_render.py   Runs inside Blender (bpy): import, material, lights, camera, render
outputs/                    Generated STEP / STL / PNG
```

## Extending

- **Batch / randomized datasets:** call `weldgen.geometry.build_part` and
  `weldgen.render.render_part` in a loop with randomized config values to
  generate labelled synthetic samples.
- **Frontend:** the JSON-driven design means a GUI can be layered on top later
  without changing the core pipeline.
