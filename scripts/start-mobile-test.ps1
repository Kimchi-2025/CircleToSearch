param([switch]$Check)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$frontendDir = Join-Path $projectRoot 'frontend'
$backendDir = Join-Path $projectRoot 'backend'
$runtimeDir = Join-Path $projectRoot '.mobile-test'
$stateFile = Join-Path $runtimeDir 'tunnel.json'
$apiUrl = 'http://127.0.0.1:8001'
$viteUrl = 'http://127.0.0.1:5173'
$script:newTunnelPid = $null

function Stop-WithError([string]$message) { throw $message }

function Get-ListenerPids([int]$port) {
  $ids = @()
  foreach ($line in (& netstat -ano -p TCP)) {
    if ($line -match '^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$' -and [int]$Matches[1] -eq $port) {
      $ids += [int]$Matches[2]
    }
  }
  return @($ids | Select-Object -Unique)
}

function Test-Api {
  try {
    $health = Invoke-RestMethod "$apiUrl/health" -TimeoutSec 3
    $schema = Invoke-RestMethod "$apiUrl/openapi.json" -TimeoutSec 3
    return $health.status -eq 'ok' -and
      $null -ne $schema.paths.'/api/circle-search/segment' -and
      $null -ne $schema.paths.'/health'
  } catch { return $false }
}

function Test-Vite {
  try {
    $page = Invoke-WebRequest "$viteUrl/" -UseBasicParsing -TimeoutSec 3
    $module = Invoke-WebRequest "$viteUrl/src/services/api.js" -UseBasicParsing -TimeoutSec 3
    return $page.StatusCode -eq 200 -and
      $page.Content.Contains('Circle to Search') -and
      $module.Content.Contains('/api/circle-search/segment')
  } catch { return $false }
}

function Test-Host([string]$tunnelHost) {
  try {
    $reply = Invoke-RestMethod "$viteUrl/health" -Headers @{ Host = $tunnelHost } -TimeoutSec 3
    return $reply.status -eq 'ok'
  } catch { return $false }
}

function Wait-For([scriptblock]$condition, [int]$seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  do {
    if (& $condition) { return $true }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)
  return $false
}

function Get-CloudflaredPath {
  $command = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
  if ($command) { return $command.Source }
  foreach ($path in @(
    "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe",
    "$env:ProgramFiles\cloudflared\cloudflared.exe"
  )) {
    if (Test-Path -LiteralPath $path) { return $path }
  }
  Stop-WithError 'cloudflared is missing. Install it from https://developers.cloudflare.com/tunnel/downloads/'
}

function Get-ViteListener {
  $ids = @(Get-ListenerPids 5173)
  if ($ids.Count -ne 1) { Stop-WithError "Cannot safely restart Vite: port 5173 has $($ids.Count) listeners." }
  try { $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($ids[0])" } catch {
    Stop-WithError "Cannot inspect Vite PID $($ids[0]); no process will be stopped."
  }
  if (-not $process -or $process.Name -ne 'node.exe' -or
      $process.CommandLine.IndexOf($frontendDir, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
      $process.CommandLine -notmatch 'vite[\\/]+bin[\\/]+vite\.js') {
    Stop-WithError "Port 5173 PID $($ids[0]) is not a verified CircleToSearch Vite process; no process will be stopped."
  }
  return $ids[0]
}

function Start-Api {
  $python = Join-Path $backendDir '.venv\Scripts\python.exe'
  if (-not (Test-Path -LiteralPath $python)) { Stop-WithError "Python environment is missing: $python" }
  Write-Host 'Starting CircleToSearch FastAPI on 8001...'
  Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'main:app', '--app-dir', $backendDir, '--host', '127.0.0.1', '--port', '8001') -WorkingDirectory $backendDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'api.out.log') -RedirectStandardError (Join-Path $runtimeDir 'api.err.log') | Out-Null
  if (-not (Wait-For ${function:Test-Api} 15)) { Stop-WithError "FastAPI did not start. Check $runtimeDir\api.err.log" }
}

