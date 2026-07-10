$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$logDir = Join-Path $root "logs"
$stopScript = Join-Path $root "stop-dashboard.ps1"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (Test-Path $stopScript) {
  & $stopScript | Out-Null
}

$backendOut = Join-Path $logDir "backend-8010.out.log"
$backendErr = Join-Path $logDir "backend-8010.err.log"
$frontendOut = Join-Path $logDir "frontend-5174.out.log"
$frontendErr = Join-Path $logDir "frontend-5174.err.log"
$backendLauncher = Join-Path $root "launch-backend.cmd"
$frontendLauncher = Join-Path $root "launch-frontend.cmd"

Remove-Item -Force $backendOut, $backendErr, $frontendOut, $frontendErr -ErrorAction SilentlyContinue

Start-Process `
  -FilePath "C:\Windows\System32\cmd.exe" `
  -ArgumentList "/c", "start", "`"agent-os-backend`"", "/min", "cmd", "/k", "`"$backendLauncher`"" `
  -WindowStyle Hidden

Start-Process `
  -FilePath "C:\Windows\System32\cmd.exe" `
  -ArgumentList "/c", "start", "`"agent-os-frontend`"", "/min", "cmd", "/k", "`"$frontendLauncher`"" `
  -WindowStyle Hidden

Start-Sleep -Seconds 3

Write-Host "Backend:  http://127.0.0.1:8010"
Write-Host "Frontend: http://127.0.0.1:5174"
Write-Host "Logs:     $logDir"
