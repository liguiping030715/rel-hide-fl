param(
    [int]$Clients = 2,
    [string]$Dataset = "mnist",
    [int]$Seed = 2024,
    [string]$OutDir = "results/preflight/openfhe_backend_smoke"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$runId = "openfhe_${Dataset}_c${Clients}_s${Seed}_" + (Get-Date -Format "yyyyMMdd_HHmmss")
$runDir = Join-Path (Join-Path $root $OutDir) $runId
New-Item -ItemType Directory -Force $runDir | Out-Null

# Avoid passing the Unicode Windows workspace path through PowerShell 5 -> WSL.
# Prefer an ASCII symlink created once on the Ubuntu side:
#   /tmp/openfhe_old_build -> .../openfhe_bgv_local_emulation_full/build
$cmd = @"
if [ ! -x /tmp/openfhe_old_build/openfhe_protocol_eval_bgv ]; then
  echo 'openfhe_protocol_eval_bgv not found at /tmp/openfhe_old_build' >&2
  exit 2
fi
cd /tmp/openfhe_old_build && LD_LIBRARY_PATH=/home/liguiping/openfhe-install/lib:`$LD_LIBRARY_PATH ./openfhe_protocol_eval_bgv --datasets $Dataset --clients $Clients --seeds $Seed --plaintext-modulus 536903681 --group-size 2 --split-k 2 --dummy-shares-k0 2 --quant-scale 1000 --clip-bound 1 --packing 1
"@

$rawPath = Join-Path $runDir "openfhe_backend_smoke.csv"
$metaPath = Join-Path $runDir "metadata.json"

wsl -d Ubuntu sh -lc $cmd | Set-Content -Path $rawPath -Encoding UTF8

$csv = Import-Csv $rawPath
$row = $csv | Select-Object -First 1
$status = "UNKNOWN"
if ($row.openfhe_mismatch_count -eq "0" -and $row.protocol_mismatch_count -eq "0") {
    $status = "PASS"
} else {
    $status = "FAIL"
}

$meta = @{
    schema = "openfhe_backend_smoke_v1"
    status = $status
    formal = $false
    scope = "openfhe_backend_smoke_only_not_splitpath_formal_result"
    dataset = $Dataset
    clients = $Clients
    seed = $Seed
    raw_csv = $rawPath
    old_binary_source = "experiments/openfhe_bgv_local_emulation_full/build/openfhe_protocol_eval_bgv"
    openfhe_mismatch_count = $row.openfhe_mismatch_count
    protocol_mismatch_count = $row.protocol_mismatch_count
    openfhe_linf_error_quantized = $row.openfhe_linf_error_quantized
    protocol_linf_error_quantized = $row.protocol_linf_error_quantized
    note = "This verifies that the existing OpenFHE-BGV backend smoke case runs in Ubuntu WSL. It is not the new formal host-client split-path result."
}

$meta | ConvertTo-Json -Depth 8 | Set-Content -Path $metaPath -Encoding UTF8

Write-Host "OpenFHE backend smoke status: $status"
Write-Host "Output: $runDir"
if ($status -ne "PASS") {
    exit 1
}
