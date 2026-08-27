# Performance Experiments

This directory contains performance-oriented runners for the paper artifact.

## Network Deployment Matrix

`run_network_deployment_matrix.py` runs the minimal counted-TCP network matrix
used to quantify end-to-end secure-aggregation latency under RTT, bandwidth and
logical-client scaling.

Recommended WSL/Linux command:

```bash
python experiments/performance/run_network_deployment_matrix.py \
  --binary /mnt/d/paper-experiment/rel-hide-fl/build/openfhe_dcrtpoly_wire_integration \
  --pilot-center \
  --repetitions 1 \
  --warmups 0
```

Full measured matrix with Linux traffic control:

```bash
python experiments/performance/run_network_deployment_matrix.py \
  --binary /mnt/d/paper-experiment/rel-hide-fl/build/openfhe_dcrtpoly_wire_integration \
  --apply-netem \
  --sudo
```

The script runs:

```text
8 network configurations x 2 schemes x 5 measured repetitions
```

It also performs one warm-up per scheme/configuration by default. The two
schemes are:

```text
aggregate_only  -> four_path_sum_only
full            -> full_protocol
```

Outputs are written to:

```text
results/formal/network_deployment/
```

Key files:

```text
network_deployment_runs.csv
network_deployment_summary.csv
figures/network_rtt_sensitivity.png
figures/network_bandwidth_sensitivity.png
figures/network_client_scalability.png
```

If `--apply-netem` is omitted, the RTT and bandwidth fields are recorded as the
intended configuration labels only; Linux traffic control is not changed. Use
`--apply-netem` for measured network-emulation runs.

The client count denotes logical clients unless each client role is externally
mapped to a separate container or host. Local FL training is excluded from the
reported control-barrier round latency.
