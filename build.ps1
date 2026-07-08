# Build script for the standalone Resynthesizer engine + CLI test harness.
# Requires a C compiler: gcc on PATH (mingw-w64 on Windows), or K:\Apps\mingw64.
# Optional: zig (on PATH or under K:\Apps\zig-*) enables Linux/macOS cross builds.

$ErrorActionPreference = "Stop"

if (-not (Get-Command gcc -ErrorAction SilentlyContinue)) {
    $mingw = "K:\Apps\mingw64\bin"
    if (Test-Path "$mingw\gcc.exe") { $env:Path = "$mingw;$env:Path" }
    else { throw "gcc not found on PATH (install mingw-w64)" }
}

$root = $PSScriptRoot
$engineSrc = @(
    "imageSynth.c", "engine.c", "glibProxy.c",
    "engineParams.c", "imageFormat.c", "progress.c"
) | ForEach-Object { Join-Path $root "engine\$_" }

New-Item -ItemType Directory -Force (Join-Path $root "build") | Out-Null

Write-Host "Building libresynth.dll ..."
& gcc -O2 -DSYNTH_LIB_ALONE -shared -o (Join-Path $root "build\libresynth.dll") @engineSrc -lm
if ($LASTEXITCODE -ne 0) { throw "DLL build failed" }

Write-Host "Building resynth_cli.exe ..."
& gcc -O2 -DSYNTH_LIB_ALONE -I (Join-Path $root "engine") `
    -o (Join-Path $root "build\resynth_cli.exe") `
    (Join-Path $root "harness\resynth_cli.c") @engineSrc -lm
if ($LASTEXITCODE -ne 0) { throw "CLI build failed" }

# Cross-compile Linux/macOS engine binaries with zig cc (pure C + libm, no SDKs needed)
$zigCmd = Get-Command zig -ErrorAction SilentlyContinue
if ($zigCmd) { $zigPath = $zigCmd.Source }
else {
    $zigItem = Get-ChildItem "K:\Apps\zig-*\zig.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($zigItem) { $zigPath = $zigItem.FullName } else { $zigPath = $null }
}
if ($zigPath) {
    $targets = @(
        @{ triple = "x86_64-linux-gnu"; out = "libresynth-linux-x64.so";      extra = @("-fPIC", "-lm") },
        @{ triple = "x86_64-macos";     out = "libresynth-macos-x64.dylib";   extra = @() },
        @{ triple = "aarch64-macos";    out = "libresynth-macos-arm64.dylib"; extra = @() }
    )
    foreach ($t in $targets) {
        Write-Host "Cross-building $($t.out) ..."
        & $zigPath cc -target $t.triple -O2 -DSYNTH_LIB_ALONE -shared `
            -o (Join-Path $root "build\$($t.out)") @engineSrc @($t.extra)
        if ($LASTEXITCODE -ne 0) { throw "cross build failed: $($t.triple)" }
    }
    # Refresh the binaries bundled in the add-on
    Copy-Item (Join-Path $root "build\libresynth.dll") (Join-Path $root "addon\resynthesizer_blender\")
    foreach ($t in $targets) {
        Copy-Item (Join-Path $root "build\$($t.out)") (Join-Path $root "addon\resynthesizer_blender\")
    }
} else {
    Write-Host "zig not found - skipping Linux/macOS cross builds"
}

Write-Host "Done. Outputs in build\"
