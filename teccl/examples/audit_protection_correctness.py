import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "teccl" / "examples"

PRESETS = {
    "dcn4wan": {
        "dedicated": EXAMPLES / "schedules" / "dcn4wan_dedicated_protection_schedule.json",
        "shared": EXAMPLES / "schedules" / "dcn4wan_shared_protection_schedule.json",
        "deferred": EXAMPLES / "schedules" / "dcn4wan_deferred_protection_schedule.json",
    },
    "interdc8": {
        "dedicated": EXAMPLES / "schedules" / "interdc8_dedicated_protection_schedule.json",
        "shared": EXAMPLES / "schedules" / "interdc8_shared_protection_schedule.json",
        "deferred": EXAMPLES / "schedules" / "interdc8_deferred_protection_schedule.json",
    },
}

FLOW_RE = re.compile(
    r"Chunk (?P<c>\d+) from (?P<s>\d+) traveled over (?P<i>\d+)->(?P<j>\d+) in epoch (?P<k>\d+)"
)
RECOVERY_FLOW_RE = re.compile(
    r"Scenario (?P<scenario>\d+): chunk (?P<c>\d+) from (?P<s>\d+) "
    r"recovered over (?P<i>\d+)->(?P<j>\d+) in epoch (?P<k>\d+)"
)
DEMAND_LINK_RE = re.compile(
    r"Demand (?P<s>\d+)->(?P<d>\d+) chunk (?P<c>\d+) uses (?P<i>\d+)->(?P<j>\d+)"
)
DEMAND_LINK_EPOCH_RE = re.compile(
    r"Demand (?P<s>\d+)->(?P<d>\d+) chunk (?P<c>\d+) uses "
    r"(?P<i>\d+)->(?P<j>\d+) in epoch (?P<k>\d+)"
)
DEMAND_RE = re.compile(r"Demand \((?P<s>\d+)->(?P<d>\d+), chunk (?P<c>\d+)\)")
PROTECTION_LINK_RE = re.compile(
    r"Chunk (?P<c>\d+) from (?P<s>\d+) reserves (?P<i>\d+)->(?P<j>\d+)"
)
LINK_RE = re.compile(r"(?P<i>\d+)->(?P<j>\d+)")


Flow = Tuple[int, int, int, int, int]
DemandLink = Tuple[int, int, int, int, int]
DemandLinkEpoch = Tuple[int, int, int, int, int, int]
Demand = Tuple[int, int, int]
Link = Tuple[int, int]
ProtectionLink = Tuple[int, int, int, int]


class Audit:
    def __init__(self) -> None:
        self.checks: List[Dict[str, object]] = []

    def record(self, name: str, passed: bool, detail: str = "", severity: str = "error") -> None:
        self.checks.append(
            {
                "name": name,
                "passed": passed,
                "severity": severity,
                "detail": detail,
            }
        )

    @property
    def errors(self) -> List[Dict[str, object]]:
        return [check for check in self.checks if not check["passed"] and check["severity"] == "error"]

    @property
    def warnings(self) -> List[Dict[str, object]]:
        return [check for check in self.checks if not check["passed"] and check["severity"] == "warning"]


def load_json(path: Path) -> Dict:
    return json.loads(path.read_text())


def first_present(data: Dict, keys: Iterable[str]):
    for key in keys:
        if key in data:
            return data[key]
    return None


def parse_link(raw: str) -> Optional[Link]:
    match = LINK_RE.search(raw)
    if not match:
        return None
    return int(match.group("i")), int(match.group("j"))


def parse_demand(raw: str) -> Optional[Demand]:
    match = DEMAND_RE.search(raw)
    if not match:
        return None
    return int(match.group("s")), int(match.group("d")), int(match.group("c"))


def parse_flow_rows(rows: Sequence[object]) -> Set[Flow]:
    flows: Set[Flow] = set()
    for row in rows:
        match = FLOW_RE.search(str(row))
        if match:
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


