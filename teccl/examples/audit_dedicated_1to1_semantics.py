"""Audit whether the current Dedicated schedule has strict DPP 1:1 semantics.

The existing Dedicated formulation emits protection flows at fixed epochs.  A
strict 1:1 interpretation treats those epochs as reserved recovery
opportunities, not as failure-free data transmissions.  Consequently,
protection flows scheduled before failure detection cannot contribute data to
the post-failure state.

This audit replays each used directed-link failure and activates only the
remaining protection opportunities at ``failure + detection_delay``.  It is a
diagnostic tool: finding invalid scenarios is an expected result and does not
make the process exit unsuccessfully.
"""

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.examples.compare_preplanned_protection import (  # noqa: E402
    EXPERIMENTS,
    Flow,
    completion_epoch,
    load,
    protection_flows,
    working_flows,
)
from teccl.examples.evaluate_preplanned_failure_execution import (  # noqa: E402
    completion_profile,
    occupancy_by_link,
    replay,
    replay_parameters,
)


RESULTS = ROOT / "teccl" / "examples" / "results" / "dedicated_1to1_semantics"
Commodity = Tuple[int, int]
Link = Tuple[int, int]
TaggedFlow = Tuple[str, Flow]


def strict_replay(
    working: Sequence[Flow],
    backup: Sequence[Flow],
    affected: Set[Commodity],
    all_commodities: Set[Commodity],
    data_nodes: Set[int],
    arrival_delay: Dict[Link, int],
    switch_copy: bool,
    failed_link: Link,
    failure_epoch: int,
    activation_epoch: int,
) -> Dict[str, object]:
    """Replay surviving working traffic and detection-gated backup traffic."""

    by_epoch: Dict[int, List[TaggedFlow]] = defaultdict(list)
    preactivation_backup: List[Flow] = []
    for flow in working:
        by_epoch[flow[4]].append(("working", flow))
    for flow in backup:
        commodity = (flow[0], flow[3])
        if commodity not in affected:
            continue
        if flow[4] < activation_epoch:
            preactivation_backup.append(flow)
        else:
            by_epoch[flow[4]].append(("backup", flow))

    persistent: Dict[Commodity, Set[int]] = {
        commodity: {commodity[0]} for commodity in all_commodities
    }
    arrivals: Dict[int, List[Tuple[Commodity, int]]] = defaultdict(list)
    completion: Dict[Commodity, int] = {}
    executed_working: List[Flow] = []
    executed_backup: List[Flow] = []
    failed_working: List[Flow] = []
    failed_backup: List[Flow] = []
    causality_working: List[Flow] = []
    causality_backup: List[Flow] = []

    max_send_epoch = max(by_epoch, default=0)
    max_arrival_delay = max(arrival_delay.values(), default=1)
    horizon = max_send_epoch + max_arrival_delay

    for epoch in range(horizon + 1):
        switch_arrivals: Dict[Tuple[Commodity, int], int] = defaultdict(int)
        for commodity, node in arrivals.get(epoch, []):
            if node in data_nodes:
                persistent[commodity].add(node)
            else:
                switch_arrivals[(commodity, node)] += 1

        for commodity, nodes in persistent.items():
            if commodity not in completion and data_nodes.issubset(nodes):
                completion[commodity] = epoch

        # Working first is a deterministic convention only.  Both phases share
        # the same causal buffer state and the original solver already enforces
        # their joint link capacity.
        for phase, flow in sorted(
            by_epoch.get(epoch, []), key=lambda tagged: tagged[0] != "working"
        ):
            source, i, j, chunk, _ = flow
            commodity = (source, chunk)
            failed = (i, j) == failed_link and epoch >= failure_epoch
            if failed:
                target = failed_working if phase == "working" else failed_backup
                target.append(flow)
                continue

            if i in data_nodes:
                available = i in persistent[commodity]
            else:
                available = switch_arrivals[(commodity, i)] > 0
            if not available:
                target = (
                    causality_working
                    if phase == "working"
                    else causality_backup
                )
                target.append(flow)
                continue

            if i not in data_nodes and not switch_copy:
                switch_arrivals[(commodity, i)] -= 1
            target = executed_working if phase == "working" else executed_backup
            target.append(flow)
            arrivals[epoch + arrival_delay[(i, j)]].append((commodity, j))

    incomplete = all_commodities.difference(completion)
    return {
        "completion": completion,
        "incomplete": incomplete,
        "preactivation_backup": preactivation_backup,
        "executed_working": executed_working,
        "executed_backup": executed_backup,
        "failed_working": failed_working,
        "failed_backup": failed_backup,
        "causality_working": causality_working,
        "causality_backup": causality_backup,
    }


