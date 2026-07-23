param(
    [string]$Datasets = "mnist,cifar10",
    [string]$Clients = "5,10",
    [string]$Seeds = "2024",
    [string]$OutDir = "results/preflight/openfhe_backend_matrix"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runId = "openfhe_backend_matrix_" + (Get-Date -Format "yyyyMMdd_HHmmss")
$runDir = Join-Path (Join-Path $root $OutDir) $runId
New-Item -ItemType Directory -Force $runDir | Out-Null

$datasetsList = $Datasets.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
$clientsList = $Clients.Split(",") | ForEach-Object { [int]$_.Trim() }
$seedsList = $Seeds.Split(",") | ForEach-Object { [int]$_.Trim() }

$rows = @()
$caseIndex = 0

foreach ($dataset in $datasetsList) {
    foreach ($clientCount in $clientsList) {
        foreach ($seed in $seedsList) {
            $caseId = "${dataset}_c${clientCount}_s${seed}"
            $rawPath = Join-Path $runDir "${caseId}.csv"
            $cmd = "cd /tmp/openfhe_old_build && LD_LIBRARY_PATH=/home/liguiping/openfhe-install/lib:`$LD_LIBRARY_PATH ./openfhe_protocol_eval_bgv --datasets $dataset --clients $clientCount --seeds $seed --plaintext-modulus 536903681 --group-size 2 --split-k 2 --dummy-shares-k0 2 --quant-scale 1000 --clip-bound 1 --packing 1"
            $started = Get-Date
            wsl -d Ubuntu sh -lc $cmd | Set-Content -Path $rawPath -Encoding UTF8
            $elapsed = ((Get-Date) - $started).TotalSeconds
            $row = Import-Csv $rawPath | Select-Object -First 1
            $status = "PASS"
            if ($row.openfhe_mismatch_count -ne "0" -or $row.protocol_mismatch_count -ne "0") {
                $status = "FAIL"
            }
            $rows += [PSCustomObject]@{
                case_id = $caseId
                dataset = $dataset
                clients = $clientCount
                seed = $seed
                status = $status
                gradient_dim = $row.gradient_dim
                openfhe_linf_error_quantized = $row.openfhe_linf_error_quantized
                openfhe_mismatch_count = $row.openfhe_mismatch_count
                protocol_linf_error_quantized = $row.protocol_linf_error_quantized
                protocol_mismatch_count = $row.protocol_mismatch_count
                total_ms = $row.total_ms
                elapsed_seconds = [Math]::Round($elapsed, 6)
                raw_csv = $rawPath
            }
            Write-Host "$caseId $status elapsed=$([Math]::Round($elapsed,3))s"
            $caseIndex += 1
        }
    }
}

$matrixCsv = Join-Path $runDir "openfhe_backend_matrix.csv"
$rows | Export-Csv -NoTypeInformation -Encoding UTF8 $matrixCsv

$allPass = -not ($rows | Where-Object { $_.status -ne "PASS" })
$summary = @{
    schema = "openfhe_backend_matrix_summary_v1"
    formal = $false
    scope = "openfhe_backend_matrix_only_not_splitpath_formal_result"
    status = $(if ($allPass) { "PASS" } else { "FAIL" })
    datasets = $datasetsList
    clients = $clientsList
    seeds = $seedsList
    case_count = $rows.Count
    passed = ($rows | Where-Object { $_.status -eq "PASS" }).Count
    failed = ($rows | Where-Object { $_.status -ne "PASS" }).Count
    matrix_csv = $matrixCsv
    note = "This validates the retained OpenFHE-BGV backend over a small matrix. It is not the new formal host-client split-path result."
}
$summaryPath = Join-Path $runDir "summary.json"
$summary | ConvertTo-Json -Depth 8 | Set-Content -Path $summaryPath -Encoding UTF8

if (-not $allPass) {
    exit 1
}
