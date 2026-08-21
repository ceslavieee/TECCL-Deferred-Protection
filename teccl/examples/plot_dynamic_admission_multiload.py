"""Audit and plot the predeclared three-load InterDC8 admission experiment."""

import csv
import json
import math
import os
import statistics
from pathlib import Path
from typing import Dict, List, Sequence

os.environ.setdefault("MPLCONFIGDIR", "/tmp/teccl_matplotlib")

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "teccl/examples/results"
ENDPOINT_ROOT = RESULTS / "dynamic_admission_multiload_endpoint_final/interdc8"
ENDPOINT_AGGREGATE = ENDPOINT_ROOT / "report.json"
MIDDLE_SUMMARY = (
    RESULTS
    / "dynamic_admission_candidate_library_holdout_final_batch"
    / "combined_16_seed_summary.json"
)
OUTPUT = RESULTS / "figures/dynamic_admission_multiload"
EXPECTED_IMPLEMENTATION_SHA256 = (
    "18a9f894a909c3edcbca9e7a065d1b61d98467227d710718a526c18dedf81847"
)
POLICIES = ("early_dpp", "ddpp")
POLICY_LABELS = {"early_dpp": "Early DPP", "ddpp": "DDPP"}
POLICY_COLORS = {"early_dpp": "#4C78A8", "ddpp": "#F58518"}
T_CRITICAL_95 = {7: 2.3646242515927844, 15: 2.131449545559323}


def read_json(path: Path) -> Dict:
    return json.loads(path.read_text())


def exact_two_sided_sign_test(wins: int, losses: int) -> float:
    non_ties = wins + losses
    if non_ties == 0:
        return 1.0
    extreme = max(wins, losses)
    upper_tail = sum(math.comb(non_ties, k) for k in range(extreme, non_ties + 1))
    return min(1.0, 2.0 * upper_tail / (2**non_ties))


def mean_t_interval(values: Sequence[int]) -> List[float]:
    mean = statistics.mean(values)
    if len(values) == 1:
        return [mean, mean]
    df = len(values) - 1
    if df not in T_CRITICAL_95:
        raise ValueError(f"No frozen t critical value for df={df}")
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    half_width = T_CRITICAL_95[df] * standard_error
    return [mean - half_width, mean + half_width]


def audit_seed_report(path: Path, expected_rate: float) -> Dict:
    report = read_json(path)
    trace = report["trace"]
    seed = int(trace["seed"])
    if trace["topology"] != "InterDC8":
        raise ValueError(f"Topology mismatch in {path}")
    if not math.isclose(float(trace["arrival_rate_per_epoch"]), expected_rate):
        raise ValueError(f"Rate mismatch in {path}")
    if int(trace["request_count"]) != 8:
        raise ValueError(f"Request-count mismatch in {path}")
    if (
        report["implementation_provenance"]["implementation_bundle_sha256"]
        != EXPECTED_IMPLEMENTATION_SHA256
    ):
        raise ValueError(f"Implementation fingerprint mismatch in {path}")

    policies = {row["policy"]: row for row in report["policies"]}
    if set(policies) != set(POLICIES):
        raise ValueError(f"Policy mismatch in {path}")
    if len({row["trace_sha256"] for row in policies.values()}) != 1:
        raise ValueError(f"Policies did not consume the same trace in {path}")
    if len({json.dumps(row["candidate_tree_pair_library"], sort_keys=True) for row in policies.values()}) != 1:
        raise ValueError(f"Policies used different candidate libraries in {path}")

    result = {"seed": seed, "arrival_rate_per_epoch": expected_rate}
    for policy_name, policy in policies.items():
        if not policy["ledger_safe"]:
            raise ValueError(f"Unsafe ledger in {path}, {policy_name}")
        if sum(int(policy[key]) for key in ("accepted", "blocked", "unknown")) != 8:
            raise ValueError(f"Outcome accounting mismatch in {path}, {policy_name}")
        if len(policy["request_results"]) != 8:
            raise ValueError(f"Request-result length mismatch in {path}, {policy_name}")
        for request in policy["request_results"]:
            outcome = request["outcome"]
            if outcome == "accepted":
                if not request.get("independent_audit_passed", False):
                    raise ValueError(f"Unaudited accepted request in {path}")
                if not request.get("failure_time_certified", False):
                    raise ValueError(f"Uncertified accepted request in {path}")
            elif outcome == "blocked":
                if request.get("solver_status") != "INFEASIBLE":
                    raise ValueError(f"Non-proved blocked request in {path}")
            elif outcome == "unknown":
                if request.get("solver_status") == "INFEASIBLE":
                    raise ValueError(f"Proved infeasible request mislabeled unknown in {path}")
            else:
                raise ValueError(f"Unexpected outcome {outcome} in {path}")
        for outcome in ("accepted", "blocked", "unknown"):
            result[f"{policy_name}_{outcome}"] = int(policy[outcome])

    result["accepted_difference_ddpp_minus_early_dpp"] = (
        result["ddpp_accepted"] - result["early_dpp_accepted"]
    )
    result["trace_sha256"] = trace["sha256"]
    result["report_file"] = str(path.relative_to(ROOT))
    return result


