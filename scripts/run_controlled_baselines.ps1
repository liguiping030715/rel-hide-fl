param(
    [string]$Variants = "plain_aggregate,shamir_shuffle_proxy,openfhe_bgv_only,four_path_sum_only,shuffle_only,full_protocol",
    [string]$Clients = "10,20,30,40,50",
    [int]$Dimension = 784,
    [string]$Seeds = "2024,2025,2026",
    [int]$Repetitions = 5,
    [int]$RingDim = 16384,
    [string]$PlaintextModulus = "2199023288321",
    [string]$Noise = "dgg32",
    [string]$OutDir = "results/formal/baselines/protocol_consistent_release_v7_idempotent_d784",
    [string]$ReleaseManifest = "manifests/formal_evaluation_release_v7_idempotent_polysubr.json",
    [switch]$SkipManifestCheck
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$wslBinDir = "/home/liguiping/openfhe_splitpath_build_v8_rc1"
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
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $stdout = wsl -d Ubuntu -- bash -lc "set -e; $cmd" 2> $stderrPath
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldEap
    return @{ Stdout = $stdout; ExitCode = $exitCode }
}

$releasePath = Join-Path $root $ReleaseManifest
$releaseManifestSha = $null
if (-not $SkipManifestCheck) {
    if (-not (Test-Path $releasePath)) {
        throw "release manifest not found: $ReleaseManifest"
    }
    $release = Get-Content -Raw $releasePath | ConvertFrom-Json
    $releaseManifestSha = HashFile $releasePath
    $wslBinDir = $release.implementation.wsl_binary_dir
    if ([string]::IsNullOrWhiteSpace($wslBinDir)) { throw "release manifest missing implementation.wsl_binary_dir" }

    $wireBinRel = $release.source_and_binary.wire_integration_binary
    $wireBin = Join-Path $root $wireBinRel
    $wireSha = HashFile $wireBin
    if ($wireSha -ne $release.source_and_binary.wire_integration_binary_sha256) {
        throw "wire integration binary hash mismatch"
    }

    $bgvBinRel = $release.source_and_binary.bgv_only_binary
    $bgvBin = Join-Path $root $bgvBinRel
    $bgvSha = HashFile $bgvBin
    if ($bgvSha -ne $release.source_and_binary.bgv_only_binary_sha256) {
        throw "BGV-only binary hash mismatch"
    }

    $shamirBinRel = $release.source_and_binary.shamir_proxy_binary
    $shamirBin = Join-Path $root $shamirBinRel
    $shamirSha = HashFile $shamirBin
    if ($shamirSha -ne $release.source_and_binary.shamir_proxy_binary_sha256) {
        throw "Shamir proxy binary hash mismatch"
    }
} elseif (Test-Path $releasePath) {
    $releaseManifestSha = HashFile $releasePath
}

$variantList = $Variants.Split(",") | ForEach-Object { $_.Trim() }
$clientList = $Clients.Split(",") | ForEach-Object { [int]$_.Trim() }
$seedList = $Seeds.Split(",") | ForEach-Object { [int]$_.Trim() }
$total = $variantList.Count * $clientList.Count * $seedList.Count * $Repetitions
$finished = 0
$failed = 0
$rows = @()
$startedAll = Get-Date