def working_flows(data: Dict) -> Set[Flow]:
    rows = first_present(data, ["11-Working_Flows", "10-Working_Flows", "7a-Raw_Flows", "7-Flows"]) or []
    return parse_flow_rows(rows)


def working_demand_links(data: Dict) -> Set[DemandLink]:
    rows = first_present(data, ["11a-Working_Demand_Links", "10a-Working_Demand_Links"]) or []
    links: Set[DemandLink] = set()
    for row in rows:
        if isinstance(row, dict):
            link = parse_link(str(row.get("link", "")))
            if link is not None:
                links.add(
                    (
                        int(row["source"]),
                        int(row["destination"]),
                        link[0],
                        link[1],
                        int(row["chunk"]),
                    )
                )
            continue
        match = DEMAND_LINK_RE.search(str(row))
        if match:
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


def working_demand_link_epochs(data: Dict) -> Set[DemandLinkEpoch]:
    rows = first_present(data, ["11b-Working_Demand_Link_Epochs", "10b-Working_Demand_Link_Epochs"]) or []
    epochs: Set[DemandLinkEpoch] = set()
    for row in rows:
        if isinstance(row, dict):
            link = parse_link(str(row.get("link", "")))
            if link is not None:
                epochs.add(
                    (
                        int(row["source"]),
                        int(row["destination"]),
                        link[0],
                        link[1],
                        int(row["chunk"]),
                        int(row["epoch"]),
                    )
                )
            continue
        match = DEMAND_LINK_EPOCH_RE.search(str(row))
        if match:
            epochs.add(
                (
                    int(match.group("s")),
                    int(match.group("d")),
                    int(match.group("i")),
                    int(match.group("j")),
                    int(match.group("c")),
                    int(match.group("k")),
                )
            )
    return epochs


def protection_links(data: Dict) -> Set[ProtectionLink]:
    links: Set[ProtectionLink] = set()
    for row in data.get("13-Protection_Links", []):
        match = PROTECTION_LINK_RE.search(str(row))
        if match:
            links.add(
                (
                    int(match.group("s")),
                    int(match.group("c")),
                    int(match.group("i")),
                    int(match.group("j")),
                )
            )
    return links


def recovery_flows(data: Dict) -> List[Tuple[int, int, int, int, int, int]]:
    flows: List[Tuple[int, int, int, int, int, int]] = []
    for row in data.get("11a-Recovery_Flows_By_Scenario", []):
        match = RECOVERY_FLOW_RE.search(str(row))
        if match:
            flows.append(
                (
                    int(match.group("scenario")),
                    int(match.group("s")),
                    int(match.group("i")),
                    int(match.group("j")),
                    int(match.group("c")),
                    int(match.group("k")),
                )
            )
    return flows


def failure_scenarios(data: Dict) -> List[Link]:
    raw = first_present(data, ["9-Failure_Scenarios", "9a-Failure_Scenarios"]) or []
    parsed = [parse_link(str(row)) for row in raw]
    return [link for link in parsed if link is not None]


def failure_summary(data: Dict) -> Dict:
    return first_present(data, ["10-Failure_Scenario_Summary", "9b-Failure_Scenario_Summary"]) or {}


def fixed_working_schedule(data: Dict) -> str:
    return str(first_present(data, ["8e-Fixed_Working_Schedule", "7l-Fixed_Working_Schedule"]) or "")


def failure_model(data: Dict) -> str:
    return str(first_present(data, ["8b-Failure_Model", "7j-Failure_Model"]) or "")


def solver_status(data: Dict) -> str:
    quality = data.get("Solver_Quality") or {}
    return str(data.get("Solver_Status_Name") or quality.get("status_name") or "")


def solver_solution_count(data: Dict):
    quality = data.get("Solver_Quality") or {}
    return data.get("Solver_Solution_Count", quality.get("solution_count"))


