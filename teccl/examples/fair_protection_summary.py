import argparse
import csv
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from validate_protection_experiment import validate as validate_protection_experiment


DEFAULT_INPUTS = {
    "No Protection": "teccl/examples/schedules/dcn4wan_baseline_schedule.json",
    "Dedicated Protection": "teccl/examples/schedules/dcn4wan_dedicated_protection_schedule.json",
    "Shared Protection": "teccl/examples/schedules/dcn4wan_shared_protection_schedule.json",
    "Deferred Protection": "teccl/examples/schedules/dcn4wan_deferred_protection_schedule.json",
}


DEMAND_RE = re.compile(r"Demand at (?P<dst>\d+) for chunk (?P<chunk>\d+) from (?P<src>\d+) met by epoch (?P<epoch>\d+)")
SEGMENT_RE = re.compile(r"(?P<u>\d+)->(?P<v>\d+) in epoch (?P<epoch>\d+)(?: via switches (?P<switches>[\d, ]+))?")
FLOW_RE = re.compile(r"(?:Scenario (?P<scenario>\d+): )?.* over (?P<link>\d+->\d+) in epoch (?P<epoch>\d+)")


def _load_json(path: Path) -> Dict:
    return json.loads(path.read_text())


def _strategy_from_raw(raw: str) -> Tuple[str, Path]:
    strategy, path = raw.split("=", 1)
    return strategy, Path(path)


def _expand_link(u: int, v: int, switches: Optional[str]) -> List[str]:
    if not switches:
        return [f"{u}->{v}"]
    switch_nodes = [int(node) for node in re.findall(r"\d+", switches)]
    nodes = [u] + switch_nodes + [v]
    return [f"{nodes[idx]}->{nodes[idx + 1]}" for idx in range(len(nodes) - 1)]


def _extract_baseline_demands(baseline: Dict) -> Dict[str, Dict[str, object]]:
    """Return demand-level links and completion epochs from one fixed baseline.

    The fixed baseline is the normalization anchor. It lets us report the same
    potentially affected and actually-at-risk demand counts for all strategies.
    """
    demands: Dict[str, Dict[str, object]] = {}
    for demand_label, path_segments in baseline.get("8-Chunk paths", {}).items():
        demand_match = DEMAND_RE.match(demand_label)
        if not demand_match:
            continue
        src = int(demand_match.group("src"))
        dst = int(demand_match.group("dst"))
        chunk = int(demand_match.group("chunk"))
        completion_epoch = int(demand_match.group("epoch"))
        demand_id = f"{src}->{dst}:c{chunk}"
        links: Set[str] = set()
        link_epochs: Set[Tuple[str, int]] = set()
        for segment in path_segments:
            segment_match = SEGMENT_RE.search(segment)
            if not segment_match:
                continue
            u = int(segment_match.group("u"))
            v = int(segment_match.group("v"))
            epoch = int(segment_match.group("epoch"))
            for link in _expand_link(u, v, segment_match.group("switches")):
                links.add(link)
                link_epochs.add((link, epoch))
        demands[demand_id] = {
            "source": src,
            "destination": dst,
            "chunk": chunk,
            "completion_epoch": completion_epoch,
            "links": links,
            "link_epochs": link_epochs,
        }
    return demands


def _failure_scenarios(rows: Iterable[Dict]) -> List[str]:
    for data in rows:
        scenarios = data.get("9-Failure_Scenarios") or data.get("9a-Failure_Scenarios")
        if scenarios:
            return list(scenarios)
    return []


def _failure_summary(data: Dict) -> Dict:
    return data.get("10-Failure_Scenario_Summary") or data.get("9b-Failure_Scenario_Summary") or {}


def _sum_failure_summary(summary: Dict) -> Tuple[int, int, int, int]:
    affected = protected = unprotected = released = 0
    for info in summary.values():
        affected += len(info.get("affected_demands", []))
        protected += len(info.get("protected_demands", []))
        unprotected += len(info.get("unprotected_demands", []))
        released += len(info.get("dynamically_released_demands", []))
    return affected, protected, unprotected, released


def _baseline_exposure_counts(
    demands: Dict[str, Dict[str, object]],
    scenarios: List[str],
    failure_time_epoch: Optional[int],
) -> Tuple[int, int]:
    potentially_affected = 0
    actually_at_risk = 0
    # Schedule JSON reports failure_time_epoch as 1-based for readability, while
    # individual flow/path epochs are 0-based. Convert before comparing with
    # path segment epochs.
    failure_time_zero_based = None if failure_time_epoch is None else max(0, failure_time_epoch - 1)
    for failed_link in scenarios:
        for demand in demands.values():
            if failed_link not in demand["links"]:
                continue
            potentially_affected += 1
            if failure_time_zero_based is None:
                continue
            # A demand is actually at risk only if the failed link would still
            # be used at or after the failure time. If the demand traversed the
            # link before the failure, that completed traversal is already safe.
            if any(
                link == failed_link and epoch >= failure_time_zero_based
                for link, epoch in demand["link_epochs"]
            ):
                actually_at_risk += 1
    return potentially_affected, actually_at_risk


