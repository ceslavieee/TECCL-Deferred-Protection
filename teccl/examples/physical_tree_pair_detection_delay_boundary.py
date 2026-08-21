"""Certify detection-delay boundaries of the fixed physical tree-pair plans.

For every affected failure epoch, the script derives a candidate boundary from
the first reserved edge on the unique backup-tree path of each affected demand.
It then asks the exact full-scenario MILP to verify all failure epochs at that
boundary and to prove one analytical witness infeasible at boundary + 1.
"""

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.examples.audit_strict_dedicated_schedule import (  # noqa: E402
    audit,
    parse_reservations,
)
from teccl.examples.physical_tree_pair_failure_time_sweep import (  # noqa: E402
    DATASETS,
    RESULTS as FAILURE_TIME_RESULTS,
    affected_counts,
    load,
    relative,
    write_json,
)


RESULTS = (
    ROOT
    / "teccl/examples/results/multicast_tree_pair_physical_25gb"
    / "detection_delay_boundary"
)
DEMAND_RE = re.compile(
    r"Demand \((?P<source>\d+)->(?P<destination>\d+), chunk (?P<chunk>\d+)\)"
)
SCENARIO_RE = re.compile(
    r"Failure (?P<scenario>\d+): (?P<i>\d+)->(?P<j>\d+)"
)


