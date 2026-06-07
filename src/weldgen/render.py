"""Invoke Blender (Cycles) as a subprocess to render the exported STL.

CadQuery runs in this (system/venv) Python while Blender uses its own bundled
Python. They communicate purely through files: this module writes a render
config JSON and the exported STL path, then launches Blender headless with
``scripts/blender_render.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from glob import glob
from pathlib import Path

from .config import IndentConfig, PlateConfig, RenderConfig

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BLENDER_SCRIPT = _REPO_ROOT / "scripts" / "blender_render.py"

# Default location for the X11 shim libraries (libSM/libICE) that a portable
# Blender build may need on a headless machine. Overridable via env var.
_DEFAULT_EXTRA_LIB = Path.home() / ".local" / "opt" / "blender-extralibs"


class RenderError(RuntimeError):
    """Raised when Blender cannot be located or the render fails."""


def find_blender(explicit: str | None = None) -> str:
    """Locate a usable Blender executable.

    Search order: explicit path -> PATH -> common local install locations.
    """
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    on_path = shutil.which("blender")
    if on_path:
        candidates.append(on_path)
    candidates.append(str(Path.home() / ".local" / "bin" / "blender"))
    candidates.extend(sorted(glob(str(Path.home() / ".local" / "opt" / "blender-*" / "blender"))))
    candidates.append("/snap/bin/blender")

    for cand in candidates:
        if cand and Path(cand).exists():
            return cand
    raise RenderError(
        "could not find a 'blender' executable. Install Blender (e.g. "
        "'snap install blender --classic' or a portable build) or pass "
        "--blender /path/to/blender."
    )


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    extra = os.environ.get("BLENDER_EXTRA_LIB_DIR", str(_DEFAULT_EXTRA_LIB))
    if extra and Path(extra).is_dir():
        env["LD_LIBRARY_PATH"] = os.pathsep.join(
            [extra, env.get("LD_LIBRARY_PATH", "")]
        ).strip(os.pathsep)
    return env


def render_part(
    stl_path: str | Path,
    out_dir: str | Path,
    basename: str,
    render: RenderConfig,
    indentation: IndentConfig | None = None,
    plate: PlateConfig | None = None,
    blender_exe: str | None = None,
) -> list[Path]:
    """Render ``stl_path`` with Blender Cycles.

    Always renders the ISO view (``<basename>.png``). If ``render.tool_view`` is
    enabled and the indentation/plate geometry is supplied, also renders a
    tilted top-down "tool-mounted" close-up (``<basename>_toolview.png``).

    Returns the list of generated PNG paths (ISO first).
    """
    blender = find_blender(blender_exe)
    if not _BLENDER_SCRIPT.is_file():
        raise RenderError(f"blender render script missing: {_BLENDER_SCRIPT}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stl_path = Path(stl_path).resolve()
    png_path = (out_dir / f"{basename}.png").resolve()
    cfg_path = (out_dir / f"{basename}_render.json").resolve()

    cfg = {
        "stl_path": str(stl_path),
        "out_png": str(png_path),
        "width": render.width,
        "height": render.height,
        "samples": render.samples,
        "camera_azimuth_deg": render.camera_azimuth_deg,
        "camera_elevation_deg": render.camera_elevation_deg,
        "camera_margin": render.camera_margin,
        "background_color": list(render.background_color),
        "material": {
            "base_color": list(render.material.base_color),
            "metallic": render.material.metallic,
            "roughness": render.material.roughness,
            "anisotropic": render.material.anisotropic,
        },
    }

    expected = [png_path]
    tool_png_path: Path | None = None
    if render.tool_view and indentation is not None and plate is not None:
        tool_png_path = (out_dir / f"{basename}_toolview.png").resolve()
        weld_center_x = (
            indentation.x_start + indentation.count * indentation.pitch / 2.0
        )
        cfg["tool_view"] = {
            "enabled": True,
            "out_png": str(tool_png_path),
            # Square window centered on the weld; size = radius * factor (mm).
            "window_mm": indentation.radius * render.tool_view_window_factor,
            # Oblique top-down view in the tool ZYX convention.
            "camera_tilt_deg": render.tool_view_camera_tilt_deg,
            "camera_height_mm": render.tool_view_camera_height_mm,
            # Weld-centre look-at point on the plate top, in model (mm) coords.
            "center_x": weld_center_x,
            "center_y": 0.0,
            "plate_top_z": plate.thickness,
            # Brushed/cast metal overrides for the close-up render.
            "material": {
                "base_color": list(render.material.base_color),
                "metallic": render.tool_view_metallic,
                "roughness": render.tool_view_roughness,
                "anisotropic": render.tool_view_anisotropic,
            },
        }
        expected.append(tool_png_path)

    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    cmd = [
        blender,
        "-b",
        "--factory-startup",
        "-P",
        str(_BLENDER_SCRIPT),
        "--",
        str(cfg_path),
    ]
    proc = subprocess.run(
        cmd,
        env=_build_env(),
        capture_output=True,
        text=True,
    )

    missing = [p for p in expected if not p.exists()]
    if proc.returncode != 0 or missing:
        tail = "\n".join((proc.stdout + "\n" + proc.stderr).strip().splitlines()[-25:])
        raise RenderError(
            f"Blender render failed (exit {proc.returncode}).\n--- blender output ---\n{tail}"
        )
    return expected
