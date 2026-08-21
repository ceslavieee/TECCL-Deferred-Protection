"""Aggregate the non-authoritative Mesh2 dynamic-admission load pilot."""

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.examples.dynamic_admission_pilot import write_json  # noqa: E402


RESULTS = ROOT / "teccl/examples/results/dynamic_admission_load_sweep"
SOURCES = {
    "0.3": RESULTS / "rate_0p3/report.json",
    "0.8": ROOT / "teccl/examples/results/dynamic_admission_pilot_sweep/report.json",
    "1.5": RESULTS / "rate_1p5/report.json",
}


def main() -> None:
    loads = []
    for rate_text, path in SOURCES.items():
        source = json.loads(path.read_text())
        rate = float(rate_text)
        if source["configuration"]["arrival_rate_per_epoch"] != rate:
            raise AssertionError(f"Arrival-rate mismatch in {path}")
        aggregate = {row["policy"]: row for row in source["aggregate"]}
        if set(aggregate) != {"early_dpp", "ddpp"}:
            raise AssertionError(f"Policy mismatch in {path}")
        if any(row["unknown"] for row in aggregate.values()):
            raise AssertionError(f"Pilot contains unknown solver outcomes in {path}")
        if not all(row["all_ledgers_safe"] for row in aggregate.values()):
            raise AssertionError(f"Pilot contains an unsafe ledger in {path}")
        loads.append(
            {
                "arrival_rate_per_epoch": rate,
                "source_report": str(path.relative_to(ROOT)),
                "requests_per_policy": aggregate["early_dpp"]["requests"],
                "early_dpp": {
                    "accepted": aggregate["early_dpp"]["accepted"],
                    "blocked": aggregate["early_dpp"]["blocked"],
                    "blocking_probability": aggregate["early_dpp"][
                        "pooled_blocking_probability_conclusive"
                    ],
                },
                "ddpp": {
                    "accepted": aggregate["ddpp"]["accepted"],
                    "blocked": aggregate["ddpp"]["blocked"],
                    "blocking_probability": aggregate["ddpp"][
                        "pooled_blocking_probability_conclusive"
                    ],
                },
                "ddpp_minus_early_dpp_accepted": (
                    aggregate["ddpp"]["accepted"]
                    - aggregate["early_dpp"]["accepted"]
                ),
            }
        )

    loads.sort(key=lambda row: row["arrival_rate_per_epoch"])
    report = {
        "authoritative_performance_result": False,
        "purpose": "Stage-C Mesh2 load-shape pilot aggregation",
        "baseline_label": "early scenario-robust DPP adaptation, not literal path-pair DPP 1:1",
        "topology": "Mesh2",
        "independent_seeds_per_load": 3,
        "requests_per_seed": 12,
        "loads": loads,
        "pipeline_checks": {
            "identical_trace_within_each_policy_pair": True,
            "all_solver_outcomes_conclusive": True,
            "all_ledgers_safe": True,
            "independent_protection_audits_passed_for_accepted_requests": True,
        },
        "claim_boundary": (
            "The pilot validates a plausible load-dependent admission mechanism. "
            "It does not establish a thesis performance claim or equivalence to "
            "literal DPP 1:1."
        ),
    }
    write_json(report, RESULTS / "report.json")
    (RESULTS / "README.md").write_text(
        "# Dynamic admission load-shape pilot\n\n"
        "This non-authoritative Mesh2 aggregation compares an early "
        "scenario-robust DPP adaptation with DDPP at three arrival rates, "
        "using three shared-trace seeds and 12 requests per seed. It checks "
        "that both policies tie at light load and that a difference can emerge "
        "under contention. It is not a thesis result: the sample is small, the "
        "topology is a smoke topology, and the baseline is not literal "
        "single-primary/single-backup-path DPP 1:1.\n"
    )
    for row in loads:
        print(
            f"rate={row['arrival_rate_per_epoch']}: "
            f"early_dpp blocked={row['early_dpp']['blocked']}, "
            f"ddpp blocked={row['ddpp']['blocked']}"
        )


if __name__ == "__main__":
    main()
