"""Audit a fixed multicast-tree DPP/DDPP timing pair."""

import argparse
import json
from pathlib import Path
import sys
from typing import Dict, Iterable, Set, Tuple


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.examples.audit_strict_dedicated_schedule import (  # noqa: E402
    audit,
    parse_reservations,
    parse_working,
)


DEFAULT_DPP = ROOT / (
    "teccl/examples/results/multicast_tree_pair/schedules/"
    "dcn4wan_dpp_tree_pair.json"
)
DEFAULT_DDPP = ROOT / (
    "teccl/examples/results/multicast_tree_pair/schedules/"
    "dcn4wan_ddpp_tree_pair_fixed_working.json"
)
DEFAULT_OUTPUT = ROOT / (
    "teccl/examples/results/multicast_tree_pair/dcn4wan_report.json"
)
TreeEdge = Tuple[int, int, int, int]


def tree_edges(flows: Iterable[Tuple[int, int, int, int, int]]) -> Set[TreeEdge]:
    return {(source, i, j, chunk) for source, i, j, chunk, _ in flows}


def metrics(schedule: Dict) -> Dict:
    return {
        "solver_status": schedule.get("Solver_Status_Name"),
        "working_completion_epoch": schedule["9e-Actual_Working_Completion_Epoch"],
        "protected_completion_epoch": schedule["9f-Actual_Protected_Completion_Epoch"],
        "reserved_occupied_link_epochs": schedule["14a-Contingency_Plan_Link_Epochs"],
        "reservation_holding_units": schedule["14c-Future_Reservation_Holding_Units"],
        "reservation_start_epoch": schedule["7w-Reservation_Start_Epoch"],
        "reservation_end_epoch": schedule["7x-Reservation_End_Epoch"],
    }


def compare(dpp: Dict, ddpp: Dict, dpp_path: Path, ddpp_path: Path) -> Dict:
    dpp_audit = audit(dpp, require_nonempty=True, require_tree_pair=True)
    ddpp_audit = audit(ddpp, require_nonempty=True, require_tree_pair=True)
    dpp_working = parse_working(dpp)
    ddpp_working = parse_working(ddpp)
    dpp_backup = tree_edges(parse_reservations(dpp))
    ddpp_backup = tree_edges(parse_reservations(ddpp))
    checks = [
        {
            "name": "DPP independent audit",
            "passed": all(row["passed"] for row in dpp_audit),
        },
        {
            "name": "DDPP independent audit",
            "passed": all(row["passed"] for row in ddpp_audit),
        },
        {
            "name": "working flows identical",
            "passed": dpp_working == ddpp_working,
        },
        {
            "name": "backup tree edges identical",
            "passed": dpp_backup == ddpp_backup and bool(dpp_backup),
        },
        {
            "name": "service deadlines identical",
            "passed": dpp["8-Final_Deadline_Epoch"] == ddpp["8-Final_Deadline_Epoch"],
        },
        {
            "name": "DDPP window follows DPP window",
            "passed": dpp["7x-Reservation_End_Epoch"] + 1
            == ddpp["7w-Reservation_Start_Epoch"],
        },
        {
            "name": "reserved occupied link-epochs identical",
            "passed": dpp["14a-Contingency_Plan_Link_Epochs"]
            == ddpp["14a-Contingency_Plan_Link_Epochs"],
        },
    ]
    return {
        "authoritative_performance_result": False,
        "purpose": "fixed multicast-tree DPP/DDPP timing-pair certificate",
        "sources": {"dpp": str(dpp_path), "ddpp": str(ddpp_path)},
        "pair_audit_passed": all(row["passed"] for row in checks),
        "checks": checks,
        "dpp": metrics(dpp),
        "ddpp": metrics(ddpp),
        "claim_boundary": (
            "This certifies a fair timing pair and feasibility, not blocking "
            "performance or global optimality of the TIME_LIMIT DPP solve."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dpp", type=Path, default=DEFAULT_DPP)
    parser.add_argument("--ddpp", type=Path, default=DEFAULT_DDPP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = compare(
        json.loads(args.dpp.read_text()),
        json.loads(args.ddpp.read_text()),
        args.dpp,
        args.ddpp,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("PASS" if report["pair_audit_passed"] else "FAIL")
    if not report["pair_audit_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