def analyse_endpoint(rate: float, aggregate_row: Dict) -> Dict:
    slug = ("%.6g" % rate).replace(".", "p")
    paths = sorted((ENDPOINT_ROOT / f"rate_{slug}").glob("seed_*/report.json"))
    rows = [audit_seed_report(path, rate) for path in paths]
    if [row["seed"] for row in rows] != list(range(20260829, 20260837)):
        raise ValueError(f"Endpoint seed set mismatch for rate {rate}")

    requests = 8 * len(rows)
    totals = {
        policy: {
            outcome: sum(row[f"{policy}_{outcome}"] for row in rows)
            for outcome in ("accepted", "blocked", "unknown")
        }
        for policy in POLICIES
    }
    for policy in POLICIES:
        for outcome in ("accepted", "blocked", "unknown"):
            if totals[policy][outcome] != aggregate_row[policy][outcome]:
                raise ValueError(
                    f"Aggregate mismatch for rate {rate}, {policy}.{outcome}"
                )

    differences = [row["accepted_difference_ddpp_minus_early_dpp"] for row in rows]
    wins = sum(value > 0 for value in differences)
    ties = sum(value == 0 for value in differences)
    losses = sum(value < 0 for value in differences)
    return {
        "topology": "InterDC8",
        "arrival_rate_per_epoch": rate,
        "offered_arrivals_per_deadline": rate * 40,
        "seeds": len(rows),
        "seed_values": [row["seed"] for row in rows],
        "requests_per_seed": 8,
        "requests_per_policy": requests,
        "early_dpp": {
            **totals["early_dpp"],
            "blocking_probability_lower_bound": totals["early_dpp"]["blocked"] / requests,
            "blocking_probability_upper_bound": (
                totals["early_dpp"]["blocked"] + totals["early_dpp"]["unknown"]
            ) / requests,
        },
        "ddpp": {
            **totals["ddpp"],
            "blocking_probability_lower_bound": totals["ddpp"]["blocked"] / requests,
            "blocking_probability_upper_bound": (
                totals["ddpp"]["blocked"] + totals["ddpp"]["unknown"]
            ) / requests,
        },
        "paired_seed_statistics": {
            "ddpp_wins": wins,
            "ddpp_ties": ties,
            "ddpp_losses": losses,
            "mean_certified_admission_difference_per_eight_request_trace": statistics.mean(differences),
            "mean_difference_95_percent_t_interval": mean_t_interval(differences),
            "two_sided_exact_sign_test_p": exact_two_sided_sign_test(wins, losses),
        },
        "paired_rows": rows,
    }


def read_middle() -> Dict:
    summary = read_json(MIDDLE_SUMMARY)
    if summary["implementation_bundle_sha256"] != EXPECTED_IMPLEMENTATION_SHA256:
        raise ValueError("Middle-load implementation fingerprint mismatch")
    requests = int(summary["requests_per_policy"])
    return {
        "topology": summary["topology"],
        "arrival_rate_per_epoch": summary["trace_model"]["arrival_rate_per_epoch"],
        "offered_arrivals_per_deadline": summary["trace_model"]["offered_arrivals_per_deadline"],
        "seeds": len(summary["seeds"]),
        "seed_values": summary["seeds"],
        "requests_per_seed": summary["requests_per_seed"],
        "requests_per_policy": requests,
        "early_dpp": {
            **summary["early_dpp"],
            "blocking_probability_lower_bound": summary["early_dpp"]["blocking_probability"],
            "blocking_probability_upper_bound": summary["early_dpp"]["blocking_probability"],
        },
        "ddpp": summary["ddpp"],
        "paired_seed_statistics": summary["paired_seed_statistics"],
    }


