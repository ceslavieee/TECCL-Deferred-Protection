import csv
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/teccl_matplotlib")

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


RESULTS_DIR = Path("teccl/examples/results")
OUTPUT_DIR = RESULTS_DIR / "figures" / "fair_protection"

DATASETS = {
    "DCN4WAN": {
        "summary_csv": RESULTS_DIR / "dcn4wan_fair_protection_summary.csv",
        "summary_json": RESULTS_DIR / "dcn4wan_fair_protection_summary.json",
        "exposure": RESULTS_DIR / "dcn4wan_fair_protection_summary_exposure_curve.csv",
    },
    "InterDC8": {
        "summary_csv": RESULTS_DIR / "interdc8_fair_protection_summary.csv",
        "summary_json": RESULTS_DIR / "interdc8_fair_protection_summary.json",
        "exposure": RESULTS_DIR / "interdc8_fair_protection_summary_exposure_curve.csv",
    },
}

COLORS = {
    "Dedicated Protection": "#2f5d7c",
    "Shared Protection": "#2f7d6d",
    "Deferred Protection": "#b65f2a",
    "Reference Path Exposed": "#94a3b8",
    "Reference Time-Aware Risk": "#d1495b",
}


def load_csv(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def load_json(path):
    return json.loads(path.read_text())


def num(value, default=0.0):
    if value in ("", None):
        return default
    return float(value)


def short_name(strategy):
    return strategy.replace(" Protection", "")


def protected_rows(rows):
    return [row for row in rows if row["strategy"] != "No Protection"]


def save(fig, name):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    png = OUTPUT_DIR / f"{name}.png"
    svg = OUTPUT_DIR / f"{name}.svg"
    fig.savefig(png, dpi=260, bbox_inches="tight", facecolor="white")
    fig.savefig(svg, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return [png, svg]


def style_axis(ax, ylabel=None):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#9ca3af")
    ax.spines["bottom"].set_color("#9ca3af")
    ax.grid(axis="y", color="#e5e7eb", linewidth=1.0)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", labelsize=11, colors="#111827")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=12, color="#111827", labelpad=8)


def label_bars(ax, bars, fmt="{:.0f}"):
    ymax = ax.get_ylim()[1]
    for bar in bars:
        height = bar.get_height()
        if height <= 0:
            continue
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height + ymax * 0.025,
            fmt.format(height),
            ha="center",
            va="bottom",
            fontsize=10,
            color="#111827",
            weight="bold",
        )


def plot_reference_and_affected(dataset, rows):
    data = protected_rows(rows)
    labels = [short_name(row["strategy"]) for row in data]
    path_exposed = [num(row["reference_path_exposed_demands"]) for row in data]
    time_risk = [num(row["reference_time_aware_at_risk_demands"]) for row in data]
    affected = [num(row["model_reported_affected"]) for row in data]

    x = list(range(len(labels)))
    width = 0.24
    fig, ax = plt.subplots(figsize=(9.6, 5.6))
    bars1 = ax.bar([i - width for i in x], path_exposed, width, color=COLORS["Reference Path Exposed"], label="Static path exposed")
    bars2 = ax.bar(x, time_risk, width, color=COLORS["Reference Time-Aware Risk"], label="Time-aware at risk")
    bars3 = ax.bar([i + width for i in x], affected, width, color=[COLORS[row["strategy"]] for row in data], label="Model affected")
    ax.set_title(f"{dataset}: Exposure and affected demand counts", fontsize=16, weight="bold", loc="left", pad=14)
    ax.set_xticks(x, labels)
    style_axis(ax, "Demand count")
    ax.set_ylim(0, max(path_exposed + time_risk + affected) * 1.25 if data else 1)
    label_bars(ax, bars1)
    label_bars(ax, bars2)
    label_bars(ax, bars3)
    ax.legend(frameon=False, fontsize=10, loc="upper center", bbox_to_anchor=(0.5, -0.11), ncol=3)
    fig.subplots_adjust(bottom=0.20)
    return save(fig, f"{dataset.lower()}_exposure_affected")


def plot_resource_tradeoff(dataset, rows):
    data = protected_rows(rows)
    labels = [short_name(row["strategy"]) for row in data]
    resources = [num(row["normalized_link_epoch_resource"]) for row in data]
    resource_per_risk = [num(row["resource_per_reference_time_aware_at_risk"]) for row in data]
    colors = [COLORS[row["strategy"]] for row in data]

    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.6))
    bars = axes[0].bar(labels, resources, color=colors, width=0.62)
    axes[0].set_title("Total protection resource", fontsize=15, weight="bold", loc="left", pad=12)
    style_axis(axes[0], "Normalized link-epochs")
    axes[0].set_ylim(0, max(resources) * 1.25 if resources else 1)
    label_bars(axes[0], bars)

    bars = axes[1].bar(labels, resource_per_risk, color=colors, width=0.62)
    axes[1].set_title("Resource per time-aware risk", fontsize=15, weight="bold", loc="left", pad=12)
    style_axis(axes[1], "Link-epochs / demand")
    axes[1].set_ylim(0, max(resource_per_risk) * 1.25 if resource_per_risk else 1)
    label_bars(axes[1], bars, "{:.1f}")

    fig.suptitle(f"{dataset}: Protection resource comparison", fontsize=18, weight="bold", x=0.02, ha="left")
    fig.subplots_adjust(top=0.80, wspace=0.30)
    return save(fig, f"{dataset.lower()}_resource_tradeoff")


