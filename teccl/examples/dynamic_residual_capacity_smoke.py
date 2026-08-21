"""End-to-end smoke test for residual-capacity dynamic admission."""

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
from typing import Dict


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.dynamic_admission import CapacityTimeLedger, ScheduleTemplate  # noqa: E402
from teccl.examples.audit_strict_dedicated_schedule import audit  # noqa: E402


BASE_INPUT = ROOT / "teccl/examples/sample_inputs/mesh2_strict_dedicated_protection.json"
FIRST_SCHEDULE = ROOT / "teccl/examples/schedules/mesh2_strict_dedicated_protection_schedule.json"
RESULTS = ROOT / "teccl/examples/results/dynamic_residual_capacity_smoke"
HORIZON = 12


def write_json(data: Dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def solve_second_request(arrival: int, occupancy_path: Path, output_path: Path) -> Path:
    payload = json.loads(BASE_INPUT.read_text())
    input_path = RESULTS / f"request_1_arrival_{arrival}_input.json"
    payload["InstanceParams"]["prior_link_epoch_occupancy_file"] = str(
        occupancy_path.relative_to(ROOT)
    )
    payload["InstanceParams"]["schedule_output_file"] = str(
        output_path.relative_to(ROOT)
    )
    write_json(payload, input_path)
    if output_path.exists():
        output_path.unlink()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "teccl",
            "solve",
            "--input_args",
            str(input_path.relative_to(ROOT)),
        ],
        cwd=ROOT,
        check=True,
    )
    return output_path


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    first = ScheduleTemplate.from_path(FIRST_SCHEDULE, "mesh2-request-0")
    ledger = CapacityTimeLedger()
    first_decision = ledger.try_admit("request-0", 0, first)
    if not first_decision.accepted:
        raise AssertionError("First request must be admitted into an empty ledger")

    attempts = []
    second_decision = None
    second_schedule = None
    for arrival in range(1, 7):
        residual = ledger.residual_occupancy_payload(arrival, HORIZON)
        occupancy_path = RESULTS / f"request_1_arrival_{arrival}_occupancy.json"
        output_path = RESULTS / f"request_1_arrival_{arrival}_schedule.json"
        write_json(residual, occupancy_path)
        solve_second_request(arrival, occupancy_path, output_path)
        if not output_path.exists():
            attempts.append(
                {"arrival_epoch": arrival, "solver_produced_schedule": False}
            )
            continue

        schedule_payload = json.loads(output_path.read_text())
        checks = audit(schedule_payload, require_nonempty=True)
        if not all(check["passed"] for check in checks):
            raise AssertionError(f"Strict schedule audit failed at arrival {arrival}")
        candidate = ScheduleTemplate.from_path(
            output_path,
            f"mesh2-request-1-arrival-{arrival}",
        )
        decision = ledger.try_admit("request-1", arrival, candidate)
        attempts.append(
            {
                "arrival_epoch": arrival,
                "solver_produced_schedule": True,
                "independent_audit_passed": True,
                "ledger_accepted": decision.accepted,
                "conflicting_cells": [list(cell) for cell in decision.conflicting_cells],
            }
        )
        if decision.accepted:
            second_decision = decision
            second_schedule = output_path
            break

        raise AssertionError(
            "Residual-capacity solver emitted a schedule that conflicts with the ledger"
        )

    if second_decision is None or second_schedule is None:
        raise AssertionError("No residual-capacity schedule admitted for request-1")
    ledger.assert_safe()

    report = {
        "authoritative_performance_result": False,
        "purpose": "Stage-B residual-capacity solver integration validation",
        "topology": "Mesh2",
        "first_schedule": str(FIRST_SCHEDULE.relative_to(ROOT)),
        "second_schedule": str(second_schedule.relative_to(ROOT)),
        "first_decision": asdict(first_decision),
        "second_decision": asdict(second_decision),
        "attempts": attempts,
        "accepted_request_ids": list(ledger.accepted_request_ids()),
        "ledger_safe": True,
    }
    write_json(report, RESULTS / "report.json")
    (RESULTS / "README.md").write_text(
        "# Residual-capacity admission smoke test\n\n"
        "This non-authoritative Stage-B integration test admits one protected "
        "Mesh2 request, exports its active link-epoch commitments, and solves "
        "a second protected request against the residual capacity. The second "
        "schedule passes the independent scenario-robust audit and the final "
        "two-request ledger is capacity-safe. This validates integration, not "
        "DPP/DDPP performance.\n"
    )
    print(
        "Residual-capacity admission: PASS; request-1 arrival=",
        second_decision.arrival_epoch,
    )


if __name__ == "__main__":
    main()
