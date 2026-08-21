"""Generate paper figures only from certified scenario-robust results."""

import json
import os
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

os.environ.setdefault("MPLCONFIGDIR", "/tmp/teccl_matplotlib")

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_RESULTS = ROOT / "teccl/examples/results"
PHYSICAL_RESULTS = EXAMPLE_RESULTS / "multicast_tree_pair_physical_25gb"
SCHEDULE_DIR = PHYSICAL_RESULTS / "full_directed_failures/schedules"
FAILURE_TIME_CERTIFICATE = (
    PHYSICAL_RESULTS / "failure_time_sweep/certificate.json"
)
DELAY_REPORT = (
    PHYSICAL_RESULTS / "detection_delay_boundary/certificate.json"
)
FIGURES = EXAMPLE_RESULTS / "figures/certified"
TOPOLOGIES = ("DCN4WAN", "InterDC8")
POLICIES = ("DPP", "DDPP")
POLICY_COLORS = {"DPP": "#4C78A8", "DDPP": "#F58518"}
POLICY_HATCHES = {"DPP": "", "DDPP": "//"}


def load(path: Path) -> Dict:
    return json.loads(path.read_text())


def style_axis(axis) -> None:
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.grid(axis="y", color="#D9DEE5", linewidth=0.75)
    axis.set_axisbelow(True)
    axis.tick_params(labelsize=9.5)


def add_value_labels(axis, bars, fmt: str = "{:.0f}") -> None:
    for bar in bars:
        value = float(bar.get_height())
        axis.annotate(
            fmt.format(value),
            (bar.get_x() + bar.get_width() / 2, value),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9.5,
            fontweight="bold",
        )


def annotate_pair(axis, x_value: float, values: Sequence[float], text: str) -> None:
    y_value = max(values)
    axis.annotate(
        text,
        (x_value, y_value),
        xytext=(0, 18),
        textcoords="offset points",
        ha="center",
        va="bottom",
        fontsize=9,
        color="#374151",
        fontweight="bold",
    )


def save_figure(figure, stem: str) -> List[Path]:
    FIGURES.mkdir(parents=True, exist_ok=True)
    outputs = [FIGURES / f"{stem}.{suffix}" for suffix in ("png", "pdf", "svg")]
    for path in outputs:
        options = {
            "bbox_inches": "tight",
            "facecolor": "white",
            "metadata": {"Creator": "TE-CCL certified-results pipeline"},
        }
        if path.suffix == ".png":
            options["dpi"] = 320
            options.pop("metadata")
        figure.savefig(path, **options)
    plt.close(figure)
    return outputs


def paired_metrics() -> Dict[Tuple[str, str], Dict[str, float]]:
    failure_time_certificate = load(FAILURE_TIME_CERTIFICATE)
    if (
        not failure_time_certificate.get("certified")
        or not failure_time_certificate.get("single_directed_link_failure")
    ):
        raise ValueError("Failure-time source plans are not certified")
    metrics = {}
    expected_scenarios = {"DCN4WAN": 32, "InterDC8": 26}
    for topology in TOPOLOGIES:
        schedules = {}
        for policy in POLICIES:
            path = SCHEDULE_DIR / f"{topology.lower()}_{policy.lower()}.json"
            row = load(path)
            schedules[policy] = row
            if (
                row.get("7aa-Multicast_Tree_Pair_Enforced") is not True
                or row.get("7j-Failure_Model") != "EXACT"
                or row.get("Solver_Status_Name") != "OPTIMAL"
                or len(row.get("9a-Failure_Scenarios", []))
                != expected_scenarios[topology]
            ):
                raise ValueError(f"Uncertified source schedule: {path}")
            metrics[(topology, policy)] = {
                "resource": float(row["14a-Contingency_Plan_Link_Epochs"]),
                "completion": float(row["9f-Actual_Protected_Completion_Epoch"]),
            }
        if schedules["DPP"]["10-Working_Flows"] != schedules["DDPP"][
            "10-Working_Flows"
        ]:
            raise ValueError(f"{topology} DPP/DDPP working schedules differ")
    return metrics


def delay_metrics() -> Dict[Tuple[str, str], float]:
    boundaries = load(DELAY_REPORT)["boundaries"]
    if not all(boundary["boundary_certified"] for boundary in boundaries):
        raise ValueError("Detection-delay report contains an uncertified boundary")
    return {
        (boundary["topology"], boundary["policy"]): float(
            boundary["maximum_detection_delay_epochs"]
        )
        for boundary in boundaries
    }


