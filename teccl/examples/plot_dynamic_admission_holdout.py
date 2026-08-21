"""Build the frozen InterDC8 holdout table and paper figure from raw reports.

The script deliberately reads every seed-level ``report.json`` instead of
copying values from the combined summary.  It then checks the independently
stored summary before exporting any figure or table.
"""

import csv
import json
import math
import os
import statistics
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

os.environ.setdefault("MPLCONFIGDIR", "/tmp/teccl_matplotlib")

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "teccl/examples/results"
REPORT_ROOTS = (
    RESULTS / "dynamic_admission_candidate_library_holdout",
    RESULTS / "dynamic_admission_candidate_library_holdout_extension",
    RESULTS / "dynamic_admission_candidate_library_holdout_extension_2",
    RESULTS / "dynamic_admission_candidate_library_holdout_final_batch",
)
COMBINED_SUMMARY = (
    RESULTS
    / "dynamic_admission_candidate_library_holdout_final_batch"
    / "combined_16_seed_summary.json"
)
OUTPUT = RESULTS / "figures/dynamic_admission_holdout"
EXPECTED_IMPLEMENTATION_SHA256 = (
    "18a9f894a909c3edcbca9e7a065d1b61d98467227d710718a526c18dedf81847"
)
POLICIES = ("early_dpp", "ddpp")
POLICY_LABELS = {"early_dpp": "Early DPP", "ddpp": "DDPP"}
POLICY_COLORS = {"early_dpp": "#4C78A8", "ddpp": "#F58518"}


def load_json(path: Path) -> Dict:
    return json.loads(path.read_text())


def find_seed_reports(expected_seeds: Iterable[int]) -> Dict[int, Path]:
    expected = set(expected_seeds)
    found: Dict[int, Path] = {}
    for root in REPORT_ROOTS:
        for path in root.glob("interdc8/rate_0p06/seed_*/report.json"):
            report = load_json(path)
            seed = int(report["trace"]["seed"])
            if seed not in expected:
                continue
            if seed in found:
                raise ValueError(f"Duplicate report for seed {seed}: {found[seed]} and {path}")
            found[seed] = path
    missing = expected.difference(found)
    if missing:
        raise ValueError(f"Missing seed reports: {sorted(missing)}")
    return found


def audit_policy(policy: Dict, request_count: int, seed: int) -> None:
    if policy["policy"] not in POLICIES:
        raise ValueError(f"Unexpected policy for seed {seed}: {policy['policy']}")
    if not policy["ledger_safe"]:
        raise ValueError(f"Unsafe ledger for seed {seed}, policy {policy['policy']}")
    counts = sum(int(policy[key]) for key in ("accepted", "blocked", "unknown"))
    if counts != request_count:
        raise ValueError(
            f"Outcome counts do not sum to {request_count} for seed {seed}, "
            f"policy {policy['policy']}"
        )
    request_results = policy["request_results"]
    if len(request_results) != request_count:
        raise ValueError(
            f"Request-result count mismatch for seed {seed}, policy {policy['policy']}"
        )
    for request in request_results:
        if request["outcome"] != "accepted":
            continue
        if not request.get("independent_audit_passed", False):
            raise ValueError(
                f"Accepted request lacks an independent audit for seed {seed}, "
                f"policy {policy['policy']}, request {request['request_id']}"
            )
        if not request.get("failure_time_certified", False):
            raise ValueError(
                f"Accepted request lacks failure-time certification for seed {seed}, "
                f"policy {policy['policy']}, request {request['request_id']}"
            )