def write_outputs(loads: Sequence[Dict]) -> List[Path]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    summary = {
        "authoritative_scope": "predeclared three-load InterDC8 evidence",
        "candidate_library_frozen": True,
        "implementation_bundle_sha256": EXPECTED_IMPLEMENTATION_SHA256,
        "loads": [{key: value for key, value in load.items() if key != "paired_rows"} for load in loads],
        "claim_boundary": (
            "Observed load trend for the InterDC8 discretized-exponential trace family; "
            "not universal dominance across topologies or arrival models."
        ),
    }
    summary_path = OUTPUT / "interdc8-three-load-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    csv_path = OUTPUT / "interdc8-endpoint-paired-results.csv"
    rows = [row for load in loads for row in load.get("paired_rows", [])]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return [summary_path, csv_path]


def plot(loads: Sequence[Dict]) -> List[Path]:
    figure, (block_axis, gain_axis) = plt.subplots(1, 2, figsize=(12.6, 5.3))
    x = np.array([load["offered_arrivals_per_deadline"] for load in loads])

    for policy in POLICIES:
        lower = np.array([load[policy]["blocking_probability_lower_bound"] for load in loads])
        upper = np.array([load[policy]["blocking_probability_upper_bound"] for load in loads])
        midpoint = (lower + upper) / 2
        yerr = np.vstack((midpoint - lower, upper - midpoint))
        block_axis.errorbar(
            x,
            midpoint * 100,
            yerr=yerr * 100,
            color=POLICY_COLORS[policy],
            marker="o" if policy == "early_dpp" else "s",
            linewidth=2.0,
            markersize=7,
            capsize=4,
            label=POLICY_LABELS[policy],
        )
    block_axis.set_title("(a) Unknown-aware blocking across load", loc="left")
    block_axis.set_xlabel("Offered arrivals per deadline ($\\lambda K$)")
    block_axis.set_ylabel("Observed blocking interval (%)")
    block_axis.set_xticks(x)
    block_axis.set_ylim(bottom=-1)
    block_axis.grid(color="#D9DEE5", linewidth=0.75)
    block_axis.legend(frameon=False)

    means = np.array([
        load["paired_seed_statistics"]["mean_certified_admission_difference_per_eight_request_trace"]
        for load in loads
    ])
    intervals = np.array([
        load["paired_seed_statistics"]["mean_difference_95_percent_t_interval"]
        for load in loads
    ])
    gain_axis.errorbar(
        x,
        means,
        yerr=np.vstack((means - intervals[:, 0], intervals[:, 1] - means)),
        color="#54A24B",
        marker="D",
        linewidth=2.0,
        markersize=7,
        capsize=5,
    )
    gain_axis.axhline(0, color="#555555", linewidth=1, linestyle="--")
    gain_axis.set_title("(b) Paired DDPP admission gain", loc="left")
    gain_axis.set_xlabel("Offered arrivals per deadline ($\\lambda K$)")
    gain_axis.set_ylabel("Mean additional admissions per 8 requests")
    gain_axis.set_xticks(x)
    gain_axis.grid(color="#D9DEE5", linewidth=0.75)

    for axis in (block_axis, gain_axis):
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
        axis.set_axisbelow(True)
    for x_value, load in zip(x, loads):
        gain_axis.annotate(
            f"n={load['seeds']} seeds",
            (x_value, load["paired_seed_statistics"]["mean_difference_95_percent_t_interval"][1]),
            xytext=(0, 7),
            textcoords="offset points",
            ha="center",
            fontsize=8.5,
        )

    figure.suptitle(
        "InterDC8 dynamic admission: deferred protection across three loads",
        fontsize=14,
        fontweight="bold",
    )
    figure.text(
        0.5,
        0.01,
        "25 GB/source, one chunk/source, 40-epoch deadline, exact single-directed-link exposure; "
        "error bars in (a) encode unknown bounds and in (b) 95% paired-mean t intervals.",
        ha="center",
        fontsize=8.5,
    )
    figure.tight_layout(rect=(0, 0.055, 1, 0.94))

    outputs = []
    for suffix in ("png", "pdf", "svg"):
        path = OUTPUT / f"interdc8-three-load-admission-evidence.{suffix}"
        figure.savefig(path, dpi=220 if suffix == "png" else None, bbox_inches="tight")
        outputs.append(path)
    plt.close(figure)
    return outputs


def main() -> None:
    endpoint_aggregate = read_json(ENDPOINT_AGGREGATE)
    aggregate_by_rate = {
        float(row["arrival_rate_per_epoch"]): row for row in endpoint_aggregate["loads"]
    }
    loads = [
        analyse_endpoint(0.03, aggregate_by_rate[0.03]),
        read_middle(),
        analyse_endpoint(0.10, aggregate_by_rate[0.10]),
    ]
    outputs = write_outputs(loads)
    outputs.extend(plot(loads))
    for path in outputs:
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
