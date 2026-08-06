import argparse
import csv
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.solvers.protection_resource_accounting import (  # noqa: E402
    build_future_reservation_accounting,
)


EXAMPLES = ROOT / "teccl" / "examples"
RESULTS = EXAMPLES / "results" / "preplanned_comparison"
FLOW_RE = re.compile(
    r"Chunk (?P<chunk>\d+) from (?P<source>\d+) traveled over "
    r"(?P<i>\d+)->(?P<j>\d+) in epoch (?P<epoch>\d+)"
)

EXPERIMENTS = {
    "DCN4WAN": {
        "dedicated_input": EXAMPLES / "sample_inputs" / "dcn4wan_dedicated_comparison.json",
        "dedicated_schedule": EXAMPLES / "schedules" / "dcn4wan_dedicated_comparison_schedule.json",
        "deferred_input": EXAMPLES / "sample_inputs" / "dcn4wan_preplanned_deferred_protection.json",
        "deferred_schedule": EXAMPLES / "schedules" / "dcn4wan_preplanned_deferred_protection_schedule.json",
    },
    "InterDC8": {
        "dedicated_input": EXAMPLES / "sample_inputs" / "interdc8_dedicated_comparison.json",
        "dedicated_schedule": EXAMPLES / "schedules" / "interdc8_dedicated_comparison_schedule.json",
        "deferred_input": EXAMPLES / "sample_inputs" / "interdc8_preplanned_deferred_protection.json",
        "deferred_schedule": EXAMPLES / "schedules" / "interdc8_preplanned_deferred_protection_schedule.json",
    },
}


Flow = Tuple[int, int, int, int, int]


def load(path: Path) -> Dict:
    return json.loads(path.read_text())


def parse_flows(rows: Sequence[str]) -> List[Flow]:
    flows = []
    for row in rows:
        match = FLOW_RE.search(str(row))
        if not match:
            raise ValueError(f"Could not parse flow row: {row}")
        flows.append(
            (
                int(match.group("source")),
                int(match.group("i")),
                int(match.group("j")),
                int(match.group("chunk")),
                int(match.group("epoch")),
            )
        )
    return sorted(flows)


def working_flows(data: Dict) -> List[Flow]:
    return parse_flows(
        data.get("10-Working_Flows")
        or data.get("11-Working_Flows")
        or data.get("7a-Raw_Flows")
        or data.get("7-Flows")
        or []
    )


def protection_flows(data: Dict) -> List[Flow]:
    return parse_flows(
        data.get("11-Protection_Flows")
        or data.get("12-Protection_Flows")
        or []
    )


def completion_epoch(data: Dict, phase: str):
    if phase == "working":
        return (
            data.get("9c-Objective_Working_Completion_Epoch")
            or data.get("4a-Objective_Working_Completion_Epoch")
            or data.get("3-Epochs_Required")
            or data.get("3-Working_Epochs_Required")
        )
    return (
        data.get("9d-Objective_Protection_Completion_Epoch")
        or data.get("4b-Objective_Protection_Completion_Epoch")
    )


def run_solver(input_path: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "teccl",
        "solve",
        "--input_args",
        str(input_path.relative_to(ROOT)),
    ]
    subprocess.run(command, cwd=ROOT, check=True)


def run_all() -> None:
    for paths in EXPERIMENTS.values():
        run_solver(paths["dedicated_input"])
        run_solver(paths["deferred_input"])


def check_disjoint(working: Sequence[Flow], backup: Sequence[Flow]) -> bool:
    working_links = {}
    backup_links = {}
    for source, i, j, chunk, _ in working:
        working_links.setdefault((source, chunk), set()).add((i, j))
    for source, i, j, chunk, _ in backup:
        backup_links.setdefault((source, chunk), set()).add((i, j))
    return all(
        not links.intersection(backup_links.get(commodity, set()))
        for commodity, links in working_links.items()
    )


