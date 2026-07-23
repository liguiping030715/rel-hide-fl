| ablation | samples | k | k0 | apbr  | avg_total_ms | std_total_ms | total_fragments_per_path | variant   | avg_total_MiB |
| -------- | ------- | - | -- | ----- | ------------ | ------------ | ------------------------ | --------- | ------------- |
| full     | 15      | 2 | 2  | True  | 907.86       | 29.04        | 62                       | Full      | 62.01         |
| k1       | 15      | 1 | 2  | True  | 584.41       | 19.48        | 32                       | k = 1     | 32            |
| no_apbr  | 15      | 2 | 2  | False | 783.6        | 25.77        | 62                       | w/o APBR  | 62.01         |
| no_dummy | 15      | 2 | 0  | True  | 888.73       | 19.42        | 60                       | w/o dummy | 60.01         |
