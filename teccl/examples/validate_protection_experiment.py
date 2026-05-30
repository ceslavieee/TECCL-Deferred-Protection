import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


PRESETS = {
    "dcn4wan": {
        "Dedicated Protection": "teccl/examples/schedules/dcn4wan_dedicated_protection_schedule.json",
        "Shared Protection": "teccl/examples/schedules/dcn4wan_shared_protection_schedule.json",
        "Deferred Protection": "teccl/examples/schedules/dcn4wan_deferred_protection_schedule.json",
    },
    "interdc8": {
        "Dedicated Protection": "teccl/examples/schedules/interdc8_dedicated_protection_schedule.json",
        "Shared Protection": "teccl/examples/schedules/interdc8_shared_protection_schedule.json",
        "Deferred Protection": "teccl/examples/schedules/interdc8_deferred_protection_schedule.json",
    },
}


FLOW_RE = re.compile(
    r"Chunk (?P<c>\d+) from (?P<s>\d+) traveled over (?P<i>\d+)->(?P<j>\d+) in epoch (?P<k>\d+)"
)
DEMAND_LINK_RE = re.compile(
    r"Demand (?P<s>\d+)->(?P<d>\d+) chunk (?P<c>\d+) uses (?P<i>\d+)->(?P<j>\d+)"
)


def _load_json(path: Path) -> Dict:
    return json.loads(path.read_text())


def _strategy_from_raw(raw: str) -> Tuple[str, Path]:
    strategy, path = raw.split("=", 1)
    return strategy, Path(path)


def _first_present(data: Dict, keys: Iterable[str]):
    for key in keys:
        if key in data:
            return data[key]
    return None


def _failure_scenarios(data: Dict) -> List[str]:
    return list(_first_present(data, ["9-Failure_Scenarios", "9a-Failure_Scenarios"]) or [])


def _failure_model(data: Dict) -> str:
    return str(_first_present(data, ["8b-Failure_Model", "7j-Failure_Model"]) or "")


def _fixed_working_schedule(data: Dict) -> str:
    return str(_first_present(data, ["8e-Fixed_Working_Schedule", "7l-Fixed_Working_Schedule"]) or "")


def _working_flows(data: Dict) -> Set[Tuple[int, int, int, int, int]]:
    rows = _first_present(data, ["11-Working_Flows", "10-Working_Flows", "7a-Raw_Flows", "7-Flows"]) or []
    flows = set()
    for row in rows:
        match = FLOW_RE.search(str(row))
        if not match:
            continue
        flows.add(
            (
                int(match.group("s")),
                int(match.group("i")),
                int(match.group("j")),
                int(match.group("c")),
                int(match.group("k")),
            )
        )
    return flows


def _working_demand_links(data: Dict) -> Set[Tuple[int, int, int, int, int]]:
    rows = _first_present(data, ["11a-Working_Demand_Links", "10a-Working_Demand_Links"]) or []
    links = set()
    for row in rows:
        if isinstance(row, dict):
            i, j = [int(node) for node in row["link"].split("->")]
            links.add((int(row["source"]), int(row["destination"]), i, j, int(row["chunk"])))
            continue
        match = DEMAND_LINK_RE.search(str(row))
        if not match:
            continue
        links.add(
            (
                int(match.group("s")),
                int(match.group("d")),
                int(match.group("i")),
                int(match.group("j")),
                int(match.group("c")),
            )
        )
    return links


def _failure_summary(data: Dict) -> Dict:
    return _first_present(data, ["10-Failure_Scenario_Summary", "9b-Failure_Scenario_Summary"]) or {}


def _sum_summary(summary: Dict) -> Tuple[int, int, int]:
    affected = protected = unprotected = 0
    for info in summary.values():
        affected += len(info.get("affected_demands", []))
        protected += len(info.get("protected_demands", []))
        unprotected += len(info.get("unprotected_demands", []))
    return affected, protected, unprotected


