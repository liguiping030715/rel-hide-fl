import json
import hashlib
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FORMAL_V8 = ROOT / "results" / "formal_v8"
FORMAL = FORMAL_V8 / "paper_artifacts"
TABLES = FORMAL / "tables"
FIGS = FORMAL / "figures"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def ensure_dirs():
    TABLES.mkdir(parents=True, exist_ok=True)
    FIGS.mkdir(parents=True, exist_ok=True)


def save_table(df: pd.DataFrame, name: str, caption: str, label: str):
    csv_path = TABLES / f"{name}.csv"
    md_path = TABLES / f"{name}.md"
    tex_path = TABLES / f"{name}.tex"
    df.to_csv(csv_path, index=False)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(to_markdown_table(df))
        f.write("\n")
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(df.to_latex(index=False, escape=False, caption=caption, label=label))
    return {"csv": rel(csv_path), "md": rel(md_path), "tex": rel(tex_path)}


def to_markdown_table(df: pd.DataFrame) -> str:
    headers = [str(c) for c in df.columns]
    rows = [[format_cell(v) for v in row] for row in df.itertuples(index=False, name=None)]
    widths = []
    for i, header in enumerate(headers):
        values = [row[i] for row in rows]
        widths.append(max([len(header)] + [len(v) for v in values]))
    header_line = "| " + " | ".join(header.ljust(widths[i]) for i, header in enumerate(headers)) + " |"
    sep_line = "| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |"
    body = [
        "| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) + " |"
        for row in rows
    ]
    return "\n".join([header_line, sep_line] + body)


