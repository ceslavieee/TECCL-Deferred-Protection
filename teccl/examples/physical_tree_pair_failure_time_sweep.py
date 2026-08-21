"""Certify fixed 25 GB DPP/DDPP tree-pair plans across failure epochs."""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RESULTS_ROOT = (
    ROOT
    / "teccl/examples/results/multicast_tree_pair_physical_25gb"
)
FULL_RESULTS = RESULTS_ROOT / "full_directed_failures"
RESULTS = RESULTS_ROOT / "failure_time_sweep"

DATASETS = {
    "DCN4WAN": {
        "slug": "dcn4wan",
        "dpp_input": FULL_RESULTS / "inputs/dcn4wan_dpp.json",
        "dpp_plan": FULL_RESULTS / "schedules/dcn4wan_dpp.json",
        "ddpp_input": FULL_RESULTS / "inputs/dcn4wan_ddpp.json",
        "ddpp_plan": FULL_RESULTS / "schedules/dcn4wan_ddpp.json",
        "expected_scenarios": 32,
    },
    "InterDC8": {
        "slug": "interdc8",
        "dpp_input": FULL_RESULTS / "inputs/interdc8_dpp.json",
        "dpp_plan": FULL_RESULTS / "schedules/interdc8_dpp.json",
        "ddpp_input": FULL_RESULTS / "inputs/interdc8_ddpp.json",
        "ddpp_plan": FULL_RESULTS / "schedules/interdc8_ddpp.json",
        "expected_scenarios": 26,
    },
}


def load(path: Path) -> Dict:
    return json.loads(path.read_text())