def plot_completion_and_quality(dataset, rows):
    data = protected_rows(rows)
    labels = [short_name(row["strategy"]) for row in data]
    final_time = [num(row["final_time"]) for row in data]
    mip_gap = [num(row["solver_mip_gap"]) * 100 for row in data]
    runtime = [num(row["solver_runtime"]) for row in data]
    colors = [COLORS[row["strategy"]] for row in data]

    fig, axes = plt.subplots(1, 3, figsize=(15.2, 5.4))
    bars = axes[0].bar(labels, final_time, color=colors, width=0.62)
    axes[0].set_title("Failure-aware completion", fontsize=14, weight="bold", loc="left", pad=12)
    style_axis(axes[0], "Epochs")
    axes[0].set_ylim(0, max(final_time) * 1.25 if final_time else 1)
    label_bars(axes[0], bars)

    bars = axes[1].bar(labels, runtime, color=colors, width=0.62)
    axes[1].set_title("Solver runtime", fontsize=14, weight="bold", loc="left", pad=12)
    style_axis(axes[1], "Seconds")
    axes[1].set_ylim(0, max(runtime) * 1.25 if runtime else 1)
    label_bars(axes[1], bars, "{:.2f}")

    bars = axes[2].bar(labels, mip_gap, color=colors, width=0.62)
    axes[2].set_title("MIP gap", fontsize=14, weight="bold", loc="left", pad=12)
    style_axis(axes[2], "Percent")
    axes[2].set_ylim(0, max(mip_gap) * 1.35 if max(mip_gap, default=0) > 0 else 0.1)
    label_bars(axes[2], bars, "{:.3f}%")

    fig.suptitle(f"{dataset}: Completion and solve quality", fontsize=18, weight="bold", x=0.02, ha="left")
    fig.subplots_adjust(top=0.78, wspace=0.35)
    return save(fig, f"{dataset.lower()}_completion_quality")


def plot_failure_time_curve(dataset, exposure_rows, rows):
    epochs = [int(row["failure_time_epoch"]) for row in exposure_rows]
    potential = [int(row["baseline_potentially_affected"]) for row in exposure_rows]
    risk = [int(row["baseline_actually_at_risk"]) for row in exposure_rows]
    failure_time = None
    for row in rows:
        if row["strategy"] == "Deferred Protection":
            failure_time = num(row["failure_time_epoch"], None)
            break

    fig, ax = plt.subplots(figsize=(9.8, 5.6))
    ax.plot(epochs, potential, color="#64748b", linewidth=2.8, marker="o", label="Baseline path exposed")
    ax.plot(epochs, risk, color=COLORS["Reference Time-Aware Risk"], linewidth=2.8, marker="o", label="Baseline time-aware risk")
    if failure_time is not None:
        ax.axvline(failure_time, color="#111827", linewidth=1.4, linestyle="--")
        ax.text(failure_time + 0.12, max(potential) * 0.82, f"failure epoch {failure_time:.0f}", fontsize=11)
    ax.set_title(f"{dataset}: Failure-time sensitivity on baseline path", fontsize=16, weight="bold", loc="left", pad=14)
    ax.set_xlabel("Failure time epoch", fontsize=12)
    style_axis(ax, "Demand count")
    ax.set_xlim(min(epochs) - 0.25, max(epochs) + 0.25)
    ax.set_ylim(0, max(potential) * 1.18 if potential else 1)
    ax.legend(frameon=False, fontsize=10, loc="upper right")
    return save(fig, f"{dataset.lower()}_failure_time_curve")


def plot_storyboard(all_data):
    fig = plt.figure(figsize=(15.6, 8.8))
    grid = fig.add_gridspec(2, 2, wspace=0.30, hspace=0.42)

    for idx, (dataset, payload) in enumerate(all_data.items()):
        rows = protected_rows(payload["summary"])
        labels = [short_name(row["strategy"]) for row in rows]
        resources = [num(row["normalized_link_epoch_resource"]) for row in rows]
        affected = [num(row["model_reported_affected"]) for row in rows]
        colors = [COLORS[row["strategy"]] for row in rows]

        ax = fig.add_subplot(grid[idx, 0])
        bars = ax.bar(labels, resources, color=colors, width=0.62)
        ax.set_title(f"{dataset}: resource", fontsize=15, weight="bold", loc="left", pad=10)
        style_axis(ax, "Link-epochs")
        ax.set_ylim(0, max(resources) * 1.25 if resources else 1)
        label_bars(ax, bars)

        ax = fig.add_subplot(grid[idx, 1])
        bars = ax.bar(labels, affected, color=colors, width=0.62)
        ax.set_title(f"{dataset}: model affected", fontsize=15, weight="bold", loc="left", pad=10)
        style_axis(ax, "Demands")
        ax.set_ylim(0, max(affected) * 1.25 if affected else 1)
        label_bars(ax, bars)

    fig.suptitle(
        "Deferred protection targets time-aware risk under a fixed protectable working schedule",
        fontsize=20,
        weight="bold",
        x=0.02,
        ha="left",
    )
    fig.subplots_adjust(top=0.90)
    return save(fig, "fair_protection_storyboard")


def main():
    written = []
    all_data = {}
    for dataset, paths in DATASETS.items():
        rows = load_csv(paths["summary_csv"])
        summary_json = load_json(paths["summary_json"])
        if not summary_json.get("validation", {}).get("passed"):
            raise ValueError(f"{dataset} summary failed validation; refusing to plot stale experiment results.")
        exposure_rows = load_csv(paths["exposure"])
        all_data[dataset] = {"summary": rows, "exposure": exposure_rows}
        written.extend(plot_reference_and_affected(dataset, rows))
        written.extend(plot_resource_tradeoff(dataset, rows))
        written.extend(plot_completion_and_quality(dataset, rows))
        written.extend(plot_failure_time_curve(dataset, exposure_rows, rows))
    written.extend(plot_storyboard(all_data))
    for path in written:
        print(path)


if __name__ == "__main__":
    main()
