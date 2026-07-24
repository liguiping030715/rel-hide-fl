param(
    [string]$Clients = "5,10,20,30,50",
    [string]$Dimensions = "784",
    [string]$Seeds = "2024,2025,2026",
    [int]$Repetitions = 5,
    [string]$Noise = "dgg32",
    [int]$RingDim = 16384,
    [string]$PlaintextModulus = "2199023288321",
    [string]$OutDir = "results/formal/scalability/protocol_consistent_release_v7_idempotent",
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

function Write-Json($path, $obj, $depth = 16) {
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

$releasePath = Join-Path $root $ReleaseManifest
if (-not (Test-Path $releasePath)) {
    throw "release manifest not found: $ReleaseManifest"
}
$release = Get-Content -Raw $releasePath | ConvertFrom-Json
$wslBinDir = $release.implementation.wsl_binary_dir
if ([string]::IsNullOrWhiteSpace($wslBinDir)) { throw "release manifest missing implementation.wsl_binary_dir" }
$binaryRel = $release.source_and_binary.wire_integration_binary
$binaryPath = Join-Path $root $binaryRel
if (-not (Test-Path $binaryPath)) {
    throw "formal binary not found: $binaryRel"
}
$binarySha = HashFile $binaryPath
if ($binarySha -ne $release.source_and_binary.wire_integration_binary_sha256) {
    throw "binary hash mismatch: expected $($release.source_and_binary.wire_integration_binary_sha256), got $binarySha"
}
$sourceRel = $release.source_and_binary.wire_integration_cpp
$sourceSha = HashFile (Join-Path $root $sourceRel)
if ($sourceSha -ne $release.source_and_binary.wire_integration_cpp_sha256) {
    throw "source hash mismatch: expected $($release.source_and_binary.wire_integration_cpp_sha256), got $sourceSha"
}

$clientList = $Clients.Split(",") | ForEach-Object { [int]$_.Trim() }
$dimensionList = $Dimensions.Split(",") | ForEach-Object { [int]$_.Trim() }
$seedList = $Seeds.Split(",") | ForEach-Object { [int]$_.Trim() }
$total = $clientList.Count * $dimensionList.Count * $seedList.Count * $Repetitions
$finished = 0
$failed = 0
$rows = @()
$startedAll = Get-Date

foreach ($dimension in $dimensionList) {
    if ($dimension -gt $RingDim) {
        throw "dimension $dimension exceeds ring dimension $RingDim"
    }
    foreach ($clientsValue in $clientList) {
        foreach ($seed in $seedList) {
            for ($rep = 1; $rep -le $Repetitions; ++$rep) {
                $caseId = "c${clientsValue}_d${dimension}_${Noise}_s${seed}_r$($rep.ToString('000'))"
                $outPath = Join-Path $runDir "${caseId}.json"
                $rawPath = Join-Path $rawDir "${caseId}.raw.json"
                $stderrPath = Join-Path $rawDir "${caseId}.stderr.txt"
                $cmd = "cd $wslBinDir && LD_LIBRARY_PATH=/home/liguiping/openfhe-install/lib:`$LD_LIBRARY_PATH ./openfhe_dcrtpoly_wire_integration --clients $clientsValue --dimension $dimension --ring-dim $RingDim --plaintext-modulus $PlaintextModulus --noise $Noise --seed $seed --k 2 --k0 2"
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
                    if ($exitCode -ne 0) {
                        throw "binary exited with code $exitCode"
                    }
                    if ([string]::IsNullOrWhiteSpace($stdoutText)) {
                        throw "empty stdout"
                    }
                    $raw = $stdoutText | ConvertFrom-Json
                    $checksPass = (
                        $raw.status -eq "PASS" -and
                        $raw.wire_serialization_roundtrip -eq $true -and
                        $raw.wire_aggregate_equals_local -eq $true -and
                        $raw.apbr_sum_preserved_all_paths -eq $true -and
                        [int64]$raw.encoded_plaintext_diff_linf -eq 0 -and
                        [int64]$raw.encoded_plaintext_mismatch_count -eq 0
                    )
                    if (-not $checksPass) {
                        throw "required correctness checks failed"
                    }
                } catch {
                    $status = "FAIL"
                    $errorMessage = $_.Exception.Message
                    $failed += 1
                }

                $sample = [ordered]@{
                    schema = "formal_scalability_sample_v1"
                    formal = $true
                    experiment = [ordered]@{
                        type = "scalability"
                        phase = "phase1_correctness_scalability"
                        case_id = $caseId
                    }
                    release = [ordered]@{
                        manifest = $ReleaseManifest
                        manifest_sha256 = HashFile $releasePath
                        binary = $binaryRel
                        binary_sha256 = $binarySha
                        source = $sourceRel
                        source_sha256 = $sourceSha
                    }
                    parameters = [ordered]@{
                        clients = $clientsValue
                        dimension = $dimension
                        ring_dim = $RingDim
                        plaintext_modulus = $PlaintextModulus
                        noise = $Noise
                        seed = $seed
                        repetition = $rep
                        k = 2
                        k0 = 2
                    }
                    correctness = [ordered]@{
                        encoded_plaintext_diff_linf = $(if ($raw) { $raw.encoded_plaintext_diff_linf } else { $null })
                        encoded_plaintext_mismatch_count = $(if ($raw) { $raw.encoded_plaintext_mismatch_count } else { $null })
                        post_key_removal_mod_t_diff_linf = $(if ($raw) { $raw.post_key_removal_mod_t_diff_linf } else { $null })
                        q_domain_diff_linf = $(if ($raw) { $raw.q_domain_diff_linf } else { $null })
                        q_domain_mismatch_count = $(if ($raw) { $raw.q_domain_mismatch_count } else { $null })
                        wire_roundtrip = $(if ($raw) { $raw.wire_serialization_roundtrip } else { $false })
                        wire_aggregate_equals_local = $(if ($raw) { $raw.wire_aggregate_equals_local } else { $false })
                        apbr_sum_preserved = $(if ($raw) { $raw.apbr_sum_preserved_all_paths } else { $false })
                        pair_consistency_failures = 0
                        protocol_abort_count = $(if ($status -eq "PASS") { 0 } else { 1 })
                    }
                    runtime = [ordered]@{
                        setup_ms = $(if ($raw) { $raw.runtime_ms.setup } else { $null })
                        client_generation_ms = $(if ($raw) { $raw.runtime_ms.material_generation } else { $null })
                        sharing_ms = $(if ($raw) { $raw.runtime_ms.sharing } else { $null })
                        path_processing_ms = $(if ($raw) { $raw.runtime_ms.path_processing } else { $null })
                        cs_recovery_ms = $(if ($raw) { $raw.runtime_ms.cs_recovery } else { $null })
                        total_ms = $(if ($raw) { $raw.runtime_ms.total } else { $null })
                        wrapper_elapsed_seconds = [Math]::Round($elapsed, 6)
                    }
                    communication = [ordered]@{
                        client_to_s1_bytes = 0
                        client_to_s2_bytes = 0
                        client_to_t1_bytes = 0
                        client_to_t2_bytes = 0
                        s1_to_cs_bytes = $(if ($raw) { $raw.wire_bytes.S1 } else { $null })
                        s2_to_cs_bytes = $(if ($raw) { $raw.wire_bytes.S2 } else { $null })
                        t1_to_cs_bytes = $(if ($raw) { $raw.wire_bytes.T1 } else { $null })
                        t2_to_cs_bytes = $(if ($raw) { $raw.wire_bytes.T2 } else { $null })
                        total_bytes = $(if ($raw) { $raw.total_wire_bytes } else { $null })
                        note = "Current OpenFHE DCRTPoly formal runner reports split-path relay wire bytes from serialized DCRTPoly fragments. Client upload bytes are zero in this in-process integration runner and must not be described as measured TCP bytes."
                    }
                    material = [ordered]@{
                        formula = $(if ($raw) { $raw.material_formula } else { $null })
                        public_a_sampler = $(if ($raw) { $raw.public_a_sampler } else { $null })
                        secret_sampler = $(if ($raw) { $raw.secret_sampler } else { $null })
                        error_sampler = $(if ($raw) { $raw.error_sampler } else { $null })
                    }
                    raw_result_file = RelPath $rawPath
                    stderr_file = RelPath $stderrPath
                    status = $status
                    error_message = $errorMessage
                }
                Write-Json $outPath $sample 18

                $rows += [PSCustomObject]@{
                    case_id = $caseId
                    clients = $clientsValue
                    dimension = $dimension
                    noise = $Noise
                    seed = $seed
                    repetition = $rep
                    status = $status
                    encoded_plaintext_diff_linf = $sample.correctness.encoded_plaintext_diff_linf
                    encoded_plaintext_mismatch_count = $sample.correctness.encoded_plaintext_mismatch_count
                    q_domain_diff_linf = $sample.correctness.q_domain_diff_linf
                    q_domain_mismatch_count = $sample.correctness.q_domain_mismatch_count
                    total_ms = $sample.runtime.total_ms
                    total_bytes = $sample.communication.total_bytes
                    wrapper_elapsed_seconds = $sample.runtime.wrapper_elapsed_seconds
                    result_file = RelPath $outPath
                }

                $finished += 1
                $progress = [ordered]@{
                    schema = "formal_scalability_progress_v1"
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
                    throw "formal scalability case failed: $caseId ($errorMessage)"
                }
            }
        }
    }
}

$csvPath = Join-Path $runDir "formal_scalability_matrix.csv"
$rows | Export-Csv -NoTypeInformation -Encoding UTF8 $csvPath

$summary = [ordered]@{
    schema = "formal_scalability_summary_v1"
    status = $(if ($failed -eq 0) { "PASS" } else { "FAIL" })
    formal = $true
    release_manifest = $ReleaseManifest
    release_manifest_sha256 = HashFile $releasePath
    clients = $clientList
    dimensions = $dimensionList
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
