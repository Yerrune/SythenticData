"""Blender (bpy) render script - runs inside Blender's bundled Python.

Invoked as:
    blender -b --factory-startup -P scripts/blender_render.py -- <render_config.json>

Reads the render config JSON (written by weldgen.render), imports the STL,
assigns a metallic Principled BSDF material, sets up softbox lighting and an
ISO-perspective camera framed to the part, and renders a PNG with Cycles.
"""

import json
import math
import sys

import bpy
import mathutils


def get_config_path():
    argv = sys.argv
    if "--" not in argv:
        raise SystemExit("blender_render.py: expected '-- <config.json>' argument")
    extra = argv[argv.index("--") + 1:]
    if not extra:
        raise SystemExit("blender_render.py: missing config path after '--'")
    return extra[0]


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    # Purge orphaned data so --factory-startup defaults don't linger.
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras):
        for item in list(block):
            block.remove(item)


def import_stl(path):
    before = set(bpy.data.objects)
    if hasattr(bpy.ops.wm, "stl_import"):
        bpy.ops.wm.stl_import(filepath=path)
    else:  # legacy importer (older Blender)
        bpy.ops.import_mesh.stl(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    if not meshes:
        raise SystemExit(f"blender_render.py: no mesh imported from {path}")

    # Join into a single object if the STL contained several solids.
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active

    # Record the original (mm) bounds centre before recentring, so world
    # coordinates (e.g. the weld centre) can be mapped into the normalized
    # scene later: normalized = (world - orig_center) * scale_factor.
    omins, omaxs = world_bounds(obj)
    orig_center = (omins + omaxs) * 0.5

    # Center geometry on the origin, then shade-smooth the curved marks.
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
    obj.location = (0.0, 0.0, 0.0)
    bpy.ops.object.shade_smooth()
    return obj, orig_center


def normalize_scale(obj, target_max_dim=2.0):
    """Scale the object so its largest dimension equals ``target_max_dim``.

    Working at a fixed (human) scale keeps lighting/camera values independent
    of the part's real-world millimetre size. Returns the applied scale factor.
    """
    mins, maxs = world_bounds(obj)
    max_dim = max((maxs - mins))
    if max_dim <= 0:
        return 1.0
    factor = target_max_dim / max_dim
    obj.scale = (factor, factor, factor)
    bpy.context.view_layer.update()
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return factor


def world_bounds(obj):
    corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
    mins = mathutils.Vector((min(c[i] for c in corners) for i in range(3)))
    maxs = mathutils.Vector((max(c[i] for c in corners) for i in range(3)))
    return mins, maxs


def make_metal_material(mat_cfg, name="FSW_Metal"):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    color = mat_cfg["base_color"]
    bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Metallic"].default_value = mat_cfg["metallic"]
    bsdf.inputs["Roughness"].default_value = mat_cfg["roughness"]
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.35
    elif "Specular" in bsdf.inputs:
        bsdf.inputs["Specular"].default_value = 0.35
    if "Anisotropic" in bsdf.inputs:
        bsdf.inputs["Anisotropic"].default_value = mat_cfg["anisotropic"]
    if "Anisotropic Rotation" in bsdf.inputs:
        # Brush strokes run along the weld / travel direction (world +X).
        bsdf.inputs["Anisotropic Rotation"].default_value = 0.0
    return mat


def add_area_light(name, location, target, energy, size):
    light_data = bpy.data.lights.new(name=name, type="AREA")
    light_data.energy = energy
    light_data.size = size
    light = bpy.data.objects.new(name, light_data)
    bpy.context.collection.objects.link(light)
    light.location = location
    direction = (mathutils.Vector(target) - mathutils.Vector(location)).normalized()
    light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return light


def setup_lighting(center, radius, background_color):
    world = bpy.data.worlds.new("FSW_World")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    # A mid-grey environment gives the metal something bright to reflect so it
    # reads as metal rather than black; the configured colour tints it.
    bg.inputs["Color"].default_value = (0.35, 0.36, 0.38, 1.0)
    bg.inputs["Strength"].default_value = 0.6
    bpy.context.scene.world = world

    c = mathutils.Vector(center)
    d = radius
    # Energies are tuned for the normalized ~2-unit scene size. A low-elevation
    # key light grazes across the transverse tool-mark ridges so the shallow
    # weld relief casts highlights and shadows; softbox size keeps it smooth.
    add_area_light("Key", c + mathutils.Vector((d * 2.2, -d * 0.6, d * 0.9)),
                   center, 600.0, d * 2.0)
    add_area_light("Fill", c + mathutils.Vector((-d * 2.0, -d * 1.2, d * 1.4)),
                   center, 150.0, d * 3.0)
    add_area_light("Rim", c + mathutils.Vector((-d * 0.6, d * 2.4, d * 1.6)),
                   center, 300.0, d * 1.6)


def add_ground(center, mins, radius, color):
    bpy.ops.mesh.primitive_plane_add(size=radius * 12.0,
                                     location=(center[0], center[1], mins[2]))
    ground = bpy.context.view_layer.objects.active
    ground.name = "Ground"
    mat = bpy.data.materials.new("Ground")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Roughness"].default_value = 0.9
    ground.data.materials.append(mat)
    return ground


def setup_camera(center, radius, cfg):
    az = math.radians(cfg["camera_azimuth_deg"])
    el = math.radians(cfg["camera_elevation_deg"])
    direction = mathutils.Vector((
        math.cos(el) * math.cos(az),
        math.cos(el) * math.sin(az),
        math.sin(el),
    ))

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.sensor_fit = "AUTO"
    hfov = cam_data.angle  # default ~ 0.69 rad horizontal FOV

    width = cfg["width"]
    height = cfg["height"]
    aspect = width / height
    # Derive the limiting (vertical) FOV when the image is wider than tall.
    if aspect >= 1.0:
        vfov = 2.0 * math.atan(math.tan(hfov / 2.0) / aspect)
    else:
        vfov = hfov
    half_fov = min(hfov, vfov) / 2.0
    distance = (radius / math.sin(half_fov)) * cfg["camera_margin"]

    cam = bpy.data.objects.new("Camera", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = mathutils.Vector(center) + direction * distance
    look = (mathutils.Vector(center) - cam.location).normalized()
    cam.rotation_euler = look.to_track_quat("-Z", "Y").to_euler()
    return cam


def setup_tool_camera(target, camera_pos, window_norm, camera_tilt_deg):
    """Orthographic close-up camera for the tool-mounted weld view.

    Camera pose follows the tool-view convention supplied by the caller:

    * Position is always ``(center_x, 0, height)`` in model coordinates.
    * Orientation uses ZYX Euler ``(0, 180 - tilt, 0)`` with the camera
      viewing axis taken as local +Z.  That maps to Blender ZYX ``(0, tilt, 90)``
      so the image X axis is parallel to the part Y axis and a 30-degree tilt
      is visibly oblique rather than face-on.

    ``window_norm`` is the orthographic window size (the larger image dimension)
    in normalized scene units.
    """
    cam_data = bpy.data.cameras.new("ToolCam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = window_norm
    cam_data.clip_start = 0.001
    cam_data.clip_end = 1000.0

    cam = bpy.data.objects.new("ToolCam", cam_data)
    bpy.context.collection.objects.link(cam)

    target = mathutils.Vector(target)
    cam.location = mathutils.Vector(camera_pos)
    cam.rotation_mode = "ZYX"
    # Tool-view ZYX (0, 180, 0) down / (0, 150, 0) at 30 deg -> Blender (0, tilt, 90).
    cam.rotation_euler = (
        0.0,
        math.radians(camera_tilt_deg),
        math.radians(90.0),
    )
    bpy.context.view_layer.update()

    # Recentre the weld point in the orthographic frame. With a fixed (non
    # look-at) orientation the target sits far off the optical axis, so it must
    # be pulled back with lens shift. Solve the linear shift -> NDC mapping
    # numerically; this is robust to Blender's sensor-fit / aspect conventions
    # (vertical NDC scales with aspect^2, which trips up closed-form formulas).
    from bpy_extras.object_utils import world_to_camera_view

    scene = bpy.context.scene

    def _ndc(sx, sy):
        cam.data.shift_x = sx
        cam.data.shift_y = sy
        bpy.context.view_layer.update()
        v = world_to_camera_view(scene, cam, target)
        return v.x, v.y

    base = _ndc(0.0, 0.0)
    dxr = _ndc(1.0, 0.0)
    dyr = _ndc(0.0, 1.0)
    kx = dxr[0] - base[0]
    ky = dyr[1] - base[1]
    shift_x = (0.5 - base[0]) / kx if abs(kx) > 1e-9 else 0.0
    shift_y = (0.5 - base[1]) / ky if abs(ky) > 1e-9 else 0.0
    cam.data.shift_x = shift_x
    cam.data.shift_y = shift_y
    bpy.context.view_layer.update()
    return cam


def prepare_tool_view_scene(ground):
    """Hide the ISO ground plane and use a soft ambient world for the close-up."""
    if ground is not None:
        ground.hide_render = True
    world = bpy.context.scene.world
    if world and world.use_nodes:
        bg = world.node_tree.nodes.get("Background")
        bg.inputs["Color"].default_value = (0.20, 0.21, 0.23, 1.0)
        bg.inputs["Strength"].default_value = 0.22


def setup_tool_lighting(target, camera_pos, window_norm):
    """Grazing 'raking' light that reveals the shallow weld ring relief.

    The crescent tool marks run transverse (part Y) and repeat along the travel
    direction (part X). A low-elevation key light raking along +X throws a thin
    shadow off each ridge so the rings read clearly on the metal. A dim overhead
    fill from the camera side and a weak opposite rake keep the valleys from
    going black without flattening the directional contrast.
    """
    for obj in list(bpy.data.objects):
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj, do_unlink=True)

    t = mathutils.Vector(target)
    cam = mathutils.Vector(camera_pos)
    w = max(window_norm, 1e-6)
    # Energies scale with window area so brightness is independent of weld size.
    intensity = (w / 1.0) ** 2

    # Small, dim overhead fill: just lifts the valleys. Kept small so its
    # specular reflection in the metal does not wash out the ring relief.
    add_area_light("ToolFill", cam.copy(), tuple(t), 3.0 * intensity, w * 2.0)

    # Two opposing low grazing rakes (~11 deg) along +/-X rake across the ring
    # ridges; near-balanced energies light both faces of each crescent arc so
    # the onion-ring pattern reads across the whole weld footprint.
    add_area_light(
        "ToolRakeKey",
        t + mathutils.Vector((w * 5.0, 0.0, w * 1.0)),
        tuple(t),
        60.0 * intensity,
        w * 1.2,
    )
    add_area_light(
        "ToolRakeBack",
        t + mathutils.Vector((-w * 5.0, 0.0, w * 1.0)),
        tuple(t),
        44.0 * intensity,
        w * 1.2,
    )


def configure_render(cfg):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    try:
        scene.cycles.device = "CPU"
    except Exception:
        pass
    scene.cycles.samples = cfg["samples"]
    scene.cycles.use_denoising = True
    scene.render.resolution_x = cfg["width"]
    scene.render.resolution_y = cfg["height"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    # Color management: filmic-like view transform for a natural metal look.
    try:
        scene.view_settings.view_transform = "AgX"
    except Exception:
        pass


def render_to(cam, filepath):
    scene = bpy.context.scene
    scene.camera = cam
    scene.render.filepath = filepath
    bpy.ops.render.render(write_still=True)
    print(f"[blender] wrote {filepath}")


def main():
    cfg_path = get_config_path()
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    clear_scene()
    obj, orig_center = import_stl(cfg["stl_path"])
    factor = normalize_scale(obj, target_max_dim=2.0)

    mins, maxs = world_bounds(obj)
    center = (mins + maxs) * 0.5
    diag = (maxs - mins).length
    radius = max(diag * 0.5, 1e-6)

    mat = make_metal_material(cfg["material"])
    obj.data.materials.clear()
    obj.data.materials.append(mat)

    setup_lighting(tuple(center), radius, cfg["background_color"])
    ground = add_ground(tuple(center), mins, radius, cfg["background_color"])
    configure_render(cfg)

    # ISO perspective view of the whole part.
    iso_cam = setup_camera(tuple(center), radius, cfg)
    render_to(iso_cam, cfg["out_png"])

    # Optional tool-mounted close-up: tilted top-down view of the weld centre.
    tv = cfg.get("tool_view")
    if tv and tv.get("enabled"):
        plate_top_z = tv.get("plate_top_z", maxs.z / factor + orig_center[2])
        center_x = tv["center_x"]
        center_y = tv.get("center_y", 0.0)
        camera_height_mm = tv.get("camera_height_mm", plate_top_z + 50.0)
        target = (
            (center_x - orig_center[0]) * factor,
            (center_y - orig_center[1]) * factor,
            (plate_top_z - orig_center[2]) * factor,
        )
        camera_pos = (
            (center_x - orig_center[0]) * factor,
            (center_y - orig_center[1]) * factor,
            (camera_height_mm - orig_center[2]) * factor,
        )
        window_norm = tv["window_mm"] * factor
        prepare_tool_view_scene(ground)
        setup_tool_lighting(target, camera_pos, window_norm)
        tool_mat_cfg = {**cfg["material"], **tv.get("material", {})}
        obj.data.materials.clear()
        obj.data.materials.append(make_metal_material(tool_mat_cfg, "FSW_Metal_ToolView"))
        tool_cam = setup_tool_camera(
            target,
            camera_pos,
            window_norm,
            tv.get("camera_tilt_deg", 0.0),
        )
        render_to(tool_cam, tv["out_png"])


if __name__ == "__main__":
    main()
