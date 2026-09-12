[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$InstallationDirectory = "",
    [ValidateRange(1024, 65535)][int]$BackendPort = 18080,
    [ValidateRange(1, 16)][int]$WebWorkers = 1,
    [string]$Python = "",
    [switch]$Restart,
    [switch]$StopOnly,
    [switch]$CheckOnly,
    [switch]$SkipMigrations,
    [switch]$LocalHttp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$serverRoot = Join-Path $repoRoot "nexus_server"
if (-not (Test-Path -LiteralPath (Join-Path $serverRoot "nexus_personal\processes.py") -PathType Leaf)) {
    throw "COMMUNITY_SOURCE_INVALID: Run this script from the Nexus Cloud Community source root."
}
if ([string]::IsNullOrWhiteSpace($InstallationDirectory)) {
    $localData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    if ([string]::IsNullOrWhiteSpace($localData)) {
        throw "COMMUNITY_INSTALLATION_REQUIRED: Pass -InstallationDirectory with an absolute prepared installation path."
    }
    $InstallationDirectory = Join-Path $localData "Nexus\Community"
}
if (-not [IO.Path]::IsPathRooted($InstallationDirectory)) {
    throw "COMMUNITY_INSTALLATION_INVALID: InstallationDirectory must be absolute."
}
$installationRoot = [IO.Path]::GetFullPath($InstallationDirectory)
$candidate = [IO.DirectoryInfo]::new($installationRoot)
while ($null -ne $candidate) {
    if ($candidate.Exists -and (($candidate.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "COMMUNITY_INSTALLATION_INVALID: Installation paths must not use links or junctions."
    }
    $candidate = $candidate.Parent
}
$runRoot = Join-Path $installationRoot "run"
$pidPath = Join-Path $runRoot "community-processes.json"
$logRoot = Join-Path $runRoot "logs"

function Read-ProcessState {
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) { return $null }
    try {
        $state = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
        if ($state.schema_version -ne 1 -or [string]$state.installation_root -ne $installationRoot) { throw "state mismatch" }
        return $state
    }
    catch { throw "COMMUNITY_PROCESS_STATE_INVALID: Inspect $pidPath; no process was stopped." }
}

function Test-TrackedIdentity([object]$Entry) {
    try {
        $processId = [int]$Entry.pid
        $process = Get-Process -Id $processId -ErrorAction Stop
        $identity = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction Stop
        $expectedStart = ([DateTimeOffset]$Entry.started_at).UtcDateTime
        $command = [string]$identity.CommandLine
        $servicePattern = '(?:^|\s)"?' + [Regex]::Escape([string]$Entry.service) + '"?(?:\s|$)'
        return (($identity.Name -match '^python(?:\.exe)?$') -and
            ($process.StartTime.ToUniversalTime().Ticks -eq $expectedStart.Ticks) -and
            $command.Contains('nexus_personal.processes') -and
            ($command -match $servicePattern))
    }
    catch { return $false }
}

function Stop-CommunityProcesses {
    $state = Read-ProcessState
    if ($null -eq $state) { return }
    foreach ($entry in @($state.processes)) {
        $process = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
        if ($null -eq $process) { continue }
        if (-not (Test-TrackedIdentity $entry)) {
            throw "COMMUNITY_PROCESS_IDENTITY_MISMATCH: PID $($entry.pid) was not stopped."
        }
        Stop-Process -Id ([int]$entry.pid) -Force
    }
    Remove-Item -LiteralPath $pidPath -Force
}

if (($StopOnly -and ($Restart -or $CheckOnly)) -or ($Restart -and $CheckOnly)) {
    throw "COMMUNITY_ARGUMENTS_INVALID: Use only one of -Restart, -StopOnly or -CheckOnly."
}

if ($StopOnly) {
    Stop-CommunityProcesses
    Write-Host "Nexus Cloud Community processes stopped. Database and installation data were retained."
    exit 0
}

if (-not (Test-Path -LiteralPath $installationRoot -PathType Container) -or
    -not (Test-Path -LiteralPath (Join-Path $installationRoot "host.json") -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $installationRoot "installation.json") -PathType Leaf)) {
    throw "COMMUNITY_INSTALLATION_REQUIRED: Prepare and initialize the installation first; see nexus_server/nexus_personal/HOST.md."
}
if ([string]::IsNullOrWhiteSpace($Python)) { $Python = (Get-Command python -ErrorAction Stop).Source }
$pythonPath = [IO.Path]::GetFullPath($Python)
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "COMMUNITY_PYTHON_INVALID: Python must name an existing interpreter."
}

$existing = Read-ProcessState
if ($CheckOnly -and $null -ne $existing) {
    foreach ($entry in @($existing.processes)) {
        if ((Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue) -and
            -not (Test-TrackedIdentity $entry)) {
            throw "COMMUNITY_PROCESS_IDENTITY_MISMATCH: PID $($entry.pid) does not belong to this installation."
        }
    }
}
elseif ($null -ne $existing) {
    $live = @($existing.processes | Where-Object { Get-Process -Id ([int]$_.pid) -ErrorAction SilentlyContinue })
    if ($live.Count -and -not $Restart) { throw "COMMUNITY_ALREADY_RUNNING: Use -Restart or -StopOnly." }
    Stop-CommunityProcesses
}
if (-not $CheckOnly) {
    $listener = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) { throw "COMMUNITY_PORT_IN_USE: Port $BackendPort is used by PID $($listener.OwningProcess)." }
}

