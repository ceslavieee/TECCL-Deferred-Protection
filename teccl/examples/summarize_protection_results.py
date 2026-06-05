import argparse
import csv
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


DEFAULT_INPUTS = {
    "No Protection": "teccl/examples/schedules/interdc8_baseline_schedule.json",
    "Dedicated Protection": "teccl/examples/schedules/interdc8_dedicated_protection_schedule.json",
    "Shared Protection": "teccl/examples/schedules/interdc8_shared_protection_schedule.json",
    "Deferred Protection": "teccl/examples/schedules/interdc8_deferred_protection_allfail_schedule.json",
}


def _sum_failure_summary(summary: Dict) -> Tuple[int, int, int, int]:
    affected = protected = unprotected = released = 0
    for info in summary.values():
        affected += len(info.get("affected_demands", []))
        protected += len(info.get("protected_demands", []))
        unprotected += len(info.get("unprotected_demands", []))
        released += len(info.get("dynamically_released_demands", []))
    return affected, protected, unprotected, released


FLOW_LINK_RE = re.compile(r" over (\d+->\d+) in epoch ")
FLOW_EPOCH_RE = re.compile(r" epoch (\d+)")


def _flow_count(data: Dict, primary_key: str, fallback_key: str) -> Optional[int]:
    flows = data.get(primary_key)
    if flows is None:
        flows = data.get(fallback_key)
    if flows is None:
        return None
    if isinstance(flows, int):
        return flows
    return len(flows)


def _first_present(data: Dict, keys: List[str]) -> object:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _flows_from_keys(data: Dict, keys: List[str]) -> Optional[List[str]]:
    flows = _first_present(data, keys)
    if flows is None:
        return None
    return flows


def _link_count_from_flows(data: Dict, primary_key: str, fallback_key: str) -> Optional[int]:
    flows = data.get(primary_key)
    if flows is None:
        flows = data.get(fallback_key)
    if flows is None:
        return None
    if isinstance(flows, int):
        return flows
    links = set()
    for flow in flows:
        match = FLOW_LINK_RE.search(flow)
        if match:
            links.add(match.group(1))
    return len(links)


def _max_flow_epoch(flows: Optional[List[str]]) -> Optional[int]:
    if not flows:
        return None
    max_epoch = None
    for flow in flows:
        match = FLOW_EPOCH_RE.search(flow)
        if not match:
            continue
        epoch = int(match.group(1))
        if max_epoch is None or epoch > max_epoch:
            max_epoch = epoch
    return max_epoch


def _normal_case_working_time(data: Dict) -> Optional[float]:
    if "4-Collective_Finish_Time" in data:
        return data["4-Collective_Finish_Time"]

    epoch_duration = data.get("1-Epoch_Duration")
    objective_working_epochs = data.get("9c-Objective_Working_Completion_Epoch")
    if epoch_duration is not None and objective_working_epochs is not None:
        return epoch_duration * objective_working_epochs

    working_epochs = data.get("3-Epochs_Required") or data.get("3-Working_Epochs_Required")
    if epoch_duration is not None and working_epochs is not None:
        return epoch_duration * working_epochs

    working_flows = _flows_from_keys(data, ["11-Working_Flows", "10-Working_Flows", "7-Flows"])
    max_epoch = _max_flow_epoch(working_flows)
    if epoch_duration is not None and max_epoch is not None:
        return epoch_duration * (max_epoch + 1)

    return None


def _failure_aware_final_time(data: Dict) -> Optional[float]:
    epoch_duration = data.get("1-Epoch_Duration")
    final_epochs = data.get("9-Epochs_Required")
    if epoch_duration is not None and final_epochs is not None:
        return epoch_duration * final_epochs

    working_flows = _flows_from_keys(data, ["11-Working_Flows", "10-Working_Flows", "7-Flows"])
    protection_flows = _flows_from_keys(data, ["12-Protection_Flows", "11-Protection_Flows"])
    working_max = _max_flow_epoch(working_flows)
    protection_max = _max_flow_epoch(protection_flows)

    max_epoch = None
    for candidate in [working_max, protection_max]:
        if candidate is None:
            continue
        if max_epoch is None or candidate > max_epoch:
            max_epoch = candidate
    if epoch_duration is not None and max_epoch is not None:
        return epoch_duration * (max_epoch + 1)

    return None


