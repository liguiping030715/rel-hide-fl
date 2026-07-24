param(
    [string]$Seeds = "2024,2025,2026",
    [int]$Clients = 5,
    [int]$Dimension = 784,
    [string]$Noise = "dgg32",
    [int]$RingDim = 1024,
    [string]$OutDir = "results/preflight/formal_sanity_v1",
    [string]$ReleaseManifest = "manifests/formal_evaluation_release_v1.json"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $root $OutDir
New-Item -ItemType Directory -Force $runDir | Out-Null

$seedList = $Seeds.Split(",") | ForEach-Object { [int]$_.Trim() }
$rows = @()

function Invoke-WslBashScript($cmd, $stderrPath) {
    $tmpPath = [System.IO.Path]::GetTempFileName() + ".sh"
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($tmpPath, "#!/usr/bin/env bash`nset -e`n$cmd`n", $utf8NoBom)
    try {
        $fullTmpPath = [System.IO.Path]::GetFullPath($tmpPath)
        $drive = $fullTmpPath.Substring(0, 1).ToLowerInvariant()
        $rest = $fullTmpPath.Substring(2).Replace("\", "/")
        $wslPath = "/mnt/$drive$rest"
        $oldEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $stdout = wsl -d Ubuntu -- bash $wslPath 2> $stderrPath
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $oldEap
        return @{ Stdout = $stdout; ExitCode = $exitCode }
    } finally {
        Remove-Item -LiteralPath $tmpPath -Force -ErrorAction SilentlyContinue
    }
}

foreach ($seed in $seedList) {
    $caseId = "c${Clients}_d${Dimension}_${Noise}_s${seed}"
    $outPath = Join-Path $runDir "${caseId}.json"
    $cmd = "cd /home/liguiping/openfhe_splitpath_build && LD_LIBRARY_PATH=/home/liguiping/openfhe-install/lib:`$LD_LIBRARY_PATH ./openfhe_dcrtpoly_wire_integration --clients $Clients --dimension $Dimension --ring-dim $RingDim --noise $Noise --seed $seed --k 2 --k0 2"
    $started = Get-Date
    $stderrPath = Join-Path $runDir "${caseId}.stderr.txt"
    $run = Invoke-WslBashScript $cmd $stderrPath
    $stdout = $run.Stdout
    $exitCode = $run.ExitCode
    ($stdout -join "`n").Trim() | Set-Content -Path $outPath -Encoding UTF8
    $elapsed = ((Get-Date) - $started).TotalSeconds
    if ($exitCode -ne 0) {
        throw "case $caseId exited with code $exitCode; see $stderrPath"
    }
    if (-not (Test-Path $outPath) -or ((Get-Item $outPath).Length -eq 0)) {
        throw "case $caseId did not produce JSON output; see $stderrPath"
    }
    $json = Get-Content -Raw $outPath | ConvertFrom-Json
    $rows += [PSCustomObject]@{
        case_id = $caseId
        clients = $Clients
        dimension = $Dimension
        ring_dim = $RingDim
        noise = $Noise
        seed = $seed
        status = $json.status
        wire_serialization_roundtrip = $json.wire_serialization_roundtrip
        wire_aggregate_equals_local = $json.wire_aggregate_equals_local
        apbr_sum_preserved_all_paths = $json.apbr_sum_preserved_all_paths
        q_domain_diff_linf = $json.q_domain_diff_linf
        q_domain_mismatch_count = $json.q_domain_mismatch_count
        message_domain_diff_linf = $json.message_domain_diff_linf
        total_fragments_per_path = $json.total_fragments_per_path
        elapsed_seconds = [Math]::Round($elapsed, 6)
        result_file = $outPath
    }
    Write-Host "$caseId $($json.status) elapsed=$([Math]::Round($elapsed,3))s"
}

$csvPath = Join-Path $runDir "formal_sanity_matrix.csv"
$rows | Export-Csv -NoTypeInformation -Encoding UTF8 $csvPath

$allPass = -not ($rows | Where-Object {
    $_.status -ne "PASS" -or
    $_.wire_serialization_roundtrip -ne $true -or
    $_.wire_aggregate_equals_local -ne $true -or
    $_.apbr_sum_preserved_all_paths -ne $true -or
    [int]$_.q_domain_diff_linf -ne 0 -or
    [int]$_.q_domain_mismatch_count -ne 0
})

$summary = @{
    schema = "formal_sanity_summary_v1"
    status = $(if ($allPass) { "PASS" } else { "FAIL" })
    formal = $false
    release_manifest = $ReleaseManifest
    clients = $Clients
    dimension = $Dimension
    ring_dim = $RingDim
    noise = $Noise
    seeds = $seedList
    case_count = $rows.Count
    passed = ($rows | Where-Object { $_.status -eq "PASS" }).Count
    failed = ($rows | Where-Object { $_.status -ne "PASS" }).Count
    csv = $csvPath
    note = "Sanity run only. This validates release artifact generation before formal Evaluation."
}
$summaryPath = Join-Path $runDir "summary.json"
$summary | ConvertTo-Json -Depth 8 | Set-Content -Path $summaryPath -Encoding UTF8

if (-not $allPass) {
    exit 1
}