def _same_path(left: str, right: Path) -> bool:
    if not left:
        return False
    left_path = Path(left)
    return left_path == right or left_path.name == right.name


def _record(
    checks: List[Dict[str, object]],
    name: str,
    passed: bool,
    detail: str,
    severity: str = "error",
) -> None:
    checks.append({"name": name, "passed": passed, "severity": severity, "detail": detail})


def validate(items: List[Tuple[str, Path]]) -> Tuple[List[Dict[str, object]], Dict[str, Dict[str, object]]]:
    data_by_strategy = {strategy: _load_json(path) for strategy, path in items}
    path_by_strategy = dict(items)
    checks: List[Dict[str, object]] = []

    required = ["Dedicated Protection", "Shared Protection", "Deferred Protection"]
    for strategy in required:
        _record(
            checks,
            f"{strategy} schedule present",
            strategy in data_by_strategy,
            str(path_by_strategy.get(strategy, "missing")),
        )
    if any(strategy not in data_by_strategy for strategy in required):
        return checks, {}

    dedicated = data_by_strategy["Dedicated Protection"]
    shared = data_by_strategy["Shared Protection"]
    deferred = data_by_strategy["Deferred Protection"]
    dedicated_path = path_by_strategy["Dedicated Protection"]

    scenario_sets = {strategy: set(_failure_scenarios(data_by_strategy[strategy])) for strategy in required}
    reference_scenarios = scenario_sets["Dedicated Protection"]
    for strategy in required:
        missing = sorted(reference_scenarios - scenario_sets[strategy])
        extra = sorted(scenario_sets[strategy] - reference_scenarios)
        _record(
            checks,
            f"{strategy} failure scenarios match dedicated",
            not missing and not extra and bool(reference_scenarios),
            f"count={len(scenario_sets[strategy])}, missing={missing[:5]}, extra={extra[:5]}",
        )

    models = {strategy: _failure_model(data_by_strategy[strategy]) for strategy in required}
    model_values = set(models.values())
    _record(
        checks,
        "failure model aligned",
        len(model_values) == 1 and "" not in model_values,
        ", ".join(f"{strategy}={model}" for strategy, model in models.items()),
    )

    for strategy, data in [("Shared Protection", shared), ("Deferred Protection", deferred)]:
        fixed_path = _fixed_working_schedule(data)
        _record(
            checks,
            f"{strategy} fixed to dedicated working schedule",
            _same_path(fixed_path, dedicated_path),
            f"fixed={fixed_path}, dedicated={dedicated_path}",
        )

    reference_flows = _working_flows(dedicated)
    _record(
        checks,
        "dedicated exports working flows",
        bool(reference_flows),
        f"count={len(reference_flows)}",
    )
    for strategy, data in [("Shared Protection", shared), ("Deferred Protection", deferred)]:
        flows = _working_flows(data)
        _record(
            checks,
            f"{strategy} working flows match dedicated",
            flows == reference_flows and bool(flows),
            f"count={len(flows)}, missing={len(reference_flows - flows)}, extra={len(flows - reference_flows)}",
        )

    reference_demand_links = _working_demand_links(dedicated)
    _record(
        checks,
        "dedicated exports working demand links",
        bool(reference_demand_links),
        f"count={len(reference_demand_links)}",
    )
    for strategy, data in [("Shared Protection", shared), ("Deferred Protection", deferred)]:
        demand_links = _working_demand_links(data)
        _record(
            checks,
            f"{strategy} working demand links match dedicated",
            demand_links == reference_demand_links and bool(demand_links),
            (
                f"count={len(demand_links)}, "
                f"missing={len(reference_demand_links - demand_links)}, "
                f"extra={len(demand_links - reference_demand_links)}"
            ),
        )

    metrics: Dict[str, Dict[str, object]] = {}
    for strategy in required:
        data = data_by_strategy[strategy]
        solver_quality = data.get("Solver_Quality") or {}
        solver_status = data.get("Solver_Status_Name") or solver_quality.get("status_name") or ""
        solver_mip_gap = data.get("Solver_MIP_Gap", solver_quality.get("mip_gap"))
        solver_solution_count = data.get("Solver_Solution_Count", solver_quality.get("solution_count"))
        summary = _failure_summary(data_by_strategy[strategy])
        affected, protected, unprotected = _sum_summary(summary)
        metrics[strategy] = {
            "failure_scenarios": len(_failure_scenarios(data_by_strategy[strategy])),
            "working_flows": len(_working_flows(data_by_strategy[strategy])),
            "working_demand_links": len(_working_demand_links(data_by_strategy[strategy])),
            "affected": affected,
            "protected": protected,
            "unprotected": unprotected,
            "fixed_working_schedule": _fixed_working_schedule(data_by_strategy[strategy]),
            "failure_model": _failure_model(data_by_strategy[strategy]),
            "solver_status": solver_status,
            "solver_mip_gap": solver_mip_gap,
            "solver_objective_value": data.get("Solver_Objective_Value", solver_quality.get("objective_value")),
            "solver_objective_bound": data.get("Solver_Objective_Bound", solver_quality.get("objective_bound")),
            "solver_runtime": data.get("Solver_Runtime", solver_quality.get("runtime")),
            "solver_solution_count": solver_solution_count,
        }
        _record(
            checks,
            f"{strategy} solver quality present",
            bool(solver_status) and solver_solution_count not in (None, 0),
            f"status={solver_status}, solutions={solver_solution_count}, mip_gap={solver_mip_gap}",
        )
        _record(
            checks,
            f"{strategy} solver finished with accepted status",
            solver_status in ("OPTIMAL", "TIME_LIMIT", "SOLUTION_LIMIT", "SUBOPTIMAL"),
            f"status={solver_status}",
        )
        _record(
            checks,
            f"{strategy} protects every reported affected demand",
            affected == protected and unprotected == 0,
            f"affected={affected}, protected={protected}, unprotected={unprotected}",
        )

    _record(
        checks,
        "dedicated and shared static exposure aligned",
        metrics["Dedicated Protection"]["affected"] == metrics["Shared Protection"]["affected"],
        (
            f"dedicated={metrics['Dedicated Protection']['affected']}, "
            f"shared={metrics['Shared Protection']['affected']}"
        ),
    )
    _record(
        checks,
        "deferred time-aware affected no larger than static exposure",
        metrics["Deferred Protection"]["affected"] <= metrics["Dedicated Protection"]["affected"],
        (
            f"deferred={metrics['Deferred Protection']['affected']}, "
            f"static={metrics['Dedicated Protection']['affected']}"
        ),
    )
    return checks, metrics


def _print_report(checks: List[Dict[str, object]], metrics: Dict[str, Dict[str, object]]) -> None:
    print("Protection Experiment Validation")
    print("")
    for check in checks:
        status = "PASS" if check["passed"] else "FAIL"
        print(f"[{status}] {check['name']}: {check['detail']}")
    if metrics:
        print("")
        print("Metrics")
        for strategy, values in metrics.items():
            print(f"- {strategy}: {json.dumps(values, sort_keys=True)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a protection experiment before comparing results.")
    parser.add_argument("--topology", choices=sorted(PRESETS), default="dcn4wan")
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Input in the form 'Strategy Name=path/to/schedule.json'. Overrides --topology when present.",
    )
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    items = (
        [_strategy_from_raw(raw) for raw in args.input]
        if args.input
        else [(strategy, Path(path)) for strategy, path in PRESETS[args.topology].items()]
    )
    checks, metrics = validate(items)
    failed = [check for check in checks if not check["passed"] and check["severity"] == "error"]
    if args.json_output:
        print(json.dumps({"checks": checks, "metrics": metrics, "passed": not failed}, indent=2))
    else:
        _print_report(checks, metrics)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
