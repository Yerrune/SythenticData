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

from .config import (
    BurLayerConfig,
    BursConfig,
    FlashConfig,
    IndentConfig,
    PartConfig,
    PlateConfig,
    VoidConfig,
    VoidLayerConfig,
)


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


def _build_void_layer(layer: VoidLayerConfig, thickness: float) -> cq.Workplane | None:
    """Build cutters for one void layer (continuous or intermittent)."""
    rng = random.Random(layer.seed)
    span = layer.x_end - layer.x_start
    steps = int(math.floor(span / layer.pitch + 1e-9)) + 1

    cutters: cq.Workplane | None = None
    for i in range(steps):
        x_i = layer.x_start + i * layer.pitch
        radius = rng.uniform(layer.r_min, layer.r_max)
        y_i = layer.y_offset + rng.uniform(-layer.y_scatter, layer.y_scatter)
        cyl = (
            cq.Workplane("XY")
            .circle(radius)
            .extrude(layer.depth + 1.0)
            .translate((x_i, y_i, thickness - layer.depth))
        )
        cutters = cyl if cutters is None else cutters.union(cyl)

    return cutters


def build_voids(void: VoidConfig, thickness: float) -> cq.Workplane | None:
    """Build the fused surface-void cutter, or ``None`` if voids are disabled.

    Each enabled layer (``continuous`` and/or ``intermittent``) is built
    independently and fused into a single cutting tool.
    """
    if not void.enabled:
        return None

    result: cq.Workplane | None = None
    for _kind, layer in void.layers():
        cutters = _build_void_layer(layer, thickness)
        if cutters is None:
            continue
        result = cutters if result is None else result.union(cutters)
    return result


def build_flash(
    flash: FlashConfig, indentation: IndentConfig, plate: PlateConfig
) -> cq.Workplane | None:
    """Build the weld flash bead, or ``None`` if flash is disabled.

    Each segment is a cylinder drawn in the **XY plane** with its top face at the
    plate top (Z = ``thickness``), centred at the advancing-side weld edge
    (Y = ``indentation.radius``). The segment is tilted about its own X axis by
    ``tilt_angle_deg``, lifting the outer (+Y) half above the surface; only that
    half is kept so the flash hugs the weld edge without overlapping the weld
    region. ``height`` is the segment depth below the top face (along Z).
    """
    if not flash.enabled:
        return None

    thickness = plate.thickness
    y_inner = indentation.radius  # advancing-side weld edge; flash starts here
    span = flash.x_stop - flash.x_start
    steps = int(math.floor(span / flash.pitch + 1e-9)) + 1
    rng = random.Random(flash.seed)

    max_radius = flash.flash_width + flash.radius_scatter
    max_tilt = math.radians(flash.tilt_angle_deg + flash.tilt_angle_scatter)
    embed = 0.05  # slight embed below top for a robust union with the plate
    # Outer rim of the tilted top face rises to roughly z = thickness + R*sin(tilt).
    z_protrusion = max_radius * math.sin(max_tilt) + max_radius * (
        1.0 - math.cos(max_tilt)
    )

    bead: cq.Workplane | None = None
    for i in range(steps):
        x_i = flash.x_start + i * flash.pitch
        radius = flash.flash_width + rng.uniform(-flash.radius_scatter, flash.radius_scatter)
        radius = max(radius, 0.05)
        tilt_deg = flash.tilt_angle_deg + rng.uniform(
            -flash.tilt_angle_scatter, flash.tilt_angle_scatter
        )
        # Circle in the XY plane; top face at the plate top, extruded downward.
        # Tilt about the segment's own X axis (through the weld edge on the top
        # surface) so the advancing-side (+Y) half rises above z = thickness.
        cyl = (
            cq.Workplane("XY")
            .workplane(offset=thickness)
            .center(x_i, y_inner)
            .circle(radius)
            .extrude(-(flash.height + embed))
            .rotate(
                (x_i, y_inner, thickness),
                (x_i + 1.0, y_inner, thickness),
                tilt_deg,
            )
        )
        bead = cyl if bead is None else bead.union(cyl)

    if bead is None:
        return None

    # Keep only the outer half (Y >= weld edge) so flash never covers the weld.
    keep_len_x = span + 2.0 * max_radius + 2.0
    keep_len_y = max_radius + 1.0
    keep_len_z = flash.height + embed + z_protrusion + 1.0
    keep = (
        cq.Workplane("XY")
        .box(keep_len_x, keep_len_y, keep_len_z)
        .translate((
            (flash.x_start + flash.x_stop) / 2.0,
            y_inner + keep_len_y / 2.0,
            thickness + z_protrusion / 2.0 - flash.height / 2.0,
        ))
    )
    return bead.intersect(keep)