def solver_gap(data: Dict):
    quality = data.get("Solver_Quality") or {}
    return data.get("Solver_MIP_Gap", quality.get("mip_gap"))


def resource_metrics(data: Dict) -> Tuple[int, int]:
    if "8-Protection_Link_Count" in data:
        units = int(data.get("8-Protection_Link_Count") or 0)
        finish_time = float(
            data.get("4b-Objective_Protection_Completion_Epoch")
            or data.get("4-Collective_Finish_Time")
            or data.get("3-Working_Epochs_Required")
            or 0
        )
        return units, int(round(units * finish_time))
    units = len(data.get("11-Protection_Flows", []))
    return units, units


def same_schedule_path(raw: str, reference: Path) -> bool:
    if not raw:
        return False
    path = Path(raw)
    return path == reference or path.name == reference.name


def summary_demands(rows: Sequence[object]) -> Set[Demand]:
    demands: Set[Demand] = set()
    for row in rows:
        demand = parse_demand(str(row))
        if demand is not None:
            demands.add(demand)
    return demands


def summary_counts(summary: Dict) -> Tuple[int, int, int]:
    affected = protected = unprotected = 0
    for info in summary.values():
        affected += len(info.get("affected_demands", []))
        protected += len(info.get("protected_demands", []))
        unprotected += len(info.get("unprotected_demands", []))
    return affected, protected, unprotected


def unique_demands_from_links(links: Set[DemandLink]) -> Set[Demand]:
    return {(source, destination, chunk) for source, destination, _i, _j, chunk in links}


def audit_summary_partition(audit: Audit, label: str, data: Dict, demand_universe: Set[Demand]) -> None:
    summary = failure_summary(data)
    bad = []
    for scenario, info in summary.items():
        affected = summary_demands(info.get("affected_demands", []))
        unaffected = summary_demands(info.get("unaffected_demands", []))
        protected = summary_demands(info.get("protected_demands", []))
        unprotected = summary_demands(info.get("unprotected_demands", []))
        missing = demand_universe - affected - unaffected
        extra = (affected | unaffected) - demand_universe
        overlap = affected & unaffected
        if missing or extra or overlap or protected | unprotected != affected:
            bad.append(
                {
                    "scenario": scenario,
                    "missing": len(missing),
                    "extra": len(extra),
                    "overlap": len(overlap),
                    "affected": len(affected),
                    "protected_plus_unprotected": len(protected | unprotected),
                }
            )
    audit.record(
        f"{label}: scenario summaries partition demand set",
        not bad and bool(summary),
        f"scenarios={len(summary)}, demand_count={len(demand_universe)}, bad={bad[:3]}",
    )


def audit_demand_link_epochs(audit: Audit, label: str, data: Dict) -> None:
    links = working_demand_links(data)
    epochs = working_demand_link_epochs(data)
    epoch_links = {(s, d, i, j, c) for s, d, i, j, c, _k in epochs}
    missing = links - epoch_links
    extra = epoch_links - links
    repeated = {}
    for s, d, i, j, c, k in epochs:
        repeated.setdefault((s, d, i, j, c), []).append(k)
    ambiguous = {key: sorted(values) for key, values in repeated.items() if len(values) > 1}
    audit.record(
        f"{label}: working demand-link epochs cover demand links",
        bool(epochs) and not missing and not extra,
        f"epochs={len(epochs)}, missing={len(missing)}, extra={len(extra)}",
    )
    audit.record(
        f"{label}: working demand-link epochs are unique",
        not ambiguous,
        f"ambiguous={len(ambiguous)}, examples={list(ambiguous.items())[:3]}",
        severity="warning",
    )