def verify_accounting(data: Dict, backup: Sequence[Flow]) -> bool:
    profile = data.get("14h-Chunk_Working_Completion_Profile") or []
    occupancy_by_link = {}
    for row in data.get("14i-Flow_Occupancy_Epochs_By_Link") or []:
        i, j = [int(node) for node in row["link"].split("->")]
        occupancy_by_link[(i, j)] = int(row["occupied_epochs_per_flow"])
    expected = build_future_reservation_accounting(
        backup,
        profile,
        occupancy_by_link,
        float(data.get("14p-Accounting_Epoch_Duration_Seconds", 1.0)),
        float(data.get("14q-Accounting_Chunk_Size_GB", 1.0)),
    )
    fields = {
        "14a-Contingency_Plan_Link_Epochs": "contingency_plan_link_epochs",
        "14b-Peak_Future_Reserved_Link_Epochs": "peak_future_reserved_link_epochs",
        "14c-Future_Reservation_Holding_Units": "future_reservation_holding_units",
        "14d-Released_Before_Scheduled_Slot_Link_Epochs": (
            "released_before_scheduled_slot_link_epochs"
        ),
        "14e-Failure_Free_Committed_Backup_Link_Epochs": (
            "failure_free_committed_backup_link_epochs"
        ),
        "14j-Contingency_Plan_Transmissions": "contingency_plan_transmissions",
        "14k-Contingency_Plan_Payload_GB_Links": (
            "contingency_plan_payload_gb_links"
        ),
        "14l-Contingency_Plan_Occupied_Link_Seconds": (
            "contingency_plan_occupied_link_seconds"
        ),
        "14m-Future_Reservation_Holding_Link_Second_Squared": (
            "future_reservation_holding_link_second_squared"
        ),
        "14o-Failure_Free_Committed_Backup_Link_Seconds": (
            "failure_free_committed_backup_link_seconds"
        ),
    }
    return all(
        math.isclose(
            float(data.get(output_key, math.nan)),
            float(expected[metric]),
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
        for output_key, metric in fields.items()
    )


def metric_row(topology: str, strategy: str, data: Dict) -> Dict[str, object]:
    epoch_duration = float(data["14p-Accounting_Epoch_Duration_Seconds"])
    working_epoch = float(completion_epoch(data, "working"))
    protection_epoch = float(completion_epoch(data, "protection"))
    solver_status = data.get("Solver_Status_Name", "")
    return {
        "topology": topology,
        "strategy": strategy,
        "primary_holding_objective": data.get(
            "12f-Primary_Holding_Objective",
            "not applicable",
        ),
        "optimization_quality": (
            "OPTIMAL" if solver_status == "OPTIMAL" else "FEASIBLE_TIME_LIMIT"
        ),
        "solver_status": solver_status,
        "solver_mip_gap": data.get("Solver_MIP_Gap", ""),
        "normal_completion_epoch": working_epoch,
        "protected_completion_epoch": protection_epoch,
        "normal_completion_seconds": working_epoch * epoch_duration,
        "protected_completion_seconds": protection_epoch * epoch_duration,
        "chunk_size_gb": data["14q-Accounting_Chunk_Size_GB"],
        "contingency_plan_transmissions": data[
            "14j-Contingency_Plan_Transmissions"
        ],
        "contingency_plan_link_epochs": data["14a-Contingency_Plan_Link_Epochs"],
        "contingency_plan_payload_gb_links": data[
            "14k-Contingency_Plan_Payload_GB_Links"
        ],
        "contingency_plan_occupied_link_seconds": data[
            "14l-Contingency_Plan_Occupied_Link_Seconds"
        ],
        "peak_future_reserved_link_epochs": data[
            "14b-Peak_Future_Reserved_Link_Epochs"
        ],
        "future_reservation_holding_units": data[
            "14c-Future_Reservation_Holding_Units"
        ],
        "future_reservation_holding_link_second_squared": data[
            "14m-Future_Reservation_Holding_Link_Second_Squared"
        ],
        "released_before_scheduled_slot_link_epochs": data[
            "14d-Released_Before_Scheduled_Slot_Link_Epochs"
        ],
        "failure_free_committed_backup_link_epochs": data[
            "14e-Failure_Free_Committed_Backup_Link_Epochs"
        ],
        "failure_free_committed_backup_link_seconds": data[
            "14o-Failure_Free_Committed_Backup_Link_Seconds"
        ],
        "failure_free_release_ratio": data["14f-Failure_Free_Release_Ratio"],
        "solver_runtime_seconds": data.get("Solver_Runtime", ""),
    }


def percent_change(baseline: float, candidate: float) -> float:
    if baseline == 0:
        return 0.0
    return 100.0 * (baseline - candidate) / baseline


def summarize():
    rows = []
    comparisons = []
    checks = []

    def add_check(topology: str, name: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "topology": topology,
                "name": name,
                "passed": passed,
                "detail": detail,
            }
        )

    for topology, paths in EXPERIMENTS.items():
        dedicated = load(paths["dedicated_schedule"])
        deferred = load(paths["deferred_schedule"])
        dedicated_working = working_flows(dedicated)
        deferred_working = working_flows(deferred)
        dedicated_backup = protection_flows(dedicated)
        deferred_backup = protection_flows(deferred)

        for label, data in (("Dedicated", dedicated), ("Preplanned Deferred", deferred)):
            add_check(
                topology,
                f"{label} solver returned a solution",
                int(data.get("Solver_Solution_Count", 0)) > 0,
                (
                    f"status={data.get('Solver_Status_Name')}, "
                    f"solutions={data.get('Solver_Solution_Count')}"
                ),
            )
        add_check(
            topology,
            "Preplanned Deferred working schedule equals Dedicated reference",
            dedicated_working == deferred_working,
            f"dedicated={len(dedicated_working)}, deferred={len(deferred_working)}",
        )
        add_check(
            topology,
            "Preplanned Deferred uses beta-weighted holding objective",
            (
                deferred.get("12f-Primary_Holding_Objective")
                == "beta-weighted occupied link-epoch holding"
            ),
            str(deferred.get("12f-Primary_Holding_Objective")),
        )
        for label, working, backup, data in (
            ("Dedicated", dedicated_working, dedicated_backup, dedicated),
            ("Preplanned Deferred", deferred_working, deferred_backup, deferred),
        ):
            working_commodities = {
                (source, chunk) for source, _, _, chunk, _ in working
            }
            backup_commodities = {
                (source, chunk) for source, _, _, chunk, _ in backup
            }
            add_check(
                topology,
                f"{label} contingency plan covers every working commodity",
                bool(backup) and backup_commodities == working_commodities,
                (
                    f"working_commodities={len(working_commodities)}, "
                    f"backup_commodities={len(backup_commodities)}"
                ),
            )
            add_check(
                topology,
                f"{label} working/backup directed-link disjointness",
                check_disjoint(working, backup),
                f"working={len(working)}, backup={len(backup)}",
            )
            add_check(
                topology,
                f"{label} common resource accounting is reproducible",
                verify_accounting(data, backup),
                f"holding={data.get('14c-Future_Reservation_Holding_Units')}",
            )

        max_working_epoch = max((flow[4] for flow in deferred_working), default=-1)
        min_deferred_epoch = min((flow[4] for flow in deferred_backup), default=-1)
        add_check(
            topology,
            "Preplanned Deferred backup starts after working traffic",
            min_deferred_epoch > max_working_epoch,
            f"max_working={max_working_epoch}, min_backup={min_deferred_epoch}",
        )
        add_check(
            topology,
            "Preplanned Deferred releases every future slot in failure-free execution",
            deferred.get("14f-Failure_Free_Release_Ratio") == 1.0,
            f"ratio={deferred.get('14f-Failure_Free_Release_Ratio')}",
        )

        rows.extend(
            [
                metric_row(topology, "Dedicated Protection", dedicated),
                metric_row(topology, "Preplanned Deferred Protection", deferred),
            ]
        )
        dedicated_row = rows[-2]
        deferred_row = rows[-1]
        comparison_quality = (
            "OPTIMAL_PAIR"
            if all(
                row["solver_status"] == "OPTIMAL"
                for row in (dedicated_row, deferred_row)
            )
            else "FEASIBLE_TIME_LIMIT_PAIR"
        )
        comparisons.append(
            {
                "topology": topology,
                "comparison_quality": comparison_quality,
                "normal_completion_delta_epochs": (
                    float(deferred_row["normal_completion_epoch"])
                    - float(dedicated_row["normal_completion_epoch"])
                ),
                "protected_completion_delay_epochs": (
                    float(deferred_row["protected_completion_epoch"])
                    - float(dedicated_row["protected_completion_epoch"])
                ),
                "normal_completion_delta_seconds": (
                    float(deferred_row["normal_completion_seconds"])
                    - float(dedicated_row["normal_completion_seconds"])
                ),
                "protected_completion_delay_seconds": (
                    float(deferred_row["protected_completion_seconds"])
                    - float(dedicated_row["protected_completion_seconds"])
                ),
                "contingency_plan_reduction_percent": percent_change(
                    float(dedicated_row["contingency_plan_link_epochs"]),
                    float(deferred_row["contingency_plan_link_epochs"]),
                ),
                "occupied_link_seconds_reduction_percent": percent_change(
                    float(dedicated_row["contingency_plan_occupied_link_seconds"]),
                    float(deferred_row["contingency_plan_occupied_link_seconds"]),
                ),
                "reservation_holding_reduction_percent": percent_change(
                    float(dedicated_row["future_reservation_holding_units"]),
                    float(deferred_row["future_reservation_holding_units"]),
                ),
                "physical_holding_reduction_percent": percent_change(
                    float(
                        dedicated_row[
                            "future_reservation_holding_link_second_squared"
                        ]
                    ),
                    float(
                        deferred_row[
                            "future_reservation_holding_link_second_squared"
                        ]
                    ),
                ),
                "failure_free_committed_slot_reduction_percent": percent_change(
                    float(
                        dedicated_row["failure_free_committed_backup_link_epochs"]
                    ),
                    float(
                        deferred_row["failure_free_committed_backup_link_epochs"]
                    ),
                ),
            }
        )

    return rows, comparisons, checks


