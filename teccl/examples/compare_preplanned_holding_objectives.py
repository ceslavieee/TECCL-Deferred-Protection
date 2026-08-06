import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.examples.compare_preplanned_protection import (  # noqa: E402
    check_disjoint,
    completion_epoch,
    load,
    optimization_quality,
    pair_optimization_quality,
    percent_change,
    protection_flows,
    run_solver,
    verify_accounting,
    working_flows,
)


EXAMPLES = ROOT / "teccl" / "examples"
RESULTS = EXAMPLES / "results" / "preplanned_objective_ablation"
EXPERIMENTS = {
    "DCN4WAN": {
        "legacy_input": RESULTS / "inputs" / "dcn4wan_legacy.json",
        "legacy_schedule": RESULTS
        / "schedules"
        / "dcn4wan_legacy_schedule.json",
        "weighted_input": EXAMPLES
        / "sample_inputs"
        / "dcn4wan_preplanned_deferred_beta_weighted.json",
        "weighted_schedule": EXAMPLES
        / "schedules"
        / "dcn4wan_preplanned_deferred_beta_weighted_schedule.json",
    },
    "InterDC8": {
        "legacy_input": RESULTS / "inputs" / "interdc8_legacy.json",
        "legacy_schedule": RESULTS
        / "schedules"
        / "interdc8_legacy_schedule.json",
        "weighted_input": EXAMPLES
        / "sample_inputs"
        / "interdc8_preplanned_deferred_beta_weighted.json",
        "weighted_schedule": EXAMPLES
        / "schedules"
        / "interdc8_preplanned_deferred_beta_weighted_schedule.json",
    },
}


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def write_legacy_input(paths: Dict[str, Path]) -> None:
    data = load(paths["weighted_input"])
    data["InstanceParams"]["beta_weighted_holding_objective"] = False
    data["InstanceParams"]["schedule_output_file"] = relative(
        paths["legacy_schedule"]
    )
    paths["legacy_input"].parent.mkdir(parents=True, exist_ok=True)
    paths["legacy_schedule"].parent.mkdir(parents=True, exist_ok=True)
    paths["legacy_input"].write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n"
    )


def metric_row(
    topology: str,
    objective_mode: str,
    data: Dict,
) -> Dict[str, object]:
    epoch_duration = float(data["14p-Accounting_Epoch_Duration_Seconds"])
    status = data.get("Solver_Status_Name", "")
    return {
        "topology": topology,
        "objective_mode": objective_mode,
        "optimization_quality": optimization_quality(data),
        "solver_status": status,
        "solver_mip_gap": data.get("Solver_MIP_Gap", ""),
        "normal_completion_seconds": (
            float(completion_epoch(data, "working")) * epoch_duration
        ),
        "protected_completion_seconds": (
            float(completion_epoch(data, "protection")) * epoch_duration
        ),
        "flow_start_holding_units": data[
            "12d-Reservation_Holding_Units"
        ],
        "beta_weighted_holding_link_second_squared": data[
            "14m-Future_Reservation_Holding_Link_Second_Squared"
        ],
        "contingency_plan_transmissions": data[
            "14j-Contingency_Plan_Transmissions"
        ],
        "contingency_plan_occupied_link_seconds": data[
            "14l-Contingency_Plan_Occupied_Link_Seconds"
        ],
        "primary_objective_label": (
            data.get("12f-Primary_Holding_Objective")
            or "unweighted reserved flow-start holding"
        ),
    }