def read_rows(summary: Dict) -> List[Dict]:
    report_paths = find_seed_reports(summary["seeds"])
    rows: List[Dict] = []
    for seed in sorted(report_paths):
        report = load_json(report_paths[seed])
        trace = report["trace"]
        provenance = report["implementation_provenance"]
        if trace["topology"] != summary["topology"]:
            raise ValueError(f"Topology mismatch for seed {seed}")
        if float(trace["arrival_rate_per_epoch"]) != float(
            summary["trace_model"]["arrival_rate_per_epoch"]
        ):
            raise ValueError(f"Arrival-rate mismatch for seed {seed}")
        if int(trace["request_count"]) != int(summary["requests_per_seed"]):
            raise ValueError(f"Request-count mismatch for seed {seed}")
        if provenance["implementation_bundle_sha256"] != EXPECTED_IMPLEMENTATION_SHA256:
            raise ValueError(f"Implementation fingerprint mismatch for seed {seed}")

        policies = {policy["policy"]: policy for policy in report["policies"]}
        if set(policies) != set(POLICIES):
            raise ValueError(f"Policy set mismatch for seed {seed}: {sorted(policies)}")
        for policy in policies.values():
            audit_policy(policy, int(trace["request_count"]), seed)

        early = policies["early_dpp"]
        ddpp = policies["ddpp"]
        rows.append(
            {
                "seed": seed,
                "trace_sha256": trace["sha256"],
                "early_dpp_accepted": int(early["accepted"]),
                "early_dpp_blocked": int(early["blocked"]),
                "early_dpp_unknown": int(early["unknown"]),
                "ddpp_accepted": int(ddpp["accepted"]),
                "ddpp_blocked": int(ddpp["blocked"]),
                "ddpp_unknown": int(ddpp["unknown"]),
                "accepted_difference_ddpp_minus_early_dpp": int(ddpp["accepted"])
                - int(early["accepted"]),
                "report_file": str(report_paths[seed].relative_to(ROOT)),
            }
        )
    return rows


def exact_two_sided_sign_test(wins: int, losses: int) -> float:
    non_ties = wins + losses
    if non_ties == 0:
        return 1.0
    extreme = max(wins, losses)
    upper_tail = sum(math.comb(non_ties, k) for k in range(extreme, non_ties + 1))
    return min(1.0, 2.0 * upper_tail / (2**non_ties))


def analyse(rows: Sequence[Dict], summary: Dict) -> Dict:
    requests = len(rows) * int(summary["requests_per_seed"])
    totals = {
        policy: {
            outcome: sum(int(row[f"{policy}_{outcome}"]) for row in rows)
            for outcome in ("accepted", "blocked", "unknown")
        }
        for policy in POLICIES
    }
    differences = [
        int(row["accepted_difference_ddpp_minus_early_dpp"]) for row in rows
    ]
    wins = sum(value > 0 for value in differences)
    ties = sum(value == 0 for value in differences)
    losses = sum(value < 0 for value in differences)
    mean_difference = statistics.mean(differences)
    standard_error = statistics.stdev(differences) / math.sqrt(len(differences))

    # The experiment has a predeclared fixed n=16; t_(0.975, 15) is therefore fixed.
    if len(differences) != 16:
        raise ValueError("This frozen holdout figure requires exactly 16 paired seeds")
    t_critical_df15 = 2.131449545559323
    mean_interval = [
        mean_difference - t_critical_df15 * standard_error,
        mean_difference + t_critical_df15 * standard_error,
    ]
    sign_test = exact_two_sided_sign_test(wins, losses)

    analysis = {
        "topology": summary["topology"],
        "arrival_rate_per_epoch": summary["trace_model"]["arrival_rate_per_epoch"],
        "seeds": len(rows),
        "requests_per_seed": summary["requests_per_seed"],
        "requests_per_policy": requests,
        "early_dpp": {
            **totals["early_dpp"],
            "blocking_probability": totals["early_dpp"]["blocked"] / requests,
        },
        "ddpp": {
            **totals["ddpp"],
            "blocking_probability_lower_bound": totals["ddpp"]["blocked"] / requests,
            "blocking_probability_upper_bound": (
                totals["ddpp"]["blocked"] + totals["ddpp"]["unknown"]
            )
            / requests,
        },
        "paired_seed_statistics": {
            "ddpp_wins": wins,
            "ddpp_ties": ties,
            "ddpp_losses": losses,
            "mean_certified_admission_difference_per_eight_request_trace": mean_difference,
            "mean_difference_95_percent_t_interval": mean_interval,
            "two_sided_exact_sign_test_p": sign_test,
        },
        "implementation_bundle_sha256": EXPECTED_IMPLEMENTATION_SHA256,
        "claim_boundary": summary["claim_boundary"],
    }
    validate_combined_summary(analysis, summary)
    return analysis


def assert_close(actual: float, expected: float, name: str) -> None:
    if not math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"Combined-summary mismatch for {name}: {actual} != {expected}")