def write_csv(rows: Sequence[Dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def format_value(value: object) -> str:
    if value in ("", None):
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def write_markdown(
    rows: Sequence[Dict[str, object]],
    comparisons: Sequence[Dict[str, object]],
    checks: Sequence[Dict[str, object]],
    path: Path,
) -> None:
    columns = [
        "topology",
        "strategy",
        "primary_holding_objective",
        "optimization_quality",
        "solver_status",
        "solver_mip_gap",
        "normal_completion_epoch",
        "protected_completion_epoch",
        "normal_completion_seconds",
        "protected_completion_seconds",
        "contingency_plan_transmissions",
        "contingency_plan_link_epochs",
        "contingency_plan_payload_gb_links",
        "contingency_plan_occupied_link_seconds",
        "peak_future_reserved_link_epochs",
        "future_reservation_holding_link_second_squared",
        "failure_free_committed_backup_link_seconds",
    ]
    lines = [
        "# Preplanned protection comparison",
        "",
        "Both protected results use the exact same working flows.",
        "Dedicated first generates a protectable working reference; Preplanned Deferred then fixes those flows exactly.",
        "An `OPTIMAL_PAIR` is proven optimal to the configured solver tolerance. A `FEASIBLE_TIME_LIMIT_PAIR` passes every exact constraint audit but is not proven optimal.",
        "The audit establishes fairness and feasibility; it does not replace an optimality proof.",
        "",
        "| " + " | ".join(columns) + " |",
        "|" + "|".join(["---"] * len(columns)) + "|",
    ]
    for row in rows:
        lines.append(
            "| " + " | ".join(format_value(row[column]) for column in columns) + " |"
        )
    lines.extend(
        [
            "",
            "## Paired changes",
            "",
            "| topology | quality | normal delta (s) | protected delay (s) | occupied-time reduction (%) | physical holding reduction (%) | committed-slot reduction (%) |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in comparisons:
        lines.append(
            "| {topology} | {comparison_quality} | "
            "{normal_completion_delta_seconds:.3f} | "
            "{protected_completion_delay_seconds:.3f} | "
            "{occupied_link_seconds_reduction_percent:.3f} | "
            "{physical_holding_reduction_percent:.3f} | "
            "{failure_free_committed_slot_reduction_percent:.3f} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Audit",
            "",
            f"Passed {sum(1 for check in checks if check['passed'])}/{len(checks)} checks.",
            "",
        ]
    )
    for check in checks:
        state = "PASS" if check["passed"] else "FAIL"
        lines.append(
            f"- [{state}] {check['topology']}: {check['name']} ({check['detail']})"
        )
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run and audit the paired Dedicated/Preplanned Deferred experiment."
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Regenerate reference, Dedicated, and Preplanned Deferred schedules.",
    )
    args = parser.parse_args()
    if args.run:
        run_all()

    rows, comparisons, checks = summarize()
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(rows, RESULTS / "comparison.csv")
    write_csv(comparisons, RESULTS / "paired_changes.csv")
    write_markdown(rows, comparisons, checks, RESULTS / "comparison.md")
    (RESULTS / "comparison.json").write_text(
        json.dumps(
            {
                "rows": rows,
                "paired_changes": comparisons,
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    failed = [check for check in checks if not check["passed"]]
    for check in checks:
        state = "PASS" if check["passed"] else "FAIL"
        print(f"[{state}] {check['topology']}: {check['name']}: {check['detail']}")
    print(f"Wrote results to {RESULTS}")
    if failed:
        raise SystemExit(f"Comparison audit failed: {len(failed)} checks")
    print(f"Comparison audit passed: {len(checks)} checks")


if __name__ == "__main__":
    main()