function Start-Vite([string]$tunnelHost) {
  $node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
  $vite = Join-Path $frontendDir 'node_modules\vite\bin\vite.js'
  if (-not $node -or -not (Test-Path -LiteralPath $vite)) { Stop-WithError 'Node.js or frontend/node_modules is missing. Run npm install in frontend.' }
  $env:MOBILE_TEST_HOST = $tunnelHost
  Remove-Item Env:\__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS -ErrorAction SilentlyContinue
  Write-Host 'Starting CircleToSearch Vite on 5173...'
  Start-Process -FilePath $node -ArgumentList @($vite, '--host', '0.0.0.0', '--port', '5173', '--strictPort') -WorkingDirectory $frontendDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'vite.out.log') -RedirectStandardError (Join-Path $runtimeDir 'vite.err.log') | Out-Null
  if (-not (Wait-For ${function:Test-Vite} 15)) { Stop-WithError "Vite did not start. Check $runtimeDir\vite.err.log" }
}

function Set-TunnelHost([string]$tunnelHost) {
  $envFile = Join-Path $frontendDir '.env.local'
  $lines = if (Test-Path -LiteralPath $envFile) { @([IO.File]::ReadAllLines($envFile)) } else { @('VITE_API_URL=') }
  $otherLines = @($lines | Where-Object { $_ -notmatch '^\s*MOBILE_TEST_HOST\s*=' })
  $newLines = @($otherLines) + "MOBILE_TEST_HOST=$tunnelHost"
  [IO.File]::WriteAllLines($envFile, [string[]]$newLines, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-TunnelProcesses {
  try { return @(Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" | Where-Object {
    $_.CommandLine -match 'tunnel' -and $_.CommandLine -match '--url(?:=|\s+)"?http://(?:127\.0\.0\.1|localhost):5173/?(?:"|\s|$)'
  }) } catch { Stop-WithError 'Cannot inspect existing cloudflared processes; refusing to create a duplicate tunnel.' }
}

function Get-ExistingTunnel {
  $processes = @(Get-TunnelProcesses)
  if (Test-Path -LiteralPath $stateFile) {
    try {
      $saved = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
      $match = @($processes | Where-Object { $_.ProcessId -eq [int]$saved.pid })
      if ($match.Count -eq 1 -and $saved.url -match '^https://[a-z0-9-]+\.trycloudflare\.com$') {
        return @{ pid = [int]$saved.pid; url = [string]$saved.url }
      }
    } catch { }
  }
  if ($processes.Count) { Stop-WithError 'A tunnel for port 5173 is already running without a reusable URL record. Stop that tunnel yourself, then double-click again.' }
  return $null
}

function Start-Tunnel([string]$cloudflared) {
  Write-Host 'Starting Cloudflare Quick Tunnel...'
  $arguments = @('tunnel', '--url', $viteUrl)
  if ($env:CIRCLE_TEST_EMAIL) { $arguments += @('--allowed-mail', $env:CIRCLE_TEST_EMAIL) }
  $outLog = Join-Path $runtimeDir 'tunnel.out.log'
  $errLog = Join-Path $runtimeDir 'tunnel.err.log'
  $process = Start-Process -FilePath $cloudflared -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
  $script:newTunnelPid = $process.Id
  $deadline = (Get-Date).AddSeconds(60)
  do {
    $logs = (Get-Content -LiteralPath $outLog -Raw -ErrorAction SilentlyContinue) + (Get-Content -LiteralPath $errLog -Raw -ErrorAction SilentlyContinue)
    $urlMatch = [regex]::Match($logs, 'https://[a-z0-9-]+\.trycloudflare\.com', 'IgnoreCase')
    if ($urlMatch.Success) {
      $url = $urlMatch.Value.ToLowerInvariant()
      @{ pid = $process.Id; url = $url } | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
      return @{ pid = $process.Id; url = $url }
    }
    if ($process.HasExited) { Stop-WithError "cloudflared exited. Check $errLog" }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)
  Stop-WithError "No Quick Tunnel URL appeared within 60 seconds. Check $errLog"
}

function Stop-TrackedTunnel([int]$processId) {
  $processes = @(Get-TunnelProcesses)
  if (@($processes | Where-Object { $_.ProcessId -eq $processId }).Count -eq 1) {
    Stop-Process -Id $processId -ErrorAction SilentlyContinue
  }
  Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue
}

try {
  New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
  $cloudflared = Get-CloudflaredPath
  $apiListeners = @(Get-ListenerPids 8001)
  if ($apiListeners.Count) {
    if (-not (Test-Api)) { Stop-WithError 'Port 8001 belongs to another or unhealthy app. Nothing was stopped.' }
    Write-Host 'Reusing CircleToSearch FastAPI on 8001.'
  } elseif (-not $Check) { Start-Api }

  $viteListeners = @(Get-ListenerPids 5173)
  if ($viteListeners.Count) {
    if (-not (Test-Vite)) { Stop-WithError 'Port 5173 belongs to another or unhealthy app. Nothing was stopped.' }
    Write-Host 'Found CircleToSearch Vite on 5173.'
  } elseif (-not $Check) { Start-Vite '' }

  if ($Check) {
    if (-not $apiListeners.Count -or -not $viteListeners.Count) { Stop-WithError 'FastAPI or Vite is not running. Double-click without --check to start missing servers.' }
    Write-Host 'FastAPI, Vite, and cloudflared are ready. No tunnel or server was started.'
    exit 0
  }

  $tunnel = Get-ExistingTunnel
  if ($tunnel) { Write-Host "Reusing tracked tunnel PID $($tunnel.pid)." }
  else { $tunnel = Start-Tunnel $cloudflared }
  $tunnelHost = ([uri]$tunnel.url).Host
  if ($tunnelHost -notmatch '^[a-z0-9-]+\.trycloudflare\.com$') { Stop-WithError 'Tunnel returned an invalid hostname.' }

  $envFile = Join-Path $frontendDir '.env.local'
  $oldHost = if (Test-Path -LiteralPath $envFile) {
    $found = @([IO.File]::ReadAllLines($envFile) | Where-Object { $_ -match '^\s*MOBILE_TEST_HOST\s*=' })
    if ($found.Count) { ($found[-1] -split '=', 2)[1].Trim() }
  }
  if ($oldHost -ne $tunnelHost) { Set-TunnelHost $tunnelHost }

  if (-not (Test-Host $tunnelHost)) {
    $vitePid = Get-ViteListener
    Write-Host "Restarting verified CircleToSearch Vite PID $vitePid for $tunnelHost..."
    Stop-Process -Id $vitePid
    if (-not (Wait-For { @(Get-ListenerPids 5173).Count -eq 0 } 10)) { Stop-WithError 'Port 5173 did not become free after stopping verified Vite.' }
    Start-Vite $tunnelHost
  }
  if (-not (Test-Host $tunnelHost)) { Stop-WithError 'Vite did not allow the exact tunnel hostname.' }
  if (-not (Test-Api)) { Stop-WithError 'FastAPI stopped during setup.' }
  if (-not $env:CIRCLE_TEST_EMAIL) {
    $publicReady = Wait-For {
      try { (Invoke-RestMethod "$($tunnel.url)/health" -TimeoutSec 5).status -eq 'ok' } catch { $false }
    } 20
    if (-not $publicReady) { Stop-WithError 'The Quick Tunnel HTTPS URL could not reach /health. Check the tunnel log and network connection.' }
  }

  Write-Host ''
  Write-Host '==============================================' -ForegroundColor Green
  Write-Host "  ANDROID HTTPS URL: $($tunnel.url)" -ForegroundColor Green
  Write-Host '==============================================' -ForegroundColor Green
  Write-Host 'Open this URL in Android Chrome. Keep this window open.'
  try { Start-Process -FilePath $tunnel.url | Out-Null } catch { Write-Host "Could not open browser automatically: $_" }
  try {
    Read-Host 'Press ENTER here to stop this Quick Tunnel' | Out-Null
  } finally {
    Stop-TrackedTunnel $tunnel.pid
    Set-TunnelHost ''
    if ((Test-Vite) -and (Test-Host $tunnelHost)) {
      $vitePid = Get-ViteListener
      Stop-Process -Id $vitePid
      if (-not (Wait-For { @(Get-ListenerPids 5173).Count -eq 0 } 10)) { Stop-WithError 'Port 5173 did not become free while removing the tunnel host.' }
      Start-Vite ''
    }
    if (-not (Test-Vite)) { Stop-WithError 'Local Vite is unavailable after tunnel shutdown.' }
  }
  Write-Host 'Tunnel stopped. FastAPI and Vite remain available locally.'
  exit 0
} catch {
  Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
  if ($script:newTunnelPid) { Stop-TrackedTunnel $script:newTunnelPid }
  exit 1
}
