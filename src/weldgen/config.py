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


_VOID_MODES = ("none", "continuous", "intermittent")


@dataclass
class VoidConfig:
    """A surface void (defect) in the weld, simulated by a row of secondary
    cylinders subtracted from the top surface.

    The void runs along X from ``x_start`` to ``x_end``, with one cutter every
    ``pitch`` mm. Each cutter has a random radius in [``r_min``, ``r_max``] and
    cuts ``depth`` mm into the top surface. Voids typically sit on the
    advancing side, so each cutter is offset in Y by ``y_offset`` (signed;
    positive = advancing side) with a uniform +/- ``y_scatter`` jitter to mimic
    real, irregular void paths.

    Continuity is governed by the radius/pitch relationship:
      - continuous:   neighbouring cutters always overlap. Ensure roughly
                      ``pitch <= 2 * r_min`` (allowing for Y scatter), i.e. the
                      minimum radius is on the order of the pitch.
      - intermittent: neighbouring cutters never touch. Ensure
                      ``2 * r_max < pitch`` so isolated voids are produced.

    ``mode`` records the intent ("none" disables voids) and is used only for
    light-touch validation warnings; the actual geometry follows the numeric
    parameters. ``seed`` makes the random radii/scatter reproducible.
    """

    mode: str = "none"
    x_start: float = 0.0
    x_end: float = 0.0
    r_min: float = 0.05
    r_max: float = 0.2
    pitch: float = 0.2
    depth: float = 0.5
    y_offset: float = 3.0
    y_scatter: float = 0.5
    seed: int = 0

    @property
    def enabled(self) -> bool:
        return self.mode != "none"

    def validate(self, plate: PlateConfig) -> None:
        if self.mode not in _VOID_MODES:
            raise ConfigError(
                f"void.mode must be one of {_VOID_MODES} (got '{self.mode}')"
            )
        if not self.enabled:
            return
        if self.x_end <= self.x_start:
            raise ConfigError(
                f"void.x_end ({self.x_end}) must be > void.x_start ({self.x_start})"
            )
        if self.r_min <= 0:
            raise ConfigError(f"void.r_min must be > 0 (got {self.r_min})")
        if self.r_max < self.r_min:
            raise ConfigError(
                f"void.r_max ({self.r_max}) must be >= void.r_min ({self.r_min})"
            )
        if self.pitch <= 0:
            raise ConfigError(f"void.pitch must be > 0 (got {self.pitch})")
        if self.depth <= 0:
            raise ConfigError(f"void.depth must be > 0 (got {self.depth})")
        if self.depth >= plate.thickness:
            raise ConfigError(
                f"void.depth ({self.depth}) must be < plate.thickness "
                f"({plate.thickness})"
            )
        if self.y_scatter < 0:
            raise ConfigError(f"void.y_scatter must be >= 0 (got {self.y_scatter})")


@dataclass
class FlashConfig:
    """Weld flash: excess material added as a continuous bead on the advancing
    side, built by unioning a row of tilted cylinders along X.

    The bead runs from ``x_start`` to ``x_stop`` with one cylinder every
    ``pitch`` mm (small enough that neighbours always overlap, so the flash is
    continuous - it is never intermittent). Each cylinder is centred at
    ``y_offset`` (0 => auto = the indentation radius, i.e. the edge of the tool
    path on the advancing side) and protrudes ``height`` mm above the top
    surface. The bead radius ramps from zero up to ``flash_width`` and back down
    to zero across the span, controlled by ``ramp_fraction`` (the fraction of
    the span used for each ramp; 0.5 gives a triangular profile, smaller gives a
    trapezoid with a flat plateau).
    """

    enabled: bool = False
    x_start: float = 0.0
    x_stop: float = 0.0
    flash_width: float = 1.5
    height: float = 0.5
    pitch: float = 1.0
    tilt_angle_deg: float = 2.0
    y_offset: float = 0.0
    ramp_fraction: float = 0.3

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
        if self.y_offset < 0:
            raise ConfigError(f"flash.y_offset must be >= 0 (got {self.y_offset})")
        if not (0.0 < self.ramp_fraction <= 0.5):
            raise ConfigError(
                f"flash.ramp_fraction must be in (0, 0.5] (got {self.ramp_fraction})"
            )


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
    # view centered on the weld (X = length/2), with a square window equal to
    # indentation.radius * tool_view_window_factor.
    tool_view: bool = True
    tool_view_window_factor: float = 2.0

    def validate(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ConfigError("render.width and render.height must be > 0")
        if self.samples <= 0:
            raise ConfigError("render.samples must be > 0")
        if len(self.background_color) != 3:
            raise ConfigError("render.background_color must have 3 components")
        if self.tool_view_window_factor <= 0:
            raise ConfigError("render.tool_view_window_factor must be > 0")
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
    render: RenderConfig = field(default_factory=RenderConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    def validate(self) -> None:
        self.plate.validate()
        self.indentation.validate(self.plate)
        self.void.validate(self.plate)
        self.flash.validate(self.plate)
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
    void = _from_dict(VoidConfig, raw.get("void", {}))
    flash = _from_dict(FlashConfig, raw.get("flash", {}))

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
        render=render,
        output=output,
    )
    part.validate()
    return part