def plot_tradeoffs() -> List[Path]:
    paired = paired_metrics()
    delay = delay_metrics()
    x = np.arange(len(TOPOLOGIES))
    width = 0.34
    figure, axes = plt.subplots(1, 3, figsize=(13.2, 4.05))
    definitions = (
        ("resource", "(a) Reserved capacity", "Occupied link-epochs"),
        ("completion", "(b) Protected completion", "Epoch"),
        ("delay", "(c) Detection-delay tolerance", "Maximum delay (epochs)"),
    )

    values_by_panel = []
    for panel_index, (metric, title, ylabel) in enumerate(definitions):
        axis = axes[panel_index]
        panel_values = {}
        for policy_index, policy in enumerate(POLICIES):
            offset = (policy_index - 0.5) * width
            values = [
                delay[(topology, policy)]
                if metric == "delay"
                else paired[(topology, policy)][metric]
                for topology in TOPOLOGIES
            ]
            panel_values[policy] = values
            bars = axis.bar(
                x + offset,
                values,
                width,
                label=policy,
                color=POLICY_COLORS[policy],
                hatch=POLICY_HATCHES[policy],
                edgecolor="white",
                linewidth=0.8,
            )
            add_value_labels(axis, bars)
        values_by_panel.append(panel_values)
        axis.set_title(title, loc="left", fontsize=12.5, fontweight="bold")
        axis.set_ylabel(ylabel, fontsize=10)
        axis.set_xticks(x, TOPOLOGIES)
        maximum = max(max(values) for values in panel_values.values())
        axis.set_ylim(0, maximum * 1.27)
        style_axis(axis)

    pair_notes = (
        ("same", "same"),
        ("+14", "+20"),
        ("+5", "+16"),
    )
    for panel_index, notes in enumerate(pair_notes):
        for topology_index, note in enumerate(notes):
            values = [
                values_by_panel[panel_index][policy][topology_index]
                for policy in POLICIES
            ]
            annotate_pair(axes[panel_index], topology_index, values, note)

    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(
        handles,
        labels,
        loc="upper center",
        ncol=2,
        frameon=False,
        bbox_to_anchor=(0.5, 1.02),
        fontsize=10,
    )
    figure.text(
        0.5,
        0.005,
        "Fixed identical working schedules; audited feasible plans. Detection values are fixed-plan certificates.",
        ha="center",
        fontsize=8.8,
        color="#4B5563",
    )
    figure.tight_layout(rect=(0, 0.055, 1, 0.91), w_pad=2.1)
    return save_figure(figure, "certified-protection-tradeoffs")


def write_manifest(outputs: Sequence[Path]) -> None:
    manifest = {
        "authoritative": True,
        "retired_figure_directory": "teccl/examples/results/figures/preplanned_failure_execution",
        "figures": [
            {
                "stem": "certified-protection-tradeoffs",
                "claim": (
                    "DDPP preserves reserved capacity, completes later, and "
                    "tolerates more detection delay for the fixed audited plans."
                ),
                "sources": [
                    str(PHYSICAL_RESULTS.relative_to(ROOT)),
                    str(FAILURE_TIME_CERTIFICATE.relative_to(ROOT)),
                    str(DELAY_REPORT.relative_to(ROOT)),
                ],
            },
        ],
        "files": [str(path.relative_to(ROOT)) for path in outputs],
    }
    (FIGURES / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    lines = [
        "# Certified paper figures",
        "",
        "Only figures in this directory are authoritative for the current "
        "scenario-robust theory. Older preplanned-comparison and exposure-"
        "granularity figures are retained outside the submission and must not "
        "be used in the thesis.",
        "",
        "| figure | defensible interpretation |",
        "|---|---|",
        "| `certified-protection-tradeoffs` | Equal reserved capacity; DDPP trades later protected completion for greater fixed-plan detection tolerance. |",
        "",
        "The figure is exported as PNG, PDF, and SVG.",
        "",
    ]
    (FIGURES / "README.md").write_text("\n".join(lines))


def main() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.labelcolor": "#111827",
            "text.color": "#111827",
            "xtick.color": "#374151",
            "ytick.color": "#374151",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    outputs = plot_tradeoffs()
    write_manifest(outputs)
    for path in outputs:
        print(path)


if __name__ == "__main__":
    main()
