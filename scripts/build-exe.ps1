$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BridgeDir = Join-Path $RepoRoot "bridge"
$VenvPython = Join-Path $BridgeDir ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    throw "Bridge virtualenv not found at $VenvPython. Create it and run pip install -e '.[dev]' first."
}

Push-Location $BridgeDir
try {
    Write-Host "Building AntigravityGoBridge.exe (--onedir) via PyInstaller..."
    & $VenvPython -m PyInstaller antigravity-go-bridge.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller exited with code $LASTEXITCODE"
    }
    $OutExe = Join-Path $BridgeDir "dist\AntigravityGoBridge\AntigravityGoBridge.exe"
    if (-not (Test-Path $OutExe)) {
        throw "Expected executable not found: $OutExe"
    }
    Write-Host "`nBuild succeeded: $OutExe"
    Write-Host "Distribute the entire folder: $(Split-Path -Parent $OutExe)"
} finally {
    Pop-Location
}
