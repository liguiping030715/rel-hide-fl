param(
    [string]$Config = "configs/default.json",
    [string]$CaseId = "c2_d16_zero",
    [string]$OutDir = "results/preflight"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $root $Config
if (-not (Test-Path $configPath)) {
    throw "Config file not found: $configPath"
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$runId = "${CaseId}_${timestamp}"
$runDir = Join-Path (Join-Path $root $OutDir) $runId
New-Item -ItemType Directory -Force $runDir | Out-Null

$envInfo = @{
    run_id = $runId
    formal = $false
    case_id = $CaseId
    config_path = $configPath
    host = $env:COMPUTERNAME
    user = $env:USERNAME
    os = (Get-CimInstance Win32_OperatingSystem).Caption
    powershell = $PSVersionTable.PSVersion.ToString()
    docker_available = [bool](Get-Command docker -ErrorAction SilentlyContinue)
    wsl_available = [bool](Get-Command wsl -ErrorAction SilentlyContinue)
    status = "ENV_PROBE_ONLY_NOT_PROTOCOL_RUN"
}

$envInfo | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $runDir "environment_probe.json") -Encoding UTF8

Write-Host "Created preflight environment probe: $runDir"
Write-Host "This script does not yet run the protocol binary."
