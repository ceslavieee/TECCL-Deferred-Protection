"""Build and audit a fair scenario-robust DPP versus DDPP timing pair."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.examples.compare_preplanned_protection import load, working_flows  # noqa: E402
from teccl.examples.audit_strict_dedicated_schedule import (  # noqa: E402
    audit as audit_serialized_schedule,
)


RESULTS = ROOT / "teccl" / "examples" / "results" / "strict_dpp_ddpp_pair"
STRICT_PLANS = {
    "DCN4WAN": (
        ROOT
        / "teccl/examples/results/strict_dedicated_failure_time/schedules/"
        "dcn4wan_joint_failure_epoch_0.json"
    ),
    "InterDC8": (
        ROOT
        / "teccl/examples/results/strict_dedicated_failure_time/schedules/"
        "interdc8_joint_failure_epoch_0.json"
    ),
}
STRICT_INPUTS = {
    "DCN4WAN": (
        ROOT
        / "teccl/examples/results/strict_dedicated_failure_time/inputs/"
        "dcn4wan_joint_failure_epoch_0.json"
    ),
    "InterDC8": (
        ROOT
        / "teccl/examples/results/strict_dedicated_failure_time/inputs/"
        "interdc8_joint_failure_epoch_0.json"
    ),
}
RESERVATION_RE = re.compile(
    r"Chunk (?P<c>\d+) from (?P<s>\d+) reserves "
    r"(?P<i>\d+)->(?P<j>\d+) in epoch (?P<k>\d+)"
)


def write_json(data: Dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def run_solver(input_path: Path) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "teccl",
            "solve",
            "--input_args",
            str(input_path.relative_to(ROOT)),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    return result.stdout


def build_input(
    topology: str,
    strict_path: Path,
    time_limit_hours: float,
    output_flag: int,
) -> tuple:
    strict = load(strict_path)
    base = load(STRICT_INPUTS[topology])
    slug = topology.lower()
    input_path = RESULTS / "inputs" / f"{slug}_paired_ddpp.json"
    schedule_path = RESULTS / "schedules" / f"{slug}_paired_ddpp.json"
    base["GurobiParams"]["time_limit"] = time_limit_hours
    base["GurobiParams"]["output_flag"] = output_flag
    base["InstanceParams"].update(
        {
            "num_epochs": int(strict["8-Final_Deadline_Epoch"]),
            "fixed_working_schedule": str(strict_path.relative_to(ROOT)),
            "protection_mode": 4,
            "deferred_timing_mode": 1,
            "working_deadline_ratio": 0.5,
            "enable_dynamic_backup_release": False,
            "beta_weighted_holding_objective": True,
            "enable_real_failure_timing": True,
            "enable_failure_scenarios": True,
            "detection_delay_epochs": 1,
            "failure_time_epoch": 0,
            "failure_model": 1,
            "schedule_output_file": str(schedule_path.relative_to(ROOT)),
        }
    )
    write_json(base, input_path)
    return input_path, schedule_path


def audit_pair(topology: str, strict: Dict, ddpp: Dict) -> Dict:
    strict_working = working_flows(strict)
    ddpp_working = working_flows(ddpp)
    backup = []
    for row in ddpp.get("11b-Reserved_Backup_Slots", []):
        match = RESERVATION_RE.fullmatch(str(row))
        if not match:
            raise ValueError(f"Could not parse reservation row: {row}")
        backup.append(
            (
                int(match.group("s")),
                int(match.group("i")),
                int(match.group("j")),
                int(match.group("c")),
                int(match.group("k")),
            )
        )
    activation = int(ddpp["7a-Deferred_Activation_Epoch"]) - 1
    service_deadline = int(ddpp["8-Final_Deadline_Epoch"])
    strict_window = int(strict["7p-DPP_Reservation_Deadline_Epoch"])
    working_completion = int(strict["9e-Actual_Working_Completion_Epoch"])
    detection_delay = int(ddpp["7f-Detection_Delay_Epochs"])
    latest_relevant_failure = max(0, working_completion - 1)
    serialized_checks = audit_serialized_schedule(ddpp, require_nonempty=True)

    checks = [
        {
            "name": "working schedules match exactly",
            "passed": strict_working == ddpp_working and bool(strict_working),
            "detail": f"strict={len(strict_working)}, ddpp={len(ddpp_working)}",
        },
        {
            "name": "service deadlines match",
            "passed": (
                int(strict["8-Final_Deadline_Epoch"]) == service_deadline
            ),
            "detail": (
                f"strict={strict['8-Final_Deadline_Epoch']}, "
                f"ddpp={service_deadline}"
            ),
        },
        {
            "name": "DDPP starts immediately after DPP window",
            "passed": activation == strict_window,
            "detail": f"activation={activation}, strict_window={strict_window}",
        },
        {
            "name": "all backup starts are in second half",
            "passed": bool(backup) and all(flow[4] >= activation for flow in backup),
            "detail": (
                f"activation={activation}, min_backup_epoch="
                f"{min((flow[4] for flow in backup), default=-1)}"
            ),
        },
        {
            "name": "backup completes by common service deadline",
            "passed": (
                int(ddpp["9f-Actual_Protected_Completion_Epoch"])
                <= service_deadline
            ),
            "detail": (
                f"completion={ddpp['9f-Actual_Protected_Completion_Epoch']}, "
                f"deadline={service_deadline}"
            ),
        },
        {
            "name": "detection precedes deferred activation",
            "passed": (
                latest_relevant_failure + detection_delay <= activation
            ),
            "detail": (
                f"latest_failure={latest_relevant_failure}, "
                f"delay={detection_delay}, activation={activation}"
            ),
        },
        {
            "name": "each scenario recovery is link-disjoint from working",
            "passed": ddpp.get(
                "7v-Recovery_Link_Disjoint_From_Working_Per_Scenario"
            )
            is True,
            "detail": "enforced as a MILP constraint for every failed link",
        },
        {
            "name": "failure-free completion releases all reservations",
            "passed": (
                int(ddpp["14e-Failure_Free_Committed_Backup_Link_Epochs"])
                == 0
            ),
            "detail": (
                "committed="
                f"{ddpp['14e-Failure_Free_Committed_Backup_Link_Epochs']}"
            ),
        },
        {
            "name": "backup is not normal-operation traffic",
            "passed": ddpp.get("7n-Normal_Operation_Backup_Data_Transmissions")
            == 0,
        },
        {
            "name": "independent serialized schedule audit",
            "passed": all(check["passed"] for check in serialized_checks),
            "detail": (
                f"{sum(check['passed'] for check in serialized_checks)}/"
                f"{len(serialized_checks)} invariants"
            ),
        },
    ]
    failures = [check for check in checks if not check["passed"]]
    return {
        "topology": topology,
        "checks": checks,
        "audit_passed": not failures,
        "strict_dpp": {
            "working_completion_epoch": strict["9e-Actual_Working_Completion_Epoch"],
            "protected_completion_epoch": strict["9f-Actual_Protected_Completion_Epoch"],
            "reserved_occupied_link_epochs": strict[
                "14a-Contingency_Plan_Link_Epochs"
            ],
            "reservation_holding_units": strict[
                "14c-Future_Reservation_Holding_Units"
            ],
            "solver_status": strict.get("Solver_Status_Name"),
        },
        "preplanned_ddpp": {
            "working_completion_epoch": ddpp["9e-Actual_Working_Completion_Epoch"],
            "protected_completion_epoch": ddpp["9f-Actual_Protected_Completion_Epoch"],
            "reserved_occupied_link_epochs": ddpp[
                "14a-Contingency_Plan_Link_Epochs"
            ],
            "reservation_holding_units": ddpp[
                "14c-Future_Reservation_Holding_Units"
            ],
            "solver_status": ddpp.get("Solver_Status_Name"),
        },
    }


def write_markdown(reports: List[Dict], path: Path) -> None:
    lines = [
        "# Scenario-robust DPP and DDPP paired audit",
        "",
        "| topology | strategy | reserved link-epochs | holding units | "
        "working completion | protected completion | status |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for report in reports:
        for key, label in (
            ("strict_dpp", "Scenario-robust DPP"),
            ("preplanned_ddpp", "Scenario-robust DDPP"),
        ):
            row = report[key]
            lines.append(
                f"| {report['topology']} | {label} | "
                f"{row['reserved_occupied_link_epochs']} | "
                f"{row['reservation_holding_units']} | "
                f"{row['working_completion_epoch']} | "
                f"{row['protected_completion_epoch']} | "
                f"{row['solver_status']} |"
            )
    lines.extend(["", "## Pair checks", ""])
    for report in reports:
        lines.append(f"### {report['topology']}")
        lines.append("")
        for check in report["checks"]:
            marker = "PASS" if check["passed"] else "FAIL"
            detail = f" - {check['detail']}" if check.get("detail") else ""
            lines.append(f"- `{marker}` {check['name']}{detail}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--topology",
        choices=["all", *sorted(STRICT_PLANS)],
        default="all",
    )
    parser.add_argument("--time-limit-hours", type=float, default=0.01)
    parser.add_argument("--output-flag", type=int, choices=(0, 1), default=0)
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()
    topologies = sorted(STRICT_PLANS) if args.topology == "all" else [args.topology]
    reports = []
    for topology in topologies:
        strict_path = STRICT_PLANS[topology]
        input_path, schedule_path = build_input(
            topology,
            strict_path,
            args.time_limit_hours,
            args.output_flag,
        )
        if not args.reuse or not schedule_path.exists():
            if schedule_path.exists():
                schedule_path.unlink()
            output = run_solver(input_path)
            if not schedule_path.exists():
                print(output)
                raise SystemExit(f"No DDPP schedule produced for {topology}")
        report = audit_pair(topology, load(strict_path), load(schedule_path))
        reports.append(report)
        print(
            f"{topology}: status={report['preplanned_ddpp']['solver_status']} "
            f"audit={'PASS' if report['audit_passed'] else 'FAIL'}"
        )
    write_json({"reports": reports}, RESULTS / "paired_audit.json")
    write_markdown(reports, RESULTS / "README.md")
    if any(not report["audit_passed"] for report in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
