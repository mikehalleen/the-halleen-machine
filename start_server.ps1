# The Halleen Machine 2.0 - fresh start for manual testing.
#
# Kills whatever is already listening on the THM port (so a stale server process
# from an earlier session can never serve outdated code), then starts serve.py in
# the foreground of this window. The web client is rebuilt first only when web\
# node_modules exists (i.e. you ran npm install to work on the UI); otherwise the
# prebuilt web\dist that ships with the repo is served as-is.
#
# Usage (from a PowerShell window, repo root):
#   .\start_server.ps1
#   .\start_server.ps1 -NoBuild        # never rebuild, just restart the API
#   .\start_server.ps1 -Build          # rebuild even if it looks like a plain install
#   .\start_server.ps1 -Port 8011

param(
    [int]$Port = 8010,
    [switch]$NoBuild,
    [switch]$Build
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Any process holding the port in any TCP state, not just Listen: a server part-way through a
# graceful shutdown has already closed its listening socket while it still owns the established
# SSE connections that are holding it there, so -State Listen walked straight past it and left
# it sitting in its own window at "Waiting for connections to close".
function Get-ThmServerProcessIds {
    param([int]$Port)
    Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess |
        Where-Object { $_ -and $_ -ne 0 -and $_ -ne $PID } |
        Select-Object -Unique
}

Write-Host "Stopping any THM server on port $Port (and any stale serve.py)..."
$procs = @(Get-ThmServerProcessIds -Port $Port)
if ($procs.Count) {
    foreach ($procId in $procs) {
        try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } catch {}
    }
    Start-Sleep -Seconds 1
    $left = @(Get-ThmServerProcessIds -Port $Port)
    if ($left.Count) {
        Write-Host "Warning: still running after kill: $($left -join ', ')" -ForegroundColor Yellow
    } else {
        Write-Host "Killed $($procs.Count) process(es)."
    }
} else {
    Write-Host "None found."
}

$canBuild = Test-Path "$PSScriptRoot\web\node_modules"
if ($NoBuild) {
    Write-Host "Skipping web build (-NoBuild); serving web\dist as-is."
} elseif ($Build -or $canBuild) {
    Write-Host "Building the web client..."
    Push-Location "$PSScriptRoot\web"
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed with exit code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "web
ode_modules not found; serving the prebuilt web\dist. (cd web; npm install; npm run build) to rebuild the UI."
}

$Py = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = Join-Path $PSScriptRoot "the-machine-ui-venv\Scripts\python.exe" }
if (-not (Test-Path $Py)) { $Py = "python" }

Write-Host "Starting: $Py serve.py --port $Port"
$env:PYTHONUNBUFFERED = "1"
& $Py "serve.py" --port $Port
