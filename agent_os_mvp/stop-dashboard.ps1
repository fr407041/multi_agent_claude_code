$ErrorActionPreference = "SilentlyContinue"

$backendPatterns = @(
  "launch-backend.cmd",
  "uvicorn app.main:app --host 127.0.0.1 --port 8010"
)

$frontendPatterns = @(
  "launch-frontend.cmd",
  "vite --host 127.0.0.1 --port 5174"
)

$processes = Get-CimInstance Win32_Process | Where-Object {
  $command = $_.CommandLine
  if (-not $command) { return $false }
  foreach ($pattern in ($backendPatterns + $frontendPatterns)) {
    if ($command -like "*$pattern*") { return $true }
  }
  return $false
}

foreach ($process in $processes) {
  Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host "Stopped dashboard background processes if they existed."
