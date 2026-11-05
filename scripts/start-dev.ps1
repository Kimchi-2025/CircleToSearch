param([switch]$Stop, [switch]$Check, [switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backendDir = Join-Path $projectRoot 'backend'
$frontendDir = Join-Path $projectRoot 'frontend'
$stateFile = Join-Path $projectRoot '.mobile-test\dev-processes.json'
$apiUrl = 'http://127.0.0.1:8001'
$viteUrl = 'http://127.0.0.1:5173'

# The manager owns this handle. Windows kills every process in the job even if
# the manager CMD is closed with X and PowerShell cannot run a finally block.
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class CircleDevJob {
    [StructLayout(LayoutKind.Sequential)]
    public struct BasicLimits {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct IoCounters {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct ExtendedLimits {
        public BasicLimits BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct StartupInfo {
        public uint cb;
        public string lpReserved, lpDesktop, lpTitle;
        public uint dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars;
        public uint dwFillAttribute, dwFlags;
        public ushort wShowWindow, cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct ProcessInfo {
        public IntPtr hProcess, hThread;
        public uint dwProcessId, dwThreadId;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimits info, int length);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr OpenProcess(uint access, bool inherit, uint processId);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool TerminateProcess(IntPtr process, uint exitCode);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CreateProcessW(string application, StringBuilder commandLine,
        IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles,
        uint creationFlags, IntPtr environment, string currentDirectory,
        ref StartupInfo startup, out ProcessInfo process);

    static Exception LastError(string action) {
        return new Win32Exception(Marshal.GetLastWin32Error(), action);
    }
    public static IntPtr Create() {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw LastError("CreateJobObject failed");
        return job;
    }
    public static void EnableKillOnClose(IntPtr job) {
        ExtendedLimits limits = new ExtendedLimits();
        limits.BasicLimitInformation.LimitFlags = 0x2000;
        if (!SetInformationJobObject(job, 9, ref limits, Marshal.SizeOf(typeof(ExtendedLimits))))
            throw LastError("SetInformationJobObject failed");
    }
    public static void Adopt(IntPtr job, int pid) {
        IntPtr process = OpenProcess(0x1101, false, (uint)pid);
        if (process == IntPtr.Zero) throw LastError("OpenProcess failed");
        try {
            bool alreadyInJob;
            if (!IsProcessInJob(process, job, out alreadyInJob)) throw LastError("IsProcessInJob failed");
            if (!alreadyInJob && !AssignProcessToJobObject(job, process))
                throw LastError("AssignProcessToJobObject failed");
        } finally { CloseHandle(process); }
    }
    public static int Start(IntPtr job, string commandLine, string workingDirectory) {
        StartupInfo startup = new StartupInfo();
        startup.cb = (uint)Marshal.SizeOf(typeof(StartupInfo));
        ProcessInfo process;
        // CREATE_SUSPENDED | CREATE_NEW_CONSOLE: children cannot start before job assignment.
        if (!CreateProcessW(null, new StringBuilder(commandLine), IntPtr.Zero, IntPtr.Zero,
                false, 0x14, IntPtr.Zero, workingDirectory, ref startup, out process))
            throw LastError("CreateProcessW failed");
        try {
            if (!AssignProcessToJobObject(job, process.hProcess)) {
                TerminateProcess(process.hProcess, 1);
                throw LastError("AssignProcessToJobObject failed");
            }
            if (ResumeThread(process.hThread) == UInt32.MaxValue) {
                TerminateProcess(process.hProcess, 1);
                throw LastError("ResumeThread failed");
            }
            return (int)process.dwProcessId;
        } finally {
            CloseHandle(process.hThread);
            CloseHandle(process.hProcess);
        }
    }
    public static void Close(IntPtr job) {
        if (job != IntPtr.Zero) CloseHandle(job);
    }
}
'@

function Get-ProcessInfo([int]$processId) {
  return Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
}

function Get-ProcessStamp($process) {
  return $process.CreationDate.ToUniversalTime().Ticks
}

function Get-PortOwner([int]$port) {
  $owners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
  if ($owners.Count -gt 1) { throw "Port $port has multiple listeners; no process was stopped." }
  if ($owners.Count -eq 0) { return $null }
  return Get-ProcessInfo $owners[0]
}

function Test-Identity([string]$service, $process) {
  if (-not $process -or -not $process.CommandLine) { return $false }
  if ($service -eq 'api') {
    $python = Join-Path $backendDir '.venv\Scripts\python.exe'
    return $process.Name -eq 'python.exe' -and
      $process.CommandLine.IndexOf($python, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
      $process.CommandLine -match 'uvicorn\s+main:app' -and
      $process.CommandLine -match '\-\-port\s+8001(?:\s|$)'
  }
  return $process.Name -eq 'node.exe' -and
    $process.CommandLine.IndexOf($frontendDir, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
    $process.CommandLine -match 'vite[\\/]+bin[\\/]+vite\.js'
}

function Test-Response([string]$service) {
  try {
    if ($service -eq 'api') {
      $health = Invoke-RestMethod "$apiUrl/health" -TimeoutSec 3
      $schema = Invoke-RestMethod "$apiUrl/openapi.json" -TimeoutSec 3
      return $health.status -eq 'ok' -and
        $null -ne $schema.paths.'/api/circle-search/segment'
    }
    $page = Invoke-WebRequest "$viteUrl/" -UseBasicParsing -TimeoutSec 3
    $module = Invoke-WebRequest "$viteUrl/src/services/api.js" -UseBasicParsing -TimeoutSec 3
    return $page.StatusCode -eq 200 -and
      $page.Content.Contains('Circle to Search') -and
      $module.Content.Contains('/api/circle-search/segment')
  } catch { return $false }
}

function Get-VerifiedService([string]$service, [switch]$AllowStarting) {
  $port = if ($service -eq 'api') { 8001 } else { 5173 }
  $process = Get-PortOwner $port
  if (-not $process) { return $null }
  if (-not (Test-Identity $service $process)) {
    throw "Port $port is used by PID $($process.ProcessId), but it is not a healthy CircleToSearch $service server. Nothing was stopped."
  }
  if (-not (Test-Response $service)) {
    if ($AllowStarting) { return $null }
    throw "Port $port is used by CircleToSearch $service PID $($process.ProcessId), but its HTTP checks failed. Nothing was stopped."
  }
  return $process
}

function Read-State {
  if (Test-Path -LiteralPath $stateFile) {
    try {
      $state = Get-Content -LiteralPath $stateFile -Encoding UTF8 -Raw | ConvertFrom-Json
      if ($state.PSObject.Properties.Name -notcontains 'manager') {
        $state | Add-Member -NotePropertyName manager -NotePropertyValue $null
      }
      return $state
    }
    catch { throw "Cannot read $stateFile. No process was stopped." }
  }
  return [pscustomobject]@{ api = $null; vite = $null; manager = $null }
}

function Save-State($state) {
  $state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $stateFile -Encoding UTF8
}

function Test-RecordedProcess([int]$processId, [long]$stamp) {
  $process = Get-ProcessInfo $processId
  if ($process -and (Get-ProcessStamp $process) -eq $stamp) { return $process }
  return $null
}

function Get-RecordedManager($record) {
  if (-not $record) { return $null }
  $process = Test-RecordedProcess ([int]$record.pid) ([long]$record.stamp)
  if ($process -and $process.Name -eq 'powershell.exe' -and $process.CommandLine -and
      $process.CommandLine.IndexOf((Join-Path $PSScriptRoot 'start-dev.ps1'),
        [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $process }
  return $null
}

function Adopt-Server([IntPtr]$job, $server) {
  [CircleDevJob]::Adopt($job, [int]$server.ProcessId)
  $processes = @(Get-CimInstance Win32_Process)
  $parents = @([int]$server.ProcessId)
  while ($parents.Count) {
    $children = @($processes | Where-Object { $parents -contains [int]$_.ParentProcessId })
    $parents = @()
    foreach ($child in $children) {
      [CircleDevJob]::Adopt($job, [int]$child.ProcessId)
      $parents += [int]$child.ProcessId
    }
  }
}

function Stop-Recorded([string]$service, $record) {
  if (-not $record) { return }
  $cmd = Test-RecordedProcess ([int]$record.cmd_pid) ([long]$record.cmd_stamp)
  $server = Test-RecordedProcess ([int]$record.server_pid) ([long]$record.server_stamp)
  if ($cmd -and $cmd.Name -eq 'cmd.exe' -and
      $cmd.CommandLine -like '*CircleToSearch*' -and
      $cmd.CommandLine.IndexOf($projectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
    Write-Host "Stopping this script's $service CMD tree (PID $($cmd.ProcessId))..."
    & taskkill.exe /T /F /PID $cmd.ProcessId | Out-Null
  } elseif ($server -and (Test-Identity $service $server)) {
    Write-Host "Stopping this script's remaining $service server tree (PID $($server.ProcessId))..."
    & taskkill.exe /T /F /PID $server.ProcessId | Out-Null
  } else {
    Write-Host "No matching script-owned $service process remains."
  }
}

function Start-Service([string]$service, [IntPtr]$job) {
  if ($service -eq 'api') {
    $python = Join-Path $backendDir '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw "Missing Python environment: $python" }
    $command = 'title CircleToSearch - FastAPI & cd /d "' + $backendDir +
      '" & "' + $python + '" -m uvicorn main:app --app-dir "' + $backendDir +
      '" --reload --host 127.0.0.1 --port 8001'
  } else {
    $node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
    $vite = Join-Path $frontendDir 'node_modules\vite\bin\vite.js'
    if (-not $node -or -not (Test-Path -LiteralPath $vite)) {
      throw 'Node.js or frontend/node_modules is missing. Run npm install in frontend.'
    }
    $command = 'title CircleToSearch - React Vite & cd /d "' + $frontendDir +
      '" & "' + $node + '" "' + $vite + '" --host 0.0.0.0 --port 5173 --strictPort'
  }
  Write-Host "Starting CircleToSearch $service in a managed CMD window..."
  $windowId = [CircleDevJob]::Start($job, ('"' + $env:ComSpec + '" /c ' + $command), $projectRoot)
  $windowInfo = Get-ProcessInfo $windowId
  if (-not $windowInfo) { throw "$service CMD window exited before startup." }
  $record = [pscustomobject]@{
    cmd_pid = $windowId
    cmd_stamp = Get-ProcessStamp $windowInfo
    server_pid = 0
    server_stamp = 0
  }
  $deadline = (Get-Date).AddSeconds(20)
  do {
    $server = Get-VerifiedService $service -AllowStarting
    if ($server) {
      $record.server_pid = $server.ProcessId
      $record.server_stamp = Get-ProcessStamp $server
      return $record
    }
    if (-not (Get-ProcessInfo $windowId)) { throw "$service exited during startup. Read its CMD window output." }
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)
  throw "$service did not start within 20 seconds. Read its CMD window output."
}

$jobHandle = [IntPtr]::Zero
try {
  $state = Read-State
  if ($Stop) {
    $manager = Get-RecordedManager $state.manager
    if ($manager) {
      Write-Host "Stopping CircleToSearch manager PID $($manager.ProcessId)..."
      Stop-Process -Id $manager.ProcessId -Force
      Start-Sleep -Milliseconds 750
    }
    # Also handles old state files and a server that escaped a former manager.
    foreach ($service in @('vite', 'api')) {
      $record = $state.$service
      if ($record -and (Test-RecordedProcess ([int]$record.server_pid) ([long]$record.server_stamp))) {
        Stop-Recorded $service $record
      }
    }
    if (Test-Path -LiteralPath $stateFile) { Remove-Item -LiteralPath $stateFile -Force }
    Write-Host 'Only verified CircleToSearch servers owned by this manager were stopped.'
    exit 0
  }

  $existing = @{}
  foreach ($service in @('api', 'vite')) {
    $existing[$service] = Get-VerifiedService $service
    if ($existing[$service]) {
      $port = if ($service -eq 'api') { 8001 } else { 5173 }
      Write-Host "Found CircleToSearch $service on port $port (PID $($existing[$service].ProcessId))."
    } elseif ($Check) {
      Write-Host "CircleToSearch $service is not running."
    }
  }
  if ($Check) { exit 0 }

  $priorManager = Get-RecordedManager $state.manager
  if ($priorManager) {
    if (-not $existing.api -or -not $existing.vite) {
      throw 'A manager is already running but a server stopped. Close that manager and start again.'
    }
    Write-Host "Reusing both servers under existing manager PID $($priorManager.ProcessId)."
    exit 0
  }

  $jobHandle = [CircleDevJob]::Create()
  # Adopt only processes verified as this project's healthy servers. A failure
  # here leaves them running because KILL_ON_JOB_CLOSE is enabled afterward.
  foreach ($service in @('api', 'vite')) {
    if ($existing[$service]) { Adopt-Server $jobHandle $existing[$service] }
  }
  [CircleDevJob]::EnableKillOnClose($jobHandle)

  foreach ($service in @('api', 'vite')) {
    if ($existing[$service]) {
      $state.$service = [pscustomobject]@{
        cmd_pid = 0; cmd_stamp = 0
        server_pid = $existing[$service].ProcessId
        server_stamp = Get-ProcessStamp $existing[$service]
      }
    } else {
      $state.$service = Start-Service $service $jobHandle
    }
  }
  $managerInfo = Get-ProcessInfo $PID
  $state.manager = [pscustomobject]@{ pid = $PID; stamp = Get-ProcessStamp $managerInfo }
  New-Item -ItemType Directory -Path (Split-Path $stateFile) -Force | Out-Null
  Save-State $state
  Write-Host 'FastAPI: http://127.0.0.1:8001   React: http://localhost:5173'
  Write-Host 'Keep this manager CMD open. X or Ctrl+C stops both servers.'
  Write-Host 'Press ENTER here to stop both servers, or use start-dev.bat --stop.'
  if (-not $NoBrowser) {
    try { Start-Process 'http://localhost:5173/' | Out-Null }
    catch { Write-Host "Browser did not open automatically: $($_.Exception.Message)" }
  }
  $answer = Read-Host 'Press ENTER to stop both servers'
  if ($null -eq $answer) { while ($true) { Start-Sleep -Seconds 1 } }
} catch {
  Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
} finally {
  # Normal exit is prompt; X also closes this process's job handle in Windows.
  [CircleDevJob]::Close($jobHandle)
  if ($jobHandle -ne [IntPtr]::Zero -and (Test-Path -LiteralPath $stateFile)) {
    try {
      $saved = Read-State
      if ($saved.manager -and [int]$saved.manager.pid -eq $PID) {
        Remove-Item -LiteralPath $stateFile -Force
      }
    } catch { }
  }
}
