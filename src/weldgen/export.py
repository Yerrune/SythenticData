"""Export a CadQuery solid to STEP and STL."""

from __future__ import annotations

from pathlib import Path

import cadquery as cq

from .config import OutputConfig


def export_step(shape: cq.Workplane, path: str | Path) -> Path:
    """Export the solid to a STEP file (exact B-rep, for downstream CAD)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(shape, str(path), exportType="STEP")
    return path


def export_stl(
    shape: cq.Workplane,
    path: str | Path,
    tolerance: float = 0.01,
    angular_tolerance: float = 0.1,
) -> Path:
    """Export the solid to an STL mesh for rendering.

    ``tolerance`` is the linear deflection (mm) and ``angular_tolerance`` the
    angular deflection (rad); smaller values give a finer mesh and smoother
    curved tool marks in the render at the cost of file size.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(
        shape,
        str(path),
        exportType="STL",
        tolerance=tolerance,
        angularTolerance=angular_tolerance,
    )
    return path


def export_part(
    shape: cq.Workplane,
    out_dir: str | Path,
    output: OutputConfig,
) -> dict[str, Path]:
    """Export STEP and/or STL according to the output config.

    Returns a mapping of format name to written path.
    """
    out_dir = Path(out_dir)
    written: dict[str, Path] = {}

    if output.export_step:
        written["step"] = export_step(shape, out_dir / f"{output.basename}.step")
    if output.export_stl:
        written["stl"] = export_stl(
            shape,
            out_dir / f"{output.basename}.stl",
            tolerance=output.stl_tolerance,
            angular_tolerance=output.stl_angular_tolerance,
        )
    return written