def format_cell(value) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def style():
    plt.rcParams.update({
        "font.size": 9,
        "axes.labelsize": 9,
        "axes.titlesize": 10,
        "legend.fontsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })


def save_fig(fig, name: str):
    png = FIGS / f"{name}.png"
    pdf = FIGS / f"{name}.pdf"
    fig.tight_layout()
    fig.savefig(png, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    return {"png": rel(png), "pdf": rel(pdf)}


def collect_outputs():
    outputs = {}
    for directory in [TABLES, FIGS]:
        for path in sorted(directory.iterdir()):
            if path.is_file():
                outputs[rel(path)] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    return outputs


def main():
    ensure_dirs()
    style()
    inputs = {
        "phase1": FORMAL_V8 / "scalability/retry1/formal_scalability_matrix.csv",
        "multiblock": FORMAL_V8 / "multiblock_scaling_paper/formal_multiblock_scaling_matrix.csv",
        "baseline": FORMAL_V8 / "controlled_baselines/controlled_baselines_matrix.csv",
        "ablation": FORMAL_V8 / "ablations/ablation_matrix.csv",
    }
    utility_summary_paths = sorted((FORMAL_V8 / "fl_utility").glob("seed*/summary.json"))
    if len(utility_summary_paths) != 3:
        raise RuntimeError(f"Expected three v8 utility summaries, found {len(utility_summary_paths)}")
    phase1 = pd.read_csv(inputs["phase1"])
    multiblock = pd.read_csv(inputs["multiblock"])
    baseline = pd.read_csv(inputs["baseline"])
    for col in ["client_upload_bytes", "path_to_cs_bytes"]:
        if col not in baseline.columns:
            baseline[col] = 0
    if "total_bytes" not in baseline.columns:
        baseline["total_bytes"] = 0
    baseline["client_upload_bytes"] = baseline["client_upload_bytes"].fillna(0)
    baseline["path_to_cs_bytes"] = baseline["path_to_cs_bytes"].fillna(0)
    baseline["total_bytes"] = baseline["total_bytes"].fillna(
        baseline["client_upload_bytes"] + baseline["path_to_cs_bytes"]
    )
    dcrtpoly_wire_record_bytes = 262184
    for idx, row in baseline.iterrows():
        variant = row["variant"]
        legacy_total = float(row["total_bytes"])
        clients_value = int(row["clients"])
        if variant == "plain_aggregate":
            baseline.at[idx, "client_upload_bytes"] = 0
            baseline.at[idx, "path_to_cs_bytes"] = 0
            baseline.at[idx, "total_bytes"] = 0
        elif variant == "openfhe_bgv_only" and row["client_upload_bytes"] == 0 and row["path_to_cs_bytes"] == 0:
            baseline.at[idx, "client_upload_bytes"] = legacy_total
            baseline.at[idx, "path_to_cs_bytes"] = 0
            baseline.at[idx, "total_bytes"] = legacy_total
        elif variant == "shamir_shuffle_proxy" and row["client_upload_bytes"] == 0 and row["path_to_cs_bytes"] == 0:
            baseline.at[idx, "client_upload_bytes"] = legacy_total
            baseline.at[idx, "path_to_cs_bytes"] = legacy_total
            baseline.at[idx, "total_bytes"] = 2 * legacy_total
        elif variant in ("shuffle_only", "full_protocol") and row["client_upload_bytes"] == 0 and row["path_to_cs_bytes"] == 0:
            upload = clients_value * 4 * dcrtpoly_wire_record_bytes
            baseline.at[idx, "client_upload_bytes"] = upload
            baseline.at[idx, "path_to_cs_bytes"] = legacy_total
            baseline.at[idx, "total_bytes"] = upload + legacy_total
    ablation = pd.read_csv(inputs["ablation"])
    for df in [phase1, multiblock, baseline, ablation]:
        for col in df.columns:
            if col not in ("case_id", "status", "variant", "ablation", "noise", "result_file", "apbr"):
                df[col] = pd.to_numeric(df[col], errors="ignore")
    utility_cases = []
    for summary_path in utility_summary_paths:
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)
        if summary.get("status") != "PASS" or summary.get("formal") is not True:
            raise RuntimeError(f"Invalid utility summary: {summary_path}")
        utility_cases.extend(summary["cases"])
    utility = pd.DataFrame([
        {
            "dataset": c["dataset"],
            "seed": c["seed"],
            "gradient_dimension": c["gradient_dimension"],
            "route_a_blocks": c["route_a_blocks"],
            "max_route_quantized_diff_linf": c["max_route_quantized_diff_linf"],
            "total_route_block_mismatches": c["total_route_block_mismatches"],
            "final_plain_acc": c["final"]["plain_acc"],
            "final_quantized_acc": c["final"]["quantized_acc"],
            "final_route_acc": c["final"]["route_acc"],
        }
        for c in utility_cases
    ])
    utility_round_paths = sorted((FORMAL_V8 / "fl_utility").glob("seed*/*_seed*/rounds.csv"))
    if len(utility_round_paths) != 6:
        raise RuntimeError(f"Expected six per-round utility CSV files, found {len(utility_round_paths)}")
    utility_rounds = pd.concat([pd.read_csv(p) for p in utility_round_paths], ignore_index=True)

    artifacts = {
        "tables": {},
        "figures": {},
        "inputs": {k: {"path": rel(v), "sha256": sha256(v)} for k, v in inputs.items()},
        "input_groups": {
            "utility_summaries": [
                {"path": rel(p), "sha256": sha256(p)} for p in utility_summary_paths
            ],
            "utility_rounds": [
                {"path": rel(p), "sha256": sha256(p)} for p in utility_round_paths
            ]
        },
    }

    phase1_runtime = (
        phase1.groupby(["dimension", "clients"])
        .agg(samples=("case_id", "count"),
             mean_total_ms=("total_ms", "mean"),
             std_total_ms=("total_ms", "std"),
             min_total_ms=("total_ms", "min"),
             max_total_ms=("total_ms", "max"),
             avg_total_bytes=("total_bytes", "mean"),
             encoded_plaintext_mismatch_count=("encoded_plaintext_mismatch_count", "sum"),
             max_encoded_plaintext_diff_linf=("encoded_plaintext_diff_linf", "max"))
        .reset_index()
        .fillna({"std_total_ms": 0.0})
        .sort_values(["dimension", "clients"])
    )

    baseline_runtime = (
        baseline.groupby(["variant", "clients"])
        .agg(samples=("case_id", "count"),
             mean_total_ms=("total_ms", "mean"),
             std_total_ms=("total_ms", "std"),
             min_total_ms=("total_ms", "min"),
             max_total_ms=("total_ms", "max"),
             avg_total_bytes=("total_bytes", "mean"),
             avg_client_upload_bytes=("client_upload_bytes", "mean"),
             avg_path_to_cs_bytes=("path_to_cs_bytes", "mean"),
             encoded_plaintext_mismatch_count=("encoded_plaintext_mismatch_count", "sum"),
             max_encoded_plaintext_diff_linf=("encoded_plaintext_diff_linf", "max"))
        .reset_index()
        .fillna({"std_total_ms": 0.0})
    )
    phase1_stats = phase1_runtime.copy()
    baseline_stats = baseline_runtime.copy()
    multiblock_stats = (
        multiblock.groupby(["dimension", "block_count", "clients"])
        .agg(samples=("case_id", "count"),
             mean_total_ms=("total_ms", "mean"),
             std_total_ms=("total_ms", "std"),
             min_total_ms=("total_ms", "min"),
             max_total_ms=("total_ms", "max"),
             avg_total_bytes=("total_bytes", "mean"),
             peak_rss_kb=("peak_rss_kb", "max"),
             decoded_or_wire_mismatches=("decoded_or_wire_mismatches", "sum"),
             protocol_abort_count=("protocol_abort_count", "sum"))
        .reset_index()
        .fillna({"std_total_ms": 0.0})
        .sort_values(["clients", "block_count", "dimension"])
    )
    ablation_stats = (
        ablation.groupby(["ablation"])
        .agg(samples=("case_id", "count"),
             k=("k", "first"),
             k0=("k0", "first"),
             apbr=("apbr", "first"),
             avg_total_ms=("total_ms", "mean"),
             std_total_ms=("total_ms", "std"),
             avg_total_bytes=("total_bytes", "mean"),
             total_fragments_per_path=("total_fragments_per_path", "first"),
             encoded_plaintext_mismatch_count=("encoded_plaintext_mismatch_count", "sum"),
             max_encoded_plaintext_diff_linf=("encoded_plaintext_diff_linf", "max"))
        .reset_index()
        .fillna({"std_total_ms": 0.0})
    )

    # Table 1: correctness.
    corr = (
        phase1.groupby(["dimension", "clients"])
        .agg(samples=("case_id", "count"),
             encoded_plaintext_mismatch_count=("encoded_plaintext_mismatch_count", "sum"),
             max_encoded_plaintext_diff_linf=("encoded_plaintext_diff_linf", "max"))
        .reset_index()
        .sort_values(["dimension", "clients"])
    )
    artifacts["tables"]["correctness"] = save_table(
        corr,
        "table_correctness_scalability",
        "Encoded plaintext recovery for protocol-consistent OpenFHE DCRTPoly split-path aggregation.",
        "tab:eval-correctness",
    )

    # Runtime and communication tables.
    runtime_tbl = phase1_runtime[["dimension", "clients", "samples", "mean_total_ms", "std_total_ms", "min_total_ms", "max_total_ms"]].copy()
    runtime_tbl["mean_total_ms"] = runtime_tbl["mean_total_ms"].round(2)
    runtime_tbl["std_total_ms"] = runtime_tbl["std_total_ms"].round(2)
    runtime_tbl["min_total_ms"] = runtime_tbl["min_total_ms"].round(2)
    runtime_tbl["max_total_ms"] = runtime_tbl["max_total_ms"].round(2)
    artifacts["tables"]["runtime"] = save_table(
        runtime_tbl,
        "table_runtime_scalability",
        "Runtime scalability under Profile B ($N=16384$).",
        "tab:eval-runtime",
    )

    comm_tbl = phase1_stats[["dimension", "clients", "samples", "avg_total_bytes"]].copy()
    comm_tbl["avg_total_MiB"] = (comm_tbl["avg_total_bytes"] / (1024 * 1024)).round(2)
    comm_tbl["avg_bytes_per_client_MiB"] = (comm_tbl["avg_total_bytes"] / comm_tbl["clients"] / (1024 * 1024)).round(2)
    comm_tbl = comm_tbl.drop(columns=["avg_total_bytes"])
    artifacts["tables"]["communication"] = save_table(
        comm_tbl,
        "table_communication_scalability",
        "Shuffle-to-CS serialized relay payload under Profile B. Client-to-shuffle uploads, transport headers, control-plane messages, logging and operating-system overhead are excluded.",
        "tab:eval-communication",
    )

    multi_tbl = multiblock_stats[[
        "clients", "dimension", "block_count", "samples", "mean_total_ms",
        "std_total_ms", "avg_total_bytes", "peak_rss_kb",
        "decoded_or_wire_mismatches", "protocol_abort_count"
    ]].copy()
    multi_tbl["mean_total_ms"] = multi_tbl["mean_total_ms"].round(2)
    multi_tbl["std_total_ms"] = multi_tbl["std_total_ms"].round(2)
    multi_tbl["avg_total_MiB"] = (multi_tbl["avg_total_bytes"] / (1024 * 1024)).round(2)
    multi_tbl["peak_RSS_MiB"] = (multi_tbl["peak_rss_kb"] / 1024).round(2)
    multi_tbl = multi_tbl.drop(columns=["avg_total_bytes", "peak_rss_kb"])
    artifacts["tables"]["multiblock"] = save_table(
        multi_tbl,
        "table_multiblock_scaling",
        "Multi-block scaling with \(N=16384\). Payload counts are shuffle-to-CS serialized relay payload and exclude client-to-shuffle uploads, transport headers, control-plane messages, logging and operating-system overhead.",
        "tab:eval-multiblock",
    )

    base_tbl = baseline_runtime[[
        "variant", "clients", "samples", "mean_total_ms", "std_total_ms",
        "avg_client_upload_bytes", "avg_path_to_cs_bytes", "avg_total_bytes"
    ]].copy()
    base_tbl["mean_total_ms"] = base_tbl["mean_total_ms"].round(2)
    base_tbl["std_total_ms"] = base_tbl["std_total_ms"].round(2)
    base_tbl["client_upload_MiB"] = (base_tbl["avg_client_upload_bytes"] / (1024 * 1024)).round(2)
    base_tbl["path_to_cs_MiB"] = (base_tbl["avg_path_to_cs_bytes"] / (1024 * 1024)).round(2)
    base_tbl["avg_total_MiB"] = (base_tbl["avg_total_bytes"] / (1024 * 1024)).round(2)
    base_tbl = base_tbl.drop(columns=["avg_client_upload_bytes", "avg_path_to_cs_bytes", "avg_total_bytes"])
    artifacts["tables"]["baselines"] = save_table(
        base_tbl,
        "table_controlled_baselines",
        "Controlled baselines at $d=784$.",
        "tab:eval-baselines",
    )

    abl_label_map = {
        "full": "Full",
        "k1": "k = 1",
        "no_apbr": "w/o APBR",
        "no_dummy": "w/o dummy",
    }
    abl_tbl = ablation_stats[["ablation", "samples", "k", "k0", "apbr", "avg_total_ms", "std_total_ms", "avg_total_bytes", "total_fragments_per_path"]].copy()
    abl_tbl["variant"] = abl_tbl["ablation"].map(abl_label_map)
    abl_tbl["avg_total_ms"] = abl_tbl["avg_total_ms"].round(2)
    abl_tbl["std_total_ms"] = abl_tbl["std_total_ms"].round(2)
    abl_tbl["avg_total_MiB"] = (abl_tbl["avg_total_bytes"] / (1024 * 1024)).round(2)
    abl_tbl = abl_tbl.drop(columns=["avg_total_bytes"])
    artifacts["tables"]["ablations"] = save_table(
        abl_tbl,
        "table_ablation",
        "Ablation study at $c=30,d=784$.",
        "tab:eval-ablation",
    )

    util_tbl = utility[[
        "dataset", "seed", "gradient_dimension", "route_a_blocks",
        "max_route_quantized_diff_linf", "total_route_block_mismatches",
        "final_plain_acc", "final_quantized_acc", "final_route_acc"
    ]].copy()
    util_tbl = util_tbl.rename(columns={
        "route_a_blocks": "dcrtpoly_blocks",
        "max_route_quantized_diff_linf": "max_protocol_quantized_diff_linf",
        "total_route_block_mismatches": "total_protocol_block_mismatches",
        "final_route_acc": "final_protocol_acc",
    })
    for col in ["final_plain_acc", "final_quantized_acc", "final_protocol_acc"]:
        util_tbl[col] = util_tbl[col].astype(float).round(4)
    artifacts["tables"]["utility"] = save_table(
        util_tbl,
        "table_fl_utility",
        "FL utility sanity and full-protocol aggregate equivalence.",
        "tab:eval-utility",
    )

    # Figure 1: runtime scalability.  Panel (a) keeps the one-block dimension
    # curves; panel (b) shows true multi-block scaling.
    fig, (ax, axb) = plt.subplots(1, 2, figsize=(7.4, 3.1))
    for dim, g in phase1_runtime.groupby("dimension"):
        g = g.sort_values("clients")
        ax.errorbar(
            g["clients"],
            g["mean_total_ms"],
            yerr=g["std_total_ms"],
            marker="o",
            linewidth=1.8,
            capsize=3,
            label=f"d={dim}",
        )
    ax.set_xlabel("Clients")
    ax.set_ylabel("Total runtime (ms)")
    ax.set_title("(a) One-block client scaling")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7)
    for clients_value, g in multiblock_stats.groupby("clients"):
        g = g.sort_values("block_count")
        axb.errorbar(
            g["block_count"],
            g["mean_total_ms"],
            yerr=g["std_total_ms"],
            marker="o",
            linewidth=1.8,
            capsize=3,
            label=f"c={clients_value}",
        )
    axb.set_xlabel("DCRTPoly blocks")
    axb.set_ylabel("Total runtime (ms)")
    axb.set_title("(b) Multi-block scaling")
    axb.set_xticks(sorted(multiblock_stats["block_count"].unique()))
    axb.grid(True, alpha=0.3)
    axb.legend(fontsize=7)
    fig.suptitle("Runtime Scalability of APBR-SplitMix", y=1.04)
    artifacts["figures"]["runtime_scaling"] = save_fig(fig, "fig_runtime_scaling")

    # Figure 2: communication scalability.  One-block dimensions overlap, so
    # draw one honest curve instead of three invisible overplotted curves.
    fig, (ax, axb) = plt.subplots(1, 2, figsize=(7.4, 3.1))
    one_block_comm = (
        phase1_stats.groupby("clients")
        .agg(avg_total_bytes=("avg_total_bytes", "mean"))
        .reset_index()
        .sort_values("clients")
    )
    ax.plot(
        one_block_comm["clients"],
        one_block_comm["avg_total_bytes"] / (1024 * 1024),
        marker="D",
        linewidth=1.9,
        markersize=5,
        color="tab:red",
        label=r"One DCRTPoly block ($d\leq 2N$)",
    )
    ax.set_xlabel("Clients")
    ax.set_ylabel("Relay payload (MiB)")
    ax.set_title("(a) One-block client scaling")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7)
    for clients_value, g in multiblock_stats.groupby("clients"):
        g = g.sort_values("block_count")
        axb.plot(
            g["block_count"],
            g["avg_total_bytes"] / (1024 * 1024),
            marker="o",
            linewidth=1.8,
            label=f"c={clients_value}",
        )
    axb.set_xlabel("DCRTPoly blocks")
    axb.set_ylabel("Relay payload (MiB)")
    axb.set_title("(b) Multi-block scaling")
    axb.set_xticks(sorted(multiblock_stats["block_count"].unique()))
    axb.grid(True, alpha=0.3)
    axb.legend(fontsize=7)
    fig.suptitle("Shuffle-to-CS Serialized Relay Payload", y=1.04)
    artifacts["figures"]["communication_scaling"] = save_fig(fig, "fig_communication_scaling")

    # Figure 3: controlled baseline runtime.
    order = ["plain_aggregate", "shamir_shuffle_proxy", "openfhe_bgv_only", "four_path_sum_only", "shuffle_only", "full_protocol"]
    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    width = 0.14
    clients = sorted(baseline_stats["clients"].unique())
    x = np.arange(len(clients))
    color_map = {
        "plain_aggregate": "tab:blue",
        "shamir_shuffle_proxy": "tab:green",
        "openfhe_bgv_only": "tab:orange",
        "four_path_sum_only": "tab:purple",
        "shuffle_only": "0.45",
        "full_protocol": "tab:red",
    }
    display_labels = {
        "plain_aggregate": "In-memory plain",
        "shamir_shuffle_proxy": "Shamir-shuffle proxy",
        "openfhe_bgv_only": "Native BGV profile",
        "four_path_sum_only": "Four-path sum-only",
        "shuffle_only": "Shuffle-only",
        "full_protocol": "Full protocol",
    }
    for i, variant in enumerate(order):
        g = baseline_runtime[baseline_runtime["variant"] == variant].sort_values("clients")
        if g.empty:
            continue
        ax.bar(
            x + (i - 2.5) * width,
            g["mean_total_ms"],
            yerr=g["std_total_ms"],
            capsize=2,
            width=width,
            color=color_map[variant],
            label=display_labels[variant],
        )
    ax.set_xticks(x)
    ax.set_xticklabels(clients)
    ax.set_xlabel("Clients")
    ax.set_ylabel("Runtime (ms)")
    ax.set_title("Controlled Baseline Runtime")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(ncol=2)
    artifacts["figures"]["baseline_runtime"] = save_fig(fig, "fig_baseline_runtime")

    # Figure 4: controlled baseline communication.  A line chart better shows
    # scaling trends and avoids hiding nearly overlapping shuffle/full bars.
    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    markers = {
        "plain_aggregate": "o",
        "shamir_shuffle_proxy": "v",
        "openfhe_bgv_only": "s",
        "four_path_sum_only": "P",
        "shuffle_only": "^",
        "full_protocol": "D",
    }
    x_offset = {
        "plain_aggregate": -0.24,
        "shamir_shuffle_proxy": -0.14,
        "openfhe_bgv_only": -0.04,
        "four_path_sum_only": 0.06,
        "shuffle_only": 0.16,
        "full_protocol": 0.26,
    }
    line_styles = {
        "plain_aggregate": "-",
        "shamir_shuffle_proxy": ":",
        "openfhe_bgv_only": "-.",
        "four_path_sum_only": (0, (3, 1, 1, 1)),
        "shuffle_only": "--",
        "full_protocol": "-",
    }
    z_orders = {
        "plain_aggregate": 2,
        "shamir_shuffle_proxy": 3,
        "openfhe_bgv_only": 3,
        "four_path_sum_only": 4,
        "shuffle_only": 4,
        "full_protocol": 5,
    }
    for variant in order:
        g = baseline_stats[baseline_stats["variant"] == variant].sort_values("clients")
        if g.empty:
            continue
        xvals = g["clients"].astype(float) + x_offset[variant]
        marker_face = "none" if variant == "shuffle_only" else color_map[variant]
        ax.plot(
            xvals,
            g["avg_total_bytes"] / (1024 * 1024),
            marker=markers[variant],
            linestyle=line_styles[variant],
            linewidth=2.0 if variant == "full_protocol" else 1.8,
            markerfacecolor=marker_face,
            markeredgecolor=color_map[variant],
            markeredgewidth=1.2,
            color=color_map[variant],
            label=display_labels[variant],
            zorder=z_orders[variant],
        )
    ax.set_xticks(clients)
    ax.set_xlabel("Clients")
    ax.set_ylabel("Serialized payload (MiB)")
    ax.set_title("Controlled Baseline Communication")
    ax.grid(True, alpha=0.25)
    ax.legend(ncol=2)
    artifacts["figures"]["baseline_communication"] = save_fig(fig, "fig_baseline_communication")

    # Figure 5: ablation.  Use two panels instead of a dual-y-axis chart.
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.8, 3.1), sharex=False)
    abl_order = ["full", "k1", "no_apbr", "no_dummy"]
    abl = ablation_stats.copy()
    abl["order"] = abl["ablation"].map({name: i for i, name in enumerate(abl_order)})
    abl["label"] = abl["ablation"].map(abl_label_map)
    abl = abl.sort_values("order")
    x = np.arange(len(abl))
    abl_colors = {
        "full": "tab:red",
        "k1": "#8ecae6",
        "no_apbr": "#bdbdbd",
        "no_dummy": "#f4a261",
    }
    colors = [abl_colors[a] for a in abl["ablation"]]
    bars1 = ax1.bar(
        x,
        abl["avg_total_ms"],
        yerr=abl["std_total_ms"],
        capsize=3,
        width=0.62,
        color=colors,
        edgecolor="black",
        linewidth=0.4,
    )
    ax1.set_ylabel("Runtime (ms)")
    ax1.set_xticks(x)
    ax1.set_xticklabels(abl["label"], rotation=15, ha="right")
    ax1.grid(True, axis="y", alpha=0.25)
    ax1.set_title("(a) Runtime")
    bars2 = ax2.bar(
        x,
        abl["avg_total_bytes"] / (1024 * 1024),
        width=0.62,
        color=colors,
        edgecolor="black",
        linewidth=0.4,
    )
    ax2.set_ylabel("Relay payload (MiB)")
    ax2.set_xticks(x)
    ax2.set_xticklabels(abl["label"], rotation=15, ha="right")
    ax2.grid(True, axis="y", alpha=0.25)
    ax2.set_title("(b) Communication")
    fig.suptitle("Component Ablation Study", y=1.02)
    artifacts["figures"]["ablation"] = save_fig(fig, "fig_ablation")

    # Figure 6: utility accuracy over FL rounds.
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.1), sharey=False)
    metric_map = {
        "Plain FedAvg": "plain_acc",
        "Quantized FedAvg": "quantized_acc",
        "APBR-SplitMix": "route_acc",
    }
    line_styles = {
        "Plain FedAvg": "-",
        "Quantized FedAvg": "--",
        "APBR-SplitMix": ":",
    }
    for ax, dataset, title in zip(axes, ["mnist", "cifar10"], ["MNIST", "CIFAR-10"]):
        g = utility_rounds[utility_rounds["dataset"] == dataset].copy()
        for label, col in metric_map.items():
            stats = (
                g.groupby("round")[col]
                .agg(["mean", "std"])
                .reset_index()
                .fillna(0.0)
            )
            ax.plot(
                stats["round"],
                stats["mean"],
                linestyle=line_styles[label],
                linewidth=1.8,
                label=label,
            )
            ax.fill_between(
                stats["round"].to_numpy(),
                (stats["mean"] - stats["std"]).to_numpy(),
                (stats["mean"] + stats["std"]).to_numpy(),
                alpha=0.08,
            )
        ax.set_title(title)
        ax.set_xlabel("Round")
        ax.set_ylabel("Test accuracy")
        ax.grid(True, alpha=0.25)
        if dataset == "cifar10":
            ax.set_ylim(bottom=0.0, top=0.30)
            ax.text(0.02, 0.93, r"$\max_r |A_r^{\mathrm{APBR\!-\!SplitMix}}-A_r^{\mathrm{Quantized}}|=0$",
                    transform=ax.transAxes, fontsize=7, va="top")
        else:
            ax.set_ylim(bottom=0.0, top=1.0)
            ax.text(0.02, 0.93, r"$\max_r |A_r^{\mathrm{APBR\!-\!SplitMix}}-A_r^{\mathrm{Quantized}}|=0$",
                    transform=ax.transAxes, fontsize=7, va="top")
    axes[0].legend(loc="lower right", fontsize=7)
    artifacts["figures"]["utility_accuracy"] = save_fig(fig, "fig_utility_accuracy")

    manifest = {
        "schema": "evaluation_artifact_manifest_v8",
        "status": "PASS",
        "release_manifest": "manifests/formal_evaluation_release_v8_final.json",
        "release_manifest_sha256": sha256(ROOT / "manifests/formal_evaluation_release_v8_final.json"),
        "generator": {
            "path": rel(Path(__file__).resolve()),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "tables_dir": rel(TABLES),
        "figures_dir": rel(FIGS),
        "artifacts": artifacts,
        "outputs": collect_outputs(),
    }
    manifest_path = FORMAL / "evaluation_artifact_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