def _extract_row(strategy: str, data: Dict) -> Dict[str, object]:
    affected = protected = unprotected = released = None
    failure_scenarios = None
    working_flows = _flows_from_keys(data, ["11-Working_Flows", "10-Working_Flows", "7-Flows"])
    protection_flows = _flows_from_keys(data, ["12-Protection_Flows", "11-Protection_Flows"])
    working_max_epoch = _max_flow_epoch(working_flows)

    if "10-Failure_Scenario_Summary" in data:
        affected, protected, unprotected, released = _sum_failure_summary(data["10-Failure_Scenario_Summary"])
        failure_scenarios = len(data.get("9-Failure_Scenarios", []))
    elif "9b-Failure_Scenario_Summary" in data:
        affected, protected, unprotected, released = _sum_failure_summary(data["9b-Failure_Scenario_Summary"])
        failure_scenarios = len(data.get("9a-Failure_Scenarios", []))

    working_epochs = (
        data.get("9c-Objective_Working_Completion_Epoch")
        or data.get("3-Epochs_Required")
        or data.get("3-Working_Epochs_Required")
    )
    if working_epochs is None and working_max_epoch is not None:
        working_epochs = working_max_epoch + 1

    failure_aware_final_time = _failure_aware_final_time(data)
    if failure_scenarios is None and protection_flows is None:
        failure_aware_final_time = None

    row = {
        "strategy": strategy,
        "epoch_duration": data.get("1-Epoch_Duration"),
        "normal_case_working_time": _normal_case_working_time(data),
        "failure_aware_final_time": failure_aware_final_time,
        "working_epochs": working_epochs,
        "final_epochs": data.get("9-Epochs_Required"),
        "working_flow_count": data.get("5-Working_Flow_Count")
        or _flow_count(data, "11-Working_Flows", "10-Working_Flows")
        or _flow_count(data, "7-Flows", "5-Working_Flow_Count"),
        "protection_flow_count": data.get("6-Protection_Flow_Count")
        or _flow_count(data, "12-Protection_Flows", "11-Protection_Flows"),
        "working_link_count": _link_count_from_flows(data, "11-Working_Flows", "10-Working_Flows")
        or _link_count_from_flows(data, "7-Flows", "5-Working_Flow_Count"),
        "protection_link_count": data.get("8-Protection_Link_Count")
        or _link_count_from_flows(data, "12-Protection_Flows", "11-Protection_Flows"),
        "per_demand_protection_link_count": data.get("8a-Per_Demand_Protection_Link_Count"),
        "failure_scenarios": failure_scenarios,
        "affected_demands": affected,
        "protected_demands": protected,
        "unprotected_demands": unprotected,
        "dynamically_released_demands": released,
        "heuristic_working_finish_epochs": data.get("3-Heuristic_Working_Finish_Epochs"),
        "heuristic_deferred_finish_epochs": data.get("4-Heuristic_Deferred_Finish_Epochs"),
        "deferred_deadline_factor": data.get("6-Deferred_Deadline_Factor"),
        "working_deadline_epoch": data.get("7-Working_Deadline_Epoch"),
        "deferred_activation_epoch": data.get("7a-Deferred_Activation_Epoch"),
        "failure_observation_epoch": data.get("7b-Failure_Observation_Epoch"),
        "real_failure_timing": data.get("7d-Real_Failure_Timing"),
        "failure_time_epoch": data.get("7e-Failure_Time_Epoch"),
        "detection_delay_epochs": data.get("7f-Detection_Delay_Epochs"),
        "recovery_start_epoch": data.get("7g-Recovery_Start_Epoch"),
        "final_deadline_epoch": data.get("8-Final_Deadline_Epoch"),
        "solver_time": data.get("Solver_Time"),
    }
    return row