def audit_solver(audit: Audit, label: str, data: Dict) -> None:
    status = solver_status(data)
    solution_count = solver_solution_count(data)
    gap = solver_gap(data)
    audit.record(
        f"{label}: solver quality present",
        bool(status) and solution_count not in (None, 0),
        f"status={status}, solutions={solution_count}, mip_gap={gap}",
    )
    audit.record(
        f"{label}: solver status accepted",
        status in {"OPTIMAL", "TIME_LIMIT", "SOLUTION_LIMIT", "SUBOPTIMAL"},
        f"status={status}",
    )
    audit.record(
        f"{label}: solver reached OPTIMAL",
        status == "OPTIMAL",
        f"status={status}, mip_gap={gap}",
        severity="warning",
    )


def audit_protection_completeness(audit: Audit, label: str, data: Dict) -> Tuple[int, int, int]:
    affected, protected, unprotected = summary_counts(failure_summary(data))
    audit.record(
        f"{label}: all affected demands protected",
        affected == protected and unprotected == 0,
        f"affected={affected}, protected={protected}, unprotected={unprotected}",
    )
    return affected, protected, unprotected


def audit_static_protection_links(audit: Audit, label: str, data: Dict) -> None:
    scenarios = failure_scenarios(data)
    summary = failure_summary(data)
    reserved = protection_links(data)
    violations = []
    for index, failed in enumerate(scenarios):
        info = summary.get(f"Failure {index}: {failed[0]}->{failed[1]}", {})
        affected = summary_demands(info.get("affected_demands", []))
        for source, _destination, chunk in affected:
            if (source, chunk, failed[0], failed[1]) in reserved:
                violations.append(
                    {
                        "scenario": index,
                        "failed_link": f"{failed[0]}->{failed[1]}",
                        "source": source,
                        "chunk": chunk,
                    }
                )
    audit.record(
        f"{label}: preplanned protection avoids failed links for affected demands",
        not violations,
        f"violations={violations[:5]}",
    )


def audit_deferred_recovery(audit: Audit, label: str, data: Dict) -> None:
    scenarios = failure_scenarios(data)
    failure_epoch = int(data.get("7e-Failure_Time_Epoch", 0)) - 1
    recovery_start = int(data.get("7g-Recovery_Start_Epoch", 0)) - 1
    flows = recovery_flows(data)
    early = []
    failed_link_use = []
    for scenario, source, i, j, chunk, epoch in flows:
        if epoch < recovery_start:
            early.append({"scenario": scenario, "source": source, "chunk": chunk, "epoch": epoch})
        if 0 <= scenario < len(scenarios) and (i, j) == scenarios[scenario] and epoch >= failure_epoch:
            failed_link_use.append(
                {
                    "scenario": scenario,
                    "failed_link": f"{i}->{j}",
                    "source": source,
                    "chunk": chunk,
                    "epoch": epoch,
                }
            )
    audit.record(
        f"{label}: recovery starts after detection delay",
        not early,
        f"failure_epoch={failure_epoch}, recovery_start={recovery_start}, early={early[:5]}",
    )
    audit.record(
        f"{label}: recovery never uses failed link after failure",
        not failed_link_use,
        f"violations={failed_link_use[:5]}",
    )


