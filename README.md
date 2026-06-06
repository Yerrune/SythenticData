# FSW Weld Surface Generator

Generate a parametric 3D CAD part of a **friction stir welded (FSW) butt joint**
from a JSON description, export it as STEP/STL, and produce a photorealistic
ISO render. The renders are intended as synthetic training data for a weld
quality monitoring vision system.

The geometry is built from a **base butt joint** plus optional weld-surface
features: tool marks, surface voids, weld flash, and burs. Each feature type is
described in [Weld feature types](#weld-feature-types) below.

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

Advancing side is **+Y** (to the right of the weld centreline when looking along
+X). Most defects and flash appear on this side, offset from the seam by
`indentation.radius`.

## Weld feature types

Every part starts from the same base geometry. Optional features are toggled in
JSON and combined freely (see [config/ButtJoint_1.json](config/ButtJoint_1.json)
for a part with all feature types enabled at once).

| Feature | JSON section | CAD operation | Real FSW analogue |
| --- | --- | --- | --- |
| Butt joint workpiece | `plate` | Base solid | Two plates joined at the seam |
| Tool marks | `indentation` | Subtract (cut) | Repeated FSW probe indentations |
| Continuous void | `void.continuous` | Subtract (cut) | Open surface channel / groove defect |
| Intermittent void | `void.intermittent` | Subtract (cut) | Isolated surface pits / pinholes |
| Weld flash | `flash` | Union (add) | Excess material squeezed out at weld edge |
| Attached bur | `burs.attached` | Union (add) | Curled chip still clinging to weld edge |
| Loose bur | `burs.loose` | Union (add) | Detached chip lying on plate near weld |

Build order in `build_part`: workpiece minus tool marks and voids, then union
flash and burs.

### Butt joint workpiece (`plate`)

Two identical rectangular plates are placed side by side, symmetric about
**Y = 0**, with an optional transverse **seam gap** (`gap`). They are fused into
one solid spanning **X** in `[0, length]`, **Z** in `[0, thickness]`. Each plate
has width `width`; the full part width along Y is `2 * width + gap`.

This is always present. All other features are applied on top of this base.

### Tool marks (`indentation`)

The characteristic FSW surface texture. A row of **tilted cylinders** is fused
into one cutting tool and subtracted from the workpiece in a single boolean cut.

Each cylinder has radius `radius` and length `length` (spanning the seam region
along Y). It is extruded along +Z, then rotated about **Y** by
`tilt_angle_deg` (tilt in the XZ plane). The cutter is positioned so the lowest
point of its tilted base rim penetrates `depth` mm below the top surface
(`Z = thickness`). Because the base is slightly tilted, only a **crescent** of
material is removed per pass - the "slice of apple" tool mark.

Cylinders are placed at `x_start + i * pitch` for `i = 0 ... count - 1`. Larger
`pitch` leaves distinct ridges; smaller pitch makes marks overlap and smooth out.
Larger `tilt_angle_deg`, `radius`, or `depth` make each mark more pronounced.

Example: [config/example_part.json](config/example_part.json).

### Surface void - continuous (`void.continuous`)

A **surface groove defect** on the advancing side: a row of vertical cylinders
subtracted from the top surface. Each cylinder has a random radius in
`[r_min, r_max]` and a Y centre of `y_offset` +/- `y_scatter`. Cylinders are
placed every `pitch` mm along X from `x_start` to `x_end`, penetrating `depth`
below the top face.

**Continuous** means neighbouring cutters **overlap** so the void reads as one
open channel rather than separate holes. Design rule:

```
pitch <= 2 * r_min   (allow margin for y_scatter)
```

Each layer has its own `seed` for reproducible random radii and scatter. Multiple
void layers (`continuous` and `intermittent`) can coexist in one part; their
cutters are fused before subtraction.

Example: [config/void_continuous.json](config/void_continuous.json).

Real voids are often sub-millimetre; they appear small in a full-part ISO render.
Use a lower `render.camera_margin`, the tool-mounted close-up, or crop to inspect
them.

### Surface void - intermittent (`void.intermittent`)

Same cutter model as continuous voids, but tuned so neighbouring cylinders
**never touch**, leaving **isolated pits** along the advancing side. Design rule:

```
2 * r_max < pitch
```

Use this for pinhole-like or spot void defects. Parameters and placement match
the continuous void layer (`x_start`, `x_end`, `r_min`, `r_max`, `pitch`,
`depth`, `y_offset`, `y_scatter`, `seed`).

Example: [config/void_intermittent.json](config/void_intermittent.json).

Legacy configs may use a single `void` block with `"mode": "continuous"` or
`"intermittent"` instead of nested `void.continuous` / `void.intermittent`.

### Weld flash (`flash`)

**Excess material** squeezed out on the advancing side during welding, modelled
as a continuous raised bead along the weld edge. Enabled with `"enabled": true`.

Each segment is a **cylinder in the XY plane** with its top face at
`Z = thickness`, centred at the advancing-side weld edge (`Y = indentation.radius`).
The segment is extruded downward by `height`, then tilted about **its own X axis**
(at the weld edge) by `tilt_angle_deg`. That tilt lifts the outer (+Y) half above
the plate top; only that outer half is kept (clipped with a bounding box) so the
flash hugs the weld edge without covering the weld region. Protrusion height
comes from the tilt angle and segment radius, not from extruding above the top
face.

Segments are placed every `pitch` mm from `x_start` to `x_stop`. Neighbouring
segments overlap (`pitch` should be small relative to `flash_width`) so the bead
is one continuous solid. Each segment radius is `flash_width` plus random +/-
`radius_scatter`; `seed` makes the edge irregularity reproducible.

Example: [config/weld_flash.json](config/weld_flash.json).

### Weld burs - attached (`burs.attached`)

**Curled chips of plasticized metal** still clinging to the advancing-side weld
edge - the material sometimes expelled as "flash" during FSW. Each bur is a flat
**annulus sector** ("slice of a donut"): an arc between inner radius `inner_r`
and outer radius `inner_r + ring_width`, spanning a random sector angle, drawn in
the XY plane with its base at the plate top and extruded upward by a small random
`height` (typically 0.1-0.5 mm).

Burs are **discontinuous random events**. Along X from `x_start` to `x_stop`, the
generator steps every `indentation.pitch` mm; at each step a bur appears with
probability `probability`. For attached burs, the **start of the outer-ring arc**
is anchored to the weld edge (`Y = indentation.radius`), so the chip sticks out
from the seam. Inner/outer radii, ring width, sector angle, and height are each
sampled uniformly from their min/max ranges; `seed` fixes the layout.

Example: [config/burs_attached.json](config/burs_attached.json).

### Weld burs - loose (`burs.loose`)

Same annulus-sector geometry and random placement as attached burs, but the chip
has **detached** from the weld and lies on the plate nearby. The sector centre is
offset from the weld edge by `loose_y_offset` along Y, with uniform +/-
`loose_scatter` jitter in both X and Y so chips land in a scattered band on the
advancing side.

Attached and loose bur layers can be combined in one config under
`burs.attached` and `burs.loose`. Legacy single-block configs with
`"mode": "attached"` or `"loose"` are still supported.

Example: [config/burs_loose.json](config/burs_loose.json).

### Combining features

[config/ButtJoint_1.json](config/ButtJoint_1.json) demonstrates a long butt joint
with tool marks along the full length and localized spans of continuous void,
intermittent void, flash, attached burs, and loose burs at different X ranges.
Use separate X spans and seeds per feature when building labelled training sets so
each defect class can be identified independently.

```bash
python main.py config/ButtJoint_1.json
python main.py config/void_continuous.json
python main.py config/void_intermittent.json
python main.py config/weld_flash.json
python main.py config/burs_attached.json
python main.py config/burs_loose.json
```

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
| `void` (optional) | `continuous` | Continuous void layer (overlapping channel); omit or set `"enabled": false` to disable. |
| | `intermittent` | Intermittent void layer (isolated pits); omit or set `"enabled": false` to disable. |
| | *(per layer)* | `x_start`, `x_end`, `r_min`, `r_max`, `pitch`, `depth`, `y_offset`, `y_scatter`, `seed`. |
| | *(legacy)* | Single block with `"mode": "continuous"` or `"intermittent"` still supported. |
| `flash` (optional) | `enabled` | Add weld flash (excess material) on the advancing side. |
| | `x_start`, `x_stop` | X extent of the flash bead. |
| | `flash_width` | Mean bead radius in the XY plane. |
| | `height` | Segment depth below the top face (along Z). |
| | `pitch` | Spacing of segments along X (smaller = smoother bead). |
| | `tilt_angle_deg` | Tilt about the segment X axis (>0); lifts the outer half above the plate top. |
| | `radius_scatter` | Random +/- jitter on each cylinder radius (irregular outer edge); must be < `flash_width`. |
| | `seed` | RNG seed for reproducible radius scatter. |
| `burs` (optional) | `attached` | Attached bur layer (chip clings to weld edge); omit or set `"enabled": false` to disable. |
| | `loose` | Loose bur layer (detached chip near weld); omit or set `"enabled": false` to disable. |
| | *(per layer)* | `x_start`, `x_stop`, `probability`, `inner_radius_min/max`, `ring_width_min/max`, `sector_angle_min/max`, `height_min/max`, `seed`; loose also has `loose_y_offset`, `loose_scatter`. |
| | *(legacy)* | Single block with `"mode": "attached"` or `"loose"` still supported. |
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

See [Surface void - continuous](#surface-void---continuous-voidcontinuous) and
[Surface void - intermittent](#surface-void---intermittent-voidintermittent) above.
Two void classes may be combined under `void.continuous` and `void.intermittent`.

### Weld flash (excess material)

See [Weld flash](#weld-flash-flash) above.

### Weld burs (curled metal chips)

See [Weld burs - attached](#weld-burs---attached-bursattached) and
[Weld burs - loose](#weld-burs---loose-bursloose) above.

### Tool-mounted close-up view

In addition to the ISO render, each run produces `<basename>_toolview.png`: an
**orthographic, tilted top-down** view as if a camera were mounted on the FSW
tool looking down at the surface. It is centered on the weld path midpoint
(`X = x_start + (count - 1) * pitch / 2`, `Y = 0`), tilted about Y by
`indentation.tilt_angle_deg` to mimic the tool tilt, and zoomed to a window of
`indentation.radius * tool_view_window_factor` mm
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
config/ButtJoint_1.json        Full example with all weld feature types
config/example_part.json       Example part with a continuous void
config/void_continuous.json   Example: continuous surface void
config/void_intermittent.json Example: intermittent surface void
config/weld_flash.json        Example: advancing-side weld flash
config/burs_attached.json     Example: burs attached to the weld edge
config/burs_loose.json        Example: loose burs near the weld
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