# Small vertical overlap so a bur's flat base fuses cleanly into the plate top
# instead of resting on a coincident face (which can break boolean unions).
_BUR_EMBED = 0.05


def _annulus_sector_solid(
    center: tuple[float, float],
    inner_r: float,
    outer_r: float,
    start_deg: float,
    span_deg: float,
    z_base: float,
    height: float,
) -> cq.Workplane:
    """A flat annulus sector ("slice of a donut") lying in the XY plane.

    The sector is centred at ``center = (cx, cy)``, drawn between ``inner_r`` and
    ``outer_r`` over the angular span ``[start_deg, start_deg + span_deg]``, with
    its base in the plane ``Z = z_base`` and extruded upward by ``height``.
    """
    cx, cy = center
    a1 = math.radians(start_deg)
    a2 = math.radians(start_deg + span_deg)
    am = (a1 + a2) / 2.0

    def pt(r: float, angle: float) -> tuple[float, float]:
        return (cx + r * math.cos(angle), cy + r * math.sin(angle))

    return (
        cq.Workplane("XY", origin=(0.0, 0.0, z_base))
        .moveTo(*pt(inner_r, a1))
        .lineTo(*pt(outer_r, a1))
        .threePointArc(pt(outer_r, am), pt(outer_r, a2))
        .lineTo(*pt(inner_r, a2))
        .threePointArc(pt(inner_r, am), pt(inner_r, a1))
        .close()
        .extrude(height)
    )


def _build_bur_layer(
    mode: str,
    layer: BurLayerConfig,
    indentation: IndentConfig,
    plate: PlateConfig,
) -> cq.Workplane | None:
    """Build burs for one defect class (``attached`` or ``loose``)."""
    thickness = plate.thickness
    y_inner = indentation.radius
    span_x = layer.x_stop - layer.x_start
    steps = int(math.floor(span_x / indentation.pitch + 1e-9)) + 1
    rng = random.Random(layer.seed)

    chips: cq.Workplane | None = None
    for i in range(steps):
        if rng.random() >= layer.probability:
            continue

        x_i = layer.x_start + i * indentation.pitch
        inner_r = rng.uniform(layer.inner_radius_min, layer.inner_radius_max)
        ring_width = rng.uniform(layer.ring_width_min, layer.ring_width_max)
        outer_r = inner_r + ring_width
        sector_span = rng.uniform(layer.sector_angle_min, layer.sector_angle_max)
        start_deg = rng.uniform(0.0, 360.0)
        height = rng.uniform(layer.height_min, layer.height_max)

        if mode == "attached":
            a1 = math.radians(start_deg)
            cx = x_i - outer_r * math.cos(a1)
            cy = y_inner - outer_r * math.sin(a1)
        else:
            cx = x_i + rng.uniform(-layer.loose_scatter, layer.loose_scatter)
            cy = (
                y_inner
                + layer.loose_y_offset
                + rng.uniform(-layer.loose_scatter, layer.loose_scatter)
            )

        chip = _annulus_sector_solid(
            (cx, cy),
            inner_r,
            outer_r,
            start_deg,
            sector_span,
            z_base=thickness - _BUR_EMBED,
            height=height + _BUR_EMBED,
        )
        chips = chip if chips is None else chips.union(chip)

    return chips


def build_burs(
    burs: BursConfig, indentation: IndentConfig, plate: PlateConfig
) -> cq.Workplane | None:
    """Build weld burs, or ``None`` if burs are disabled.

    Each enabled layer (``attached`` and/or ``loose``) is built independently
    and fused into a single solid.
    """
    if not burs.enabled:
        return None

    result: cq.Workplane | None = None
    for mode, layer in burs.layers():
        chips = _build_bur_layer(mode, layer, indentation, plate)
        if chips is None:
            continue
        result = chips if result is None else result.union(chips)
    return result


def build_part(part: PartConfig) -> cq.Workplane:
    """Build the full part: workpiece minus indentations/voids, plus flash/burs."""
    workpiece = build_workpiece(part.plate)
    cutters = build_cutters(part.indentation, part.plate.thickness)
    result = workpiece.cut(cutters)

    voids = build_voids(part.void, part.plate.thickness)
    if voids is not None:
        result = result.cut(voids)

    flash = build_flash(part.flash, part.indentation, part.plate)
    if flash is not None:
        result = result.union(flash)

    burs = build_burs(part.burs, part.indentation, part.plate)
    if burs is not None:
        result = result.union(burs)
    return result