def _baseline_exposure_curve(
    demands: Dict[str, Dict[str, object]],
    scenarios: List[str],
) -> List[Dict[str, int]]:
    max_epoch = 0
    for demand in demands.values():
        max_epoch = max(max_epoch, int(demand["completion_epoch"]))
        for _, epoch in demand["link_epochs"]:
            max_epoch = max(max_epoch, int(epoch))

    rows = []
    for failure_time_epoch in range(1, max_epoch + 2):
        potentially_affected, actually_at_risk = _baseline_exposure_counts(
            demands,
            scenarios,
            failure_time_epoch,
        )
        rows.append(
            {
                "failure_time_epoch": failure_time_epoch,
                "baseline_potentially_affected": potentially_affected,
                "baseline_actually_at_risk": actually_at_risk,
            }
        )
    return rows


def _reference_exposure_counts(
    data_by_strategy: Dict[str, Dict],
    baseline_demands: Dict[str, Dict[str, object]],
    scenarios: List[str],
    failure_time_epoch: Optional[int],
) -> Tuple[int, int, str]:
    reference_note = "no-protection baseline working schedule"
    path_exposed, time_aware_at_risk = _baseline_exposure_counts(
        baseline_demands,
        scenarios,
        failure_time_epoch,
    )

    dedicated = data_by_strategy.get("Dedicated Protection", {})
    dedicated_summary = _failure_summary(dedicated)
    if dedicated_summary:
        path_exposed = _sum_failure_summary(dedicated_summary)[0]
        reference_note = "dedicated fixed working demand exposure"

    deferred = data_by_strategy.get("Deferred Protection", {})
    deferred_summary = _failure_summary(deferred)
    if deferred_summary and deferred.get("7l-Fixed_Working_Schedule"):
        time_aware_at_risk = _sum_failure_summary(deferred_summary)[0]
        reference_note += " plus deferred time-aware risk"

    return path_exposed, time_aware_at_risk, reference_note


def _flow_link_epochs(flows: Optional[List[str]], include_scenario: bool = False) -> Tuple[int, int]:
    if not flows:
        return 0, 0
    flow_link_epochs = 0
    occupied: Set[Tuple[str, str, int]] = set()
    for flow in flows:
        match = FLOW_RE.search(flow)
        if not match:
            continue
        flow_link_epochs += 1
        scenario = match.group("scenario") if include_scenario else "static"
        occupied.add((scenario or "static", match.group("link"), int(match.group("epoch"))))
    return flow_link_epochs, len(occupied)


def _resource_metrics(strategy: str, data: Dict) -> Tuple[Optional[int], Optional[int], str]:
    final_epoch = data.get("4b-Objective_Protection_Completion_Epoch") or data.get("9d-Objective_Protection_Completion_Epoch")
    if final_epoch is None:
        final_epoch = data.get("9-Epochs_Required") or data.get("3-Epochs_Required")

    if strategy == "Dedicated Protection":
        links = data.get("8-Protection_Link_Count")
        if links is None or final_epoch is None:
            return None, None, "reserved backup link-epochs"
        return int(links), int(links * final_epoch), "reserved backup link-epochs"

    if strategy == "Shared Protection":
        links = data.get("8-Protection_Link_Count")
        if links is None or final_epoch is None:
            return None, None, "shared reserved backup link-epochs"
        return int(links), int(links * final_epoch), "shared reserved backup link-epochs"

    if strategy == "Deferred Protection":
        recovery_flows = data.get("11a-Recovery_Flows_By_Scenario") or data.get("11-Protection_Flows")
        flow_link_epochs, occupied_link_epochs = _flow_link_epochs(recovery_flows, include_scenario=True)
        return flow_link_epochs, occupied_link_epochs, "post-failure recovery link-epochs"

    return None, None, "none"


def _working_time(data: Dict) -> Optional[float]:
    if "4-Collective_Finish_Time" in data:
        return data["4-Collective_Finish_Time"]
    return data.get("9c-Objective_Working_Completion_Epoch") or data.get("3-Working_Epochs_Required")


def _final_time(data: Dict) -> Optional[float]:
    return data.get("9d-Objective_Protection_Completion_Epoch") or data.get("4b-Objective_Protection_Completion_Epoch")


