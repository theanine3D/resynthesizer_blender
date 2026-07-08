# Resynthesizer for Blender

Texture healing for Blender's Texture Paint mode: select faces on your mesh
and **Resynthesize** regenerates the part of the texture on those faces by
analyzing their surroundings (similar to Photoshop's Content Aware Fill) —
removing blemishes, baked-in shadows, logos, or seam artifacts while
matching the surrounding pattern.

Powered by the [Resynthesizer](https://github.com/bootchk/resynthesizer)
texture synthesis engine by Lloyd Konneker, implementing Paul Harrison's
algorithm — the same engine behind GIMP's well-known "Heal selection" plugin.

## Features

- **Face-selection workflow**: select faces in Edit Mode (or with Texture
  Paint's face selection mask), press *Resynthesize* — the faces' UV
  footprint is refilled from nearby texture.
- **UV-island aware**: sampling is restricted to the UV island(s) being
  healed (toggleable), so a fill never borrows texels from unrelated parts
  of a texture atlas — and never from the empty void between islands.
- **Texture tiling support**: faces with UVs outside the 0–1 tile, or
  spanning a tile seam, work correctly; fills wrap across image edges.
- **Overlap warning**: if unselected faces share the healed texels
  (intentionally tiled UVs), you get a warning with the shared texel count
  (suppressible via *Ignore Overlap Warnings* checkbox).
- **Explicit target selectors**: choose the exact texture and UV map in the
  panel, or leave on auto with a visible indicator of what will be modified.
- **Responsive**: the engine runs on a worker thread with progress display;
  ESC cancels. Typical fills take well under a second.
- **Cross-platform**: Windows, Linux, and macOS (x64 + Apple Silicon).

## Installing

Releases ship as a Blender extension zip (Blender 4.2+): Preferences >
Extensions > Install from Disk.

Building the zip from source (Windows host):

```powershell
./build.ps1     # engine binaries: Windows via mingw-w64 gcc; Linux/macOS via zig cc
./package.ps1   # extension zip via `blender --command extension build` + validate
```

`build.ps1` needs `gcc` (mingw-w64) on PATH; if `zig` is also present it
cross-compiles the Linux and macOS engines (pure C + libm, no SDKs needed).
`package.ps1` needs `blender` on PATH. For a legacy (non-extension) install,
run `build.ps1` and copy `addon/resynthesizer_blender/` into Blender's
`scripts/addons/`.

## Usage

1. UV-unwrapped mesh with an image texture.
2. Select the faces to heal (Edit Mode, or Texture Paint with face selection
   masking enabled).
3. In Texture Paint mode: Sidebar (N) > Tool > **Resynthesize**.
4. Check the target texture / UV map shown in the panel (or pick explicitly),
   adjust the sampling radius if desired, press **Resynthesize**.

Settings: *Sampling Radius* (how far around the region to sample), *Seam
Margin* (extra fill beyond the UV footprint, covers bake bleed), *Patch
Size* / *Max Probes* (engine quality/speed trade-off), *Same UV Island Only*,
*Ignore Overlap Warnings*.

Note: pixel edits from scripts bypass Blender's paint undo stack; an undo
step is pushed on a best-effort basis. Save before large fills.

## Repository layout

- `engine/` — the platform-independent C synthesis engine, vendored from
  [bootchk/resynthesizer](https://github.com/bootchk/resynthesizer) (`lib/`)
  with two small patches, both marked `PATCHED` in the source:
  threading made opt-in (`buildSwitches.h`) and a clang-incompatible
  prototype fixed (`progress.h`). Compiled with `-DSYNTH_LIB_ALONE`
  (upstream's own glib-free configuration); zero external dependencies.
- `addon/resynthesizer_blender/` — the Blender add-on: ctypes binding
  (`resynth.py`), UV rasterization (`uv_raster.py`), operator/panel
  (`__init__.py`), extension manifest. Engine binaries are built into this
  folder by `build.ps1` and are not committed.
- `harness/` — standalone CLI (`resynth_cli.c` + [stb](https://github.com/nothings/stb)
  image I/O) for testing the engine on PNGs without Blender.
- `python/` — the ctypes wrapper and its Blender-free test suite.
- `test_images/` — test images from the upstream Resynthesizer repository.

## Tests

```powershell
./build.ps1

# Engine + ctypes binding (needs Python 3 with numpy and Pillow).
# Includes a byte-exact parity check against the CLI harness — run the CLI once first:
./build/resynth_cli.exe test_images/ufo-input.png build/ufo-healed.png --rect 80 70 145 90
python python/test_resynth.py

# Add-on integration suite (33 checks), headless:
blender --background --factory-startup --python addon/test_addon_blender.py
```

The Windows engine is exercised by the full suite; Linux/macOS binaries are
cross-compiled and format-verified — run `python/test_resynth.py` on those
platforms to smoke-test them natively.

## License and credits

GPL-2.0-or-later (see `COPYING`) — the engine is GPL2+ and Blender add-ons
are GPL-compatible by design.

- Texture synthesis algorithm: **Paul Harrison**
  ([Image Texture Tools](https://logarithmic.net/pfh/thesis))
- Resynthesizer engine: **Lloyd Konneker**
  ([bootchk/resynthesizer](https://github.com/bootchk/resynthesizer))
- Blender integration: **Pedro Valencia Oseguera**
- Image I/O in the CLI harness: [stb](https://github.com/nothings/stb)
  (public domain)