def audit_main_topology(audit: Audit, topology: str, paths: Dict[str, Path]) -> Dict[str, Dict[str, object]]:
    data = {name: load_json(path) for name, path in paths.items()}
    for name, path in paths.items():
        audit.record(f"{topology}/{name}: schedule exists", path.exists(), str(path))
        audit_solver(audit, f"{topology}/{name}", data[name])

    dedicated = data["dedicated"]
    reference_flows = working_flows(dedicated)
    reference_links = working_demand_links(dedicated)
    demand_universe = unique_demands_from_links(reference_links)
    audit.record(
        f"{topology}: dedicated exports working flows and demand links",
        bool(reference_flows) and bool(reference_links),
        f"flows={len(reference_flows)}, demand_links={len(reference_links)}, demands={len(demand_universe)}",
    )

    scenario_sets = {name: set(failure_scenarios(rows)) for name, rows in data.items()}
    reference_scenarios = scenario_sets["dedicated"]
    for name, scenarios in scenario_sets.items():
        audit.record(
            f"{topology}/{name}: failure scenarios match dedicated",
            scenarios == reference_scenarios and bool(scenarios),
            f"count={len(scenarios)}, missing={len(reference_scenarios - scenarios)}, extra={len(scenarios - reference_scenarios)}",
        )

    models = {name: failure_model(rows) for name, rows in data.items()}
    audit.record(
        f"{topology}: failure model aligned",
        len(set(models.values())) == 1 and "" not in set(models.values()),
        ", ".join(f"{name}={model}" for name, model in models.items()),
    )

    for name in ("shared", "deferred"):
        audit.record(
            f"{topology}/{name}: fixed to dedicated working schedule",
            same_schedule_path(fixed_working_schedule(data[name]), paths["dedicated"]),
            f"fixed={fixed_working_schedule(data[name])}",
        )
        flows = working_flows(data[name])
        links = working_demand_links(data[name])
        audit.record(
            f"{topology}/{name}: working flows match dedicated",
            flows == reference_flows and bool(flows),
            f"count={len(flows)}, missing={len(reference_flows - flows)}, extra={len(flows - reference_flows)}",
        )
        audit.record(
            f"{topology}/{name}: working demand links match dedicated",
            links == reference_links and bool(links),
            f"count={len(links)}, missing={len(reference_links - links)}, extra={len(links - reference_links)}",
        )

    metrics: Dict[str, Dict[str, object]] = {}
    for name, rows in data.items():
        affected, protected, unprotected = audit_protection_completeness(audit, f"{topology}/{name}", rows)
        audit_summary_partition(audit, f"{topology}/{name}", rows, demand_universe)
        audit_demand_link_epochs(audit, f"{topology}/{name}", rows)
        resource_units, normalized_resource = resource_metrics(rows)
        metrics[name] = {
            "affected": affected,
            "protected": protected,
            "unprotected": unprotected,
            "resource_units": resource_units,
            "normalized_link_epoch_resource": normalized_resource,
            "status": solver_status(rows),
            "mip_gap": solver_gap(rows),
        }

    audit_static_protection_links(audit, f"{topology}/dedicated", data["dedicated"])
    audit_static_protection_links(audit, f"{topology}/shared", data["shared"])
    audit_deferred_recovery(audit, f"{topology}/deferred", data["deferred"])

    audit.record(
        f"{topology}: deferred affected set is time-aware subset of static exposure",
        metrics["deferred"]["affected"] <= metrics["dedicated"]["affected"]
        and metrics["deferred"]["affected"] <= metrics["shared"]["affected"],
        (
            f"dedicated={metrics['dedicated']['affected']}, "
            f"shared={metrics['shared']['affected']}, deferred={metrics['deferred']['affected']}"
        ),
    )
    return metrics


def audit_deferred_schedule(
    audit: Audit,
    label: str,
    path: Path,
    reference_path: Path,
    reference_flows: Set[Flow],
    reference_links: Set[DemandLink],
    demand_universe: Set[Demand],
) -> Dict[str, object]:
    data = load_json(path)
    audit_solver(audit, label, data)
    audit.record(
        f"{label}: fixed to dedicated working schedule",
        same_schedule_path(fixed_working_schedule(data), reference_path),
        f"fixed={fixed_working_schedule(data)}",
    )
    flows = working_flows(data)
    links = working_demand_links(data)
    audit.record(
        f"{label}: working flows match dedicated",
        flows == reference_flows and bool(flows),
        f"count={len(flows)}, missing={len(reference_flows - flows)}, extra={len(flows - reference_flows)}",
    )
    audit.record(
        f"{label}: working demand links match dedicated",
        links == reference_links and bool(links),
        f"count={len(links)}, missing={len(reference_links - links)}, extra={len(links - reference_links)}",
    )
    affected, protected, unprotected = audit_protection_completeness(audit, label, data)
    audit_summary_partition(audit, label, data, demand_universe)
    audit_demand_link_epochs(audit, label, data)
    audit_deferred_recovery(audit, label, data)
    resource_units, normalized_resource = resource_metrics(data)
    return {
        "affected": affected,
        "protected": protected,
        "unprotected": unprotected,
        "resource_units": resource_units,
        "normalized_link_epoch_resource": normalized_resource,
        "status": solver_status(data),
        "mip_gap": solver_gap(data),
    }