def write_csv(rows: Sequence[Dict[str, object]], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_demand(label: str) -> Tuple[int, int, int]:
    match = DEMAND_RE.fullmatch(label)
    if not match:
        raise ValueError(f"Could not parse affected demand: {label}")
    return (
        int(match.group("source")),
        int(match.group("destination")),
        int(match.group("chunk")),
    )


def parse_scenario(label: str) -> Tuple[int, int, int]:
    match = SCENARIO_RE.fullmatch(label)
    if not match:
        raise ValueError(f"Could not parse failure scenario: {label}")
    return (
        int(match.group("scenario")),
        int(match.group("i")),
        int(match.group("j")),
    )


def first_backup_edge(
    reservations: Sequence[Tuple[int, int, int, int, int]],
    source: int,
    destination: int,
    chunk: int,
) -> Tuple[int, int, int]:
    """Return ``(i, j, epoch)`` for the first edge on the unique tree path."""

    parents: Dict[int, Tuple[int, int]] = {}
    for reserved_source, i, j, reserved_chunk, epoch in reservations:
        if (reserved_source, reserved_chunk) != (source, chunk):
            continue
        if j in parents and parents[j] != (i, epoch):
            raise ValueError(
                f"Backup commodity {(source, chunk)} has multiple parents for {j}"
            )
        parents[j] = (i, epoch)

    reverse_path = []
    current = destination
    visited = set()
    while current != source:
        if current in visited:
            raise ValueError(
                f"Cycle on backup path {(source, chunk)} to {destination}"
            )
        visited.add(current)
        if current not in parents:
            raise ValueError(
                f"No backup path {(source, chunk)} to destination {destination}"
            )
        parent, epoch = parents[current]
        reverse_path.append((parent, current, epoch))
        current = parent
    if not reverse_path:
        raise ValueError("AllGather demand destination unexpectedly equals source")
    return reverse_path[-1]


def derive_boundary(topology: str, policy: str) -> Dict[str, object]:
    """Derive the tight notification slack from certified failure-time rows."""

    config = DATASETS[topology]
    plan_path = Path(config[f"{policy}_plan"])
    plan = load(plan_path)
    reservations = sorted(parse_reservations(plan))
    completion = int(plan["9e-Actual_Working_Completion_Epoch"])
    witnesses = []

    for failure_epoch in range(completion):
        sweep_path = (
            FAILURE_TIME_RESULTS
            / "schedules"
            / f"{config['slug']}_{policy}_tau_{failure_epoch}.json"
        )
        sweep = load(sweep_path)
        for scenario_label, summary in sweep.get(
            "9b-Failure_Scenario_Summary", {}
        ).items():
            affected = summary.get("affected_demands", [])
            if not affected:
                continue
            scenario, failed_i, failed_j = parse_scenario(scenario_label)
            for demand_label in affected:
                source, destination, chunk = parse_demand(str(demand_label))
                root_i, root_j, root_epoch = first_backup_edge(
                    reservations,
                    source,
                    destination,
                    chunk,
                )
                witnesses.append(
                    {
                        "failure_epoch_zero_based": failure_epoch,
                        "scenario_index": scenario,
                        "failed_link": f"{failed_i}->{failed_j}",
                        "affected_demand": str(demand_label),
                        "backup_root_edge": f"{root_i}->{root_j}",
                        "backup_root_reservation_epoch_zero_based": root_epoch,
                        "notification_slack_epochs": root_epoch - failure_epoch,
                        "source_failure_time_schedule": relative(sweep_path),
                    }
                )

    if not witnesses:
        raise ValueError(f"No affected demands found for {topology} {policy}")
    boundary = min(int(row["notification_slack_epochs"]) for row in witnesses)
    tight = [
        row
        for row in witnesses
        if int(row["notification_slack_epochs"]) == boundary
    ]
    return {
        "candidate_boundary_epochs": boundary,
        "affected_failure_epochs": list(range(completion)),
        "analytical_witness": tight[0],
        "all_tight_witnesses": tight,
        "derivation": (
            "For an affected strict-tree demand, recovery must originate at "
            "the source and follow reserved backup-tree capacity. Therefore "
            "notification cannot occur after the first reserved root edge on "
            "its unique source-to-destination backup path."
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
    delay: int,
    failure_epoch: int,
    role: str,
    time_limit_hours: float,
    reuse: bool,
) -> Dict[str, object]:
    config = DATASETS[topology]
    input_template = Path(config[f"{policy}_input"])
    fixed_plan = Path(config[f"{policy}_plan"])
    stem = (
        f"{config['slug']}_{policy}_delay_{delay}_tau_{failure_epoch}_{role}"
    )
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
            "detection_delay_epochs": delay,
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

    solver_output = "reused existing solver result"
    if not reuse or not status_path.exists():
        solver_output = run_solver(input_path)

    status = load(status_path) if status_path.exists() else {}
    quality = status.get("solver_quality", {})
    produced_schedule = bool(status.get("produced_schedule"))
    solver_status = str(quality.get("status_name", "UNKNOWN"))
    base_row: Dict[str, object] = {
        "topology": topology,
        "policy": policy.upper(),
        "role": role,
        "detection_delay_epochs": delay,
        "detection_delay_seconds": delay * 2.0,
        "failure_epoch_zero_based": failure_epoch,
        "solver_status": solver_status,
        "solver_runtime_seconds": quality.get("runtime"),
        "solver_mip_gap": quality.get("mip_gap"),
        "failure_model": "EXACT",
        "input_file": relative(input_path),
        "status_file": relative(status_path) if status_path.exists() else "",
    }
    if not produced_schedule or not schedule_path.exists():
        classification = (
            "INFEASIBLE"
            if solver_status in {"INFEASIBLE", "INF_OR_UNBD"}
            else "UNKNOWN"
        )
        return {
            **base_row,
            "classification": classification,
            "failure_scenarios": 0,
            "affected_scenarios": 0,
            "affected_demands": 0,
            "recovery_flows": 0,
            "audit_passed": False,
            "fixed_plan_verified": False,
            "audit_failures": (
                "" if classification == "INFEASIBLE" else solver_output.strip()
            ),
            "schedule_file": "",
        }

    schedule = load(schedule_path)
    counts = affected_counts(schedule)
    checks = audit(schedule, require_nonempty=True, require_tree_pair=True)
    failed = [str(check["name"]) for check in checks if not check["passed"]]
    scenarios = len(schedule.get("9a-Failure_Scenarios", []))
    if scenarios != int(config["expected_scenarios"]):
        failed.append(
            f"scenario count {scenarios} != {config['expected_scenarios']}"
        )
    if counts["unprotected_demands"]:
        failed.append(f"unprotected demands={counts['unprotected_demands']}")
    fixed_schedule = load(fixed_plan)
    working_same = set(schedule.get("10-Working_Flows", [])) == set(
        fixed_schedule.get("10-Working_Flows", [])
    )
    reservation_same = set(
        schedule.get("11b-Reserved_Backup_Slots", [])
    ) == set(fixed_schedule.get("11b-Reserved_Backup_Slots", []))
    if not working_same:
        failed.append("working schedule differs from fixed plan")
    if not reservation_same:
        failed.append("reservation schedule differs from fixed plan")
    if int(schedule.get("7f-Detection_Delay_Epochs", -1)) != delay:
        failed.append("exported detection delay differs from requested delay")
    return {
        **base_row,
        "classification": "FEASIBLE" if not failed else "INVALID",
        "failure_scenarios": scenarios,
        **counts,
        "recovery_flows": schedule.get("11d-Executed_Recovery_Flow_Count", 0),
        "audit_passed": not failed,
        "fixed_plan_verified": working_same and reservation_same,
        "audit_failures": ", ".join(failed),
        "schedule_file": relative(schedule_path),
    }


def write_markdown(boundaries: Sequence[Dict[str, object]], path: Path) -> None:
    lines = [
        "# Physical tree-pair detection-delay boundary",
        "",
        "| Topology | Policy | Maximum delay | Seconds | Next delay | "
        "Infeasible witness τ | Certified |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in boundaries:
        lines.append(
            f"| {row['topology']} | {row['policy']} | "
            f"{row['maximum_detection_delay_epochs']} | "
            f"{row['maximum_detection_delay_seconds']} | "
            f"{row['first_infeasible_delay_epochs']} | "
            f"{row['infeasible_witness_failure_epoch_zero_based']} | "
            f"{'PASS' if row['boundary_certified'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "Each row fixes the exported working schedule and the complete "
            "reservation schedule. The boundary is feasible for every affected "
            "failure epoch under all directed-link scenarios; boundary + 1 is "
            "proved infeasible at an analytically identified witness.",
            "",
            "Epochs and reservation labels are zero-based in this report. One "
            "epoch is 2 seconds.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def certify_case(
    topology: str,
    policy: str,
    time_limit_hours: float,
    reuse: bool,
) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    derived = derive_boundary(topology, policy)
    boundary = int(derived["candidate_boundary_epochs"])
    affected_epochs = list(derived["affected_failure_epochs"])
    witness = dict(derived["analytical_witness"])
    rows = []
    for failure_epoch in affected_epochs:
        row = trial(
            topology,
            policy,
            boundary,
            int(failure_epoch),
            "boundary",
            time_limit_hours,
            reuse,
        )
        rows.append(row)
        print(
            f"{topology} {policy.upper()} delay={boundary} "
            f"τ={failure_epoch}: {row['classification']} "
            f"status={row['solver_status']}",
            flush=True,
        )

    witness_epoch = int(witness["failure_epoch_zero_based"])
    next_row = trial(
        topology,
        policy,
        boundary + 1,
        witness_epoch,
        "next_delay_witness",
        time_limit_hours,
        reuse,
    )
    rows.append(next_row)
    print(
        f"{topology} {policy.upper()} delay={boundary + 1} "
        f"τ={witness_epoch}: {next_row['classification']} "
        f"status={next_row['solver_status']}",
        flush=True,
    )

    boundary_rows = rows[:-1]
    certified = (
        len(boundary_rows) == len(affected_epochs)
        and all(row["classification"] == "FEASIBLE" for row in boundary_rows)
        and all(row["solver_status"] == "OPTIMAL" for row in boundary_rows)
        and all(bool(row["audit_passed"]) for row in boundary_rows)
        and all(bool(row["fixed_plan_verified"]) for row in boundary_rows)
        and next_row["classification"] == "INFEASIBLE"
        and next_row["solver_status"] in {"INFEASIBLE", "INF_OR_UNBD"}
    )
    result = {
        "topology": topology,
        "policy": policy.upper(),
        "fixed_plan": relative(Path(DATASETS[topology][f"{policy}_plan"])),
        "maximum_detection_delay_epochs": boundary,
        "maximum_detection_delay_seconds": boundary * 2.0,
        "first_infeasible_delay_epochs": boundary + 1,
        "first_infeasible_delay_seconds": (boundary + 1) * 2.0,
        "affected_failure_epochs": affected_epochs,
        "boundary_all_epochs_feasible": all(
            row["classification"] == "FEASIBLE" for row in boundary_rows
        ),
        "boundary_all_solver_statuses_optimal": all(
            row["solver_status"] == "OPTIMAL" for row in boundary_rows
        ),
        "boundary_all_audits_passed": all(
            bool(row["audit_passed"]) for row in boundary_rows
        ),
        "fixed_working_and_reservation_plan_verified": all(
            bool(row["fixed_plan_verified"]) for row in boundary_rows
        ),
        "infeasible_witness_failure_epoch_zero_based": witness_epoch,
        "infeasible_witness_solver_status": next_row["solver_status"],
        "analytical_witness": witness,
        "boundary_derivation": derived["derivation"],
        "boundary_certified": certified,
    }
    return result, rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--topology", choices=["all", *DATASETS], default="all"
    )
    parser.add_argument(
        "--policy", choices=["all", "dpp", "ddpp"], default="all"
    )
    parser.add_argument("--time-limit-hours", type=float, default=0.005)
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()

    topologies: Iterable[str] = (
        DATASETS if args.topology == "all" else [args.topology]
    )
    policies: Iterable[str] = (
        ("dpp", "ddpp") if args.policy == "all" else [args.policy]
    )
    boundaries = []
    trials = []
    for topology in topologies:
        for policy in policies:
            boundary, rows = certify_case(
                topology,
                policy,
                args.time_limit_hours,
                args.reuse,
            )
            boundaries.append(boundary)
            trials.extend(rows)
            write_json(
                {"boundaries": boundaries, "trials": trials},
                RESULTS / "summary.json",
            )
            write_csv(trials, RESULTS / "trials.csv")
            write_markdown(boundaries, RESULTS / "README.md")

    certificate = {
        "certificate": "fixed-plan exact detection-delay boundary",
        "certified": all(row["boundary_certified"] for row in boundaries),
        "chunk_size_gb": 25.0,
        "chunks_per_source": 1,
        "epoch_duration_seconds": 2.0,
        "failure_model": "EXACT",
        "single_directed_link_failure": True,
        "full_scenario_sets": {
            topology: int(DATASETS[topology]["expected_scenarios"])
            for topology in topologies
        },
        "fixed_working_and_reservation_plans": True,
        "proof_method": (
            "All affected failure epochs are exact-MILP feasible at the "
            "analytically derived boundary; the adjacent larger delay is "
            "exact-MILP infeasible at a necessary-root-reservation witness. "
            "Feasibility is monotone because increasing detection delay only "
            "removes recovery slots from the same fixed plan."
        ),
        "boundaries": boundaries,
        "evaluated_subproblems": len(trials),
        "scope_boundary": (
            "Certifies only the exported 25 GB, one-chunk fixed plans under one "
            "directed-link failure. It does not cover alternative optimized "
            "plans, bidirectional-fiber failures, node failures, or multiple "
            "simultaneous failures."
        ),
    }
    write_json(certificate, RESULTS / "certificate.json")
    if not certificate["certified"]:
        raise SystemExit("At least one detection-delay boundary did not certify")


if __name__ == "__main__":
    main()
