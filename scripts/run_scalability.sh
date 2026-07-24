#!/usr/bin/env bash
set -euo pipefail

powershell -ExecutionPolicy Bypass -File scripts/run_formal_scalability_matrix.ps1
powershell -ExecutionPolicy Bypass -File scripts/run_multiblock_scaling_matrix.ps1
