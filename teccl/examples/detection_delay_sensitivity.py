import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

os.environ.setdefault("MPLCONFIGDIR", "/tmp/teccl_matplotlib")

import matplotlib.pyplot as plt


RESULTS_DIR = Path("teccl/examples/results/detection_delay_sensitivity")
FIGURES_DIR = Path("teccl/examples/results/figures/fair_protection")

DATASETS = {
    "dcn4wan": {
        "label": "DCN4WAN",
        "deferred_input": Path("teccl/examples/sample_inputs/dcn4wan_deferred_protection.json"),
        "delays": [0, 1, 2, 3, 4, 5],
    },
    "interdc8": {
        "label": "InterDC8",
        "deferred_input": Path("teccl/examples/sample_inputs/interdc8_deferred_protection.json"),
        "delays": [0, 1, 2, 3, 4, 5],
    },
}

FLOW_RE = re.compile(r"(?:Scenario (?P<scenario>\d+): )?.* over (?P<link>\d+->\d+) in epoch (?P<epoch>\d+)")


def load_json(path: Path) -> Dict:
    return json.loads(path.read_text())


def write_json(data: Dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def failure_summary(data: Dict) -> Dict:
    return data.get("9b-Failure_Scenario_Summary") or data.get("10-Failure_Scenario_Summary") or {}


def sum_summary(summary: Dict) -> Tuple[int, int, int]:
    affected = protected = unprotected = 0
    for info in summary.values():
        affected += len(info.get("affected_demands", []))
        protected += len(info.get("protected_demands", []))
        unprotected += len(info.get("unprotected_demands", []))
    return affected, protected, unprotected


def flow_link_epochs(flows: List[str]) -> Tuple[int, int]:
    flow_count = 0
    occupied = set()
    for flow in flows:
        match = FLOW_RE.search(flow)
        if not match:
            continue
        flow_count += 1
        scenario = match.group("scenario") or "static"
        occupied.add((scenario, match.group("link"), int(match.group("epoch"))))
    return flow_count, len(occupied)


def run_solver(input_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "teccl", "solve", "--input_args", str(input_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def sweep_dataset(name: str, config: Dict) -> List[Dict[str, object]]:
    base_input = load_json(config["deferred_input"])
    rows = []
    for delay in config["delays"]:
        trial_input = json.loads(json.dumps(base_input))
        trial_input["InstanceParams"]["detection_delay_epochs"] = delay
        schedule_path = RESULTS_DIR / "schedules" / f"{name}_deferred_detection_delay_{delay}.json"
        input_path = RESULTS_DIR / "inputs" / f"{name}_deferred_detection_delay_{delay}.json"
        trial_input["InstanceParams"]["schedule_output_file"] = str(schedule_path)
        write_json(trial_input, input_path)
        run_solver(input_path)

        schedule = load_json(schedule_path)
        affected, protected, unprotected = sum_summary(failure_summary(schedule))
        recovery_flows = schedule.get("11a-Recovery_Flows_By_Scenario") or schedule.get("11-Protection_Flows") or []
        recovery_flow_link_epochs, recovery_occupied_link_epochs = flow_link_epochs(recovery_flows)
        rows.append(
            {
                "dataset": config["label"],
                "detection_delay_epochs": delay,
                "failure_time_epoch": schedule.get("7e-Failure_Time_Epoch"),
                "recovery_start_epoch": schedule.get("7g-Recovery_Start_Epoch"),
                "affected": affected,
                "protected": protected,
                "unprotected": unprotected,
                "recovery_flow_link_epochs": recovery_flow_link_epochs,
                "recovery_occupied_link_epochs": recovery_occupied_link_epochs,
                "final_epoch": schedule.get("9-Epochs_Required"),
                "solver_status": schedule.get("Solver_Status_Name"),
                "solver_mip_gap": schedule.get("Solver_MIP_Gap"),
                "solver_runtime": schedule.get("Solver_Runtime"),
                "schedule_file": str(schedule_path),
            }
        )
    return rows


def write_csv(rows: List[Dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_dataset(name: str, rows: List[Dict[str, object]]) -> List[Path]:
    label = rows[0]["dataset"]
    x = [int(row["detection_delay_epochs"]) for row in rows]
    recovery_start = [int(row["recovery_start_epoch"]) for row in rows]
    final_epoch = [int(row["final_epoch"]) for row in rows]
    resource = [int(row["recovery_occupied_link_epochs"]) for row in rows]
    unprotected = [int(row["unprotected"]) for row in rows]

    fig, axes = plt.subplots(1, 3, figsize=(15.0, 4.8))
    plots = [
        (axes[0], recovery_start, final_epoch, "Recovery and completion", "Epochs"),
        (axes[1], None, resource, "Recovery resource", "Occupied link-epochs"),
        (axes[2], None, unprotected, "Unprotected demands", "Demand count"),
    ]
    for ax, baseline, values, title, ylabel in plots:
        if baseline is not None:
            ax.plot(x, baseline, color="#64748b", linewidth=2.5, marker="o", label="Recovery start")
            label_name = "Final completion"
        else:
            label_name = title
        ax.plot(x, values, color="#b65f2a", linewidth=2.8, marker="o", label=label_name)
        ax.set_title(title, fontsize=13, weight="bold", loc="left")
        ax.set_xlabel("Detection delay epochs")
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", color="#e5e7eb", linewidth=1.0)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        if title == "Unprotected demands" and max(values) == 0:
            ax.set_ylim(0, 1)
        ax.legend(frameon=False, fontsize=9)
    fig.suptitle(f"{label}: Deferred detection-delay sensitivity", fontsize=17, weight="bold", x=0.02, ha="left")
    fig.subplots_adjust(top=0.78, wspace=0.32)

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    png = FIGURES_DIR / f"{name}_detection_delay_sensitivity.png"
    svg = FIGURES_DIR / f"{name}_detection_delay_sensitivity.svg"
    fig.savefig(png, dpi=260, bbox_inches="tight", facecolor="white")
    fig.savefig(svg, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return [png, svg]


def main() -> None:
    all_rows = []
    written = []
    for name, config in DATASETS.items():
        rows = sweep_dataset(name, config)
        all_rows.extend(rows)
        csv_path = RESULTS_DIR / f"{name}_detection_delay_sensitivity.csv"
        json_path = RESULTS_DIR / f"{name}_detection_delay_sensitivity.json"
        write_csv(rows, csv_path)
        write_json({"rows": rows}, json_path)
        written.extend([csv_path, json_path])
        written.extend(plot_dataset(name, rows))

    combined_csv = RESULTS_DIR / "detection_delay_sensitivity.csv"
    combined_json = RESULTS_DIR / "detection_delay_sensitivity.json"
    write_csv(all_rows, combined_csv)
    write_json({"rows": all_rows}, combined_json)
    written.extend([combined_csv, combined_json])
    for path in written:
        print(path)


if __name__ == "__main__":
    main()
