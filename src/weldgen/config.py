"""Configuration dataclasses and JSON loading/validation.

A part is described entirely by a JSON file. This module parses that JSON
into typed dataclasses and validates the values so downstream geometry and
render code can assume sane inputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List


class ConfigError(ValueError):
    """Raised when a part configuration is missing fields or invalid."""


@dataclass
class PlateConfig:
    """Two identical plates butted together with a seam gap.

    length: extent along X (weld/travel direction), mm.
    width:  extent along Y of each plate, mm.
    thickness: extent along Z, mm. Top surface sits at Z = thickness.
    gap: transverse gap between the two plates along Y, mm.
    """

    length: float
    width: float
    thickness: float
    gap: float

    def validate(self) -> None:
        for name in ("length", "width", "thickness"):
            value = getattr(self, name)
            if value <= 0:
                raise ConfigError(f"plate.{name} must be > 0 (got {value})")
        if self.gap < 0:
            raise ConfigError(f"plate.gap must be >= 0 (got {self.gap})")


@dataclass
class IndentConfig:
    """Repeated tilted-cylinder indentations along the weld line.

    radius: cutter cylinder radius, mm.
    length: cutter cylinder length, mm (should span the seam region).
    tilt_angle_deg: tilt of the cylinder axis in the XZ plane, degrees.
    depth: penetration of the cutter below the top surface, mm.
    pitch: spacing between successive indentations along X, mm.
    count: number of indentations (>= 1).
    x_start: X position of the first indentation, mm.
    """

    radius: float
    length: float
    tilt_angle_deg: float
    depth: float
    pitch: float
    count: int
    x_start: float

    def validate(self, plate: PlateConfig) -> None:
        for name in ("radius", "length", "pitch"):
            value = getattr(self, name)
            if value <= 0:
                raise ConfigError(f"indentation.{name} must be > 0 (got {value})")
        if self.depth <= 0:
            raise ConfigError(f"indentation.depth must be > 0 (got {self.depth})")
        if self.depth >= plate.thickness:
            raise ConfigError(
                f"indentation.depth ({self.depth}) must be < plate.thickness "
                f"({plate.thickness})"
            )
        if self.count < 1:
            raise ConfigError(f"indentation.count must be >= 1 (got {self.count})")


_VOID_KINDS = ("continuous", "intermittent")


@dataclass
class VoidLayerConfig:
    """Parameters for one surface-void defect class (continuous or intermittent).

    A row of vertical cylinders runs along X from ``x_start`` to ``x_end`` every
    ``pitch`` mm. Each has a random radius in [``r_min``, ``r_max``] and a Y
    centre of ``y_offset`` +/- ``y_scatter``. Continuity is governed by the
    radius/pitch relationship (continuous: overlapping cutters; intermittent:
    isolated pits). ``seed`` makes the layout reproducible for that layer.
    """

    enabled: bool = True
    x_start: float = 0.0
    x_end: float = 0.0
    r_min: float = 0.05
    r_max: float = 0.2
    pitch: float = 0.2
    depth: float = 0.5
    y_offset: float = 3.0
    y_scatter: float = 0.5
    seed: int = 0

    def validate(self, plate: PlateConfig, label: str) -> None:
        if not self.enabled:
            return
        prefix = f"void.{label}"
        if self.x_end <= self.x_start:
            raise ConfigError(
                f"{prefix}.x_end ({self.x_end}) must be > {prefix}.x_start "
                f"({self.x_start})"
            )
        if self.r_min <= 0:
            raise ConfigError(f"{prefix}.r_min must be > 0 (got {self.r_min})")
        if self.r_max < self.r_min:
            raise ConfigError(
                f"{prefix}.r_max ({self.r_max}) must be >= {prefix}.r_min "
                f"({self.r_min})"
            )
        if self.pitch <= 0:
            raise ConfigError(f"{prefix}.pitch must be > 0 (got {self.pitch})")
        if self.depth <= 0:
            raise ConfigError(f"{prefix}.depth must be > 0 (got {self.depth})")
        if self.depth >= plate.thickness:
            raise ConfigError(
                f"{prefix}.depth ({self.depth}) must be < plate.thickness "
                f"({plate.thickness})"
            )
        if self.y_scatter < 0:
            raise ConfigError(
                f"{prefix}.y_scatter must be >= 0 (got {self.y_scatter})"
            )


@dataclass
class VoidConfig:
    """Surface voids (weld defects) simulated by rows of subtracted cylinders.

    A part may specify ``continuous`` voids (overlapping channel), ``intermittent``
    voids (isolated pits), or both under ``void.continuous`` / ``void.intermittent``
    in JSON. Legacy single-block configs with ``void.mode`` are still accepted.
    """

    continuous: VoidLayerConfig | None = None
    intermittent: VoidLayerConfig | None = None

    @property
    def enabled(self) -> bool:
        return (
            (self.continuous is not None and self.continuous.enabled)
            or (self.intermittent is not None and self.intermittent.enabled)
        )

    def validate(self, plate: PlateConfig) -> None:
        if self.continuous is not None:
            self.continuous.validate(plate, "continuous")
        if self.intermittent is not None:
            self.intermittent.validate(plate, "intermittent")

    def layers(self) -> list[tuple[str, VoidLayerConfig]]:
        """Return enabled (kind, config) pairs in build order."""
        out: list[tuple[str, VoidLayerConfig]] = []
        if self.continuous is not None and self.continuous.enabled:
            out.append(("continuous", self.continuous))
        if self.intermittent is not None and self.intermittent.enabled:
            out.append(("intermittent", self.intermittent))
        return out


@dataclass
class FlashConfig:
    """Weld flash: excess material added as a continuous bead immediately
    surrounding the weld on the advancing side, built by unioning a row of
    tilted half-cylinders along X.

    The bead runs from ``x_start`` to ``x_stop`` with one half-cylinder every
    ``pitch`` mm (small enough that neighbours always overlap, so the flash is
    continuous - it is never intermittent). Each segment is a cylinder in the
    **XY plane** with its top face at the plate top (Z = ``thickness``), centred
    at the advancing-side weld edge (Y = ``indentation.radius``). The segment is
    tilted about its own X axis by ``tilt_angle_deg``, lifting the outer (+Y)
    half above the surface; only that half is kept so the flash hugs the weld
    edge without overlapping the weld region. ``height`` is the segment depth
    below the top face (along Z). Radius is ``flash_width`` plus a random +/-
    ``radius_scatter`` per cylinder; tilt is ``tilt_angle_deg`` plus random +/-
    ``tilt_angle_scatter`` per cylinder; ``seed`` makes the scatter reproducible.
    """

    enabled: bool = False
    x_start: float = 0.0
    x_stop: float = 0.0
    flash_width: float = 1.5
    height: float = 1.0
    pitch: float = 0.6
    tilt_angle_deg: float = 2.0
    tilt_angle_scatter: float = 0.0
    radius_scatter: float = 0.3
    seed: int = 0

    def validate(self, plate: PlateConfig) -> None:
        if not self.enabled:
            return
        if self.x_stop <= self.x_start:
            raise ConfigError(
                f"flash.x_stop ({self.x_stop}) must be > flash.x_start ({self.x_start})"
            )
        if self.flash_width <= 0:
            raise ConfigError(f"flash.flash_width must be > 0 (got {self.flash_width})")
        if self.height <= 0:
            raise ConfigError(f"flash.height must be > 0 (got {self.height})")
        if self.pitch <= 0:
            raise ConfigError(f"flash.pitch must be > 0 (got {self.pitch})")
        if self.tilt_angle_deg <= 0:
            raise ConfigError(
                f"flash.tilt_angle_deg must be > 0 (got {self.tilt_angle_deg})"
            )
        if self.tilt_angle_scatter < 0:
            raise ConfigError(
                f"flash.tilt_angle_scatter must be >= 0 (got {self.tilt_angle_scatter})"
            )
        if self.tilt_angle_scatter >= self.tilt_angle_deg:
            raise ConfigError(
                f"flash.tilt_angle_scatter ({self.tilt_angle_scatter}) must be < "
                f"flash.tilt_angle_deg ({self.tilt_angle_deg}) to keep tilts positive"
            )
        if self.radius_scatter < 0:
            raise ConfigError(
                f"flash.radius_scatter must be >= 0 (got {self.radius_scatter})"
            )
        if self.radius_scatter >= self.flash_width:
            raise ConfigError(
                f"flash.radius_scatter ({self.radius_scatter}) must be < "
                f"flash.flash_width ({self.flash_width}) to keep radii positive"
            )


_BURS_MODES = ("attached", "loose")


@dataclass
class BurLayerConfig:
    """Parameters for one bur defect class (attached or loose).

    Burs are discontinuous, random events. Placement is sampled every
    ``indentation.pitch`` mm along X from ``x_start`` to ``x_stop``; at each step
    a bur appears independently with probability ``probability``. ``seed`` makes
    the layout reproducible for that layer.
    """

    enabled: bool = True
    x_start: float = 0.0
    x_stop: float = 0.0
    probability: float = 0.1
    inner_radius_min: float = 4.0
    inner_radius_max: float = 4.0
    ring_width_min: float = 1.0
    ring_width_max: float = 1.0
    sector_angle_min: float = 90.0
    sector_angle_max: float = 150.0
    height_min: float = 0.1
    height_max: float = 0.5
    loose_y_offset: float = 3.0
    loose_scatter: float = 1.5
    seed: int = 0

    def validate(self, plate: PlateConfig, label: str) -> None:
        if not self.enabled:
            return
        prefix = f"burs.{label}"
        if self.x_stop <= self.x_start:
            raise ConfigError(
                f"{prefix}.x_stop ({self.x_stop}) must be > {prefix}.x_start "
                f"({self.x_start})"
            )
        if not 0.0 < self.probability <= 1.0:
            raise ConfigError(
                f"{prefix}.probability must be in (0, 1] (got {self.probability})"
            )
        if self.inner_radius_min <= 0:
            raise ConfigError(
                f"{prefix}.inner_radius_min must be > 0 (got {self.inner_radius_min})"
            )
        if self.inner_radius_max < self.inner_radius_min:
            raise ConfigError(
                f"{prefix}.inner_radius_max ({self.inner_radius_max}) must be >= "
                f"{prefix}.inner_radius_min ({self.inner_radius_min})"
            )
        if self.ring_width_min <= 0:
            raise ConfigError(
                f"{prefix}.ring_width_min must be > 0 (got {self.ring_width_min})"
            )
        if self.ring_width_max < self.ring_width_min:
            raise ConfigError(
                f"{prefix}.ring_width_max ({self.ring_width_max}) must be >= "
                f"{prefix}.ring_width_min ({self.ring_width_min})"
            )
        if self.sector_angle_min <= 0 or self.sector_angle_min > 360:
            raise ConfigError(
                f"{prefix}.sector_angle_min must be in (0, 360] "
                f"(got {self.sector_angle_min})"
            )
        if self.sector_angle_max < self.sector_angle_min or self.sector_angle_max > 360:
            raise ConfigError(
                f"{prefix}.sector_angle_max ({self.sector_angle_max}) must be in "
                f"[{prefix}.sector_angle_min, 360]"
            )
        if self.height_min <= 0:
            raise ConfigError(
                f"{prefix}.height_min must be > 0 (got {self.height_min})"
            )
        if self.height_max < self.height_min:
            raise ConfigError(
                f"{prefix}.height_max ({self.height_max}) must be >= "
                f"{prefix}.height_min ({self.height_min})"
            )
        if label == "loose" and self.loose_scatter < 0:
            raise ConfigError(
                f"{prefix}.loose_scatter must be >= 0 (got {self.loose_scatter})"
            )


@dataclass
class BursConfig:
    """Weld burs: occasional curled chips of plasticized metal expelled at the
    weld edge during friction stir welding.

    A part may specify ``attached`` burs (chips clinging to the weld edge),
    ``loose`` burs (detached chips on the plate), or both. Each class has its
    own parameter block under ``burs.attached`` / ``burs.loose`` in JSON.

    Legacy single-block configs with ``burs.mode`` set to ``attached`` or
    ``loose`` are still accepted.
    """

    attached: BurLayerConfig | None = None
    loose: BurLayerConfig | None = None

    @property
    def enabled(self) -> bool:
        return (
            (self.attached is not None and self.attached.enabled)
            or (self.loose is not None and self.loose.enabled)
        )

    def validate(self, plate: PlateConfig) -> None:
        if self.attached is not None:
            self.attached.validate(plate, "attached")
        if self.loose is not None:
            self.loose.validate(plate, "loose")

    def layers(self) -> list[tuple[str, BurLayerConfig]]:
        """Return enabled (mode, config) pairs in build order."""
        out: list[tuple[str, BurLayerConfig]] = []
        if self.attached is not None and self.attached.enabled:
            out.append(("attached", self.attached))
        if self.loose is not None and self.loose.enabled:
            out.append(("loose", self.loose))
        return out


@dataclass
class MaterialConfig:
    """Principled BSDF metal parameters for rendering."""

    base_color: List[float] = field(default_factory=lambda: [0.62, 0.64, 0.66])
    metallic: float = 1.0
    roughness: float = 0.35
    anisotropic: float = 0.6

    def validate(self) -> None:
        if len(self.base_color) != 3:
            raise ConfigError("render.material.base_color must have 3 components")


@dataclass
class RenderConfig:
    """Camera, lighting and output settings for the Blender render."""

    width: int = 1280
    height: int = 960
    samples: int = 128
    camera_azimuth_deg: float = 45.0
    camera_elevation_deg: float = 30.0
    camera_margin: float = 1.15
    background_color: List[float] = field(default_factory=lambda: [0.05, 0.05, 0.06])
    material: MaterialConfig = field(default_factory=MaterialConfig)
    # Additional close-up "tool-mounted" render: a tilted top-down orthographic
    # view centered on the weld path midpoint, with a square window equal to
    # indentation.radius * tool_view_window_factor (default 4x gives weld
    # footprint diameter = 2*radius and field width = 4*radius).
    tool_view: bool = True
    tool_view_window_factor: float = 4.0
    # Viewing angle from vertical-down (ZYX Y=180 in the tool-view convention;
    # 0 = straight down, 30 = oblique view with Blender ZYX (0, 30, 90)).
    tool_view_camera_tilt_deg: float = 30.0
    # Camera standoff along +Z in model coordinates (Y is always 0).
    tool_view_camera_height_mm: float = 100.0
    # Brushed/cast metal appearance for the close-up (avoids mirror hotspots).
    tool_view_roughness: float = 0.72
    tool_view_metallic: float = 0.55
    tool_view_anisotropic: float = 0.0

    def validate(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ConfigError("render.width and render.height must be > 0")
        if self.samples <= 0:
            raise ConfigError("render.samples must be > 0")
        if len(self.background_color) != 3:
            raise ConfigError("render.background_color must have 3 components")
        if self.tool_view_window_factor <= 0:
            raise ConfigError("render.tool_view_window_factor must be > 0")
        if not 0.0 <= self.tool_view_camera_tilt_deg < 90.0:
            raise ConfigError(
                "render.tool_view_camera_tilt_deg must be in [0, 90) "
                f"(got {self.tool_view_camera_tilt_deg})"
            )
        if self.tool_view_camera_height_mm <= 0:
            raise ConfigError(
                "render.tool_view_camera_height_mm must be > 0 "
                f"(got {self.tool_view_camera_height_mm})"
            )
        if not 0.0 <= self.tool_view_roughness <= 1.0:
            raise ConfigError("render.tool_view_roughness must be in [0, 1]")
        if not 0.0 <= self.tool_view_metallic <= 1.0:
            raise ConfigError("render.tool_view_metallic must be in [0, 1]")
        if not 0.0 <= self.tool_view_anisotropic <= 1.0:
            raise ConfigError("render.tool_view_anisotropic must be in [0, 1]")
        self.material.validate()


@dataclass
class OutputConfig:
    """Output filenames and export tolerances."""

    basename: str = "fsw_part"
    export_step: bool = True
    export_stl: bool = True
    stl_tolerance: float = 0.01
    stl_angular_tolerance: float = 0.1

    def validate(self) -> None:
        if not self.basename:
            raise ConfigError("output.basename must not be empty")
        if self.stl_tolerance <= 0:
            raise ConfigError("output.stl_tolerance must be > 0")
        if self.stl_angular_tolerance <= 0:
            raise ConfigError("output.stl_angular_tolerance must be > 0")


@dataclass
class PartConfig:
    """Full part description loaded from JSON."""

    name: str
    plate: PlateConfig
    indentation: IndentConfig
    void: VoidConfig = field(default_factory=VoidConfig)
    flash: FlashConfig = field(default_factory=FlashConfig)
    burs: BursConfig = field(default_factory=BursConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    def validate(self) -> None:
        self.plate.validate()
        self.indentation.validate(self.plate)
        self.void.validate(self.plate)
        self.flash.validate(self.plate)
        self.burs.validate(self.plate)
        self.render.validate()
        self.output.validate()


def _require(data: dict, key: str, context: str) -> Any:
    if key not in data:
        raise ConfigError(f"missing required field '{context}.{key}'")
    return data[key]


def _from_dict(cls, data: dict):
    """Build a dataclass from a dict, ignoring unknown keys."""
    valid = {f.name for f in cls.__dataclass_fields__.values()}
    unknown = set(data) - valid
    if unknown:
        raise ConfigError(f"unknown field(s) for {cls.__name__}: {sorted(unknown)}")
    return cls(**{k: v for k, v in data.items() if k in valid})


def _load_voids(raw: dict) -> VoidConfig:
    """Load void config, supporting both nested and legacy single-block formats."""
    if not raw:
        return VoidConfig()

    # Legacy: { "mode": "continuous"|"intermittent"|"none", ...params }
    if "mode" in raw:
        mode = raw["mode"]
        if mode == "none":
            return VoidConfig()
        if mode not in _VOID_KINDS:
            raise ConfigError(
                f"void.mode must be one of ('none',) + {_VOID_KINDS} (got '{mode}')"
            )
        layer = _from_dict(VoidLayerConfig, {k: v for k, v in raw.items() if k != "mode"})
        if mode == "continuous":
            return VoidConfig(continuous=layer)
        return VoidConfig(intermittent=layer)

    unknown = set(raw) - {"continuous", "intermittent"}
    if unknown:
        raise ConfigError(f"unknown field(s) for VoidConfig: {sorted(unknown)}")

    continuous = (
        _from_dict(VoidLayerConfig, raw["continuous"]) if "continuous" in raw else None
    )
    intermittent = (
        _from_dict(VoidLayerConfig, raw["intermittent"]) if "intermittent" in raw else None
    )
    return VoidConfig(continuous=continuous, intermittent=intermittent)


def _load_burs(raw: dict) -> BursConfig:
    """Load burs config, supporting both nested and legacy single-block formats."""
    if not raw:
        return BursConfig()

    # Legacy: { "mode": "attached"|"loose"|"none", ...params }
    if "mode" in raw:
        mode = raw["mode"]
        if mode == "none":
            return BursConfig()
        if mode not in _BURS_MODES:
            raise ConfigError(
                f"burs.mode must be one of ('none',) + {_BURS_MODES} (got '{mode}')"
            )
        layer = _from_dict(BurLayerConfig, {k: v for k, v in raw.items() if k != "mode"})
        if mode == "attached":
            return BursConfig(attached=layer)
        return BursConfig(loose=layer)

    # Nested: { "attached": {...}, "loose": {...} }
    unknown = set(raw) - {"attached", "loose"}
    if unknown:
        raise ConfigError(f"unknown field(s) for BursConfig: {sorted(unknown)}")

    attached = _from_dict(BurLayerConfig, raw["attached"]) if "attached" in raw else None
    loose = _from_dict(BurLayerConfig, raw["loose"]) if "loose" in raw else None
    return BursConfig(attached=attached, loose=loose)


def load_part_config(path: str | Path) -> PartConfig:
    """Load and validate a part configuration from a JSON file."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    name = raw.get("name", path.stem)

    plate = _from_dict(PlateConfig, _require(raw, "plate", "root"))
    indentation = _from_dict(IndentConfig, _require(raw, "indentation", "root"))
    void = _load_voids(raw.get("void", {}))
    flash = _from_dict(FlashConfig, raw.get("flash", {}))
    burs = _load_burs(raw.get("burs", {}))

    render_raw = dict(raw.get("render", {}))
    material_raw = render_raw.pop("material", None)
    render = _from_dict(RenderConfig, render_raw)
    if material_raw is not None:
        render.material = _from_dict(MaterialConfig, material_raw)

    output = _from_dict(OutputConfig, raw.get("output", {}))

    part = PartConfig(
        name=name,
        plate=plate,
        indentation=indentation,
        void=void,
        flash=flash,
        burs=burs,
        render=render,
        output=output,
    )
    part.validate()
    return part