def _safe_ratio(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _row(
    strategy: str,
    data: Dict,
    path_exposed: int,
    time_aware_at_risk: Optional[int],
    scenario_count: int,
) -> Dict[str, object]:
    affected, protected, unprotected, released = _sum_failure_summary(_failure_summary(data))
    resource_units, normalized_resource, resource_metric = _resource_metrics(strategy, data)
    failure_model = data.get("7j-Failure_Model") or data.get("8b-Failure_Model") or ""
    exposure_granularity = data.get("7k-Failure_Exposure_Granularity") or data.get("8c-Failure_Exposure_Granularity") or ""
    protection_timing = data.get("8d-Protection_Timing_Model")
    if not protection_timing and strategy == "Deferred Protection":
        protection_timing = "failure-time-aware deferred recovery"
    resource_per_path_exposed = _safe_ratio(normalized_resource, path_exposed)
    resource_per_time_aware_risk = _safe_ratio(normalized_resource, time_aware_at_risk)
    solver_quality = data.get("Solver_Quality") or {}
    return {
        "strategy": strategy,
        "failure_scenarios": scenario_count if strategy != "No Protection" else "",
        "reference_path_exposed_demands": path_exposed if strategy != "No Protection" else "",
        "reference_time_aware_at_risk_demands": time_aware_at_risk if strategy != "No Protection" else "",
        "model_reported_affected": affected if strategy != "No Protection" else "",
        "model_reported_protected": protected if strategy != "No Protection" else "",
        "model_reported_unprotected": unprotected if strategy != "No Protection" else "",
        "model_reported_released": released if strategy != "No Protection" else "",
        "failure_model": failure_model if strategy != "No Protection" else "",
        "failure_exposure_granularity": exposure_granularity if strategy != "No Protection" else "",
        "protection_timing_model": protection_timing if strategy != "No Protection" else "",
        "working_time": _working_time(data),
        "final_time": _final_time(data) if strategy != "No Protection" else "",
        "resource_units": resource_units if strategy != "No Protection" else "",
        "normalized_link_epoch_resource": normalized_resource if strategy != "No Protection" else "",
        "resource_per_reference_path_exposed": resource_per_path_exposed if strategy != "No Protection" else "",
        "resource_per_reference_time_aware_at_risk": resource_per_time_aware_risk if strategy != "No Protection" else "",
        "resource_metric": resource_metric if strategy != "No Protection" else "",
        "solver_status": data.get("Solver_Status_Name") or solver_quality.get("status_name") or "",
        "solver_mip_gap": data.get("Solver_MIP_Gap", solver_quality.get("mip_gap")),
        "solver_objective_value": data.get("Solver_Objective_Value", solver_quality.get("objective_value")),
        "solver_objective_bound": data.get("Solver_Objective_Bound", solver_quality.get("objective_bound")),
        "solver_runtime": data.get("Solver_Runtime", solver_quality.get("runtime")),
        "solver_solution_count": data.get("Solver_Solution_Count", solver_quality.get("solution_count")),
        "failure_time_epoch": data.get("7e-Failure_Time_Epoch", ""),
        "detection_delay_epochs": data.get("7f-Detection_Delay_Epochs", ""),
        "recovery_start_epoch": data.get("7g-Recovery_Start_Epoch", ""),
        "note": (
            "baseline only"
            if strategy == "No Protection"
            else protection_timing or "pre-planned protection"
        ),
    }


def _write_csv(rows: List[Dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _fmt(value: object) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _markdown(rows: List[Dict[str, object]], exposure_curve: List[Dict[str, int]], reference_note: str) -> str:
    headers = [
        "Strategy",
        "Failure Scenarios",
        "Reference Path Exposed",
        "Reference Time-Aware At Risk",
        "Model Affected",
        "Model Protected",
        "Unprotected",
        "Failure Model",
        "Exposure Granularity",
        "Protection Timing",
        "Working Time",
        "Final Time",
        "Resource Units",
        "Normalized Link-Epoch Resource",
        "Resource / Reference Path Exposed",
        "Resource / Reference Time-Aware Risk",
        "Resource Metric",
        "Solver Status",
        "MIP Gap",
        "Objective",
        "Bound",
        "Solver Runtime",
    ]
    keys = [
        "strategy",
        "failure_scenarios",
        "reference_path_exposed_demands",
        "reference_time_aware_at_risk_demands",
        "model_reported_affected",
        "model_reported_protected",
        "model_reported_unprotected",
        "failure_model",
        "failure_exposure_granularity",
        "protection_timing_model",
        "working_time",
        "final_time",
        "resource_units",
        "normalized_link_epoch_resource",
        "resource_per_reference_path_exposed",
        "resource_per_reference_time_aware_at_risk",
        "resource_metric",
        "solver_status",
        "solver_mip_gap",
        "solver_objective_value",
        "solver_objective_bound",
        "solver_runtime",
    ]
    lines = [
        "## Fair Protection Comparison Summary",
        "",
        f"This summary uses {reference_note} as the main exposure reference.",
        "It separates static path exposure from time-aware risk at the configured failure epoch.",
        "It also reports a normalized link-epoch-style resource metric so that reserved and recovery resources are not mixed without a label.",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(row[key]) for key in keys) + " |")
    lines.extend(
        [
            "",
            "### Interpretation Notes",
            "",
            "- `Reference Path Exposed` defaults to the dedicated fixed working demand exposure when available; otherwise it falls back to the no-protection baseline path exposure.",
            "- `Reference Time-Aware At Risk` uses deferred's fixed-schedule, failure-time-aware affected count when available; otherwise it falls back to the baseline path plus configured failure epoch.",
            "- `Model Affected` is the solver-reported affected count under that strategy's own protection timing model.",
            "- Resource ratios use the normalized link-epoch resource divided by the common reference exposure counts.",
            "- Fixed-schedule comparisons now pin both raw working flows and demand-level working links when the reference schedule includes them.",
            "- Dedicated/shared resources are pre-failure reserved link-epochs; deferred resources are post-failure recovery link-epochs.",
            "- Solver status, objective, bound, and MIP gap are copied from the schedule JSON generated by Gurobi.",
            "",
        ]
    )
    if exposure_curve:
        lines.extend(
            [
                "### Baseline Failure-Time Exposure Sweep",
                "",
                "This auxiliary sweep uses the no-protection baseline schedule and shows how many demands would still be at risk if the failure happened at each epoch.",
                "",
                "| Failure Time Epoch | Potentially Affected | Actually At Risk |",
                "| --- | --- | --- |",
            ]
        )
        for row in exposure_curve:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _fmt(row["failure_time_epoch"]),
                        _fmt(row["baseline_potentially_affected"]),
                        _fmt(row["baseline_actually_at_risk"]),
                    ]
                )
                + " |"
            )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a fairer normalized protection-comparison summary.")
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Input in the form 'Strategy Name=path/to/schedule.json'. Can be repeated.",
    )
    parser.add_argument(
        "--output-prefix",
        default="teccl/examples/results/dcn4wan_fair_protection_summary",
        help="Output prefix for .md, .csv, and .json files.",
    )
    args = parser.parse_args()

    items = [_strategy_from_raw(raw) for raw in args.input] if args.input else [(k, Path(v)) for k, v in DEFAULT_INPUTS.items()]
    data_by_strategy = {strategy: _load_json(path) for strategy, path in items}
    baseline = data_by_strategy.get("No Protection")
    if baseline is None:
        raise ValueError("A 'No Protection' baseline schedule is required for fair exposure accounting.")

    baseline_demands = _extract_baseline_demands(baseline)
    scenario_list = _failure_scenarios(data_by_strategy.values())
    scenario_count = len(scenario_list)
    exposure_curve = _baseline_exposure_curve(baseline_demands, scenario_list)

    deferred_data = data_by_strategy.get("Deferred Protection", {})
    failure_time_epoch = deferred_data.get("7e-Failure_Time_Epoch")
    path_exposed, time_aware_at_risk, reference_note = _reference_exposure_counts(
        data_by_strategy,
        baseline_demands,
        scenario_list,
        int(failure_time_epoch) if failure_time_epoch not in (None, "") else None,
    )

    rows = [
        _row(strategy, data, path_exposed, time_aware_at_risk, scenario_count)
        for strategy, data in data_by_strategy.items()
    ]
    validation_checks, validation_metrics = validate_protection_experiment(
        [(strategy, path) for strategy, path in items if strategy != "No Protection"]
    )
    validation_passed = all(
        check["passed"] or check["severity"] != "error"
        for check in validation_checks
    )

    output_prefix = Path(args.output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    md_path = output_prefix.with_suffix(".md")
    csv_path = output_prefix.with_suffix(".csv")
    json_path = output_prefix.with_suffix(".json")
    exposure_csv_path = output_prefix.with_name(output_prefix.name + "_exposure_curve").with_suffix(".csv")

    md_path.write_text(_markdown(rows, exposure_curve, reference_note))
    _write_csv(rows, csv_path)
    _write_csv(exposure_curve, exposure_csv_path)
    json_path.write_text(
        json.dumps(
            {
                "summary": rows,
                "reference_note": reference_note,
                "validation": {
                    "passed": validation_passed,
                    "checks": validation_checks,
                    "metrics": validation_metrics,
                },
                "baseline_exposure_curve": exposure_curve,
            },
            indent=2,
        )
    )

    print(md_path.read_text())
    print(f"Wrote {md_path}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")
    print(f"Wrote {exposure_csv_path}")


if __name__ == "__main__":
    main()
