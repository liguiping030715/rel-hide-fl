#!/usr/bin/env bash
set -euo pipefail

python experiments/correctness/run_v8_distributed_tcp_preflight.py
python experiments/correctness/run_v8_docker_wsl_topology_preflight.py
