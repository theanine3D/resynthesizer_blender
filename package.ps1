# Build the Blender extension zip from the add-on source.
# Requires Blender 4.2+ (on PATH, or at the default install location).
# Run build.ps1 first so the engine binaries are present in the add-on folder.

$ErrorActionPreference = "Stop"
$blenderCmd = Get-Command blender -ErrorAction SilentlyContinue
if ($blenderCmd) { $blender = $blenderCmd.Source }
else {
    $blender = Get-ChildItem "C:\Program Files\Blender Foundation\Blender*\blender.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    if (-not $blender) { throw "blender.exe not found (add it to PATH)" }
}
$root = $PSScriptRoot

$manifest = Get-Content (Join-Path $root "addon\resynthesizer_blender\blender_manifest.toml") -Raw
$version = [regex]::Match($manifest, '(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
$out = Join-Path $root "build\resynthesizer-$version.zip"

& $blender --command extension build `
    --source-dir (Join-Path $root "addon\resynthesizer_blender") `
    --output-filepath $out
if ($LASTEXITCODE -ne 0) { throw "extension build failed" }

& $blender --command extension validate $out
if ($LASTEXITCODE -ne 0) { throw "extension validate failed" }

Write-Host "Packaged: $out"