def validate_combined_summary(analysis: Dict, summary: Dict) -> None:
    if analysis["requests_per_policy"] != summary["requests_per_policy"]:
        raise ValueError("Combined-summary request total mismatch")
    for policy in POLICIES:
        for outcome in ("accepted", "blocked", "unknown"):
            if analysis[policy][outcome] != summary[policy][outcome]:
                raise ValueError(f"Combined-summary mismatch for {policy}.{outcome}")
    assert_close(
        analysis["early_dpp"]["blocking_probability"],
        summary["early_dpp"]["blocking_probability"],
        "early_dpp.blocking_probability",
    )
    for bound in ("lower_bound", "upper_bound"):
        key = f"blocking_probability_{bound}"
        assert_close(analysis["ddpp"][key], summary["ddpp"][key], f"ddpp.{key}")
    for key in ("ddpp_wins", "ddpp_ties", "ddpp_losses"):
        if analysis["paired_seed_statistics"][key] != summary["paired_seed_statistics"][key]:
            raise ValueError(f"Combined-summary mismatch for paired_seed_statistics.{key}")
    for key in (
        "mean_certified_admission_difference_per_eight_request_trace",
        "two_sided_exact_sign_test_p",
    ):
        assert_close(
            analysis["paired_seed_statistics"][key],
            summary["paired_seed_statistics"][key],
            f"paired_seed_statistics.{key}",
        )
    for index, value in enumerate(
        analysis["paired_seed_statistics"]["mean_difference_95_percent_t_interval"]
    ):
        assert_close(
            value,
            summary["paired_seed_statistics"]["mean_difference_95_percent_t_interval"][
                index
            ],
            f"paired_seed_statistics.mean_interval[{index}]",
        )


def write_tables(rows: Sequence[Dict], analysis: Dict) -> List[Path]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT / "interdc8-16-seed-paired-results.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary_path = OUTPUT / "interdc8-16-seed-figure-summary.json"
    summary_path.write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n")
    return [csv_path, summary_path]


def style_axis(axis) -> None:
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.grid(axis="x", color="#D9DEE5", linewidth=0.75)
    axis.set_axisbelow(True)
    axis.tick_params(labelsize=9.2)


