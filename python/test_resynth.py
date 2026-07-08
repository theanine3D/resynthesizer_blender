"""
Standalone tests for the resynth ctypes wrapper

Run:  python test_resynth.py
Needs: numpy, Pillow, ../build/libresynth.dll, and (for the parity test)
../build/ufo-healed.png produced by the CLI harness with --rect 80 70 145 90.
"""

import os
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resynth

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "..", "build")


def _find_test_images():
    candidates = [
        os.path.join(HERE, "..", "test_images"),  # vendored in the repo
        os.path.join(HERE, "..", "..", "resynthesizer-gimp", "Test", "in_images"),
    ]
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, "ufo-input.png")):
            return candidate
    raise FileNotFoundError(f"test images not found in any of: {candidates}")


TEST_IMAGES = _find_test_images()

UFO_RECT = (80, 70, 145, 90)  # x, y, w, h — same as the verified CLI run

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def rect_mask(shape, rect):
    x, y, w, h = rect
    mask = np.zeros(shape, dtype=np.uint8)
    mask[y : y + h, x : x + w] = 255
    return mask


def load_ufo_rgba():
    img = Image.open(os.path.join(TEST_IMAGES, "ufo-input.png")).convert("RGBA")
    return np.array(img)


print("== Library load & defaults ==")
lib = resynth.load_library()
params = resynth.default_parameters()
check("defaults match engineParams.c",
      params.patchSize == 30 and params.maxProbeCount == 200
      and params.matchContextType == 1
      and abs(params.sensitivityToOutliers - 0.117) < 1e-9)

print("== uint8 RGBA heal + CLI parity ==")
ufo = load_ufo_rgba()
mask = rect_mask(ufo.shape[:2], UFO_RECT)

progress_values = []
start = time.time()
healed = resynth.heal(ufo, mask, progress=progress_values.append)
elapsed = time.time() - start
print(f"  healed {ufo.shape[1]}x{ufo.shape[0]} in {elapsed:.2f}s, "
      f"{len(progress_values)} progress callbacks")

check("returns same shape/dtype", healed.shape == ufo.shape and healed.dtype == np.uint8)
check("input not modified", np.array_equal(ufo, np.array(load_ufo_rgba())))
check("unmasked region unchanged",
      np.array_equal(healed[mask == 0], ufo[mask == 0]))
check("masked region was synthesized",
      not np.array_equal(healed[mask == 255], ufo[mask == 255]))
check("progress reported and clamped",
      len(progress_values) > 0 and max(progress_values) <= 100)

cli_out_path = os.path.join(BUILD, "ufo-healed.png")
if os.path.isfile(cli_out_path):
    cli_out = np.array(Image.open(cli_out_path).convert("RGBA"))
    check("byte-identical to CLI harness output", np.array_equal(healed, cli_out),
          f"diff pixels: {np.count_nonzero(np.any(healed != cli_out, axis=-1))}")
else:
    print("  SKIP  CLI parity (build/ufo-healed.png missing)")

Image.fromarray(healed).save(os.path.join(BUILD, "ufo-healed-py.png"))

print("== float32 path (Blender-style) ==")
ufo_f = ufo.astype(np.float32) / 255.0
healed_f = resynth.heal(ufo_f, mask)
check("float32 in, float32 out", healed_f.dtype == np.float32)
back = np.clip(np.rint(healed_f * 255.0), 0, 255).astype(np.uint8)
check("float path matches uint8 path exactly", np.array_equal(back, healed))

print("== RGB (3-channel) ==")
ufo_rgb = np.array(Image.open(os.path.join(TEST_IMAGES, "ufo-input.png")).convert("RGB"))
healed_rgb = resynth.heal(ufo_rgb, mask)
check("RGB heal succeeds, region synthesized",
      not np.array_equal(healed_rgb[mask == 255], ufo_rgb[mask == 255]))

print("== corpus mask (imageSynth2, donut sampling) ==")
x, y, w, h = UFO_RECT
radius = 50
corpus = np.zeros(ufo.shape[:2], dtype=np.uint8)
corpus[max(0, y - radius) : y + h + radius, max(0, x - radius) : x + w + radius] = 255
corpus[mask == 255] = 0  # never sample from the hole itself
healed_donut = resynth.heal(ufo, mask, corpus_mask=corpus)
check("donut corpus heal succeeds, region synthesized",
      not np.array_equal(healed_donut[mask == 255], ufo[mask == 255]))
Image.fromarray(healed_donut).save(os.path.join(BUILD, "ufo-healed-donut.png"))

print("== error handling ==")
try:
    resynth.heal(ufo, np.zeros(ufo.shape[:2], dtype=np.uint8))
    check("empty mask raises", False)
except resynth.ResynthError as e:
    check("empty mask raises", e.code == 5, str(e))

try:
    resynth.heal(ufo, np.full(ufo.shape[:2], 255, dtype=np.uint8))
    check("full mask raises (empty corpus)", False)
except resynth.ResynthError as e:
    check("full mask raises (empty corpus)", e.code == 6, str(e))

try:
    resynth.heal(ufo, np.zeros((10, 10), dtype=np.uint8))
    check("mismatched mask raises ValueError", False)
except ValueError:
    check("mismatched mask raises ValueError", True)

print("== cancellation ==")
token = resynth.CancelToken()


def cancel_immediately(_percent):
    token.cancelled = True


healed_cancelled = resynth.heal(ufo, mask, progress=cancel_immediately, cancel=token)
# Engine treats cancel as success but skips writing results back
check("cancel returns unmodified image", np.array_equal(healed_cancelled, ufo))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
