param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PluginSrc = Join-Path $RepoRoot "plugin"
$PluginDst = Join-Path $env:USERPROFILE ".gemini\config\plugins\antigravity-go"

if (-not $PythonExe) {
    $VenvPython = Join-Path $RepoRoot "bridge\.venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $PythonExe = $VenvPython
    } else {
        $PythonExe = (Get-Command python).Source
    }
}

Write-Host "Installing Antigravity-Go plugin into: $PluginDst"
New-Item -ItemType Directory -Force -Path $PluginDst | Out-Null
Copy-Item -Path (Join-Path $PluginSrc "*") -Destination $PluginDst -Recurse -Force

# Pin the sidecar command to the resolved Python executable (venv-aware)
$SidecarPath = Join-Path $PluginDst "sidecars\bridge\sidecar.json"
$Sidecar = Get-Content -Path $SidecarPath -Raw | ConvertFrom-Json
$Sidecar.command = $PythonExe
$Sidecar | ConvertTo-Json -Depth 5 | Set-Content -Path $SidecarPath -Encoding UTF8

# Update hooks.json to use the resolved Python executable
$HooksPath = Join-Path $PluginDst "hooks.json"
$HooksRaw = Get-Content -Path $HooksPath -Raw
$EscapedPy = ($PythonExe -replace '\\', '\\')
$HooksUpdated = $HooksRaw -replace '"python \./scripts/hook_forward\.py"', "`"`"$EscapedPy`" ./scripts/hook_forward.py`""
Set-Content -Path $HooksPath -Value $HooksUpdated -Encoding UTF8

Write-Host "Done! Restart Antigravity 2.0 or verify under ~/.gemini/config/plugins/antigravity-go"