def plot_evidence(rows: Sequence[Dict], analysis: Dict) -> List[Path]:
    figure, (paired_axis, block_axis) = plt.subplots(
        1,
        2,
        figsize=(12.8, 7.1),
        gridspec_kw={"width_ratios": (1.85, 1.0)},
    )

    y = np.arange(len(rows))
    early_values = np.array([row["early_dpp_accepted"] for row in rows], dtype=float)
    ddpp_values = np.array([row["ddpp_accepted"] for row in rows], dtype=float)
    for y_value, early, ddpp in zip(y, early_values, ddpp_values):
        paired_axis.plot(
            [early, ddpp],
            [y_value, y_value],
            color="#AEB6C2",
            linewidth=1.35,
            zorder=1,
        )
    paired_axis.scatter(
        early_values,
        y,
        s=70,
        color=POLICY_COLORS["early_dpp"],
        marker="o",
        label=POLICY_LABELS["early_dpp"],
        zorder=2,
    )
    paired_axis.scatter(
        ddpp_values,
        y,
        s=34,
        color=POLICY_COLORS["ddpp"],
        marker="s",
        label=POLICY_LABELS["ddpp"],
        zorder=3,
    )
    paired_axis.set_yticks(y, [str(row["seed"]) for row in rows])
    paired_axis.invert_yaxis()
    paired_axis.set_xticks(np.arange(4, 9, 1))
    paired_axis.set_xlim(3.65, 8.35)
    paired_axis.set_xlabel("Certified admissions (requests out of 8)", fontsize=10)
    paired_axis.set_ylabel("Held-out seed", fontsize=10)
    paired_axis.set_title(
        "(a) Paired certified admissions by seed",
        loc="left",
        fontsize=12.2,
        fontweight="bold",
    )
    paired_axis.legend(loc="lower left", frameon=False, fontsize=9.5, ncol=2)
    paired_axis.text(
        0.99,
        0.015,
        "DDPP: 12 wins, 4 ties, 0 losses",
        transform=paired_axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=9.2,
        color="#374151",
        fontweight="bold",
    )
    style_axis(paired_axis)

    early_rate = 100.0 * analysis["early_dpp"]["blocking_probability"]
    ddpp_lower = 100.0 * analysis["ddpp"]["blocking_probability_lower_bound"]
    ddpp_upper = 100.0 * analysis["ddpp"]["blocking_probability_upper_bound"]
    x = np.array([0.0, 1.0])
    block_axis.scatter(
        [x[0]],
        [early_rate],
        s=72,
        color=POLICY_COLORS["early_dpp"],
        marker="o",
        zorder=3,
    )
    midpoint = (ddpp_lower + ddpp_upper) / 2.0
    block_axis.errorbar(
        [x[1]],
        [midpoint],
        yerr=[[midpoint - ddpp_lower], [ddpp_upper - midpoint]],
        fmt="s",
        markersize=7.5,
        color=POLICY_COLORS["ddpp"],
        ecolor=POLICY_COLORS["ddpp"],
        elinewidth=3.0,
        capsize=8,
        capthick=1.8,
        zorder=3,
    )
    block_axis.annotate(
        f"{early_rate:.3f}%",
        (x[0], early_rate),
        xytext=(0, 10),
        textcoords="offset points",
        ha="center",
        fontsize=9.5,
        fontweight="bold",
    )
    block_axis.annotate(
        f"{ddpp_lower:.3f}–{ddpp_upper:.3f}%",
        (x[1], ddpp_upper),
        xytext=(0, 10),
        textcoords="offset points",
        ha="center",
        fontsize=9.5,
        fontweight="bold",
    )
    block_axis.set_xticks(x, [POLICY_LABELS[policy] for policy in POLICIES])
    block_axis.set_xlim(-0.5, 1.5)
    block_axis.set_ylim(0, 18.2)
    block_axis.set_yticks(np.arange(0, 19, 3))
    block_axis.set_ylabel("Blocking probability (%)", fontsize=10)
    block_axis.set_title(
        "(b) Aggregate blocking evidence",
        loc="left",
        fontsize=12.2,
        fontweight="bold",
    )
    block_axis.text(
        0.5,
        0.018,
        "DDPP interval counts its sole unknown\nas unblocked (lower) or blocked (upper).",
        transform=block_axis.transAxes,
        ha="center",
        va="bottom",
        fontsize=8.8,
        color="#4B5563",
    )
    style_axis(block_axis)
    block_axis.grid(axis="y", color="#D9DEE5", linewidth=0.75)
    block_axis.grid(axis="x", visible=False)

    figure.suptitle(
        "Frozen InterDC8 holdout at arrival rate λ = 0.06 per epoch",
        fontsize=13.2,
        fontweight="bold",
        y=0.995,
    )
    mean_gain = analysis["paired_seed_statistics"][
        "mean_certified_admission_difference_per_eight_request_trace"
    ]
    mean_interval = analysis["paired_seed_statistics"][
        "mean_difference_95_percent_t_interval"
    ]
    sign_test = analysis["paired_seed_statistics"]["two_sided_exact_sign_test_p"]
    figure.text(
        0.5,
        0.026,
        f"Mean paired gain: {mean_gain:+.2f} requests "
        f"(95% t interval [{mean_interval[0]:+.2f}, {mean_interval[1]:+.2f}]); "
        f"two-sided exact sign-test p = {sign_test:.5f}.",
        ha="center",
        fontsize=8.8,
        color="#374151",
        fontweight="bold",
    )
    figure.text(
        0.5,
        0.007,
        "16 paired seeds; 8 deadline-driven AllGather requests per seed and policy. "
        "Frozen five-tree candidate library; certified accepted schedules only.",
        ha="center",
        fontsize=8.7,
        color="#4B5563",
    )
    figure.tight_layout(rect=(0, 0.065, 1, 0.965), w_pad=3.1)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    stem = OUTPUT / "interdc8-16-seed-admission-evidence"
    outputs = [stem.with_suffix(suffix) for suffix in (".png", ".pdf", ".svg")]
    for path in outputs:
        options = {
            "bbox_inches": "tight",
            "facecolor": "white",
            "metadata": {"Creator": "TE-CCL frozen-holdout figure pipeline"},
        }
        if path.suffix == ".png":
            options["dpi"] = 320
            options.pop("metadata")
        figure.savefig(path, **options)
    plt.close(figure)
    return outputs


def main() -> None:
    summary = load_json(COMBINED_SUMMARY)
    if summary["authoritative_performance_result"]:
        raise ValueError("The source summary unexpectedly broadens the claim boundary")
    if not summary["candidate_library_frozen"]:
        raise ValueError("Candidate library was not frozen")
    if summary["implementation_bundle_sha256"] != EXPECTED_IMPLEMENTATION_SHA256:
        raise ValueError("Combined-summary implementation fingerprint mismatch")

    rows = read_rows(summary)
    analysis = analyse(rows, summary)
    paths = write_tables(rows, analysis) + plot_evidence(rows, analysis)
    for path in paths:
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
