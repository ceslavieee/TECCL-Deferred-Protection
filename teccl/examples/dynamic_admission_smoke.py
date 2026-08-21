"""Run deterministic Stage-A admission-ledger smoke tests."""

from dataclasses import asdict
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.dynamic_admission import ScheduleTemplate, run_trace  # noqa: E402


RESULTS = ROOT / "teccl/examples/results/dynamic_admission_smoke"
ARRIVALS = (0, 1, 6, 7, 12)
TEMPLATES = {
    ("DCN4WAN", "early_dpp"): ROOT
    / "teccl/examples/results/strict_dedicated_failure_time/schedules/"
    "dcn4wan_joint_failure_epoch_0.json",
    ("DCN4WAN", "ddpp"): ROOT
    / "teccl/examples/results/strict_dpp_ddpp_pair/schedules/"
    "dcn4wan_paired_ddpp.json",
    ("InterDC8", "early_dpp"): ROOT
    / "teccl/examples/results/strict_dedicated_failure_time/schedules/"
    "interdc8_joint_failure_epoch_0.json",
    ("InterDC8", "ddpp"): ROOT
    / "teccl/examples/results/strict_dpp_ddpp_pair/schedules/"
    "interdc8_paired_ddpp.json",
}


def main() -> None:
    reports = []
    for (topology, policy), path in TEMPLATES.items():
        template = ScheduleTemplate.from_path(path, f"{topology}-{policy}")
        ledger, decisions = run_trace(template, ARRIVALS)
        accepted = sum(decision.accepted for decision in decisions)
        blocked = len(decisions) - accepted
        if accepted + blocked != len(ARRIVALS):
            raise AssertionError("Admission accounting identity failed")
        reports.append(
            {
                "topology": topology,
                "policy": policy,
                "source_schedule": str(path.relative_to(ROOT)),
                "trace_kind": "hand-written accounting smoke test",
                "arrival_epochs": list(ARRIVALS),
                "accepted": accepted,
                "blocked": blocked,
                "occupied_cells_after_trace": ledger.occupied_cell_count,
                "decisions": [asdict(decision) for decision in decisions],
            }
        )
        print(f"{topology} {policy}: accepted={accepted}, blocked={blocked}, PASS")

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "report.json").write_text(
        json.dumps(
            {
                "authoritative_performance_result": False,
                "purpose": "Stage-A transactional capacity-ledger validation",
                "reports": reports,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (RESULTS / "README.md").write_text(
        "# Dynamic admission smoke test\n\n"
        "This is a deterministic Stage-A accounting test, not a performance "
        "result. It validates full link-occupancy expansion, atomic "
        "accept/block decisions, and release of future protection capacity. "
        "Performance claims require residual-capacity solver integration and "
        "shared stochastic traces.\n"
    )


if __name__ == "__main__":
    main()
