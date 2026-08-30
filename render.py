#!/usr/bin/env python3
"""Headless Cycles still from a cycles pack ZIP (booth.glb + cameras.json + world.json)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PACK_GLB = "booth.glb"
PACK_CAMERAS = "cameras.json"
PACK_WORLD = "world.json"


def extract_pack(pack_path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pack_path, "r") as zf:
        zf.extractall(dest)
    glb = dest / PACK_GLB
    if not glb.is_file():
        raise FileNotFoundError(f"pack missing {PACK_GLB}")
    return dest


def load_pack_meta(pack_dir: Path) -> dict:
    cameras = json.loads((pack_dir / PACK_CAMERAS).read_text(encoding="utf-8"))
    world_path = pack_dir / PACK_WORLD
    world = (
        json.loads(world_path.read_text(encoding="utf-8"))
        if world_path.is_file()
        else {
            "hdri": "warehouse_fair",
            "strength": 0.1,
            "exposure": 0.02,
            "color": [0.034, 0.037, 0.044],
        }
    )
    hero = cameras.get("hero") or {}
    if "position" not in hero or "target" not in hero:
        raise ValueError("cameras.json must include hero.position and hero.target")
    return {"cameras": cameras, "world": world, "glb": str(pack_dir / PACK_GLB)}


def blender_script_source() -> str:
    return r"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector

argv = sys.argv
sep = argv.index("--") if "--" in argv else len(argv)
args = argv[sep + 1 :]
meta_path = args[0]
out_path = args[1]
samples = int(args[2])
width = int(args[3])
height = int(args[4])

meta = json.loads(open(meta_path, "r", encoding="utf-8").read())
glb = meta["glb"]
hero = meta["cameras"]["hero"]
world_cfg = meta["world"]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.render.filepath = out_path
scene.render.image_settings.file_format = "PNG"
scene.render.resolution_x = width
scene.render.resolution_y = height
scene.render.resolution_percentage = 100
scene.render.film_transparent = False
scene.cycles.samples = samples
scene.cycles.use_denoising = True
scene.cycles.denoiser = "OPENIMAGEDENOISE"
scene.cycles.use_adaptive_sampling = True
scene.cycles.adaptive_threshold = 0.01
scene.cycles.max_bounces = 12
scene.cycles.diffuse_bounces = 4
scene.cycles.glossy_bounces = 6
scene.cycles.transmission_bounces = 8
scene.cycles.transparent_max_bounces = 8
scene.cycles.caustics_reflective = False
scene.cycles.caustics_refractive = False
scene.cycles.sample_clamp_direct = 12.0
scene.cycles.sample_clamp_indirect = 8.0
scene.render.dither_intensity = 1.0
for vt in (str(world_cfg.get("viewTransform", "AgX")), "AgX", "Filmic", "Standard"):
    try:
        scene.view_settings.view_transform = vt
        break
    except Exception:
        continue
for look in (str(world_cfg.get("look", "Punchy")), "Punchy", "Medium High Contrast", "None"):
    try:
        scene.view_settings.look = look
        break
    except Exception:
        continue
scene.view_settings.exposure = float(world_cfg.get("exposure", 0.02))
scene.view_settings.gamma = 1.0

prefs = bpy.context.preferences.addons.get("cycles")
if prefs:
    cprefs = prefs.preferences
    try:
        cprefs.compute_device_type = "OPTIX"
        scene.cycles.device = "GPU"
        cprefs.get_devices()
        for device in cprefs.devices:
            device.use = True
    except Exception:
        try:
            cprefs.compute_device_type = "CUDA"
            scene.cycles.device = "GPU"
        except Exception:
            scene.cycles.device = "CPU"

cam_data = bpy.data.cameras.new("HeroCam")
cam_data.sensor_fit = "VERTICAL"
cam_data.lens_unit = "FOV"
cam_data.angle = math.radians(float(hero.get("fov") or 55))
cam_data.clip_start = 0.15
cam_data.clip_end = 90
cam_obj = bpy.data.objects.new("HeroCam", cam_data)
scene.collection.objects.link(cam_obj)
scene.camera = cam_obj

# cameras.json is already Blender Z-up (pack converts from Three/glTF Y-up).
pos = Vector(hero["position"])
target = Vector(hero["target"])
cam_obj.location = pos
direction = target - pos
if direction.length < 1e-6:
    direction = Vector((0, 0, -1))
cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
use_dof = world_cfg.get("dof", True) not in (False, 0, "0")
cam_data.dof.use_dof = bool(use_dof)
cam_data.dof.focus_distance = float(world_cfg.get("focusDistance") or direction.length)
cam_data.dof.aperture_fstop = float(world_cfg.get("fStop", 5.6))
cam_data.dof.aperture_blades = 6

world = bpy.data.worlds.new("FairHall")
world.use_nodes = True
nodes = world.node_tree.nodes
links = world.node_tree.links
nodes.clear()
bg = nodes.new("ShaderNodeBackground")
out = nodes.new("ShaderNodeOutputWorld")
col = world_cfg.get("color", [0.07, 0.075, 0.085])
if isinstance(col, (list, tuple)) and len(col) >= 3:
    bg.inputs["Color"].default_value = (float(col[0]), float(col[1]), float(col[2]), 1.0)
else:
    bg.inputs["Color"].default_value = (0.07, 0.075, 0.085, 1.0)
bg.inputs["Strength"].default_value = float(world_cfg.get("strength", 0.16))
links.new(bg.outputs["Background"], out.inputs["Surface"])
scene.world = world

for obj in bpy.data.objects:
    if obj.type == "MESH" and obj.data:
        if hasattr(obj.data, "use_auto_smooth"):
            obj.data.use_auto_smooth = False
        for poly in obj.data.polygons:
            poly.use_smooth = True

def add_area(name, loc, size, energy, color):
    light = bpy.data.lights.new(name, type="AREA")
    light.shape = "RECTANGLE"
    light.size = size[0]
    light.size_y = size[1]
    light.energy = energy
    light.color = color
    if hasattr(light, "spread"):
        light.spread = math.radians(160)
    obj = bpy.data.objects.new(name, light)
    obj.location = loc
    scene.collection.objects.link(obj)
    return obj

def add_spot(name, loc, energy, color):
    light = bpy.data.lights.new(name, type="SPOT")
    light.energy = energy
    light.color = color
    light.spot_size = math.radians(52)
    light.spot_blend = 0.62
    light.shadow_soft_size = 0.22
    obj = bpy.data.objects.new(name, light)
    obj.location = loc
    scene.collection.objects.link(obj)
    return obj

key_energy = float(world_cfg.get("keyEnergy", 170))
fill_energy = float(world_cfg.get("fillEnergy", 38))
rim_energy = float(world_cfg.get("rimEnergy", 70))
spot_energy = float(world_cfg.get("spotEnergy", 95))
add_area("HallKey", (target.x, target.y, 5.55), (5.2, 3.8), key_energy, (1.0, 0.97, 0.93))
add_area("HallFill", (target.x - 2.6, target.y + 1.4, 4.4), (2.8, 2.1), fill_energy, (0.78, 0.84, 0.92))
add_area("HallRim", (target.x + 2.4, target.y - 3.1, 4.9), (2.0, 1.5), rim_energy, (0.72, 0.78, 0.9))

spot_i = 0
for obj in list(bpy.data.objects):
    if obj.type != "MESH" or "hall-spot" not in obj.name:
        continue
    loc = obj.matrix_world.translation.copy()
    add_spot("HallSpot%d" % spot_i, loc, spot_energy, (1.0, 0.96, 0.9))
    spot_i += 1

scene.use_nodes = True
scene.render.use_compositing = True
tree = scene.node_tree
tree.nodes.clear()
rl = tree.nodes.new("CompositorNodeRLayers")
current = rl.outputs["Image"]

glare_amt = float(world_cfg.get("glare", 0.28))
if glare_amt > 0:
    glare = tree.nodes.new("CompositorNodeGlare")
    try:
        glare.glare_type = "FOG_GLOW"
    except Exception:
        pass
    glare.quality = "MEDIUM"
    glare.mix = max(-0.92, -1.0 + glare_amt * 0.35)
    glare.threshold = 0.82
    if "Size" in glare.inputs:
        glare.inputs["Size"].default_value = 7
    tree.links.new(current, glare.inputs["Image"])
    current = glare.outputs["Image"]

hs = tree.nodes.new("CompositorNodeHueSat")
hs.inputs["Saturation"].default_value = float(world_cfg.get("saturation", 1.03))
tree.links.new(current, hs.inputs["Image"])
current = hs.outputs["Image"]

bc = tree.nodes.new("CompositorNodeBrightContrast")
bc.inputs["Contrast"].default_value = float(world_cfg.get("contrast", 0.06))
tree.links.new(current, bc.inputs["Image"])
current = bc.outputs["Image"]

try:
    ld = tree.nodes.new("CompositorNodeLensdist")
    ld.inputs["Distort"].default_value = -0.012
    ld.inputs["Dispersion"].default_value = 0.01
    tree.links.new(current, ld.inputs["Image"])
    current = ld.outputs["Image"]
except Exception:
    pass

vignette = float(world_cfg.get("vignette", 0.2))
if vignette > 0:
    el = tree.nodes.new("CompositorNodeEllipseMask")
    el.width = 0.92
    el.height = 0.78
    blur = tree.nodes.new("CompositorNodeBlur")
    blur.size_x = 180
    blur.size_y = 140
    tree.links.new(el.outputs[0], blur.inputs["Image"])
    mixv = tree.nodes.new("CompositorNodeMixRGB")
    mixv.blend_type = "MULTIPLY"
    mixv.inputs[0].default_value = vignette
    tree.links.new(current, mixv.inputs[1])
    tree.links.new(blur.outputs["Image"], mixv.inputs[2])
    current = mixv.outputs["Image"]

grain = float(world_cfg.get("grain", 0.028))
if grain > 0:
    noise = bpy.data.textures.new("FilmGrain", type="NOISE")
    nt = tree.nodes.new("CompositorNodeTexture")
    nt.texture = noise
    mixg = tree.nodes.new("CompositorNodeMixRGB")
    mixg.blend_type = "OVERLAY"
    mixg.inputs[0].default_value = grain
    tree.links.new(current, mixg.inputs[1])
    tree.links.new(nt.outputs["Color"], mixg.inputs[2])
    current = mixg.outputs["Image"]

comp = tree.nodes.new("CompositorNodeComposite")
tree.links.new(current, comp.inputs["Image"])

scene.frame_set(1)
bpy.ops.render.render(write_still=True)
"""


