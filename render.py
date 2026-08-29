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
        else {"hdri": "warehouse_fair", "strength": 0.85, "exposure": 1}
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
scene.cycles.max_bounces = 8
scene.cycles.sample_clamp_indirect = 5.0
scene.view_settings.exposure = float(world_cfg.get("exposure", 1))

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
cam_data.lens_unit = "FOV"
cam_data.angle = math.radians(42)
cam_obj = bpy.data.objects.new("HeroCam", cam_data)
scene.collection.objects.link(cam_obj)
scene.camera = cam_obj

pos = Vector(hero["position"])
target = Vector(hero["target"])
cam_obj.location = pos
direction = target - pos
if direction.length < 1e-6:
    direction = Vector((0, 0, -1))
cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

world = bpy.data.worlds.new("FairHall")
world.use_nodes = True
nodes = world.node_tree.nodes
links = world.node_tree.links
nodes.clear()
bg = nodes.new("ShaderNodeBackground")
out = nodes.new("ShaderNodeOutputWorld")
bg.inputs["Color"].default_value = (0.78, 0.82, 0.86, 1.0)
bg.inputs["Strength"].default_value = float(world_cfg.get("strength", 0.85))
links.new(bg.outputs["Background"], out.inputs["Surface"])
scene.world = world

def add_area(name, loc, size, energy, color):
    light = bpy.data.lights.new(name, type="AREA")
    light.shape = "RECTANGLE"
    light.size = size[0]
    light.size_y = size[1]
    light.energy = energy
    light.color = color
    obj = bpy.data.objects.new(name, light)
    obj.location = loc
    scene.collection.objects.link(obj)
    return obj

add_area("HallKey", (pos.x, pos.y + 6.5, pos.z), (10.0, 6.0), 450.0, (0.95, 0.97, 1.0))
add_area("HallFill", (target.x - 3.0, 4.2, target.z + 1.5), (4.0, 3.0), 180.0, (0.84, 0.88, 0.92))

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