def evaluate() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for topology, paths in EXPERIMENTS.items():
        schedule = load(paths["dedicated_schedule"])
        input_data = load(paths["dedicated_input"])
        working = working_flows(schedule)
        backup = protection_flows(schedule)
        profile = completion_profile(schedule)
        commodities = set(profile)
        data_nodes = {source for source, _ in commodities}
        occupancy = occupancy_by_link(schedule)
        epoch_duration = float(
            schedule.get("14p-Accounting_Epoch_Duration_Seconds", 1.0)
        )
        switch_copy, arrival_delay = replay_parameters(
            paths["dedicated_input"],
            data_nodes,
            occupancy,
            epoch_duration,
        )
        detection_delay = int(
            input_data["InstanceParams"].get("detection_delay_epochs", 1)
        )
        failed_links = sorted({(i, j) for _, i, j, _, _ in working})
        working_horizon = int(
            math.ceil(float(completion_epoch(schedule, "working")))
        )

        for failed_link in failed_links:
            for failure_epoch in range(working_horizon):
                working_only = replay(
                    working,
                    data_nodes,
                    occupancy,
                    arrival_delay,
                    switch_copy,
                    failed_link,
                    failure_epoch,
                )
                affected = set(working_only["incomplete"])
                activation_epoch = failure_epoch + detection_delay
                result = strict_replay(
                    working,
                    backup,
                    affected,
                    commodities,
                    data_nodes,
                    arrival_delay,
                    switch_copy,
                    failed_link,
                    failure_epoch,
                    activation_epoch,
                )
                recovered = affected.issubset(result["completion"])
                complete = not result["incomplete"]
                final_epoch: Optional[int] = (
                    max(result["completion"].values(), default=0)
                    if complete
                    else None
                )
                rows.append(
                    {
                        "topology": topology,
                        "failed_link": f"{failed_link[0]}->{failed_link[1]}",
                        "failure_epoch": failure_epoch,
                        "detection_delay_epochs": detection_delay,
                        "activation_epoch": activation_epoch,
                        "affected_commodities": len(affected),
                        "preactivation_backup_transmissions": len(
                            result["preactivation_backup"]
                        ),
                        "executed_backup_transmissions": len(
                            result["executed_backup"]
                        ),
                        "backup_causality_drops": len(
                            result["causality_backup"]
                        ),
                        "failed_link_backup_drops": len(result["failed_backup"]),
                        "affected_recovered": recovered,
                        "all_commodities_complete": complete,
                        "scenario_completion_epoch": (
                            final_epoch if final_epoch is not None else ""
                        ),
                    }
                )
    return rows


def write_csv(rows: Sequence[Dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(rows: Sequence[Dict[str, object]], path: Path) -> None:
    lines = [
        "# Strict DPP 1:1 semantic audit",
        "",
        "Protection-flow slots before `failure + detection_delay` are treated "
        "as reservations only. They do not place protection data in buffers.",
        "",
        "| topology | scenarios | affected scenarios | invalid affected | "
        "recovery rate | scenarios using pre-activation plan slots |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for topology in sorted({str(row["topology"]) for row in rows}):
        group = [row for row in rows if row["topology"] == topology]
        affected = [row for row in group if row["affected_commodities"]]
        invalid = [row for row in affected if not row["affected_recovered"]]
        preactivation = [
            row for row in group if row["preactivation_backup_transmissions"]
        ]
        rate = (
            100.0 * (len(affected) - len(invalid)) / len(affected)
            if affected
            else 100.0
        )
        lines.append(
            f"| {topology} | {len(group)} | {len(affected)} | "
            f"{len(invalid)} | {rate:.1f}% | {len(preactivation)} |"
        )

    invalid_rows = [
        row
        for row in rows
        if row["affected_commodities"] and not row["affected_recovered"]
    ]
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            (
                f"The current Dedicated schedule fails {len(invalid_rows)} "
                "affected link-time scenarios under strict DPP 1:1 semantics."
            ),
            "This does not prove that dedicated protection is infeasible. It "
            "shows that the current `flow_p` schedule cannot simultaneously be "
            "interpreted as failure-free reservation and post-detection data "
            "execution.",
            "",
            "The required correction is a two-stage model: first-stage backup "
            "reservation variables and scenario-specific recovery-flow "
            "variables constrained by those reservations.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=RESULTS)
    args = parser.parse_args()
    rows = evaluate()
    write_csv(rows, args.output_dir / "scenarios.csv")
    write_markdown(rows, args.output_dir / "README.md")
    invalid = sum(
        bool(row["affected_commodities"]) and not row["affected_recovered"]
        for row in rows
    )
    affected = sum(bool(row["affected_commodities"]) for row in rows)
    print(f"Scenarios: {len(rows)}")
    print(f"Affected scenarios: {affected}")
    print(f"Invalid affected scenarios under strict DPP 1:1: {invalid}")
    print(f"Report: {args.output_dir / 'README.md'}")


if __name__ == "__main__":
    main()