$previousConfig = [Environment]::GetEnvironmentVariable('NEXUS_PERSONAL_CONFIG', 'Process')
$previousSettings = [Environment]::GetEnvironmentVariable('DJANGO_SETTINGS_MODULE', 'Process')
$previousRole = [Environment]::GetEnvironmentVariable('NEXUS_PROCESS_ROLE', 'Process')
$previousLocalHttp = [Environment]::GetEnvironmentVariable('NEXUS_PERSONAL_LOCAL_HTTP_PORT', 'Process')
$configPath = Join-Path $installationRoot "host.json"
$started = [Collections.Generic.List[object]]::new()
Push-Location -LiteralPath $serverRoot
try {
    $env:NEXUS_PERSONAL_CONFIG = $configPath
    $env:DJANGO_SETTINGS_MODULE = 'nexus_personal.settings'
    Remove-Item Env:\NEXUS_PROCESS_ROLE -ErrorAction SilentlyContinue
    if ($LocalHttp) { $env:NEXUS_PERSONAL_LOCAL_HTTP_PORT = [string]$BackendPort }
    else { Remove-Item Env:\NEXUS_PERSONAL_LOCAL_HTTP_PORT -ErrorAction SilentlyContinue }

    & $pythonPath -m nexus_personal.install check --directory $installationRoot
    if ($LASTEXITCODE -ne 0) { throw "COMMUNITY_INSTALLATION_CHECK_FAILED" }
    if ($SkipMigrations -or $CheckOnly) { & $pythonPath -m nexus_personal.manage migrate --check }
    else { & $pythonPath -m nexus_personal.manage migrate --noinput }
    if ($LASTEXITCODE -ne 0) { throw "COMMUNITY_DATABASE_NOT_READY" }
    if ($CheckOnly) {
        $runningCount = if ($null -eq $existing) { 0 } else {
            @($existing.processes | Where-Object { Get-Process -Id ([int]$_.pid) -ErrorAction SilentlyContinue }).Count
        }
        Write-Host "Nexus Cloud Community installation and database checks passed. Tracked processes running: $runningCount."
        exit 0
    }

    New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
    $check = & $pythonPath -m nexus_personal.install check --directory $installationRoot | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw "COMMUNITY_INSTALLATION_CHECK_FAILED" }
    $services = [Collections.Generic.List[string]]::new()
    foreach ($controller in @($check.controllers_configured)) { $services.Add("$controller-controller") }
    if ([bool]$check.python_builder_enabled) { $services.Add('python-builder') }
    foreach ($service in @('worker', 'agent-worker', 'beat', 'web')) { $services.Add($service) }

    foreach ($service in $services) {
        $arguments = @('-m', 'nexus_personal.processes', $service)
        if ($service -eq 'web') {
            $arguments += @('--port', [string]$BackendPort, '--web-workers', [string]$WebWorkers)
            if ($LocalHttp) { $arguments += '--local-http' }
        }
        $process = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $serverRoot `
            -RedirectStandardOutput (Join-Path $logRoot "$service.stdout.log") `
            -RedirectStandardError (Join-Path $logRoot "$service.stderr.log") -WindowStyle Hidden -PassThru
        $process.Refresh()
        $started.Add([ordered]@{ service = $service; pid = $process.Id; started_at = $process.StartTime.ToUniversalTime().ToString('o') })
    }

    Start-Sleep -Seconds 2
    foreach ($entry in $started) {
        if (-not (Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue)) {
            throw "COMMUNITY_PROCESS_START_FAILED: $($entry.service); inspect $logRoot."
        }
    }

    $hostConfig = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $origin = [Uri][string]$hostConfig.public_origin
    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get,
        "http://127.0.0.1:$BackendPort/api/v1/public/bootstrap/")
    if ($LocalHttp) { $request.Headers.Host = "127.0.0.1:$BackendPort" }
    else {
        $request.Headers.Host = $origin.Authority
        [void]$request.Headers.TryAddWithoutValidation('X-Forwarded-Proto', 'https')
    }
    $client = [Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds(10)
    try { $response = $client.Send($request) }
    finally { $request.Dispose(); $client.Dispose() }
    if ([int]$response.StatusCode -ne 200) { throw "COMMUNITY_HEALTH_CHECK_FAILED: HTTP $([int]$response.StatusCode)." }

    $browserOrigin = if ($LocalHttp) { "http://127.0.0.1:$BackendPort" } else { $origin.GetLeftPart([UriPartial]::Authority) }
    $state = [ordered]@{ schema_version = 1; installation_root = $installationRoot
        public_origin = $browserOrigin; backend = "http://127.0.0.1:$BackendPort"; local_http = [bool]$LocalHttp
        processes = @($started); started_at = [DateTimeOffset]::UtcNow.ToString('o') }
    [IO.File]::WriteAllText($pidPath, ($state | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
    if ($LocalHttp) {
        Write-Host "Nexus Cloud Community local test is ready at $browserOrigin."
        Write-Host "LocalHttp is loopback-only and must not be exposed as a production service. Logs: $logRoot"
    }
    else {
        Write-Host "Nexus Cloud Community backend is ready. Open the configured HTTPS proxy at $browserOrigin."
        Write-Host "Loopback backend: $($state.backend) (do not expose directly). Logs: $logRoot"
    }
}
catch {
    foreach ($entry in $started) {
        $process = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
        if ($process) { Stop-Process -Id $process.Id -Force }
    }
    throw
}
finally {
    Pop-Location
    [Environment]::SetEnvironmentVariable('NEXUS_PERSONAL_CONFIG', $previousConfig, 'Process')
    [Environment]::SetEnvironmentVariable('DJANGO_SETTINGS_MODULE', $previousSettings, 'Process')
    [Environment]::SetEnvironmentVariable('NEXUS_PROCESS_ROLE', $previousRole, 'Process')
    [Environment]::SetEnvironmentVariable('NEXUS_PERSONAL_LOCAL_HTTP_PORT', $previousLocalHttp, 'Process')
}