def sensitivity_paths(topology: str) -> List[Path]:
    roots = [
        EXAMPLES / "results" / "failure_time_sensitivity" / "schedules",
        EXAMPLES / "results" / "detection_delay_sensitivity" / "schedules",
    ]
    paths: List[Path] = []
    for root in roots:
        if root.exists():
            paths.extend(sorted(root.glob(f"{topology}_*.json")))
    return paths


def run_audit(include_sensitivity: bool) -> Dict[str, object]:
    audit = Audit()
    metrics: Dict[str, object] = {"main": {}, "sensitivity": {}}

    for topology, paths in PRESETS.items():
        metrics["main"][topology] = audit_main_topology(audit, topology, paths)

    if include_sensitivity:
        for topology, paths in PRESETS.items():
            reference = load_json(paths["dedicated"])
            reference_flows = working_flows(reference)
            reference_links = working_demand_links(reference)
            demand_universe = unique_demands_from_links(reference_links)
            topology_metrics = {}
            for path in sensitivity_paths(topology):
                label = f"{topology}/sensitivity/{path.stem}"
                topology_metrics[path.name] = audit_deferred_schedule(
                    audit,
                    label,
                    path,
                    paths["dedicated"],
                    reference_flows,
                    reference_links,
                    demand_universe,
                )
            metrics["sensitivity"][topology] = topology_metrics
            audit.record(
                f"{topology}: sensitivity schedules audited",
                bool(topology_metrics),
                f"count={len(topology_metrics)}",
                severity="warning",
            )

    return {
        "passed": not audit.errors,
        "error_count": len(audit.errors),
        "warning_count": len(audit.warnings),
        "checks": audit.checks,
        "metrics": metrics,
    }


def print_human(report: Dict[str, object]) -> None:
    checks = report["checks"]
    failures = [check for check in checks if not check["passed"]]
    print(
        f"Protection correctness audit: {'PASS' if report['passed'] else 'FAIL'} "
        f"({len(checks)} checks, {report['error_count']} errors, {report['warning_count']} warnings)"
    )
    if failures:
        print("\nFailures / warnings:")
        for check in failures:
            marker = "WARN" if check["severity"] == "warning" else "FAIL"
            print(f"- [{marker}] {check['name']}: {check['detail']}")
    print("\nMain metrics:")
    for topology, by_mode in report["metrics"]["main"].items():
        print(f"- {topology}")
        for mode, values in by_mode.items():
            print(
                "  "
                f"{mode}: affected={values['affected']}, protected={values['protected']}, "
                f"unprotected={values['unprotected']}, "
                f"resource_units={values['resource_units']}, "
                f"normalized_link_epoch_resource={values['normalized_link_epoch_resource']}, "
                f"status={values['status']}, mip_gap={values['mip_gap']}"
            )
    if report["metrics"]["sensitivity"]:
        print("\nSensitivity schedules:")
        for topology, rows in report["metrics"]["sensitivity"].items():
            print(f"- {topology}: {len(rows)} schedules")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit protection schedule correctness invariants.")
    parser.add_argument(
        "--skip-sensitivity",
        action="store_true",
        help="Only audit the main dcn4wan/interdc8 dedicated/shared/deferred schedules.",
    )
    parser.add_argument("--json", action="store_true", help="Print the full audit report as JSON.")
    args = parser.parse_args()

    report = run_audit(include_sensitivity=not args.skip_sensitivity)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human(report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
