"""CadQuery geometry construction for the FSW butt-joint part.

Coordinate system:
    X = weld / travel direction (indentations advance along X by ``pitch``)
    Y = transverse direction (the two plates sit side by side, seam at Y=0)
    Z = thickness (top surface at Z = thickness, bottom at Z = 0)

The workpiece is two identical plates fused across a seam gap. A series of
tilted cylinders is fused into a single cutting tool and subtracted in one
boolean operation, leaving the crescent "slice of apple" tool marks that
resemble a friction stir welded surface.
"""

from __future__ import annotations

import math
import random

import cadquery as cq

from .config import FlashConfig, IndentConfig, PartConfig, PlateConfig, VoidConfig


def build_workpiece(plate: PlateConfig) -> cq.Workplane:
    """Build the two-plate workpiece, fused into a single solid.

    The part spans X in [0, length] and Z in [0, thickness]. The two plates
    are placed symmetrically about Y=0 with ``gap`` between them.
    """
    y_center = plate.gap / 2.0 + plate.width / 2.0
    x_center = plate.length / 2.0
    z_center = plate.thickness / 2.0

    plate1 = (
        cq.Workplane("XY")
        .box(plate.length, plate.width, plate.thickness)
        .translate((x_center, -y_center, z_center))
    )
    plate2 = (
        cq.Workplane("XY")
        .box(plate.length, plate.width, plate.thickness)
        .translate((x_center, y_center, z_center))
    )
    return plate1.union(plate2)


def build_cutters(indent: IndentConfig, thickness: float) -> cq.Workplane:
    """Build the fused cutting tool: N tilted cylinders along the weld line.

    Each cylinder is extruded along +Z, tilted about the Y axis by
    ``tilt_angle_deg`` (a tilt in the XZ plane), then positioned so that the
    lowest point of its (tilted) base rim penetrates ``depth`` below the top
    surface. The slightly tilted flat base means only a crescent of material
    is removed per indentation.
    """
    theta = math.radians(indent.tilt_angle_deg)
    # After tilting the base disc about Y, the lowest rim point drops by
    # radius * sin(theta) relative to the disc centre.
    rim_drop = indent.radius * math.sin(theta)
    z_center = thickness - indent.depth + rim_drop

    cutters: cq.Workplane | None = None
    for i in range(indent.count):
        x_i = indent.x_start + i * indent.pitch
        cyl = (
            cq.Workplane("XY")
            .circle(indent.radius)
            .extrude(indent.length)
            .rotate((0, 0, 0), (0, 1, 0), indent.tilt_angle_deg)
            .translate((x_i, 0.0, z_center))
        )
        cutters = cyl if cutters is None else cutters.union(cyl)

    assert cutters is not None  # count >= 1 enforced by config validation
    return cutters


def build_voids(void: VoidConfig, thickness: float) -> cq.Workplane | None:
    """Build the fused surface-void cutter, or ``None`` if voids are disabled.

    A row of vertical cylinders runs along X from ``x_start`` to ``x_end`` every
    ``pitch`` mm. Each has a random radius in [``r_min``, ``r_max``] and a Y
    centre of ``y_offset`` +/- ``y_scatter`` (the advancing-side offset with
    physical jitter). Each cylinder cuts ``depth`` mm into the top surface.

    Whether the result is a continuous channel or isolated pits depends purely
    on the radius/pitch relationship (see ``VoidConfig``); the same code path
    serves both the "continuous" and "intermittent" modes.
    """
    if not void.enabled:
        return None

    rng = random.Random(void.seed)
    span = void.x_end - void.x_start
    steps = int(math.floor(span / void.pitch + 1e-9)) + 1

    cutters: cq.Workplane | None = None
    for i in range(steps):
        x_i = void.x_start + i * void.pitch
        radius = rng.uniform(void.r_min, void.r_max)
        y_i = void.y_offset + rng.uniform(-void.y_scatter, void.y_scatter)
        # Vertical cylinder breaking the top surface: its base sits at
        # z = thickness - depth and it extrudes up through the surface.
        cyl = (
            cq.Workplane("XY")
            .circle(radius)
            .extrude(void.depth + 1.0)
            .translate((x_i, y_i, thickness - void.depth))
        )
        cutters = cyl if cutters is None else cutters.union(cyl)

    return cutters


def _flash_ramp(t: float, ramp_fraction: float) -> float:
    """Trapezoidal ramp: 0 at the ends, 1 across the plateau.

    Rises linearly over the first ``ramp_fraction`` of the span and falls
    linearly over the last ``ramp_fraction``; flat in between.
    """
    if t <= 0.0 or t >= 1.0:
        return 0.0
    if t < ramp_fraction:
        return t / ramp_fraction
    if t > 1.0 - ramp_fraction:
        return (1.0 - t) / ramp_fraction
    return 1.0


def build_flash(
    flash: FlashConfig, indentation: IndentConfig, plate: PlateConfig
) -> cq.Workplane | None:
    """Build the weld flash bead, or ``None`` if flash is disabled.

    A row of tilted cylinders is fused along X on the advancing side. The bead
    radius ramps from zero up to ``flash_width`` and back to zero across the
    span, so the flash always tapers in and out. Each cylinder extends from the
    plate bottom up to ``thickness + height``, so unioning it with the part adds
    a continuous raised layer of excess material above the top surface.
    """
    if not flash.enabled:
        return None

    thickness = plate.thickness
    y0 = flash.y_offset if flash.y_offset > 0 else indentation.radius
    span = flash.x_stop - flash.x_start
    steps = int(math.floor(span / flash.pitch + 1e-9)) + 1
    # Drop cylinders too small to overlap their neighbour; this keeps the bead a
    # single continuous solid (never intermittent) while still tapering to near
    # zero at the very ends.
    min_radius = flash.pitch / 2.0

    bead: cq.Workplane | None = None
    for i in range(steps):
        x_i = flash.x_start + i * flash.pitch
        t = (x_i - flash.x_start) / span if span > 0 else 0.0
        factor = _flash_ramp(t, flash.ramp_fraction)
        radius = flash.flash_width * factor
        if radius < min_radius:
            continue
        # Both width (radius) and protrusion height ramp together so the bead
        # rises smoothly from the surface and falls back to it.
        cap_height = flash.height * factor
        cyl = (
            cq.Workplane("XY")
            .circle(radius)
            .extrude(thickness + cap_height)
            .rotate((0, 0, 0), (0, 1, 0), flash.tilt_angle_deg)
            .translate((x_i, y0, 0.0))
        )
        bead = cyl if bead is None else bead.union(cyl)

    return bead


def build_part(part: PartConfig) -> cq.Workplane:
    """Build the full part: workpiece minus indentations/voids, plus flash."""
    workpiece = build_workpiece(part.plate)
    cutters = build_cutters(part.indentation, part.plate.thickness)
    result = workpiece.cut(cutters)

    voids = build_voids(part.void, part.plate.thickness)
    if voids is not None:
        result = result.cut(voids)

    flash = build_flash(part.flash, part.indentation, part.plate)
    if flash is not None:
        result = result.union(flash)
    return result
