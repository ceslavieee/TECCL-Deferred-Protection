"""Run resumable paired load/seed sweeps on thesis topologies."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Dict, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.examples.dynamic_admission_pilot import (  # noqa: E402
    POLICIES,
    TOPOLOGY_CONFIGS,
    generate_trace,
    implementation_provenance,
    run_policy,
    write_json,
)


RESULTS = ROOT / "teccl/examples/results/dynamic_admission_physical_locator"
DEFAULT_RATES = {
    "DCN4WAN": (0.05, 0.1, 0.3, 0.6, 1.0),
    "InterDC8": (0.01, 0.03, 0.06, 0.1, 0.2),
}
EXISTING_CORRECTED_RUNS = {}


def rate_slug(rate: float) -> str:
    return ("%.6g" % rate).replace(".", "p")


def validate_run(
    report: Dict,
    topology: str,
    rate: float,
    seed: int,
    requests: int,
    time_limit_hours: float,
    wall_clock_limit_seconds: float,
    no_rel_heur_work: float,
    candidate_tree_pair_first: bool,
) -> None:
    trace = report["trace"]
    expected = {
        "topology": topology,
        "arrival_rate_per_epoch": rate,
        "seed": seed,
        "request_count": requests,
        "chunk_size_gb": 25.0,
        "epoch_duration_seconds": 2.0,
        "detection_delay_epochs": 1,
    }
    for key, value in expected.items():
        if trace[key] != value:
            raise AssertionError(
                f"Run metadata mismatch for {key}: {trace[key]} != {value}"
            )
    policies = report["policies"]
    if report.get("fallback_time_limit_hours") != time_limit_hours:
        raise AssertionError("Run Gurobi time-limit policy mismatch")
    if report.get("fallback_wall_clock_limit_seconds") != wall_clock_limit_seconds:
        raise AssertionError("Run wall-clock policy mismatch")
    if report.get("fallback_no_rel_heur_work") != no_rel_heur_work:
        raise AssertionError("Run NoRel heuristic-work policy mismatch")
    if report.get("candidate_tree_pair_first") is not candidate_tree_pair_first:
        raise AssertionError("Run fixed-tree candidate policy mismatch")
    if {row["policy"] for row in policies} != set(POLICIES):
        raise AssertionError("Run policy set mismatch")
    if len({row["trace_sha256"] for row in policies}) != 1:
        raise AssertionError("Policies did not consume the same trace")
    if not all(row["ledger_safe"] for row in policies):
        raise AssertionError("Run contains an unsafe ledger")
    provenance = report.get("implementation_provenance") or {}
    if len(str(provenance.get("implementation_bundle_sha256", ""))) != 64:
        raise AssertionError("Run lacks an implementation fingerprint")
    if not all(
        row.get("baseline_structure")
        == "fixed rooted multicast working/backup tree pair"
        for row in policies
    ):
        raise AssertionError("Run does not use the strict multicast tree-pair baseline")
    config = TOPOLOGY_CONFIGS[topology]
    for row in policies:
        if row.get("residual_failure_time_validation") != config.get(
            "residual_failure_time_validation"
        ):
            raise AssertionError(
                "Run residual failure-time validation policy mismatch"
            )
        provenance = row.get("source_provenance") or {}
        expected_template = config["cached_templates"][row["policy"]]
        if provenance.get("template_file") != str(expected_template.relative_to(ROOT)):
            raise AssertionError("Run references a stale cached template")
        if provenance.get("fixed_plan_boundary_certified") is not True:
            raise AssertionError("Run template lacks a detection-boundary certificate")
        if provenance.get("failure_model") != "EXACT":
            raise AssertionError("Run template does not use EXACT exposure")
        expected_library = (
            [
                {
                    "file": str(Path(path).relative_to(ROOT)),
                    "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                }
                for path in config.get("candidate_tree_pair_library", [])
            ]
            if candidate_tree_pair_first
            else []
        )
        if row.get("candidate_tree_pair_library") != expected_library:
            raise AssertionError("Run fixed-tree candidate library mismatch")


def execute_run(
    topology: str,
    rate: float,
    seed: int,
    requests: int,
    time_limit_hours: float,
    wall_clock_limit_seconds: float,
    no_rel_heur_work: float,
    candidate_tree_pair_first: bool,
    results_dir: Path,
    reuse: bool,
) -> Tuple[Dict, Path]:
    key = (topology, rate, seed, requests)
    existing = EXISTING_CORRECTED_RUNS.get(key)
    if existing is not None:
        report = json.loads(existing.read_text())
        validate_run(
            report,
            topology,
            rate,
            seed,
            requests,
            time_limit_hours,
            wall_clock_limit_seconds,
            no_rel_heur_work,
            candidate_tree_pair_first,
        )
        return report, existing

    run_dir = results_dir / f"rate_{rate_slug(rate)}" / f"seed_{seed}"
    report_path = run_dir / "report.json"
    if reuse and report_path.exists():
        report = json.loads(report_path.read_text())
        validate_run(
            report,
            topology,
            rate,
            seed,
            requests,
            time_limit_hours,
            wall_clock_limit_seconds,
            no_rel_heur_work,
            candidate_tree_pair_first,
        )
        return report, report_path

    config = TOPOLOGY_CONFIGS[topology]
    trace = generate_trace(
        seed,
        requests,
        rate,
        topology,
        config["horizon"],
        config.get("chunk_size_gb"),
        config.get("epoch_duration_seconds"),
        config.get("detection_delay_epochs", 1),
    )
    write_json(trace, run_dir / "shared_trace.json")
    policies = [
        run_policy(
            policy,
            timing_mode,
            trace,
            run_dir,
            config["base_input"],
            config["horizon"],
            time_limit_hours,
            config["cached_templates"][policy],
            config.get("require_tree_pair", False),
            config,
            wall_clock_limit_seconds,
            no_rel_heur_work,
            candidate_tree_pair_first,
        )
        for policy, timing_mode in POLICIES.items()
    ]
    report = {
        "authoritative_performance_result": False,
        "purpose": f"{topology} paired load/seed sweep run",
        "fallback_time_limit_hours": time_limit_hours,
        "fallback_wall_clock_limit_seconds": wall_clock_limit_seconds,
        "fallback_no_rel_heur_work": no_rel_heur_work,
        "candidate_tree_pair_first": candidate_tree_pair_first,
        "trace": trace,
        "implementation_provenance": implementation_provenance(),
        "policies": policies,
    }
    validate_run(
        report,
        topology,
        rate,
        seed,
        requests,
        time_limit_hours,
        wall_clock_limit_seconds,
        no_rel_heur_work,
        candidate_tree_pair_first,
    )
    write_json(report, report_path)
    return report, report_path


def aggregate_runs(
    topology: str,
    rates: Sequence[float],
    seeds: Sequence[int],
    requests: int,
    runs: Sequence[Tuple[float, int, Dict, Path]],
) -> Dict:
    deadline_epochs = int(TOPOLOGY_CONFIGS[topology]["horizon"])
    load_rows: List[Dict] = []
    for rate in rates:
        selected = [row for row in runs if row[0] == rate]
        if len(selected) != len(seeds):
            raise AssertionError(f"Missing runs for {topology} rate {rate}")
        policy_rows = {}
        for policy in POLICIES:
            rows = [
                next(item for item in report["policies"] if item["policy"] == policy)
                for _, _, report, _ in selected
            ]
            accepted = sum(row["accepted"] for row in rows)
            blocked = sum(row["blocked"] for row in rows)
            unknown = sum(row["unknown"] for row in rows)
            conclusive = accepted + blocked
            policy_rows[policy] = {
                "accepted": accepted,
                "blocked": blocked,
                "unknown": unknown,
                "blocking_probability_conclusive": (
                    blocked / conclusive if conclusive else None
                ),
                "unknown_rate": unknown / (len(rows) * requests),
                "all_ledgers_safe": all(row["ledger_safe"] for row in rows),
            }
        load_rows.append(
            {
                "arrival_rate_per_epoch": rate,
                "deadline_epochs": deadline_epochs,
                "offered_arrivals_per_deadline": rate * deadline_epochs,
                "seeds": list(seeds),
                "requests_per_policy": len(seeds) * requests,
                "trace_sha256_by_seed": {
                    str(seed): report["trace"]["sha256"]
                    for _, seed, report, _ in selected
                },
                "implementation_bundle_sha256s": sorted(
                    {
                        report["implementation_provenance"][
                            "implementation_bundle_sha256"
                        ]
                        for _, _, report, _ in selected
                    }
                ),
                "source_template_sha256_by_policy": {
                    policy: sorted(
                        {
                            next(
                                item
                                for item in report["policies"]
                                if item["policy"] == policy
                            )["source_provenance"]["template_sha256"]
                            for _, _, report, _ in selected
                        }
                    )
                    for policy in POLICIES
                },
                "early_dpp": policy_rows["early_dpp"],
                "ddpp": policy_rows["ddpp"],
                "ddpp_minus_early_dpp_accepted": (
                    policy_rows["ddpp"]["accepted"]
                    - policy_rows["early_dpp"]["accepted"]
                ),
                "source_reports": [
                    str(path.relative_to(ROOT)) for _, _, _, path in selected
                ],
            }
        )
    return {
        "authoritative_performance_result": False,
        "purpose": f"resumable {topology} load/seed locator sweep",
        "topology": topology,
        "baseline_label": (
            "fixed rooted multicast-tree DPP adaptation, not literal unicast "
            "path-pair DPP 1:1"
        ),
        "gurobi_time_limit_hours_per_fallback": (
            runs[0][2]["fallback_time_limit_hours"] if runs else None
        ),
        "wall_clock_limit_seconds_per_fallback": (
            runs[0][2]["fallback_wall_clock_limit_seconds"] if runs else None
        ),
        "no_rel_heur_work_per_fallback": (
            runs[0][2]["fallback_no_rel_heur_work"] if runs else None
        ),
        "candidate_tree_pair_first": (
            runs[0][2]["candidate_tree_pair_first"] if runs else None
        ),
        "load_normalization": (
            "offered_arrivals_per_deadline = arrival_rate_per_epoch * "
            "deadline_epochs; raw rates should not be compared across "
            "topologies without this context"
        ),
        "requests_per_seed": requests,
        "loads": load_rows,
        "claim_boundary": (
            "This locator sweep selects load neighborhoods. Final thesis results "
            "require more seeds, longer traces, confidence intervals, and the "
            "complete predeclared main-topology load range."
        ),
    }


def main(argv: Sequence[str] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topology", choices=sorted(DEFAULT_RATES), required=True)
    parser.add_argument("--rates", type=float, nargs="+")
    parser.add_argument(
        "--seeds", type=int, nargs="+", default=(20260807, 20260808)
    )
    parser.add_argument("--requests", type=int, default=4)
    parser.add_argument("--time-limit-hours", type=float, default=0.01)
    parser.add_argument("--wall-clock-limit-seconds", type=float, default=60.0)
    parser.add_argument("--no-rel-heur-work", type=float, default=0.0)
    parser.add_argument("--candidate-tree-pair-first", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=RESULTS)
    parser.add_argument("--no-reuse", action="store_true")
    args = parser.parse_args(argv)
    rates = tuple(args.rates or DEFAULT_RATES[args.topology])
    if (
        args.requests <= 0
        or any(rate <= 0 for rate in rates)
        or args.no_rel_heur_work < 0
    ):
        raise SystemExit(
            "requests and rates must be positive; "
            "no-rel-heur-work must be nonnegative"
        )
    results_root = args.output_dir
    if not results_root.is_absolute():
        results_root = ROOT / results_root
    results_dir = results_root / args.topology.lower()

    runs = []
    for rate in rates:
        for seed in args.seeds:
            report, path = execute_run(
                args.topology,
                rate,
                seed,
                args.requests,
                args.time_limit_hours,
                args.wall_clock_limit_seconds,
                args.no_rel_heur_work,
                args.candidate_tree_pair_first,
                results_dir,
                not args.no_reuse,
            )
            runs.append((rate, seed, report, path))
            summary = ", ".join(
                f"{row['policy']}={row['accepted']}A/{row['blocked']}B/"
                f"{row['unknown']}U"
                for row in report["policies"]
            )
            print(f"{args.topology} rate={rate} seed={seed}: {summary}")

    aggregate = aggregate_runs(
        args.topology,
        rates,
        args.seeds,
        args.requests,
        runs,
    )
    write_json(aggregate, results_dir / "report.json")
    (results_dir / "README.md").write_text(
        f"# {args.topology} dynamic-admission locator sweep\n\n"
        "This resumable, non-authoritative sweep uses paired traces and a "
        "certified-template-first solver. It locates candidate load regions; "
        "it is not yet the final confidence-interval experiment.\n"
    )


if __name__ == "__main__":
    main()
