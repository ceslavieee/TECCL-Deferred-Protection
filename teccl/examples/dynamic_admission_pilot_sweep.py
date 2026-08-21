"""Run a small independent-seed sweep of the dynamic-admission pilot."""

import argparse
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.examples.dynamic_admission_pilot import (  # noqa: E402
    POLICIES,
    generate_trace,
    run_policy,
    write_json,
)


RESULTS = ROOT / "teccl/examples/results/dynamic_admission_pilot_sweep"
DEFAULT_SEEDS = (20260807, 20260808, 20260809)


def main(argv: Sequence[str] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=DEFAULT_SEEDS)
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument("--arrival-rate", type=float, default=0.8)
    parser.add_argument("--output-dir", type=Path, default=RESULTS)
    args = parser.parse_args(argv)
    if args.requests <= 0 or args.arrival_rate <= 0:
        raise SystemExit("requests and arrival-rate must be positive")
    results_dir = args.output_dir
    if not results_dir.is_absolute():
        results_dir = ROOT / results_dir

    runs = []
    for seed in args.seeds:
        run_dir = results_dir / f"seed_{seed}"
        trace = generate_trace(seed, args.requests, args.arrival_rate)
        write_json(trace, run_dir / "shared_trace.json")
        policies = [
            run_policy(policy, timing_mode, trace, run_dir)
            for policy, timing_mode in POLICIES.items()
        ]
        if len({row["trace_sha256"] for row in policies}) != 1:
            raise AssertionError(f"Trace mismatch for seed {seed}")
        run = {"seed": seed, "trace_sha256": trace["sha256"], "policies": policies}
        write_json(
            {
                "authoritative_performance_result": False,
                "purpose": "one run in the independent-seed pilot sweep",
                "trace": trace,
                "policies": policies,
            },
            run_dir / "report.json",
        )
        runs.append(run)

    aggregate = []
    for policy in POLICIES:
        rows = [
            next(item for item in run["policies"] if item["policy"] == policy)
            for run in runs
        ]
        accepted = sum(row["accepted"] for row in rows)
        blocked = sum(row["blocked"] for row in rows)
        unknown = sum(row["unknown"] for row in rows)
        conclusive = accepted + blocked
        aggregate.append(
            {
                "policy": policy,
                "runs": len(rows),
                "requests": sum(row["requests"] for row in rows),
                "accepted": accepted,
                "blocked": blocked,
                "unknown": unknown,
                "pooled_blocking_probability_conclusive": (
                    blocked / conclusive if conclusive else None
                ),
                "all_ledgers_safe": all(row["ledger_safe"] for row in rows),
            }
        )

    report = {
        "authoritative_performance_result": False,
        "purpose": "Stage-C independent-seed pipeline pilot",
        "configuration": {
            "topology": "Mesh2",
            "seeds": list(args.seeds),
            "requests_per_seed": args.requests,
            "arrival_rate_per_epoch": args.arrival_rate,
        },
        "aggregate": aggregate,
        "runs": runs,
    }
    write_json(report, results_dir / "report.json")
    (results_dir / "README.md").write_text(
        "# Dynamic admission independent-seed pilot\n\n"
        "This non-authoritative Mesh2 pilot runs early DPP and DDPP on the "
        "same trace for each of three independent seeds. It validates the "
        "multi-run pipeline and provides a preliminary signal only. It is too "
        "small for confidence intervals or thesis performance claims.\n"
    )
    for row in aggregate:
        print(
            f"{row['policy']}: accepted={row['accepted']} "
            f"blocked={row['blocked']} unknown={row['unknown']}"
        )


if __name__ == "__main__":
    main()
