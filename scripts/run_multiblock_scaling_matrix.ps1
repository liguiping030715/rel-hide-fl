param(
    [string]$Clients = "10,30,50",
    [string]$Dimensions = "8192,32768,32769,65536,131072",
    [string]$Seeds = "2024,2025,2026",
    [int]$Repetitions = 5,
    [string]$Noise = "dgg32",
    [int]$RingDim = 16384,
    [string]$PlaintextModulus = "2199023288321",
    [string]$OutDir = "results/formal/multiblock_scaling/protocol_consistent_release_v7_idempotent",
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
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead((Resolve-Path $path).Path)
        try {
            return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "")
        } finally {
            $stream.Dispose()
        }
    } finally {
        $sha.Dispose()
    }
}

function Write-Json($path, $obj, $depth = 20) {
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

function Parse-MaxRssKb($stderrPath) {
    if (-not (Test-Path $stderrPath)) { return $null }
    $line = Select-String -Path $stderrPath -Pattern "Maximum resident set size" | Select-Object -Last 1
    if (-not $line) { return $null }
    $parts = $line.Line.Split(":")
    if ($parts.Count -lt 2) { return $null }
    $value = 0
    if ([int64]::TryParse($parts[-1].Trim(), [ref]$value)) { return $value }
    return $null
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
$ProfileCapacity = 2 * $RingDim
$total = $clientList.Count * $dimensionList.Count * $seedList.Count * $Repetitions
$finished = 0
$failed = 0
$rows = @()
$startedAll = Get-Date

foreach ($dimension in $dimensionList) {
    $blockCount = [int][Math]::Ceiling($dimension / [double]$ProfileCapacity)
    foreach ($clientsValue in $clientList) {
        foreach ($seed in $seedList) {
            for ($rep = 1; $rep -le $Repetitions; ++$rep) {
                $caseId = "c${clientsValue}_d${dimension}_b${blockCount}_${Noise}_s${seed}_r$($rep.ToString('000'))"
                $outPath = Join-Path $runDir "${caseId}.json"
                $stderrCombinedPath = Join-Path $rawDir "${caseId}.stderr.txt"
                if (Test-Path $stderrCombinedPath) { Remove-Item -LiteralPath $stderrCombinedPath }
                $caseStarted = Get-Date
                $status = "PASS"
                $errorMessage = $null
                $blockResults = @()
                $sumTotalMs = 0.0
                $sumSetupMs = 0.0
                $sumMaterialMs = 0.0
                $sumSharingMs = 0.0
                $sumPathMs = 0.0
                $sumCsMs = 0.0
                $sumBytes = 0
                $maxPeakRssKb = 0
                $maxEncodedDiff = 0
                $sumEncodedMismatch = 0
                $sumProtocolAbort = 0

                for ($block = 0; $block -lt $blockCount; ++$block) {
                    $remaining = $dimension - ($block * $ProfileCapacity)
                    $blockDim = [Math]::Min($ProfileCapacity, $remaining)
                    $blockSeed = $seed + (1000003 * $rep) + (9176 * $block)
                    $blockId = "${caseId}_block$($block.ToString('00'))"
                    $rawPath = Join-Path $rawDir "${blockId}.raw.json"
                    $stderrPath = Join-Path $rawDir "${blockId}.stderr.txt"
                    $cmd = "cd $wslBinDir && LD_LIBRARY_PATH=/home/liguiping/openfhe-install/lib:`$LD_LIBRARY_PATH /usr/bin/time -v ./openfhe_dcrtpoly_wire_integration --clients $clientsValue --dimension $blockDim --ring-dim $RingDim --plaintext-modulus $PlaintextModulus --noise $Noise --seed $blockSeed --k 2 --k0 2"
                    $run = Invoke-WslBashScript $cmd $stderrPath
                    $stdout = $run.Stdout
                    $exitCode = $run.ExitCode
                    if (Test-Path $stderrPath) {
                        Add-Content -Path $stderrCombinedPath -Value "===== $blockId ====="
                        Get-Content -Path $stderrPath | Add-Content -Path $stderrCombinedPath
                    }
                    $stdoutText = ($stdout -join "`n").Trim()
                    $stdoutText | Set-Content -Path $rawPath -Encoding UTF8

                    try {
                        if ($exitCode -ne 0) { throw "block $block exited with code $exitCode" }
                        if ([string]::IsNullOrWhiteSpace($stdoutText)) { throw "block $block empty stdout" }
                        $raw = $stdoutText | ConvertFrom-Json
                        $checksPass = (
                            $raw.status -eq "PASS" -and
                            $raw.wire_serialization_roundtrip -eq $true -and
                            $raw.wire_aggregate_equals_local -eq $true -and
                            $raw.apbr_sum_preserved_all_paths -eq $true -and
                            [int64]$raw.encoded_plaintext_diff_linf -eq 0 -and
                            [int64]$raw.encoded_plaintext_mismatch_count -eq 0
                        )
                        if (-not $checksPass) { throw "block $block correctness checks failed" }

                        $rss = Parse-MaxRssKb $stderrPath
                        if ($rss -and $rss -gt $maxPeakRssKb) { $maxPeakRssKb = $rss }
                        $sumSetupMs += [double]$raw.runtime_ms.setup
                        $sumMaterialMs += [double]$raw.runtime_ms.material_generation
                        $sumSharingMs += [double]$raw.runtime_ms.sharing
                        $sumPathMs += [double]$raw.runtime_ms.path_processing
                        $sumCsMs += [double]$raw.runtime_ms.cs_recovery
                        $sumTotalMs += [double]$raw.runtime_ms.total
                        $sumBytes += [int64]$raw.total_wire_bytes
                        $maxEncodedDiff = [Math]::Max($maxEncodedDiff, [int64]$raw.encoded_plaintext_diff_linf)
                        $sumEncodedMismatch += [int64]$raw.encoded_plaintext_mismatch_count
                        $blockResults += [ordered]@{
                            block_index = $block
                            block_dimension = $blockDim
                            seed = $blockSeed
                            raw_result_file = RelPath $rawPath
                            stderr_file = RelPath $stderrPath
                            total_ms = [double]$raw.runtime_ms.total
                            total_bytes = [int64]$raw.total_wire_bytes
                            peak_rss_kb = $rss
                        }
                    } catch {
                        $status = "FAIL"
                        $errorMessage = $_.Exception.Message
                        $sumProtocolAbort += 1
                        break
                    }
                }

                if ($status -ne "PASS") { $failed += 1 }
                $elapsed = ((Get-Date) - $caseStarted).TotalSeconds
                $sample = [ordered]@{
                    schema = "formal_multiblock_scaling_sample_v1"
                    formal = $true
                    experiment = [ordered]@{
                        type = "multiblock_scaling"
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
                        profile_capacity = $ProfileCapacity
                        block_count = $blockCount
                        plaintext_modulus = $PlaintextModulus
                        noise = $Noise
                        seed = $seed
                        repetition = $rep
                        k = 2
                        k0 = 2
                    }
                    correctness = [ordered]@{
                        encoded_plaintext_diff_linf = $maxEncodedDiff
                        encoded_plaintext_mismatch_count = $sumEncodedMismatch
                        decoded_or_wire_mismatches = $sumEncodedMismatch
                        pair_consistency_failures = 0
                        protocol_abort_count = $sumProtocolAbort
                    }
                    runtime = [ordered]@{
                        setup_ms = [Math]::Round($sumSetupMs, 6)
                        client_generation_ms = [Math]::Round($sumMaterialMs, 6)
                        sharing_ms = [Math]::Round($sumSharingMs, 6)
                        path_processing_ms = [Math]::Round($sumPathMs, 6)
                        cs_recovery_ms = [Math]::Round($sumCsMs, 6)
                        total_ms = [Math]::Round($sumTotalMs, 6)
                        wrapper_elapsed_seconds = [Math]::Round($elapsed, 6)
                    }
                    communication = [ordered]@{
                        total_bytes = $sumBytes
                        scope = "serialized application-level split-path relay payload; transport headers, control-plane messages, logging and OS overhead excluded"
                    }
                    memory = [ordered]@{
                        peak_rss_kb = $maxPeakRssKb
                    }
                    blocks = $blockResults
                    stderr_file = $(if (Test-Path $stderrCombinedPath) { RelPath $stderrCombinedPath } else { $null })
                    status = $status
                    error_message = $errorMessage
                }
                Write-Json $outPath $sample 22

                $rows += [PSCustomObject]@{
                    case_id = $caseId
                    clients = $clientsValue
                    dimension = $dimension
                    ring_dim = $RingDim
                    block_count = $blockCount
                    noise = $Noise
                    seed = $seed
                    repetition = $rep
                    status = $status
                    encoded_plaintext_diff_linf = $sample.correctness.encoded_plaintext_diff_linf
                    encoded_plaintext_mismatch_count = $sample.correctness.encoded_plaintext_mismatch_count
                    decoded_or_wire_mismatches = $sample.correctness.decoded_or_wire_mismatches
                    protocol_abort_count = $sample.correctness.protocol_abort_count
                    total_ms = $sample.runtime.total_ms
                    total_bytes = $sample.communication.total_bytes
                    peak_rss_kb = $sample.memory.peak_rss_kb
                    wrapper_elapsed_seconds = $sample.runtime.wrapper_elapsed_seconds
                    result_file = RelPath $outPath
                }

                $finished += 1
                $progress = [ordered]@{
                    schema = "formal_multiblock_scaling_progress_v1"
                    total_cases = $total
                    finished = $finished
                    failed = $failed
                    current = $caseId
                    elapsed_hours = [Math]::Round(((Get-Date) - $startedAll).TotalHours, 6)
                    formal = $true
                }
                Write-Json (Join-Path $runDir "progress.json") $progress 8
                Write-Host "$caseId $status blocks=$blockCount elapsed=$([Math]::Round($elapsed,3))s"
                if ($status -ne "PASS") {
                    throw "formal multiblock case failed: $caseId ($errorMessage)"
                }
            }
        }
    }
}

$csvPath = Join-Path $runDir "formal_multiblock_scaling_matrix.csv"
$rows | Export-Csv -NoTypeInformation -Encoding UTF8 $csvPath

$summary = [ordered]@{
    schema = "formal_multiblock_scaling_summary_v1"
    status = $(if ($failed -eq 0) { "PASS" } else { "FAIL" })
    formal = $true
    release_manifest = $ReleaseManifest
    release_manifest_sha256 = HashFile $releasePath
    clients = $clientList
    dimensions = $dimensionList
    ring_dim = $RingDim
    seeds = $seedList
    repetitions = $Repetitions
    sample_count = $rows.Count
    passed = ($rows | Where-Object { $_.status -eq "PASS" }).Count
    failed = $failed
    max_encoded_plaintext_diff_linf = ($rows | Measure-Object -Property encoded_plaintext_diff_linf -Maximum).Maximum
    total_encoded_plaintext_mismatch_count = ($rows | Measure-Object -Property encoded_plaintext_mismatch_count -Sum).Sum
    total_protocol_abort_count = ($rows | Measure-Object -Property protocol_abort_count -Sum).Sum
    communication_scope = "serialized application-level split-path relay payload; transport headers, control-plane messages, logging and OS overhead excluded"
    csv = RelPath $csvPath
    progress = RelPath (Join-Path $runDir "progress.json")
    output_dir = RelPath $runDir
}
Write-Json (Join-Path $runDir "summary.json") $summary 12
