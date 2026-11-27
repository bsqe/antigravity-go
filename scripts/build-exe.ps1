$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BridgeDir = Join-Path $RepoRoot "bridge"
$VenvPython = Join-Path $BridgeDir ".venv\Scripts\python.exe"
$VendorBinDir = Join-Path $BridgeDir "vendor\bin"
$BundledCloudflared = Join-Path $VendorBinDir "cloudflared.exe"

if (-not (Test-Path $VenvPython)) {
    throw "Bridge virtualenv not found at $VenvPython. Create it and run pip install -e '.[dev]' first."
}

if (-not (Test-Path $BundledCloudflared)) {
    New-Item -ItemType Directory -Force -Path $VendorBinDir | Out-Null
    $SysCloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($SysCloudflared -and (Test-Path $SysCloudflared.Source)) {
        Write-Host "Copying local cloudflared from $($SysCloudflared.Source)..."
        Copy-Item $SysCloudflared.Source $BundledCloudflared -Force
    } else {
        Write-Host "Downloading official cloudflared-windows-amd64.exe for bundling..."
        Invoke-WebRequest `
            -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
            -OutFile $BundledCloudflared
    }
}

Push-Location $BridgeDir
try {
    Write-Host "Building AntigravityGoBridge.exe (--onedir) via PyInstaller..."
    & $VenvPython -m PyInstaller antigravity-go-bridge.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller exited with code $LASTEXITCODE"
    }
    $OutExe = Join-Path $BridgeDir "dist\AntigravityGoBridge\AntigravityGoBridge.exe"
    $OutHarness = Join-Path $BridgeDir "dist\AntigravityGoBridge\_internal\google\antigravity\bin\localharness.exe"
    $OutCloudflared = Join-Path $BridgeDir "dist\AntigravityGoBridge\_internal\agy_bridge\bin\cloudflared.exe"
    if (-not (Test-Path $OutExe)) {
        throw "Expected executable not found: $OutExe"
    }
    if (-not (Test-Path $OutHarness)) {
        throw "Expected bundled localharness.exe not found: $OutHarness"
    }
    if (-not (Test-Path $OutCloudflared)) {
        throw "Expected bundled cloudflared.exe not found: $OutCloudflared"
    }
    Write-Host "`nBuild succeeded: $OutExe"
    Write-Host "Bundled cloudflared: $OutCloudflared"
    Write-Host "Distribute the entire folder: $(Split-Path -Parent $OutExe)"
} finally {
    Pop-Location
}
