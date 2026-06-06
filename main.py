"""CLI entry point for the FSW weld surface generator.

Usage:
    python main.py config/example_part.json
    python main.py config/example_part.json --out-dir outputs --no-render

Pipeline: load JSON config -> build CadQuery solid -> export STEP/STL ->
render a photorealistic ISO image via Blender Cycles.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Allow running directly from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from weldgen.config import ConfigError, load_part_config  # noqa: E402
from weldgen.export import export_part  # noqa: E402
from weldgen.geometry import build_part  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an FSW butt-joint CAD part and photorealistic render from JSON."
    )
    parser.add_argument("config", help="Path to the part JSON config file.")
    parser.add_argument(
        "--out-dir",
        default="outputs",
        help="Directory for generated STEP/STL/PNG files (default: outputs).",
    )
    parser.add_argument(
        "--no-render",
        action="store_true",
        help="Skip the Blender render step (CAD export only).",
    )
    parser.add_argument(
        "--blender",
        default=None,
        help="Path to the blender executable (default: search PATH).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        part = load_part_config(args.config)
    except ConfigError as err:
        print(f"[config] error: {err}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    print(f"[1/3] Building geometry for '{part.name}' "
          f"({part.indentation.count} indentations)...")
    t0 = time.time()
    shape = build_part(part)
    print(f"      done in {time.time() - t0:.1f}s")

    print(f"[2/3] Exporting CAD files to {out_dir}/ ...")
    written = export_part(shape, out_dir, part.output)
    for fmt, path in written.items():
        print(f"      {fmt.upper()}: {path}")

    if args.no_render:
        print("[3/3] Render skipped (--no-render).")
        return 0

    stl_path = written.get("stl")
    if stl_path is None:
        print("[3/3] Render skipped: STL export is disabled in config.",
              file=sys.stderr)
        return 1

    # Imported lazily so CAD-only runs don't require the render dependencies.
    from weldgen.render import RenderError, render_part

    print("[3/3] Rendering with Blender Cycles...")
    try:
        png_paths = render_part(
            stl_path=stl_path,
            out_dir=out_dir,
            basename=part.output.basename,
            render=part.render,
            indentation=part.indentation,
            plate=part.plate,
            blender_exe=args.blender,
        )
    except RenderError as err:
        print(f"[render] error: {err}", file=sys.stderr)
        return 1
    for png_path in png_paths:
        print(f"      PNG: {png_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
