import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt


DEFAULT_INPUT = "teccl/examples/results/interdc8_protection_summary.csv"
DEFAULT_OUTPUT_DIR = "teccl/examples/results/figures"


def _to_float(value):
    if value == "" or value is None:
        return None
    return float(value)


def _load_rows(path):
    with Path(path).open(newline="") as f:
        return list(csv.DictReader(f))


def _save(fig, output_dir, filename):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return path


def _style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="#d7dde6", linewidth=0.8, alpha=0.8)
    ax.set_axisbelow(True)


def _bar_label(ax, bars, fmt="{:.0f}"):
    for bar in bars:
        height = bar.get_height()
        if height is None:
            continue
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height,
            fmt.format(height),
            ha="center",
            va="bottom",
            fontsize=9,
        )


def plot_coverage(rows, output_dir):
    rows = [row for row in rows if row["strategy"] != "No Protection"]
    strategies = [row["strategy"].replace(" Protection", "") for row in rows]
    protected = [_to_float(row["protected_demands"]) for row in rows]
    affected = [_to_float(row["affected_demands"]) for row in rows]

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    bars = ax.bar(strategies, protected, color="#2f6f9f", width=0.58)
    ax.plot(strategies, affected, color="#c84c31", marker="o", linewidth=2, label="Affected demands")
    ax.set_title("Protection Coverage under Single-Link Failures", fontsize=14, weight="bold")
    ax.set_ylabel("Number of demands")
    ax.legend(frameon=False, loc="upper right")
    ax.set_ylim(0, max(affected) * 1.18)
    _bar_label(ax, bars)
    _style_axes(ax)
    return _save(fig, output_dir, "protection_coverage.png")


def plot_resource(rows, output_dir):
    rows = [row for row in rows if row["strategy"] != "No Protection"]
    strategies = [row["strategy"].replace(" Protection", "") for row in rows]
    values = [_to_float(row["protection_link_count"]) for row in rows]

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    bars = ax.bar(strategies, values, color=["#8c4f3d", "#347f6c", "#4e6fa8"], width=0.58)
    ax.set_title("Protection Link Occupation", fontsize=14, weight="bold")
    ax.set_ylabel("Protection links")
    ax.set_ylim(0, max(values) * 1.22)
    _bar_label(ax, bars)
    _style_axes(ax)
    return _save(fig, output_dir, "protection_links.png")


def plot_completion_time(rows, output_dir):
    strategies = [row["strategy"].replace(" Protection", "") for row in rows]
    working = [_to_float(row["normal_case_working_time"]) for row in rows]
    final = [_to_float(row["failure_aware_final_time"]) for row in rows]

    x = range(len(strategies))
    width = 0.34
    fig, ax = plt.subplots(figsize=(8.0, 4.4))
    working_bars = ax.bar([i - width / 2 for i in x], working, width, label="Working time", color="#2f6f9f")
    final_values = [v if v is not None else 0 for v in final]
    final_bars = ax.bar([i + width / 2 for i in x], final_values, width, label="Final time", color="#c8903d")

    for idx, value in enumerate(final):
        if value is None:
            ax.text(idx + width / 2, 0.5, "N/A", ha="center", va="bottom", fontsize=9, color="#555")

    ax.set_title("Working and Protection-Aware Completion Time", fontsize=14, weight="bold")
    ax.set_ylabel("Time (epochs)")
    ax.set_xticks(list(x))
    ax.set_xticklabels(strategies)
    ax.set_ylim(0, max([v for v in final_values + working if v is not None]) * 1.22)
    ax.legend(frameon=False, loc="upper left")
    _bar_label(ax, working_bars)
    _bar_label(ax, final_bars)
    _style_axes(ax)
    return _save(fig, output_dir, "completion_time.png")


def main():
    parser = argparse.ArgumentParser(description="Generate figures from the protection summary CSV.")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    rows = _load_rows(args.input)
    paths = [
        plot_coverage(rows, args.output_dir),
        plot_resource(rows, args.output_dir),
        plot_completion_time(rows, args.output_dir),
    ]
    for path in paths:
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
