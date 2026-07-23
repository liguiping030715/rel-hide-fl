# Manifests

This directory stores immutable manifests for formal and preflight runs.

Do not overwrite an existing manifest after a run has been reported. Create a new version instead:

```text
route_a_host_client_preflight_v1.json
route_a_host_client_preflight_v2.json
```

Required fields for formal manifests:

```yaml
run_id:
source_digest:
binary_digest:
config_digest:
openfhe_version:
client_runtime:
host_roles:
formal:
result_digest:
paper_claims_supported:
paper_claims_not_supported:
```
