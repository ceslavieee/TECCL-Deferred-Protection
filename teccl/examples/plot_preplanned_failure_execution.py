import csv
import os
from pathlib import Path
from typing import Dict, List

os.environ.setdefault("MPLCONFIGDIR", "/tmp/teccl_matplotlib")

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
RESULTS = (
    ROOT / "teccl" / "examples" / "results" / "preplanned_failure_execution"
)
FIGURES = (
    ROOT
    / "teccl"
    / "examples"
    / "results"
    / "figures"
    / "preplanned_failure_execution"
)
STRATEGIES = (
    "Dedicated Protection",
    "Preplanned Deferred Protection",
)
TOPOLOGIES = ("DCN4WAN", "InterDC8")
COLORS = {
    "Dedicated Protection": "#1f4e79",
    "Preplanned Deferred Protection": "#e07a2d",
}
LABELS = {
    "Dedicated Protection": "Dedicated",
    "Preplanned Deferred Protection": "Preplanned Deferred",
}


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def index_rows(
    rows: List[Dict[str, str]],
) -> Dict[tuple, Dict[str, str]]:
    return {
        (row["topology"], row["strategy"]): row
        for row in rows
    }


def style_axis(axis) -> None:
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.grid(axis="y", color="#d9dde2", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.tick_params(axis="both", labelsize=10)


def label_bars(axis, bars, decimals: int = 1) -> None:
    for bar in bars:
        value = bar.get_height()
        axis.annotate(
            f"{value:.{decimals}f}",
            (bar.get_x() + bar.get_width() / 2, value),
            xytext=(0, 5),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
        )


def add_pair_change(
    axis,
    x_position: float,
    y_position: float,
    text: str,
    color: str,
) -> None:
    axis.annotate(
        text,
        (x_position, y_position),
        ha="center",
        va="bottom",
        fontsize=10,
        color=color,
        fontweight="bold",
    )


def plot_overall(rows: List[Dict[str, str]]) -> List[Path]:
    indexed = index_rows(rows)
    x = np.arange(len(TOPOLOGIES))
    width = 0.34
    fig, axes = plt.subplots(1, 2, figsize=(11.8, 4.7))

    resource_bars = []
    completion_bars = []
    for strategy_index, strategy in enumerate(STRATEGIES):
        offset = (strategy_index - 0.5) * width
        resource = [
            float(
                indexed[(topology, strategy)][
                    "mean_failure_execution_backup_occupied_link_seconds_when_affected"
                ]
            )
            for topology in TOPOLOGIES
        ]
        completion = [
            float(
                indexed[(topology, strategy)][
                    "mean_completion_seconds_when_affected"
                ]
            )
            for topology in TOPOLOGIES
        ]
        resource_bars.append(
            axes[0].bar(
                x + offset,
                resource,
                width,
                label=LABELS[strategy],
                color=COLORS[strategy],
                edgecolor="white",
                linewidth=0.8,
            )
        )
        completion_bars.append(
            axes[1].bar(
                x + offset,
                completion,
                width,
                label=LABELS[strategy],
                color=COLORS[strategy],
                edgecolor="white",
                linewidth=0.8,
            )
        )

    axes[0].set_title(
        "(a) Protection resource",
        loc="left",
        fontsize=13,
        fontweight="bold",
    )
    axes[0].set_ylabel("Average occupied link-seconds")
    axes[0].set_xticks(x, TOPOLOGIES)
    axes[0].set_ylim(0, 115)

    axes[1].set_title(
        "(b) AllGather completion",
        loc="left",
        fontsize=13,
        fontweight="bold",
    )
    axes[1].set_ylabel("Average completion time (s)")
    axes[1].set_xticks(x, TOPOLOGIES)
    axes[1].set_ylim(0, 42)

    for bars in resource_bars:
        label_bars(axes[0], bars, decimals=1)
    for bars in completion_bars:
        label_bars(axes[1], bars, decimals=1)

    for topology_index, topology in enumerate(TOPOLOGIES):
        dedicated = indexed[(topology, STRATEGIES[0])]
        deferred = indexed[(topology, STRATEGIES[1])]
        dedicated_resource = float(
            dedicated[
                "mean_failure_execution_backup_occupied_link_seconds_when_affected"
            ]
        )
        deferred_resource = float(
            deferred[
                "mean_failure_execution_backup_occupied_link_seconds_when_affected"
            ]
        )
        reduction = 100.0 * (
            dedicated_resource - deferred_resource
        ) / dedicated_resource
        completion_delay = float(
            deferred["mean_completion_seconds_when_affected"]
        ) - float(dedicated["mean_completion_seconds_when_affected"])
        add_pair_change(
            axes[0],
            topology_index,
            max(dedicated_resource, deferred_resource) + 9,
            f"-{reduction:.1f}%",
            COLORS[STRATEGIES[1]],
        )
        add_pair_change(
            axes[1],
            topology_index,
            max(
                float(dedicated["mean_completion_seconds_when_affected"]),
                float(deferred["mean_completion_seconds_when_affected"]),
            )
            + 3.0,
            f"+{completion_delay:.1f} s",
            COLORS[STRATEGIES[1]],
        )

    for axis in axes:
        style_axis(axis)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        ncol=2,
        frameon=False,
        bbox_to_anchor=(0.5, 1.03),
        fontsize=10,
    )
    fig.text(
        0.5,
        0.01,
        "Single directed-link failures that affect AllGather; identical working schedules",
        ha="center",
        fontsize=9.5,
        color="#4b5563",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 0.92), w_pad=3.0)

    FIGURES.mkdir(parents=True, exist_ok=True)
    outputs = [
        FIGURES / "failure_resource_completion_tradeoff.png",
        FIGURES / "failure_resource_completion_tradeoff.pdf",
        FIGURES / "failure_resource_completion_tradeoff.svg",
    ]
    for path in outputs:
        if path.suffix == ".png":
            fig.savefig(
                path,
                dpi=300,
                bbox_inches="tight",
                facecolor="white",
            )
        else:
            fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return outputs


def main() -> None:
    rows = read_csv(RESULTS / "overall.csv")
    outputs = plot_overall(rows)
    for path in outputs:
        print(path)


if __name__ == "__main__":
    main()
