@echo off
setlocal
cls
title The Halleen Machine 2.0

rem === Config ===
set "APP=serve.py"
rem Fixed port so we can kill the right thing
if not defined THM_PORT set "THM_PORT=8010"
if not defined THM_HOST set "THM_HOST=0.0.0.0"

echo Stopping anything listening on port %THM_PORT%...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$port=[int]$env:THM_PORT; " ^
  "$procs = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | " ^
  "         Select-Object -Expand OwningProcess -Unique; " ^
  "if($procs){ $procs | ForEach-Object { try { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } catch {} }; " ^
  "            Write-Host ('Killed ' + ($procs | Measure-Object).Count + ' process(es).') } else { Write-Host 'None found.' }"

rem brief pause to release the port
timeout /t 1 /nobreak >nul

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0the-machine-ui-venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo Starting: "%PY%" "%APP%" --host %THM_HOST% --port %THM_PORT%
set "PYTHONUNBUFFERED=1"
pushd "%~dp0"
"%PY%" "%APP%" --host %THM_HOST% --port %THM_PORT%
popd

endlocal