def summarize():
    rows: List[Dict[str, object]] = []
    comparisons: List[Dict[str, object]] = []
    checks: List[Dict[str, object]] = []

    def add_check(
        topology: str,
        name: str,
        passed: bool,
        detail: str,
    ) -> None:
        checks.append(
            {
                "topology": topology,
                "name": name,
                "passed": passed,
                "detail": detail,
            }
        )

    for topology, paths in EXPERIMENTS.items():
        legacy = load(paths["legacy_schedule"])
        weighted = load(paths["weighted_schedule"])
        legacy_working = working_flows(legacy)
        weighted_working = working_flows(weighted)
        legacy_backup = protection_flows(legacy)
        weighted_backup = protection_flows(weighted)

        for label, data, backup in (
            ("Legacy", legacy, legacy_backup),
            ("Beta weighted", weighted, weighted_backup),
        ):
            add_check(
                topology,
                f"{label} solver returned a solution",
                int(data.get("Solver_Solution_Count", 0)) > 0,
                (
                    f"status={data.get('Solver_Status_Name')}, "
                    f"gap={data.get('Solver_MIP_Gap')}"
                ),
            )
            add_check(
                topology,
                f"{label} accounting recomputes exactly",
                verify_accounting(data, backup),
                (
                    "holding_link_second_squared="
                    f"{data.get('14m-Future_Reservation_Holding_Link_Second_Squared')}"
                ),
            )
            add_check(
                topology,
                f"{label} is directed-link disjoint",
                check_disjoint(working_flows(data), backup),
                f"working={len(working_flows(data))}, backup={len(backup)}",
            )

        add_check(
            topology,
            "objective variants use identical working flows",
            legacy_working == weighted_working,
            f"legacy={len(legacy_working)}, weighted={len(weighted_working)}",
        )
        weighted_holding = float(
            weighted["14c-Future_Reservation_Holding_Units"]
        )
        add_check(
            topology,
            "weighted schedule reports the selected primary objective",
            (
                weighted.get("12f-Primary_Holding_Objective")
                == "beta-weighted occupied link-epoch holding"
                and math.isclose(
                    float(
                        weighted.get(
                            "12g-Primary_Holding_Objective_Value",
                            math.nan,
                        )
                    ),
                    weighted_holding,
                )
            ),
            (
                f"label={weighted.get('12f-Primary_Holding_Objective')}, "
                f"value={weighted.get('12g-Primary_Holding_Objective_Value')}, "
                f"accounting={weighted_holding}"
            ),
        )

        legacy_row = metric_row(topology, "LEGACY_FLOW_START", legacy)
        weighted_row = metric_row(
            topology,
            "BETA_WEIGHTED_HOLDING",
            weighted,
        )
        rows.extend([legacy_row, weighted_row])
        comparisons.append(
            {
                "topology": topology,
                "comparison_quality": pair_optimization_quality(
                    (legacy_row, weighted_row)
                ),
                "physical_holding_reduction_percent": percent_change(
                    float(
                        legacy_row[
                            "beta_weighted_holding_link_second_squared"
                        ]
                    ),
                    float(
                        weighted_row[
                            "beta_weighted_holding_link_second_squared"
                        ]
                    ),
                ),
                "occupied_link_seconds_reduction_percent": percent_change(
                    float(
                        legacy_row[
                            "contingency_plan_occupied_link_seconds"
                        ]
                    ),
                    float(
                        weighted_row[
                            "contingency_plan_occupied_link_seconds"
                        ]
                    ),
                ),
                "protected_completion_delta_seconds": (
                    float(weighted_row["protected_completion_seconds"])
                    - float(legacy_row["protected_completion_seconds"])
                ),
                "transmission_count_delta": (
                    int(weighted_row["contingency_plan_transmissions"])
                    - int(legacy_row["contingency_plan_transmissions"])
                ),
            }
        )
    return rows, comparisons, checks


def write_csv(rows: Sequence[Dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0].keys()),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: object) -> str:
    if value in ("", None):
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def write_markdown(
    rows: Sequence[Dict[str, object]],
    comparisons: Sequence[Dict[str, object]],
    checks: Sequence[Dict[str, object]],
) -> None:
    row_columns = [
        "topology",
        "objective_mode",
        "optimization_quality",
        "normal_completion_seconds",
        "protected_completion_seconds",
        "flow_start_holding_units",
        "beta_weighted_holding_link_second_squared",
        "contingency_plan_transmissions",
        "contingency_plan_occupied_link_seconds",
    ]
    comparison_columns = [
        "topology",
        "comparison_quality",
        "physical_holding_reduction_percent",
        "occupied_link_seconds_reduction_percent",
        "protected_completion_delta_seconds",
        "transmission_count_delta",
    ]
    lines = [
        "# Preplanned holding-objective ablation",
        "",
        "Both objective variants fix the same Dedicated working schedule and use identical protection constraints.",
        "Only the highest-priority Preplanned Deferred holding objective changes.",
        "",
        "| " + " | ".join(row_columns) + " |",
        "|" + "|".join(["---"] * len(row_columns)) + "|",
    ]
    for row in rows:
        lines.append(
            "| " + " | ".join(fmt(row[column]) for column in row_columns) + " |"
        )
    lines.extend(
        [
            "",
            "## Paired changes",
            "",
            "| " + " | ".join(comparison_columns) + " |",
            "|" + "|".join(["---"] * len(comparison_columns)) + "|",
        ]
    )
    for row in comparisons:
        lines.append(
            "| "
            + " | ".join(fmt(row[column]) for column in comparison_columns)
            + " |"
        )
    lines.extend(
        [
            "",
            "## Audit",
            "",
            f"Passed {sum(1 for item in checks if item['passed'])}/{len(checks)} checks.",
            "",
        ]
    )
    for item in checks:
        state = "PASS" if item["passed"] else "FAIL"
        lines.append(
            f"- [{state}] {item['topology']}: {item['name']} "
            f"({item['detail']})"
        )
    (RESULTS / "summary.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare legacy and beta-weighted preplanned objectives."
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Regenerate beta-weighted schedules before comparison.",
    )
    args = parser.parse_args()
    if args.run:
        for paths in EXPERIMENTS.values():
            write_legacy_input(paths)
            run_solver(paths["legacy_input"])
            run_solver(paths["weighted_input"])

    rows, comparisons, checks = summarize()
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(rows, RESULTS / "metrics.csv")
    write_csv(comparisons, RESULTS / "paired_changes.csv")
    write_csv(checks, RESULTS / "audit.csv")
    write_markdown(rows, comparisons, checks)
    (RESULTS / "summary.json").write_text(
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

    failed = [item for item in checks if not item["passed"]]
    print(
        f"Objective ablation audit: "
        f"{len(checks) - len(failed)}/{len(checks)} passed"
    )
    print(f"Wrote results to {RESULTS}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
