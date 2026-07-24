# Controlled baselines

The paper uses controlled references rather than claiming strict end-to-end equivalence across all variants.

- `plain_aggregation/`: in-memory computational lower bound;
- `bgv_only/`: native OpenFHE BGV/RNS reference;
- `shamir_shuffle_proxy/`: compact shuffled plaintext-share reference;
- `aggregate_only/`: four-path aggregate-only split-path baseline;
- `shuffle_only/`: synthetic shuffle pipeline reference.

These baselines isolate overhead sources and should not be read as identical-trust deployments.
