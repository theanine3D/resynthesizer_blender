"""
Headless integration test for the Resynthesizer Blender add-on (Milestone 3).

Run:
  blender --background --factory-startup --python addon/test_addon_blender.py

Scene: an 8x8-face plane UV-mapped over brick.png with a red square "defect"
painted in the middle. The 2x2 center faces are selected; the operator must
resynthesize their UV footprint, removing the defect.

Writes build/blender-fill-before.png and build/blender-fill-after.png.
"""

import os
import sys

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "build")


def _find_brick():
    candidates = [
        os.path.join(ROOT, "test_images", "brick.png"),  # vendored in the repo
        os.path.join(os.path.dirname(ROOT), "resynthesizer-gimp", "Test", "in_images", "brick.png"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(f"brick.png not found in any of: {candidates}")


BRICK = _find_brick()

sys.path.insert(0, HERE)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}  {name}  {detail if not condition else ''}")
    if not condition:
        failures.append(name)


def save_image_copy(image, path):
    image.save_render(path)


print("== register add-on ==")
import resynthesizer_blender
resynthesizer_blender.register()
check("operator registered", hasattr(bpy.ops.paint, "resynthesize"))
check("scene settings registered", hasattr(bpy.context.scene, "resynthesizer"))

print("== build test scene ==")
bpy.ops.mesh.primitive_plane_add()
obj = bpy.context.active_object
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.subdivide(number_cuts=7)  # 8x8 = 64 faces
bpy.ops.object.mode_set(mode='OBJECT')
mesh = obj.data
check("64 faces", len(mesh.polygons) == 64, f"got {len(mesh.polygons)}")

image = bpy.data.images.load(BRICK)
width, height = image.size
channels = image.channels
check("brick loaded 512x512", (width, height) == (512, 512), f"got {width}x{height}")

# Paint a red square defect in the center (pixel rows here are bottom-origin)
pixels = np.empty(width * height * channels, dtype=np.float32)
image.pixels.foreach_get(pixels)
pixels = pixels.reshape(height, width, channels)
original = pixels.copy()
defect = slice(224, 288)
pixels[defect, defect, 0] = 1.0
pixels[defect, defect, 1] = 0.0
pixels[defect, defect, 2] = 0.0
image.pixels.foreach_set(pixels.ravel())
save_image_copy(image, os.path.join(BUILD, "blender-fill-before.png"))

# Paint onto this image directly (IMAGE mode = explicit canvas)
tool_settings = bpy.context.scene.tool_settings
tool_settings.image_paint.mode = 'IMAGE'
tool_settings.image_paint.canvas = image

# Select the 2x2 center faces via their UV centers (each face is 0.125 UV wide;
# centers at 0.4375/0.5625 span UV 0.375..0.625 = pixels 192..320, covering the defect)
uv_layer = mesh.uv_layers.active
loop_uvs = np.empty(len(mesh.loops) * 2, dtype=np.float32)
uv_layer.uv.foreach_get("vector", loop_uvs)
loop_uvs = loop_uvs.reshape(-1, 2)

mesh.use_paint_mask = True
selected_count = 0
for poly in mesh.polygons:
    center = loop_uvs[poly.loop_start : poly.loop_start + poly.loop_total].mean(axis=0)
    poly.select = bool(np.all(np.abs(center - 0.5) < 0.125))
    selected_count += poly.select
check("2x2 center faces selected", selected_count == 4, f"got {selected_count}")

print("== error path: nothing selected ==")
for poly in mesh.polygons:
    poly.select = False
try:
    bpy.ops.paint.resynthesize()
    check("no-selection rejected", False, "operator did not fail")
except RuntimeError as exc:
    check("no-selection rejected", "No faces selected" in str(exc), str(exc))

print("== run resynthesize ==")
for poly in mesh.polygons:
    center = loop_uvs[poly.loop_start : poly.loop_start + poly.loop_total].mean(axis=0)
    poly.select = bool(np.all(np.abs(center - 0.5) < 0.125))

settings = bpy.context.scene.resynthesizer
settings.sampling_radius = 64
settings.seam_margin = 2

import time
start = time.time()
result = bpy.ops.paint.resynthesize()
elapsed = time.time() - start
check("operator finished", result == {'FINISHED'}, str(result))
print(f"  fill took {elapsed:.2f}s")

print("== verify result ==")
healed = np.empty(width * height * channels, dtype=np.float32)
image.pixels.foreach_get(healed)
healed = healed.reshape(height, width, channels)

defect_region = healed[defect, defect]
pure_red = (
    (defect_region[..., 0] > 0.99) & (defect_region[..., 1] < 0.01) & (defect_region[..., 2] < 0.01)
)
check("red defect removed", not pure_red.any(), f"{pure_red.sum()} red texels remain")
check("defect region synthesized (differs from pre-defect original)",
      not np.allclose(healed[defect, defect], original[defect, defect]))

# Outside the selected faces' UV footprint (0.375..0.625 => px 192..320) + margin,
# pixels must be untouched
outer = healed.copy()
inner = slice(192 - 8, 320 + 8)
outer[inner, inner] = original[inner, inner]
check("pixels outside fill region untouched", np.array_equal(outer, original))

save_image_copy(image, os.path.join(BUILD, "blender-fill-after.png"))
print("  wrote blender-fill-before.png / blender-fill-after.png")


def reset_image_with_defect(rows, cols):
    """Restore the original brick pixels, then paint a red defect at (rows, cols)."""
    fresh = original.copy()
    fresh[rows, cols, 0] = 1.0
    fresh[rows, cols, 1] = 0.0
    fresh[rows, cols, 2] = 0.0
    image.pixels.foreach_set(fresh.ravel())
    return fresh


def read_image():
    out = np.empty(width * height * channels, dtype=np.float32)
    image.pixels.foreach_get(out)
    return out.reshape(height, width, channels)


def red_texels(array, rows, cols):
    region = array[rows, cols]
    return int(((region[..., 0] > 0.99) & (region[..., 1] < 0.01) & (region[..., 2] < 0.01)).sum())


def shift_uvs(du, dv):
    shifted = loop_uvs + np.array([du, dv], dtype=np.float32)
    uv_layer.uv.foreach_set("vector", shifted.ravel())


print("== UVs outside 0-1 tile (texture tiling) ==")
# Reported bug: faces using tiling (UVs beyond 0..1) errored with
# "Selected faces cover no texels". Integer offsets map to the same texels.
shift_uvs(3.0, -2.0)
reset_image_with_defect(defect, defect)
result = bpy.ops.paint.resynthesize()
check("offset-tile fill finished", result == {'FINISHED'}, str(result))
check("offset-tile defect removed", red_texels(read_image(), defect, defect) == 0)

print("== UV footprint spanning the tile seam ==")
# +0.5 u shifts the selected faces' footprint onto the horizontal wrap seam:
# px 448..512 plus 0..64. The defect is painted split across both edges.
shift_uvs(0.5, 0.0)  # offsets are absolute from the original UVs
seam_cols = np.r_[448:512, 0:64]
reset_image_with_defect(defect, seam_cols)
result = bpy.ops.paint.resynthesize()
check("seam-spanning fill finished", result == {'FINISHED'}, str(result))
check("seam-spanning defect removed", red_texels(read_image(), defect, seam_cols) == 0)
save_image_copy(image, os.path.join(BUILD, "blender-fill-seam-after.png"))
shift_uvs(0.0, 0.0)  # restore original UVs

print("== explicit texture and UV map selectors ==")
settings.image = image
tool_settings.image_paint.canvas = None  # auto-detection would now fail
reset_image_with_defect(defect, defect)
result = bpy.ops.paint.resynthesize()
check("explicit texture selector used", result == {'FINISHED'}, str(result))
check("explicit-texture defect removed", red_texels(read_image(), defect, defect) == 0)

uv_layer.name = "MainUV"
settings.uv_map = "MainUV"
reset_image_with_defect(defect, defect)
result = bpy.ops.paint.resynthesize()
check("explicit UV map selector used", result == {'FINISHED'}, str(result))

settings.uv_map = "DoesNotExist"
try:
    bpy.ops.paint.resynthesize()
    check("missing UV map rejected", False, "operator did not fail")
except RuntimeError as exc:
    check("missing UV map rejected", "not found" in str(exc), str(exc))
settings.uv_map = ""
settings.image = None

print("== tiling overlap warning ==")
from resynthesizer_blender import prepare_job

tool_settings.image_paint.canvas = image  # restore auto-detection

# Plain adjacent faces share only boundary texels — must NOT warn
job, err = prepare_job(bpy.context, obj, settings)
check("no warning for plain adjacent faces", err is None and job.warning is None,
      f"err={err} warning={job.warning if job else None}")

# Remap an unselected corner face (UV 0..0.125) onto the fill footprint on
# another tile: +0.4375 lands it on 0.4375..0.5625, +2 tiles offsets it.
corner_poly = next(
    p for p in mesh.polygons
    if not p.select
    and np.all(loop_uvs[p.loop_start : p.loop_start + p.loop_total].mean(axis=0) < 0.1)
)
overlapped_uvs = loop_uvs.copy()
corner_loops = slice(corner_poly.loop_start, corner_poly.loop_start + corner_poly.loop_total)
overlapped_uvs[corner_loops] += np.array([2.4375, 2.4375], dtype=np.float32)
uv_layer.uv.foreach_set("vector", overlapped_uvs.ravel())

job, err = prepare_job(bpy.context, obj, settings)
check("overlapping unselected face warns",
      err is None and job.warning is not None and "unselected faces" in job.warning,
      f"err={err} warning={job.warning if job else None}")

settings.ignore_overlap_warnings = True
job, err = prepare_job(bpy.context, obj, settings)
check("ignore setting suppresses overlap warning",
      err is None and job.warning is None,
      f"err={err} warning={job.warning if job else None}")
settings.ignore_overlap_warnings = False

reset_image_with_defect(defect, defect)
result = bpy.ops.paint.resynthesize()
check("fill still completes with overlap warning", result == {'FINISHED'}, str(result))
check("overlap-warned defect removed", red_texels(read_image(), defect, defect) == 0)

uv_layer.uv.foreach_set("vector", loop_uvs.ravel())  # restore UVs

print("== UV island restriction ==")
# Two-island mesh over one texture: island A = 2x2 faces on the left half,
# island B = 1 face on the right half. Island B's texels are painted green;
# with the restriction on, a fill in island A must never sample that green.


def remap_uvs(mesh_data, scale, offset_u, offset_v):
    layer = mesh_data.uv_layers.active
    count = len(mesh_data.loops)
    arr = np.empty(count * 2, dtype=np.float32)
    layer.uv.foreach_get("vector", arr)
    arr = arr.reshape(count, 2) * scale + np.array([offset_u, offset_v], dtype=np.float32)
    layer.uv.foreach_set("vector", arr.ravel())


bpy.ops.object.select_all(action='DESELECT')
bpy.ops.mesh.primitive_plane_add(location=(0, 5, 0))
obj_a = bpy.context.active_object
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.subdivide(number_cuts=1)
bpy.ops.object.mode_set(mode='OBJECT')
remap_uvs(obj_a.data, 0.4, 0.05, 0.3)  # UVs 0.05..0.45 x 0.3..0.7

bpy.ops.mesh.primitive_plane_add(location=(3, 5, 0))
obj_b = bpy.context.active_object
remap_uvs(obj_b.data, 0.4, 0.55, 0.3)  # UVs 0.55..0.95 x 0.3..0.7

bpy.ops.object.select_all(action='DESELECT')
obj_a.select_set(True)
obj_b.select_set(True)
bpy.context.view_layer.objects.active = obj_a
bpy.ops.object.join()
mesh2 = obj_a.data
check("two-island mesh built", len(mesh2.polygons) == 5, f"got {len(mesh2.polygons)} polys")

# Texture: brick, island B region green, red defect inside island A's bottom-left face
green_rows, green_cols = slice(154, 358), slice(282, 486)
defect2_rows, defect2_cols = slice(170, 240), slice(40, 110)
fresh = original.copy()
fresh[green_rows, green_cols, 0] = 0.0
fresh[green_rows, green_cols, 1] = 1.0
fresh[green_rows, green_cols, 2] = 0.0
fresh[defect2_rows, defect2_cols, 0] = 1.0
fresh[defect2_rows, defect2_cols, 1] = 0.0
fresh[defect2_rows, defect2_cols, 2] = 0.0
image.pixels.foreach_set(fresh.ravel())

# Select only island A's bottom-left face (UV center ~0.15, 0.4)
uv_layer2 = mesh2.uv_layers.active
loop_uvs2 = np.empty(len(mesh2.loops) * 2, dtype=np.float32)
uv_layer2.uv.foreach_get("vector", loop_uvs2)
loop_uvs2 = loop_uvs2.reshape(-1, 2)


def select_island_a_faces(max_u, max_v=0.51):
    count = 0
    for poly in mesh2.polygons:
        center = loop_uvs2[poly.loop_start : poly.loop_start + poly.loop_total].mean(axis=0)
        poly.select = bool(center[0] < max_u and center[1] < max_v)
        count += poly.select
    return count


check("one island-A face selected", select_island_a_faces(0.26) == 1)
settings.sampling_radius = 500  # window covers the whole image


def full_corpus_mask(job):
    full = np.zeros((height, width), dtype=bool)
    x0, y0, x1, y1 = job.crop
    full[y0:y1, x0:x1] = job.corpus_mask
    return full


job, err = prepare_job(bpy.context, obj_a, settings)
check("restricted corpus excludes other island",
      err is None and not full_corpus_mask(job)[green_rows, green_cols].any(),
      f"err={err}")

settings.same_island_only = False
job, err = prepare_job(bpy.context, obj_a, settings)
check("unrestricted corpus includes other island",
      err is None and full_corpus_mask(job)[green_rows, green_cols].any(),
      f"err={err}")
settings.same_island_only = True

result = bpy.ops.paint.resynthesize()
check("island-restricted fill finished", result == {'FINISHED'}, str(result))
after = read_image()
check("island fill defect removed", red_texels(after, defect2_rows, defect2_cols) == 0)
filled = after[defect2_rows, defect2_cols]
greenish = (filled[..., 1] > 0.7) & (filled[..., 0] < 0.4)
check("no green sampled from the other island", not greenish.any(),
      f"{int(greenish.sum())} green texels")

# Selecting all of island A leaves nothing in-island to sample
selected_all = select_island_a_faces(0.5, max_v=1.0)
check("all 4 island-A faces selected", selected_all == 4, f"got {selected_all}")
try:
    bpy.ops.paint.resynthesize()
    check("whole-island selection rejected", False, "operator did not fail")
except RuntimeError as exc:
    check("whole-island selection rejected", "entire UV island" in str(exc), str(exc))

print("== unregister ==")
resynthesizer_blender.unregister()
check("clean unregister", not hasattr(bpy.context.scene, "resynthesizer")
      or bpy.context.scene.get("resynthesizer") is None)

print(f"\n{'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
