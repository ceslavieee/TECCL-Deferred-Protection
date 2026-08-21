"""Compare approximate source-chunk exposure with deterministic exact replay."""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.examples.compare_preplanned_protection import (  # noqa: E402
    load,
    working_flows,
)
from teccl.examples.evaluate_preplanned_failure_execution import (  # noqa: E402
    occupancy_by_link,
    replay,
    replay_parameters,
)


RESULTS = ROOT / "teccl/examples/results/failure_exposure_comparison"
PHYSICAL_RESULTS = (
    ROOT / "teccl/examples/results/multicast_tree_pair_physical_25gb"
)
FULL_FAILURE_RESULTS = PHYSICAL_RESULTS / "full_directed_failures"
FAILURE_TIME_RESULTS = PHYSICAL_RESULTS / "failure_time_sweep"
DATASETS = {
    "DCN4WAN": {
        "plan": FULL_FAILURE_RESULTS / "schedules/dcn4wan_dpp.json",
        "input": FULL_FAILURE_RESULTS / "inputs/dcn4wan_dpp.json",
        "summary": FAILURE_TIME_RESULTS / "summary.json",
        "candidate_stem": "dcn4wan_dpp_tau_",
    },
    "InterDC8": {
        "plan": FULL_FAILURE_RESULTS / "schedules/interdc8_dpp.json",
        "input": FULL_FAILURE_RESULTS / "inputs/interdc8_dpp.json",
        "summary": FAILURE_TIME_RESULTS / "summary.json",
        "candidate_stem": "interdc8_dpp_tau_",
    },
}

Commodity = Tuple[int, int]
Demand = Tuple[int, int, int]
Link = Tuple[int, int]


