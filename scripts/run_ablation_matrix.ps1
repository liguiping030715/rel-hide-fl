param(
    [string]$Ablations = "full,no_dummy,no_apbr,k1",
    [int]$Clients = 30,
    [int]$Dimension = 3072,
    [string]$Seeds = "2024,2025,2026",
    [int]$Repetitions = 5,
    [int]$RingDim = 16384,
    [string]$PlaintextModulus = "2199023288321",
    [string]$Noise = "dgg32",
    [string]$OutDir = "results/formal/ablations/protocol_consistent_release_v7_idempotent_c30_d3072",
    [string]$ReleaseManifest = "manifests/formal_evaluation_release_v7_idempotent_polysubr.json"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $root $OutDir
$rawDir = Join-Path $runDir "raw"
New-Item -ItemType Directory -Force $runDir | Out-Null
New-Item -ItemType Directory -Force $rawDir | Out-Null

function RelPath($path) {
    $resolvedRoot = (Resolve-Path $root).Path
    $resolvedPath = (Resolve-Path $path).Path
    return $resolvedPath.Substring($resolvedRoot.Length + 1).Replace("\", "/")
}

function HashFile($path) {
    return (Get-FileHash -Algorithm SHA256 -Path $path).Hash
}

function Write-Json($path, $obj, $depth = 18) {
    $obj | ConvertTo-Json -Depth $depth | Set-Content -Path $path -Encoding UTF8
}

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

function AblationArgs($name) {
    switch ($name) {
        "full" { return @{ k = 2; k0 = 2; apbr = "true"; label = "Full protocol" } }
        "no_dummy" { return @{ k = 2; k0 = 0; apbr = "true"; label = "Full protocol without dummy padding" } }
        "no_apbr" { return @{ k = 2; k0 = 2; apbr = "false"; label = "Full protocol without APBR refresh" } }
        "k1" { return @{ k = 1; k0 = 2; apbr = "true"; label = "Fragmentation with k=1" } }
        default { throw "unknown ablation: $name" }
    }
}

$releasePath = Join-Path $root $ReleaseManifest
if (-not (Test-Path $releasePath)) {
    throw "release manifest not found: $ReleaseManifest"
}
$release = Get-Content -Raw $releasePath | ConvertFrom-Json
$wslBinDir = $release.implementation.wsl_binary_dir
if ([string]::IsNullOrWhiteSpace($wslBinDir)) { throw "release manifest missing implementation.wsl_binary_dir" }
$binRel = $release.source_and_binary.wire_integration_binary
$binPath = Join-Path $root $binRel
$binSha = HashFile $binPath
if ($binSha -ne $release.source_and_binary.wire_integration_binary_sha256) {
    throw "wire integration binary hash mismatch"
}

$ablationList = $Ablations.Split(",") | ForEach-Object { $_.Trim() }
$seedList = $Seeds.Split(",") | ForEach-Object { [int]$_.Trim() }
$total = $ablationList.Count * $seedList.Count * $Repetitions
$finished = 0
$failed = 0
$rows = @()
$startedAll = Get-Date

foreach ($ablation in $ablationList) {
    $cfg = AblationArgs $ablation
    foreach ($seed in $seedList) {
        for ($rep = 1; $rep -le $Repetitions; ++$rep) {
            $caseId = "${ablation}_c${Clients}_d${Dimension}_s${seed}_r$($rep.ToString('000'))"
            $outPath = Join-Path $runDir "${caseId}.json"
            $rawPath = Join-Path $rawDir "${caseId}.raw.json"
            $stderrPath = Join-Path $rawDir "${caseId}.stderr.txt"
            $cmd = "cd $wslBinDir && LD_LIBRARY_PATH=/home/liguiping/openfhe-install/lib:`$LD_LIBRARY_PATH ./openfhe_dcrtpoly_wire_integration --variant full_protocol --clients $Clients --dimension $Dimension --ring-dim $RingDim --plaintext-modulus $PlaintextModulus --noise $Noise --seed $seed --k $($cfg.k) --k0 $($cfg.k0) --apbr $($cfg.apbr)"

            $caseStarted = Get-Date
            $run = Invoke-WslBashScript $cmd $stderrPath
            $stdout = $run.Stdout
            $exitCode = $run.ExitCode
            $elapsed = ((Get-Date) - $caseStarted).TotalSeconds
            $stdoutText = ($stdout -join "`n").Trim()
            $stdoutText | Set-Content -Path $rawPath -Encoding UTF8

            $status = "PASS"
            $errorMessage = $null
            $raw = $null
            try {
                if ($exitCode -ne 0) { throw "binary exited with code $exitCode" }
                if ([string]::IsNullOrWhiteSpace($stdoutText)) { throw "empty stdout" }
                $raw = $stdoutText | ConvertFrom-Json
                if ($raw.status -ne "PASS" -or [int64]$raw.encoded_plaintext_diff_linf -ne 0 -or [int64]$raw.encoded_plaintext_mismatch_count -ne 0) {
                    throw "required correctness checks failed"
                }
            } catch {
                $status = "FAIL"
                $errorMessage = $_.Exception.Message
                $failed += 1
            }

            $sample = [ordered]@{
                schema = "ablation_sample_v1"
                formal = $true
                experiment = [ordered]@{
                    type = "ablation"
                    ablation = $ablation
                    label = $cfg.label
                    case_id = $caseId
                }
                release = [ordered]@{
                    manifest = $ReleaseManifest
                    manifest_sha256 = HashFile $releasePath
                }
                parameters = [ordered]@{
                    clients = $Clients
                    dimension = $Dimension
                    ring_dim = $RingDim
                    plaintext_modulus = $PlaintextModulus
                    seed = $seed
                    repetition = $rep
                    k = $cfg.k
                    k0 = $cfg.k0
                    apbr = [bool]($cfg.apbr -eq "true")
                    noise = $Noise
                }
                correctness = [ordered]@{
                    encoded_plaintext_diff_linf = $(if ($raw) { $raw.encoded_plaintext_diff_linf } else { $null })
                    encoded_plaintext_mismatch_count = $(if ($raw) { $raw.encoded_plaintext_mismatch_count } else { $null })
                    q_domain_diff_linf = $(if ($raw) { $raw.q_domain_diff_linf } else { $null })
                    q_domain_mismatch_count = $(if ($raw) { $raw.q_domain_mismatch_count } else { $null })
                    status_pass = ($status -eq "PASS")
                }
                runtime_ms = $(if ($raw) { $raw.runtime_ms } else { $null })
                communication = [ordered]@{
                    total_bytes = $(if ($raw) { $raw.total_wire_bytes } else { $null })
                    real_fragments_per_path = $(if ($raw) { $raw.real_fragments_per_path } else { $null })
                    dummy_fragments_per_path = $(if ($raw) { $raw.dummy_fragments_per_path } else { $null })
                    total_fragments_per_path = $(if ($raw) { $raw.total_fragments_per_path } else { $null })
                }
                material = [ordered]@{
                    formula = $(if ($raw) { $raw.material_formula } else { $null })
                    public_a_sampler = $(if ($raw) { $raw.public_a_sampler } else { $null })
                    secret_sampler = $(if ($raw) { $raw.secret_sampler } else { $null })
                    error_sampler = $(if ($raw) { $raw.error_sampler } else { $null })
                }
                raw_result_file = RelPath $rawPath
                stderr_file = RelPath $stderrPath
                wrapper_elapsed_seconds = [Math]::Round($elapsed, 6)
                status = $status
                error_message = $errorMessage
            }
            Write-Json $outPath $sample 20

            $rows += [PSCustomObject]@{
                case_id = $caseId
                ablation = $ablation
                clients = $Clients
                dimension = $Dimension
                seed = $seed
                repetition = $rep
                k = $cfg.k
                k0 = $cfg.k0
                apbr = $cfg.apbr
                status = $status
                encoded_plaintext_diff_linf = $sample.correctness.encoded_plaintext_diff_linf
                encoded_plaintext_mismatch_count = $sample.correctness.encoded_plaintext_mismatch_count
                q_domain_diff_linf = $sample.correctness.q_domain_diff_linf
                q_domain_mismatch_count = $sample.correctness.q_domain_mismatch_count
                total_ms = $sample.runtime_ms.total
                path_processing_ms = $sample.runtime_ms.path_processing
                cs_recovery_ms = $sample.runtime_ms.cs_recovery
                total_bytes = $sample.communication.total_bytes
                total_fragments_per_path = $sample.communication.total_fragments_per_path
                result_file = RelPath $outPath
            }

            $finished += 1
            $progress = [ordered]@{
                schema = "ablation_progress_v1"
                total_cases = $total
                finished = $finished
                failed = $failed
                current = $caseId
                elapsed_hours = [Math]::Round(((Get-Date) - $startedAll).TotalHours, 6)
                formal = $true
            }
            Write-Json (Join-Path $runDir "progress.json") $progress 8
            Write-Host "$caseId $status elapsed=$([Math]::Round($elapsed,3))s"
            if ($status -ne "PASS") {
                throw "ablation case failed: $caseId ($errorMessage)"
            }
        }
    }
}

$csvPath = Join-Path $runDir "ablation_matrix.csv"
$rows | Export-Csv -NoTypeInformation -Encoding UTF8 $csvPath

$summary = [ordered]@{
    schema = "ablation_summary_v1"
    status = $(if ($failed -eq 0) { "PASS" } else { "FAIL" })
    formal = $true
    release_manifest = $ReleaseManifest
    release_manifest_sha256 = HashFile $releasePath
    ablations = $ablationList
    clients = $Clients
    dimension = $Dimension
    ring_dim = $RingDim
    seeds = $seedList
    repetitions = $Repetitions
    sample_count = $rows.Count
    passed = ($rows | Where-Object { $_.status -eq "PASS" }).Count
    failed = $failed
    max_q_domain_diff_linf = ($rows | Measure-Object -Property q_domain_diff_linf -Maximum).Maximum
    total_q_domain_mismatch_count = ($rows | Measure-Object -Property q_domain_mismatch_count -Sum).Sum
    max_encoded_plaintext_diff_linf = ($rows | Measure-Object -Property encoded_plaintext_diff_linf -Maximum).Maximum
    total_encoded_plaintext_mismatch_count = ($rows | Measure-Object -Property encoded_plaintext_mismatch_count -Sum).Sum
    csv = RelPath $csvPath
    progress = RelPath (Join-Path $runDir "progress.json")
    output_dir = RelPath $runDir
}
Write-Json (Join-Path $runDir "summary.json") $summary 12