foreach ($variant in $variantList) {
    foreach ($clientsValue in $clientList) {
        foreach ($seed in $seedList) {
            for ($rep = 1; $rep -le $Repetitions; ++$rep) {
                $caseId = "${variant}_c${clientsValue}_d${Dimension}_s${seed}_r$($rep.ToString('000'))"
                $outPath = Join-Path $runDir "${caseId}.json"
                $rawPath = Join-Path $rawDir "${caseId}.raw.json"
                $stderrPath = Join-Path $rawDir "${caseId}.stderr.txt"
                if ($variant -eq "openfhe_bgv_only") {
                    $cmd = "cd $wslBinDir && LD_LIBRARY_PATH=/home/liguiping/openfhe-install/lib:`$LD_LIBRARY_PATH ./openfhe_bgv_only_baseline --clients $clientsValue --dimension $Dimension --ring-dim $RingDim --seed $seed"
                } elseif ($variant -eq "shamir_shuffle_proxy") {
                    $cmd = "cd $wslBinDir && LD_LIBRARY_PATH=/home/liguiping/openfhe-install/lib:`$LD_LIBRARY_PATH ./shamir_shuffle_proxy_baseline --clients $clientsValue --dimension $Dimension --plaintext-modulus $PlaintextModulus --seed $seed"
                } else {
                    $cmd = "cd $wslBinDir && LD_LIBRARY_PATH=/home/liguiping/openfhe-install/lib:`$LD_LIBRARY_PATH ./openfhe_dcrtpoly_wire_integration --variant $variant --clients $clientsValue --dimension $Dimension --ring-dim $RingDim --plaintext-modulus $PlaintextModulus --noise $Noise --seed $seed --k 2 --k0 2"
                }

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

                $runtime = $raw.runtime_ms
                $clientUploadBytes = $null
                $pathToCsBytes = $null
                $totalPayloadBytes = $null
                if ($raw) {
                    if ($null -ne $raw.client_upload_bytes) { $clientUploadBytes = [int64]$raw.client_upload_bytes }
                    if ($null -ne $raw.path_to_cs_bytes) { $pathToCsBytes = [int64]$raw.path_to_cs_bytes }
                    if ($null -ne $raw.total_payload_bytes) { $totalPayloadBytes = [int64]$raw.total_payload_bytes }
                    if ($null -eq $clientUploadBytes -or $null -eq $pathToCsBytes -or $null -eq $totalPayloadBytes) {
                        $fallbackBytes = [int64]$raw.total_wire_bytes
                        switch ($variant) {
                            "plain_aggregate" {
                                $clientUploadBytes = 0
                                $pathToCsBytes = 0
                                $totalPayloadBytes = 0
                            }
                            "openfhe_bgv_only" {
                                $clientUploadBytes = $fallbackBytes
                                $pathToCsBytes = 0
                                $totalPayloadBytes = $fallbackBytes
                            }
                            default {
                                if ($null -eq $clientUploadBytes) { $clientUploadBytes = 0 }
                                if ($null -eq $pathToCsBytes) { $pathToCsBytes = $fallbackBytes }
                                if ($null -eq $totalPayloadBytes) { $totalPayloadBytes = $clientUploadBytes + $pathToCsBytes }
                            }
                        }
                    }
                }
                $sample = [ordered]@{
                    schema = "controlled_baseline_sample_v1"
                    formal = $true
                    experiment = [ordered]@{
                        type = "controlled_baseline"
                        variant = $variant
                        case_id = $caseId
                    }
                    release = [ordered]@{
                        manifest = $ReleaseManifest
                        manifest_sha256 = $releaseManifestSha
                        manifest_check_skipped = [bool]$SkipManifestCheck
                    }
                    parameters = [ordered]@{
                        clients = $clientsValue
                        dimension = $Dimension
                        ring_dim = $RingDim
                        plaintext_modulus = $PlaintextModulus
                        seed = $seed
                        repetition = $rep
                        k = 2
                        k0 = 2
                        noise = $Noise
                    }
                    correctness = [ordered]@{
                        encoded_plaintext_diff_linf = $(if ($raw) { $raw.encoded_plaintext_diff_linf } else { $null })
                        encoded_plaintext_mismatch_count = $(if ($raw) { $raw.encoded_plaintext_mismatch_count } else { $null })
                        q_domain_diff_linf = $(if ($raw) { $raw.q_domain_diff_linf } else { $null })
                        q_domain_mismatch_count = $(if ($raw) { $raw.q_domain_mismatch_count } else { $null })
                        status_pass = ($status -eq "PASS")
                    }
                    runtime_ms = $runtime
                    communication = [ordered]@{
                        client_upload_bytes = $clientUploadBytes
                        path_to_cs_bytes = $pathToCsBytes
                        total_bytes = $totalPayloadBytes
                        legacy_total_wire_bytes = $(if ($raw) { $raw.total_wire_bytes } else { $null })
                        scope = $(switch ($variant) {
                            "plain_aggregate" { "plaintext vector sum; no cryptographic wire bytes counted" }
                            "shamir_shuffle_proxy" { "same-platform Shamir-style two-shuffler plaintext-share proxy; not a faithful UFL reimplementation" }
                            "openfhe_bgv_only" { "serialized native OpenFHE BGV ciphertext bytes from clients to aggregator" }
                            "four_path_sum_only" { "four-path aggregate-only DCRTPoly key/body shares; one local aggregate record per path" }
                            "shuffle_only" { "serialized DCRTPoly APBR/SplitMix relay fragment bytes without RLWE key-removal semantics" }
                            "full_protocol" { "serialized DCRTPoly APBR-SplitMix relay fragment bytes" }
                        })
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
                    variant = $variant
                    clients = $clientsValue
                    dimension = $Dimension
                    seed = $seed
                    repetition = $rep
                    status = $status
                    encoded_plaintext_diff_linf = $sample.correctness.encoded_plaintext_diff_linf
                    encoded_plaintext_mismatch_count = $sample.correctness.encoded_plaintext_mismatch_count
                    q_domain_diff_linf = $sample.correctness.q_domain_diff_linf
                    q_domain_mismatch_count = $sample.correctness.q_domain_mismatch_count
                    total_ms = $runtime.total
                    client_upload_bytes = $sample.communication.client_upload_bytes
                    path_to_cs_bytes = $sample.communication.path_to_cs_bytes
                    total_bytes = $sample.communication.total_bytes
                    legacy_total_wire_bytes = $sample.communication.legacy_total_wire_bytes
                    wrapper_elapsed_seconds = $sample.wrapper_elapsed_seconds
                    result_file = RelPath $outPath
                }

                $finished += 1
                $progress = [ordered]@{
                    schema = "controlled_baseline_progress_v1"
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
                    throw "controlled baseline case failed: $caseId ($errorMessage)"
                }
            }
        }
    }
}

$csvPath = Join-Path $runDir "controlled_baselines_matrix.csv"
$rows | Export-Csv -NoTypeInformation -Encoding UTF8 $csvPath

$summary = [ordered]@{
    schema = "controlled_baseline_summary_v1"
    status = $(if ($failed -eq 0) { "PASS" } else { "FAIL" })
    formal = $true
    release_manifest = $ReleaseManifest
    release_manifest_sha256 = $releaseManifestSha
    manifest_check_skipped = [bool]$SkipManifestCheck
    variants = $variantList
    clients = $clientList
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