def write_json(data: Dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def write_csv(rows: Sequence[Dict[str, object]], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def demand_label(demand: Demand) -> str:
    source, destination, chunk = demand
    return f"{source}->{destination}:c{chunk}"


def candidate_schedule(topology: str, failure_epoch: int) -> Dict:
    stem = DATASETS[topology]["candidate_stem"]
    path = FAILURE_TIME_RESULTS / "schedules" / f"{stem}{failure_epoch}.json"
    return load(path)


def model_affected_counts(schedule: Dict) -> Dict[Link, int]:
    counts = {}
    for summary in schedule.get("9b-Failure_Scenario_Summary", {}).values():
        i, j = (int(node) for node in summary["failed_link"].split("->"))
        counts[(i, j)] = len(summary.get("affected_demands", []))
    return counts


def evaluate_topology(topology: str) -> Tuple[List[Dict], Dict]:
    config = DATASETS[topology]
    plan = load(config["plan"])
    flows = working_flows(plan)
    commodities: Set[Commodity] = {(flow[0], flow[3]) for flow in flows}
    data_nodes = {commodity[0] for commodity in commodities}
    demands: Set[Demand] = {
        (source, destination, chunk)
        for source, chunk in commodities
        for destination in data_nodes
        if destination != source
    }
    occupancy = occupancy_by_link(plan)
    epoch_duration = float(plan["14p-Accounting_Epoch_Duration_Seconds"])
    switch_copy, arrival_delay = replay_parameters(
        config["input"], data_nodes, occupancy, epoch_duration
    )
    normal = replay(
        flows,
        data_nodes,
        occupancy,
        arrival_delay,
        switch_copy,
    )
    if normal["causality_drops"] or normal["incomplete"]:
        raise ValueError(
            f"{topology} normal replay failed: "
            f"causality={normal['causality_drops'][:5]}, "
            f"incomplete={normal['incomplete']}"
        )

    epoch_rows = [
        row
        for row in load(config["summary"]).get("rows", [])
        if row.get("topology") == topology and row.get("policy") == "DPP"
    ]
    if not epoch_rows or not all(
        row.get("classification") == "FEASIBLE"
        and row.get("solver_status") == "OPTIMAL"
        and row.get("audit_passed") is True
        for row in epoch_rows
    ):
        raise ValueError(f"{topology} failure-time source rows are not certified")
    failure_epochs = [int(row["failure_epoch_zero_based"]) for row in epoch_rows]
    links = sorted(occupancy)
    normal_arrival = normal["node_arrival_epoch"]
    rows = []
    count_mismatches = []

    for failure_epoch in failure_epochs:
        exported_counts = model_affected_counts(
            candidate_schedule(topology, failure_epoch)
        )
        for failed_link in links:
            failed = replay(
                flows,
                data_nodes,
                occupancy,
                arrival_delay,
                switch_copy,
                failed_link,
                failure_epoch,
            )
            delivered = failed["delivered_nodes"]
            exact = {
                demand
                for demand in demands
                if demand[1] not in delivered[(demand[0], demand[2])]
            }
            future_commodities = {
                (source, chunk)
                for source, i, j, chunk, epoch in flows
                if (i, j) == failed_link and epoch >= failure_epoch
            }
            approximate = {
                demand
                for demand in demands
                if (demand[0], demand[2]) in future_commodities
                and normal_arrival[((demand[0], demand[2]), demand[1])]
                > failure_epoch
            }
            false_positive = approximate - exact
            false_negative = exact - approximate
            approximate_commodities = {(s, c) for s, _, c in approximate}
            exact_commodities = {(s, c) for s, _, c in exact}
            model_count = exported_counts.get(failed_link, 0)
            if model_count != len(exact):
                count_mismatches.append(
                    {
                        "failure_epoch": failure_epoch,
                        "failed_link": f"{failed_link[0]}->{failed_link[1]}",
                        "exported": model_count,
                        "replay_exact": len(exact),
                    }
                )
            rows.append(
                {
                    "topology": topology,
                    "failure_epoch_zero_based": failure_epoch,
                    "failed_link": f"{failed_link[0]}->{failed_link[1]}",
                    "approximate_affected_demands": len(approximate),
                    "exact_replay_affected_demands": len(exact),
                    "false_positive_demands": len(false_positive),
                    "false_negative_demands": len(false_negative),
                    "approximate_affected_commodities": len(approximate_commodities),
                    "exact_affected_commodities": len(exact_commodities),
                    "false_positive_commodities": len(
                        approximate_commodities - exact_commodities
                    ),
                    "false_negative_commodities": len(
                        exact_commodities - approximate_commodities
                    ),
                    "approximate_only": ",".join(
                        demand_label(demand) for demand in sorted(false_positive)
                    ),
                    "exact_only": ",".join(
                        demand_label(demand) for demand in sorted(false_negative)
                    ),
                    "failed_working_starts": len(failed["failed_drops"]),
                    "downstream_causality_drops": len(failed["causality_drops"]),
                }
            )

    affected_rows = [
        row
        for row in rows
        if row["approximate_affected_demands"]
        or row["exact_replay_affected_demands"]
    ]
    summary = {
        "topology": topology,
        "link_time_scenarios": len(rows),
        "approximate_affected_scenarios": sum(
            bool(row["approximate_affected_demands"]) for row in rows
        ),
        "exact_affected_scenarios": sum(
            bool(row["exact_replay_affected_demands"]) for row in rows
        ),
        "approximate_affected_demand_incidents": sum(
            int(row["approximate_affected_demands"]) for row in rows
        ),
        "exact_affected_demand_incidents": sum(
            int(row["exact_replay_affected_demands"]) for row in rows
        ),
        "false_positive_demand_incidents": sum(
            int(row["false_positive_demands"]) for row in rows
        ),
        "false_negative_demand_incidents": sum(
            int(row["false_negative_demands"]) for row in rows
        ),
        "approximate_affected_commodity_incidents": sum(
            int(row["approximate_affected_commodities"]) for row in rows
        ),
        "exact_affected_commodity_incidents": sum(
            int(row["exact_affected_commodities"]) for row in rows
        ),
        "false_positive_commodity_incidents": sum(
            int(row["false_positive_commodities"]) for row in rows
        ),
        "false_negative_commodity_incidents": sum(
            int(row["false_negative_commodities"]) for row in rows
        ),
        "scenarios_with_false_positives": sum(
            bool(row["false_positive_demands"]) for row in rows
        ),
        "scenarios_with_false_negatives": sum(
            bool(row["false_negative_demands"]) for row in rows
        ),
        "mean_approximate_overcount_when_affected": (
            sum(int(row["false_positive_demands"]) for row in affected_rows)
            / len(affected_rows)
            if affected_rows
            else 0.0
        ),
        "normal_replay_causality_drops": len(normal["causality_drops"]),
        "normal_replay_incomplete_commodities": len(normal["incomplete"]),
        "exported_approximate_count_mismatches": count_mismatches,
        "exact_is_subset_of_approximate": not any(
            row["false_negative_demands"] for row in rows
        ),
        "atomic_commodity_sets_equal": not any(
            row["false_positive_commodities"]
            or row["false_negative_commodities"]
            for row in rows
        ),
    }
    return rows, summary


def write_markdown(summaries: List[Dict], path: Path) -> None:
    lines = [
        "# Failure exposure: approximate versus deterministic replay",
        "",
        "| topology | link-time scenarios | approximate affected scenarios | exact affected scenarios | approximate demand incidents | exact demand incidents | false-positive demands | false-negative demands | approximate commodity incidents | exact commodity incidents | commodity mismatch |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['topology']} | {row['link_time_scenarios']} | "
            f"{row['approximate_affected_scenarios']} | "
            f"{row['exact_affected_scenarios']} | "
            f"{row['approximate_affected_demand_incidents']} | "
            f"{row['exact_affected_demand_incidents']} | "
            f"{row['false_positive_demand_incidents']} | "
            f"{row['false_negative_demand_incidents']} | "
            f"{row['approximate_affected_commodity_incidents']} | "
            f"{row['exact_affected_commodity_incidents']} | "
            f"{row['false_positive_commodity_incidents'] + row['false_negative_commodity_incidents']} |"
        )
    lines.extend(
        [
            "",
            "Exact replay removes working starts on the failed directed link at "
            "and after the failure epoch, then replays all surviving starts with "
            "the configured buffer, delay, and switch-copy semantics.",
            "",
            "A false positive is protected by the approximate source-chunk rule "
            "but still reaches its destination in exact replay. A false negative "
            "would be an exact replay loss omitted by the approximation.",
            "",
            "The comparison concerns exposure classification. The strict solver "
            "already imposes protected completion for every demand in every "
            "scenario, so changing the label alone does not reduce its hard "
            "recovery guarantee or reservation plan.",
            "",
            "For the current atomic `(source, chunk)` protection unit, the two "
            "affected commodity sets are identical in every evaluated scenario. "
            "The approximate mode is therefore safe and exact at the protection "
            "unit level on these schedules, but its per-demand counts are "
            "conservative labels rather than exact loss counts.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--topology", choices=["all", *sorted(DATASETS)], default="all"
    )
    args = parser.parse_args()
    topologies = sorted(DATASETS) if args.topology == "all" else [args.topology]
    all_rows = []
    summaries = []
    for topology in topologies:
        rows, summary = evaluate_topology(topology)
        all_rows.extend(rows)
        summaries.append(summary)
        print(
            f"{topology}: exact_subset={summary['exact_is_subset_of_approximate']} "
            f"commodity_equal={summary['atomic_commodity_sets_equal']} "
            f"false_positive={summary['false_positive_demand_incidents']} "
            f"false_negative={summary['false_negative_demand_incidents']}"
        )
    write_csv(all_rows, RESULTS / "scenarios.csv")
    write_json(
        {
            "source_family": str(PHYSICAL_RESULTS.relative_to(ROOT)),
            "source_policy": "DPP",
            "source_plans": {
                topology: str(DATASETS[topology]["plan"].relative_to(ROOT))
                for topology in topologies
            },
            "summaries": summaries,
            "rows": all_rows,
        },
        RESULTS / "results.json",
    )
    write_markdown(summaries, RESULTS / "README.md")
    if any(
        not summary["exact_is_subset_of_approximate"]
        or not summary["atomic_commodity_sets_equal"]
        or summary["exported_approximate_count_mismatches"]
        or summary["normal_replay_causality_drops"]
        or summary["normal_replay_incomplete_commodities"]
        for summary in summaries
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