def write_json(data: Dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def write_csv(rows: Sequence[Dict[str, object]], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def affected_counts(schedule: Dict) -> Dict[str, int]:
    summaries = schedule.get("9b-Failure_Scenario_Summary", {})
    return {
        "affected_scenarios": sum(
            bool(row.get("affected_demands")) for row in summaries.values()
        ),
        "affected_demands": sum(
            len(row.get("affected_demands", [])) for row in summaries.values()
        ),
        "protected_demands": sum(
            len(row.get("protected_demands", [])) for row in summaries.values()
        ),
        "unprotected_demands": sum(
            len(row.get("unprotected_demands", [])) for row in summaries.values()
        ),
    }


def run_solver(input_path: Path) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "teccl",
            "solve",
            "--input_args",
            relative(input_path),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return result.stdout


def trial(
    topology: str,
    policy: str,
    failure_epoch: int,
    time_limit_hours: float,
    reuse: bool,
) -> Dict[str, object]:
    from teccl.examples.audit_strict_dedicated_schedule import audit

    config = DATASETS[topology]
    input_template = Path(config[f"{policy}_input"])
    fixed_plan = Path(config[f"{policy}_plan"])
    stem = f"{config['slug']}_{policy}_tau_{failure_epoch}"
    input_path = RESULTS / "inputs" / f"{stem}.json"
    schedule_path = RESULTS / "schedules" / f"{stem}.json"
    status_path = RESULTS / "status" / f"{stem}.json"

    data = load(input_template)
    data["GurobiParams"].update(
        {
            "time_limit": time_limit_hours,
            "output_flag": 0,
        }
    )
    data["InstanceParams"].update(
        {
            "failure_time_epoch": failure_epoch,
            "detection_delay_epochs": 1,
            "failure_model": 2,
            "max_failure_scenarios": -1,
            "fixed_working_schedule": relative(fixed_plan),
            "fixed_reservation_schedule": relative(fixed_plan),
            "minimum_reservation_schedule": "",
            "fixed_backup_tree_schedule": "",
            "schedule_output_file": relative(schedule_path),
            "solver_result_output_file": relative(status_path),
        }
    )
    write_json(data, input_path)

    solver_output = "reused existing schedule"
    if not reuse or not schedule_path.exists():
        solver_output = run_solver(input_path)

    if not schedule_path.exists():
        status = load(status_path) if status_path.exists() else {}
        quality = status.get("solver_quality", {})
        return {
            "topology": topology,
            "policy": policy.upper(),
            "failure_epoch_zero_based": failure_epoch,
            "classification": "NO_SCHEDULE",
            "solver_status": quality.get("status_name", "UNKNOWN"),
            "solver_runtime_seconds": quality.get("runtime"),
            "solver_mip_gap": quality.get("mip_gap"),
            "failure_scenarios": 0,
            "affected_scenarios": 0,
            "affected_demands": 0,
            "protected_demands": 0,
            "unprotected_demands": 0,
            "reserved_slots": 0,
            "recovery_flows": 0,
            "audit_passed": False,
            "audit_failures": solver_output.strip(),
            "schedule_file": "",
        }

    schedule = load(schedule_path)
    counts = affected_counts(schedule)
    require_nonempty = counts["affected_scenarios"] > 0
    checks = audit(
        schedule,
        require_nonempty=require_nonempty,
        require_tree_pair=True,
    )
    failed = [str(check["name"]) for check in checks if not check["passed"]]
    scenarios = len(schedule.get("9a-Failure_Scenarios", []))
    if scenarios != int(config["expected_scenarios"]):
        failed.append(
            f"scenario count {scenarios} != {config['expected_scenarios']}"
        )
    if counts["unprotected_demands"]:
        failed.append(
            f"unprotected demands={counts['unprotected_demands']}"
        )
    fixed_schedule = load(fixed_plan)
    if set(schedule.get("10-Working_Flows", [])) != set(
        fixed_schedule.get("10-Working_Flows", [])
    ):
        failed.append("working schedule differs from the fixed plan")
    if set(schedule.get("11b-Reserved_Backup_Slots", [])) != set(
        fixed_schedule.get("11b-Reserved_Backup_Slots", [])
    ):
        failed.append("reservation schedule differs from the fixed plan")
    return {
        "topology": topology,
        "policy": policy.upper(),
        "failure_epoch_zero_based": failure_epoch,
        "classification": "FEASIBLE" if not failed else "INVALID",
        "solver_status": schedule.get("Solver_Status_Name", "UNKNOWN"),
        "solver_runtime_seconds": schedule.get("Solver_Runtime"),
        "solver_mip_gap": schedule.get("Solver_MIP_Gap"),
        "failure_scenarios": scenarios,
        **counts,
        "reserved_slots": schedule.get("11c-Reserved_Backup_Slot_Count", 0),
        "recovery_flows": schedule.get("11d-Executed_Recovery_Flow_Count", 0),
        "audit_passed": not failed,
        "audit_failures": ", ".join(failed),
        "schedule_file": relative(schedule_path),
    }


def write_markdown(rows: Sequence[Dict[str, object]], path: Path) -> None:
    lines = [
        "# Physical tree-pair failure-time sweep",
        "",
        "| Topology | Policy | Failure epoch | Status | Affected scenarios | "
        "Affected demands | Recovery flows | Audit |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['topology']} | {row['policy']} | "
            f"{row['failure_epoch_zero_based']} | {row['solver_status']} | "
            f"{row['affected_scenarios']} | {row['affected_demands']} | "
            f"{row['recovery_flows']} | "
            f"{'PASS' if row['audit_passed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "Each policy fixes one previously certified working schedule and its "
            "complete reservation schedule across every row. The final row for "
            "each topology/policy is a post-working-completion control.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def run_cases(
    topologies: Iterable[str],
    policies: Iterable[str],
    time_limit_hours: float,
    reuse: bool,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for topology in topologies:
        config = DATASETS[topology]
        for policy in policies:
            plan = load(Path(config[f"{policy}_plan"]))
            working_completion = int(plan["9e-Actual_Working_Completion_Epoch"])
            for failure_epoch in range(working_completion + 1):
                row = trial(
                    topology,
                    policy,
                    failure_epoch,
                    time_limit_hours,
                    reuse,
                )
                rows.append(row)
                print(
                    f"{topology} {policy.upper()} tau={failure_epoch}: "
                    f"{row['classification']} affected="
                    f"{row['affected_scenarios']} audit="
                    f"{'PASS' if row['audit_passed'] else 'FAIL'}",
                    flush=True,
                )
                write_json({"rows": rows}, RESULTS / "summary.json")
                write_csv(rows, RESULTS / "summary.csv")
                write_markdown(rows, RESULTS / "README.md")
    return rows


def write_certificate(
    rows: Sequence[Dict[str, object]],
    topologies: Iterable[str],
    policies: Iterable[str],
) -> Dict[str, object]:
    topology_names = list(topologies)
    policy_names = list(policies)
    expected_rows = 0
    windows = []
    for topology in topology_names:
        config = DATASETS[topology]
        for policy in policy_names:
            plan = load(Path(config[f"{policy}_plan"]))
            completion = int(plan["9e-Actual_Working_Completion_Epoch"])
            expected_epochs = list(range(completion + 1))
            actual_epochs = [
                int(row["failure_epoch_zero_based"])
                for row in rows
                if row["topology"] == topology
                and row["policy"] == policy.upper()
            ]
            expected_rows += len(expected_epochs)
            windows.append(
                {
                    "topology": topology,
                    "policy": policy.upper(),
                    "working_completion_epoch": completion,
                    "expected_failure_epochs": expected_epochs,
                    "evaluated_failure_epochs": actual_epochs,
                    "full_window_evaluated": actual_epochs == expected_epochs,
                }
            )
    certified = (
        len(rows) == expected_rows
        and all(row["audit_passed"] for row in rows)
        and all(row["classification"] == "FEASIBLE" for row in rows)
        and all(window["full_window_evaluated"] for window in windows)
    )
    certificate = {
        "certificate": "fixed-plan exact directed-link failure-time coverage",
        "certified": certified,
        "chunk_size_gb": 25.0,
        "chunks_per_source": 1,
        "epoch_duration_seconds": 2.0,
        "detection_delay_epochs": 1,
        "failure_model": "EXACT",
        "single_directed_link_failure": True,
        "fixed_working_and_reservation_plan_verified": True,
        "evaluated_rows": len(rows),
        "expected_rows": expected_rows,
        "all_solver_statuses_optimal": all(
            row["solver_status"] == "OPTIMAL" for row in rows
        ),
        "windows": windows,
        "scope_boundary": (
            "Covers every failure epoch before working completion plus one "
            "post-completion control, with one-epoch detection delay. It does "
            "not certify other detection delays, bidirectional-fiber failures, "
            "node failures, or multiple simultaneous failures."
        ),
    }
    write_json(certificate, RESULTS / "certificate.json")
    return certificate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--topology",
        choices=["all", *DATASETS],
        default="all",
    )
    parser.add_argument(
        "--policy",
        choices=["all", "dpp", "ddpp"],
        default="all",
    )
    parser.add_argument("--time-limit-hours", type=float, default=0.005)
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()

    topologies = DATASETS if args.topology == "all" else [args.topology]
    policies = ("dpp", "ddpp") if args.policy == "all" else [args.policy]
    rows = run_cases(
        topologies,
        policies,
        args.time_limit_hours,
        args.reuse,
    )
    certificate = write_certificate(rows, topologies, policies)
    failed = [row for row in rows if not row["audit_passed"]]
    if failed or not certificate["certified"]:
        raise SystemExit(f"{len(failed)} failure-time rows did not certify")


if __name__ == "__main__":
    main()
