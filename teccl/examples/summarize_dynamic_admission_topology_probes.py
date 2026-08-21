"""Aggregate the corrected DCN4WAN and InterDC8 admission probes."""

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.examples.dynamic_admission_pilot import write_json  # noqa: E402


RESULTS = ROOT / "teccl/examples/results/dynamic_admission_topology_pilot"
SOURCES = {
    "DCN4WAN": RESULTS / "dcn4wan_probe_18s_fixed/report.json",
    "InterDC8": RESULTS / "interdc8_probe_18s_fixed/report.json",
}


def main() -> None:
    topologies = []
    for topology, path in SOURCES.items():
        source = json.loads(path.read_text())
        if source["trace"]["topology"] != topology:
            raise AssertionError(f"Topology mismatch in {path}")
        policies = {row["policy"]: row for row in source["policies"]}
        if set(policies) != {"early_dpp", "ddpp"}:
            raise AssertionError(f"Policy mismatch in {path}")
        if len({row["trace_sha256"] for row in policies.values()}) != 1:
            raise AssertionError(f"Trace mismatch in {path}")
        if not all(row["ledger_safe"] for row in policies.values()):
            raise AssertionError(f"Unsafe ledger in {path}")
        if any(row["unknown"] for row in policies.values()):
            raise AssertionError(f"Unknown solver outcome remains in {path}")
        topologies.append(
            {
                "topology": topology,
                "source_report": str(path.relative_to(ROOT)),
                "arrival_rate_per_epoch": source["trace"][
                    "arrival_rate_per_epoch"
                ],
                "arrival_epochs": source["trace"]["arrival_epochs"],
                "trace_sha256": source["trace"]["sha256"],
                "requests_per_policy": source["trace"]["request_count"],
                "fallback_time_limit_hours": 0.005,
                "early_dpp": {
                    "accepted": policies["early_dpp"]["accepted"],
                    "blocked": policies["early_dpp"]["blocked"],
                },
                "ddpp": {
                    "accepted": policies["ddpp"]["accepted"],
                    "blocked": policies["ddpp"]["blocked"],
                },
                "ddpp_minus_early_dpp_accepted": (
                    policies["ddpp"]["accepted"]
                    - policies["early_dpp"]["accepted"]
                ),
            }
        )

    report = {
        "authoritative_performance_result": False,
        "purpose": "corrected main-topology dynamic-admission probes",
        "baseline_label": (
            "early scenario-robust DPP adaptation, not literal path-pair DPP 1:1"
        ),
        "admission_algorithm": (
            "certified-template-first with residual-capacity solver fallback"
        ),
        "topologies": topologies,
        "pipeline_checks": {
            "same_trace_within_each_policy_pair": True,
            "all_outcomes_conclusive": True,
            "all_ledgers_safe": True,
            "accepted_schedules_independently_audited": True,
            "slow_link_epoch_zero_capacity_boundary_fixed": True,
        },
        "claim_boundary": (
            "Each topology has one four-request probe only. The results validate "
            "main-topology execution and locate candidate load regions; they are "
            "not thesis performance estimates."
        ),
    }
    write_json(report, RESULTS / "report.json")
    (RESULTS / "README.md").write_text(
        "# Main-topology dynamic admission probes\n\n"
        "These corrected, non-authoritative probes run one four-request trace "
        "on DCN4WAN and InterDC8 with certified-template-first admission and an "
        "18-second residual-capacity solver fallback. All outcomes are "
        "conclusive and all accepted schedules and ledgers are audited. The "
        "sample is only large enough to validate execution and identify pilot "
        "loads; it is not thesis performance evidence.\n"
    )
    for row in topologies:
        print(
            f"{row['topology']}: early_dpp={row['early_dpp']['accepted']}/"
            f"{row['requests_per_policy']}, ddpp={row['ddpp']['accepted']}/"
            f"{row['requests_per_policy']}"
        )


if __name__ == "__main__":
    main()