def write_blender_script(path: Path) -> Path:
    path.write_text(blender_script_source(), encoding="utf-8")
    return path


def find_blender() -> str:
    env = os.environ.get("BLENDER_BIN")
    if env and Path(env).exists():
        return env
    found = shutil.which("blender")
    if found:
        return found
    raise FileNotFoundError("Blender not found — set BLENDER_BIN")


def render_pack(
    pack_path: Path,
    out_path: Path,
    samples: int = 128,
    resolution: tuple[int, int] = (1920, 1080),
    blender_bin: str | None = None,
) -> Path:
    work = Path(tempfile.mkdtemp(prefix="cycles-pack-"))
    try:
        pack_dir = extract_pack(pack_path, work / "pack")
        meta = load_pack_meta(pack_dir)
        meta_path = work / "meta.json"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        script_path = write_blender_script(work / "render_inner.py")
        binary = blender_bin or find_blender()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            binary,
            "-b",
            "-P",
            str(script_path),
            "--",
            str(meta_path),
            str(out_path),
            str(samples),
            str(resolution[0]),
            str(resolution[1]),
        ]
        subprocess.run(cmd, check=True)
        if not out_path.is_file():
            raise RuntimeError("Blender finished without writing a PNG")
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a Cycles still from a pack ZIP")
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=Path("still.png"))
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument(
        "--meta-only",
        action="store_true",
        help="Validate the pack and print cameras/world JSON (no Blender)",
    )
    args = parser.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="cycles-meta-"))
    try:
        pack_dir = extract_pack(args.pack, work)
        meta = load_pack_meta(pack_dir)
        if args.meta_only:
            print(json.dumps(meta["cameras"]["hero"]))
            return 0
        render_pack(args.pack, args.out, args.samples, (args.width, args.height))
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