def _fmt(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _build_markdown(rows: List[Dict[str, object]]) -> str:
    normal_headers = [
        "Strategy",
        "Working Time",
        "Working Epochs",
        "Working Flows",
        "Working Links",
        "Solver Time",
    ]
    failure_headers = [
        "Strategy",
        "Final Time",
        "Final Epochs",
        "Protection Flows",
        "Protection Links",
        "Affected",
        "Protected",
        "Released",
        "Unprotected",
        "Failure Scenarios",
        "Deadline Factor",
        "Working Deadline",
        "Deferred Start",
        "Failure Time",
        "Detection Delay",
        "Recovery Start",
        "Final Deadline",
    ]

    lines = [
        "## Normal-Case Working Summary",
        "",
        "This table focuses on the normal no-failure execution of the working collective.",
        "",
    ]
    lines.extend(
        [
            "| " + " | ".join(normal_headers) + " |",
            "| " + " | ".join(["---"] * len(normal_headers)) + " |",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _fmt(row["strategy"]),
                    _fmt(row["normal_case_working_time"]),
                    _fmt(row["working_epochs"]),
                    _fmt(row["working_flow_count"]),
                    _fmt(row["working_link_count"]),
                    _fmt(row["solver_time"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Failure-Aware / Protection-Aware Summary",
            "",
            "This table focuses on protection-related completion and coverage under the configured failure scenarios.",
            "",
        ]
    )
    lines.extend(
        [
            "| " + " | ".join(failure_headers) + " |",
            "| " + " | ".join(["---"] * len(failure_headers)) + " |",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _fmt(row["strategy"]),
                    _fmt(row["failure_aware_final_time"]),
                    _fmt(row["final_epochs"]),
                    _fmt(row["protection_flow_count"]),
                    _fmt(row["protection_link_count"]),
                    _fmt(row["affected_demands"]),
                    _fmt(row["protected_demands"]),
                    _fmt(row["dynamically_released_demands"]),
                    _fmt(row["unprotected_demands"]),
                    _fmt(row["failure_scenarios"]),
                    _fmt(row["deferred_deadline_factor"]),
                    _fmt(row["working_deadline_epoch"]),
                    _fmt(row["deferred_activation_epoch"]),
                    _fmt(row["failure_time_epoch"]),
                    _fmt(row["detection_delay_epochs"]),
                    _fmt(row["recovery_start_epoch"]),
                    _fmt(row["final_deadline_epoch"]),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _write_csv(rows: List[Dict[str, object]], path: Path) -> None:
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _load_rows(items: Iterable[Tuple[str, Path]]) -> List[Dict[str, object]]:
    rows = []
    for strategy, path in items:
        data = json.loads(path.read_text())
        rows.append(_extract_row(strategy, data))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize protection strategy schedules into a comparison table.")
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Custom input in the form 'Strategy Name=path/to/schedule.json'. Can be repeated.",
    )
    parser.add_argument(
        "--output-prefix",
        default="teccl/examples/results/interdc8_protection_summary",
        help="Output prefix for .md, .csv, and .json files.",
    )
    args = parser.parse_args()

    if args.input:
        items = []
        for raw in args.input:
            strategy, path = raw.split("=", 1)
            items.append((strategy, Path(path)))
    else:
        items = [(name, Path(path)) for name, path in DEFAULT_INPUTS.items()]

    rows = _load_rows(items)
    markdown = _build_markdown(rows)

    output_prefix = Path(args.output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    md_path = output_prefix.with_suffix(".md")
    csv_path = output_prefix.with_suffix(".csv")
    json_path = output_prefix.with_suffix(".json")

    md_path.write_text(markdown)
    _write_csv(rows, csv_path)
    json_path.write_text(json.dumps(rows, indent=2))

    print(markdown)
    print(f"Wrote {md_path}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
