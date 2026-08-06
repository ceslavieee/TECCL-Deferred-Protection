import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

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
from teccl.input_data import TopologyParams  # noqa: E402
from teccl.topologies.dcn4wan import DCN4WAN  # noqa: E402
from teccl.topologies.interdc8 import InterDC8  # noqa: E402


RESULTS = (
    ROOT / "teccl" / "examples" / "results" / "preplanned_failure_execution"
)
Commodity = Tuple[int, int]
Link = Tuple[int, int]


def occupancy_by_link(data: Dict) -> Dict[Link, int]:
    occupancy = {}
    for row in data.get("14i-Flow_Occupancy_Epochs_By_Link") or []:
        i, j = (int(node) for node in row["link"].split("->"))
        occupancy[(i, j)] = int(row["occupied_epochs_per_flow"])
    return occupancy


def completion_profile(data: Dict) -> Dict[Commodity, int]:
    return {
        (int(row["source"]), int(row["chunk"])): int(
            row["working_completion_epoch"]
        )
        for row in data.get("14h-Chunk_Working_Completion_Profile") or []
    }


def replay_parameters(
    input_path: Path,
    data_nodes: Set[int],
    occupancy: Dict[Link, int],
    epoch_duration: float,
) -> Tuple[bool, Dict[Link, int]]:
    raw = load(input_path)
    topology_params = TopologyParams()
    for key, value in raw["TopologyParams"].items():
        setattr(topology_params, key, value)
    topology_classes = {
        "DCN4WAN": DCN4WAN,
        "InterDC8": InterDC8,
    }
    topology = topology_classes[topology_params.name](topology_params)
    instance = raw["InstanceParams"]
    alpha_threshold = float(instance["alpha_threshold"])
    switch_to_gpu_link_on = bool(instance["switch_to_gpu_link_on"])
    arrival_delay = {}
    for (i, j), occupied_epochs in occupancy.items():
        alpha_ratio = topology.alpha[i][j] / epoch_duration
        alpha_epochs = (
            math.ceil(alpha_ratio) if alpha_ratio > alpha_threshold else 0
        )
        if (
            i in topology.switch_indices
            and j in data_nodes
            and not switch_to_gpu_link_on
        ):
            arrival_delay[(i, j)] = alpha_epochs
        else:
            arrival_delay[(i, j)] = alpha_epochs + occupied_epochs
    return bool(instance["switch_copy"]), arrival_delay


def resource_metrics(
    flows: Sequence[Flow],
    occupancy: Dict[Link, int],
    epoch_duration: float,
    chunk_size_gb: float,
) -> Dict[str, float]:
    occupied_epochs = sum(occupancy[(i, j)] for _, i, j, _, _ in flows)
    return {
        "transmissions": len(flows),
        "payload_gb_links": len(flows) * chunk_size_gb,
        "occupied_link_epochs": occupied_epochs,
        "occupied_link_seconds": occupied_epochs * epoch_duration,
    }


def selected_flows(
    flows: Sequence[Flow], commodities: Set[Commodity]
) -> List[Flow]:
    return [
        flow
        for flow in flows
        if (flow[0], flow[3]) in commodities
    ]


def replay(
    flows: Sequence[Flow],
    data_nodes: Set[int],
    occupancy: Dict[Link, int],
    arrival_delay: Dict[Link, int],
    switch_copy: bool,
    failed_link: Optional[Link] = None,
    failure_epoch: Optional[int] = None,
) -> Dict[str, object]:
    by_epoch: Dict[int, List[Flow]] = defaultdict(list)
    commodities = {(source, chunk) for source, _, _, chunk, _ in flows}
    for flow in flows:
        by_epoch[flow[4]].append(flow)

    persistent: Dict[Commodity, Set[int]] = {
        commodity: {commodity[0]} for commodity in commodities
    }
    arrivals: Dict[int, List[Tuple[Commodity, int]]] = defaultdict(list)
    completion: Dict[Commodity, int] = {}
    executed: List[Flow] = []
    failed_drops: List[Flow] = []
    causality_drops: List[Flow] = []
    max_send_epoch = max((flow[4] for flow in flows), default=0)
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

        for flow in sorted(by_epoch.get(epoch, [])):
            source, i, j, chunk, _ = flow
            commodity = (source, chunk)
            if (
                failed_link == (i, j)
                and failure_epoch is not None
                and epoch >= failure_epoch
            ):
                failed_drops.append(flow)
                continue

            if i in data_nodes:
                available = i in persistent[commodity]
            else:
                available = switch_arrivals[(commodity, i)] > 0
            if not available:
                causality_drops.append(flow)
                continue

            if i not in data_nodes and not switch_copy:
                switch_arrivals[(commodity, i)] -= 1
            executed.append(flow)
            arrivals[epoch + arrival_delay[(i, j)]].append((commodity, j))

    incomplete = sorted(commodities.difference(completion))
    return {
        "completion": completion,
        "incomplete": incomplete,
        "executed": executed,
        "failed_drops": failed_drops,
        "causality_drops": causality_drops,
    }


def scenario_completion(
    commodities: Set[Commodity],
    affected: Set[Commodity],
    working_result: Dict[str, object],
    backup_result: Dict[str, object],
) -> Optional[int]:
    working_completion = working_result["completion"]
    backup_completion = backup_result["completion"]
    per_commodity = []
    for commodity in commodities:
        source = backup_completion if commodity in affected else working_completion
        if commodity not in source:
            return None
        per_commodity.append(source[commodity])
    return max(per_commodity, default=0)


def commodity_labels(commodities: Iterable[Commodity]) -> str:
    return ",".join(
        f"s{source}:c{chunk}" for source, chunk in sorted(commodities)
    )


def add_check(
    checks: List[Dict[str, object]],
    topology: str,
    name: str,
    passed: bool,
    detail: str,
) -> None:
    checks.append(
        {
            "topology": topology,
            "name": name,
            "passed": passed,
            "detail": detail,
        }
    )


def evaluate() -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    rows: List[Dict[str, object]] = []
    checks: List[Dict[str, object]] = []

    for topology, paths in EXPERIMENTS.items():
        dedicated = load(paths["dedicated_schedule"])
        deferred = load(paths["deferred_schedule"])
        working = working_flows(dedicated)
        deferred_working = working_flows(deferred)
        dedicated_backup = protection_flows(dedicated)
        deferred_backup = protection_flows(deferred)
        profile = completion_profile(dedicated)
        deferred_profile = completion_profile(deferred)
        commodities = set(profile)
        data_nodes = {source for source, _ in commodities}
        occupancy = occupancy_by_link(dedicated)
        deferred_occupancy = occupancy_by_link(deferred)
        epoch_duration = float(
            dedicated.get("14p-Accounting_Epoch_Duration_Seconds", 1.0)
        )
        chunk_size_gb = float(
            dedicated.get("14q-Accounting_Chunk_Size_GB", 1.0)
        )
        switch_copy, arrival_delay = replay_parameters(
            paths["dedicated_input"],
            data_nodes,
            occupancy,
            epoch_duration,
        )

        add_check(
            checks,
            topology,
            "paired working schedules are identical",
            working == deferred_working,
            f"dedicated={len(working)}, deferred={len(deferred_working)}",
        )
        add_check(
            checks,
            topology,
            "paired working completion profiles are identical",
            profile == deferred_profile,
            f"commodities={len(profile)}",
        )
        add_check(
            checks,
            topology,
            "paired link occupancy maps are identical",
            occupancy == deferred_occupancy,
            f"links={len(occupancy)}",
        )

        normal = replay(
            working,
            data_nodes,
            occupancy,
            arrival_delay,
            switch_copy,
        )
        add_check(
            checks,
            topology,
            "working schedule replay has no causality drops",
            not normal["causality_drops"],
            f"drops={len(normal['causality_drops'])}",
        )
        add_check(
            checks,
            topology,
            "working replay reproduces reported completion profile",
            normal["completion"] == profile,
            (
                f"reported={profile}, "
                f"replayed={normal['completion']}"
            ),
        )

        full_backup_metrics = {
            "Dedicated Protection": resource_metrics(
                dedicated_backup,
                occupancy,
                epoch_duration,
                chunk_size_gb,
            ),
            "Preplanned Deferred Protection": resource_metrics(
                deferred_backup,
                occupancy,
                epoch_duration,
                chunk_size_gb,
            ),
        }
        strategies = (
            ("Dedicated Protection", dedicated_backup),
            ("Preplanned Deferred Protection", deferred_backup),
        )
        failed_links = sorted({(i, j) for _, i, j, _, _ in working})
        working_completion = int(
            math.ceil(float(completion_epoch(dedicated, "working")))
        )

        for failed_link in failed_links:
            for failure_epoch in range(working_completion):
                failed_working = replay(
                    working,
                    data_nodes,
                    occupancy,
                    arrival_delay,
                    switch_copy,
                    failed_link,
                    failure_epoch,
                )
                affected = set(failed_working["incomplete"])

                for strategy, backup in strategies:
                    activated_plan = selected_flows(backup, affected)
                    activated = replay(
                        activated_plan,
                        data_nodes,
                        occupancy,
                        arrival_delay,
                        switch_copy,
                        failed_link,
                        failure_epoch,
                    )
                    activated_metrics = resource_metrics(
                        activated["executed"],
                        occupancy,
                        epoch_duration,
                        chunk_size_gb,
                    )
                    failure_execution_metrics = (
                        full_backup_metrics[strategy]
                        if strategy == "Dedicated Protection"
                        else activated_metrics
                    )
                    final_epoch = scenario_completion(
                        commodities,
                        affected,
                        failed_working,
                        activated,
                    )
                    affected_recovered = (
                        set(activated["completion"]) == affected
                        and not activated["incomplete"]
                    )
                    failed_link_in_affected_backup = any(
                        (i, j) == failed_link
                        for _, i, j, _, _ in activated_plan
                    )

                    rows.append(
                        {
                            "topology": topology,
                            "failed_link": (
                                f"{failed_link[0]}->{failed_link[1]}"
                            ),
                            "failure_epoch_zero_based": failure_epoch,
                            "failure_time_seconds": (
                                failure_epoch * epoch_duration
                            ),
                            "strategy": strategy,
                            "affected_commodities": len(affected),
                            "affected_source_chunks": commodity_labels(affected),
                            "failed_working_transmissions": len(
                                failed_working["failed_drops"]
                            ),
                            "downstream_working_causality_drops": len(
                                failed_working["causality_drops"]
                            ),
                            "provisioned_backup_transmissions": (
                                full_backup_metrics[strategy]["transmissions"]
                            ),
                            "provisioned_backup_occupied_link_seconds": (
                                full_backup_metrics[strategy][
                                    "occupied_link_seconds"
                                ]
                            ),
                            "activated_backup_transmissions": (
                                activated_metrics["transmissions"]
                            ),
                            "activated_backup_payload_gb_links": (
                                activated_metrics["payload_gb_links"]
                            ),
                            "activated_backup_occupied_link_seconds": (
                                activated_metrics["occupied_link_seconds"]
                            ),
                            "failure_execution_backup_transmissions": (
                                failure_execution_metrics["transmissions"]
                            ),
                            "failure_execution_backup_payload_gb_links": (
                                failure_execution_metrics[
                                    "payload_gb_links"
                                ]
                            ),
                            "failure_execution_backup_occupied_link_seconds": (
                                failure_execution_metrics[
                                    "occupied_link_seconds"
                                ]
                            ),
                            "scenario_completion_epoch": (
                                final_epoch if final_epoch is not None else ""
                            ),
                            "scenario_completion_seconds": (
                                final_epoch * epoch_duration
                                if final_epoch is not None
                                else ""
                            ),
                            "delay_vs_failure_free_working_seconds": (
                                (
                                    final_epoch
                                    - float(
                                        completion_epoch(
                                            dedicated, "working"
                                        )
                                    )
                                )
                                * epoch_duration
                                if final_epoch is not None
                                else ""
                            ),
                            "affected_commodities_recovered": (
                                affected_recovered
                            ),
                            "failed_link_used_by_affected_backup": (
                                failed_link_in_affected_backup
                            ),
                            "backup_causality_drops": len(
                                activated["causality_drops"]
                            ),
                        }
                    )

        topology_rows = [
            row for row in rows if row["topology"] == topology
        ]
        failed_recovery_rows = [
            row
            for row in topology_rows
            if (
                not row["affected_commodities_recovered"]
                or row["failed_link_used_by_affected_backup"]
                or row["backup_causality_drops"]
            )
        ]
        add_check(
            checks,
            topology,
            "every affected commodity is recovered by both strategies",
            not failed_recovery_rows,
            (
                f"scenarios={len(topology_rows)}, "
                f"failed={len(failed_recovery_rows)}"
            ),
        )

    return rows, checks


def aggregate(
    rows: Sequence[Dict[str, object]],
    keys: Sequence[str],
) -> List[Dict[str, object]]:
    groups: Dict[Tuple[object, ...], List[Dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)

    summaries = []
    for group_key, group in sorted(groups.items()):
        affected_group = [
            row for row in group if int(row["affected_commodities"]) > 0
        ]
        base = dict(zip(keys, group_key))
        completions = [
            float(row["scenario_completion_seconds"])
            for row in group
            if row["scenario_completion_seconds"] != ""
        ]
        affected_activated_resources = [
            float(row["activated_backup_occupied_link_seconds"])
            for row in affected_group
        ]
        affected_failure_execution_resources = [
            float(row["failure_execution_backup_occupied_link_seconds"])
            for row in affected_group
        ]
        affected_completions = [
            float(row["scenario_completion_seconds"])
            for row in affected_group
            if row["scenario_completion_seconds"] != ""
        ]
        summaries.append(
            {
                **base,
                "directed_link_scenarios": len(group),
                "scenarios_with_affected_commodities": len(affected_group),
                "mean_affected_commodities": mean(
                    float(row["affected_commodities"]) for row in group
                ),
                "max_affected_commodities": max(
                    int(row["affected_commodities"]) for row in group
                ),
                "provisioned_backup_occupied_link_seconds": group[0][
                    "provisioned_backup_occupied_link_seconds"
                ],
                "mean_activated_backup_occupied_link_seconds_all": mean(
                    float(row["activated_backup_occupied_link_seconds"])
                    for row in group
                ),
                "mean_activated_backup_occupied_link_seconds_when_affected": (
                    mean(affected_activated_resources)
                    if affected_activated_resources
                    else 0.0
                ),
                "max_activated_backup_occupied_link_seconds": max(
                    float(row["activated_backup_occupied_link_seconds"])
                    for row in group
                ),
                "mean_failure_execution_backup_occupied_link_seconds_all": (
                    mean(
                        float(
                            row[
                                "failure_execution_backup_occupied_link_seconds"
                            ]
                        )
                        for row in group
                    )
                ),
                "mean_failure_execution_backup_occupied_link_seconds_when_affected": (
                    mean(affected_failure_execution_resources)
                    if affected_failure_execution_resources
                    else 0.0
                ),
                "max_failure_execution_backup_occupied_link_seconds": max(
                    float(
                        row["failure_execution_backup_occupied_link_seconds"]
                    )
                    for row in group
                ),
                "mean_completion_seconds_all": (
                    mean(completions) if completions else ""
                ),
                "mean_completion_seconds_when_affected": (
                    mean(affected_completions)
                    if affected_completions
                    else ""
                ),
                "max_completion_seconds": (
                    max(completions) if completions else ""
                ),
            }
        )
    return summaries


def write_csv(rows: Sequence[Dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: object) -> str:
    if value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def write_markdown(
    rows: Sequence[Dict[str, object]],
    overall: Sequence[Dict[str, object]],
    by_time: Sequence[Dict[str, object]],
    checks: Sequence[Dict[str, object]],
    path: Path,
) -> None:
    lines = [
        "# Directed-link failure execution",
        "",
        "A failure occurs at the beginning of the listed zero-based epoch. "
        "Working transmissions on that directed link at or after the failure "
        "are removed, and the remaining fixed working schedule is replayed.",
        "A `(source, chunk)` is affected only when it can no longer reach every "
        "AllGather endpoint. Only affected units activate a backup plan.",
        "",
        "Dedicated provisioned resource is the complete static backup plan. "
        "For the comparable failure-execution resource, Dedicated counts its "
        "complete committed backup plan, while Deferred counts only the plans "
        "activated for affected units. The CSV also retains the affected-only "
        "portion of Dedicated for diagnosis.",
        "",
        "## Overall",
        "",
        "| topology | strategy | link-time scenarios | affected scenarios | "
        "mean affected | provisioned backup link-s | mean failure-execution "
        "link-s (affected only) | worst failure-execution link-s | mean completion s "
        "(affected only) | worst completion s |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in overall:
        lines.append(
            "| {topology} | {strategy} | {directed_link_scenarios} | "
            "{scenarios_with_affected_commodities} | "
            "{mean_affected_commodities} | "
            "{provisioned_backup_occupied_link_seconds} | "
            "{mean_failure_execution_backup_occupied_link_seconds_when_affected} | "
            "{max_failure_execution_backup_occupied_link_seconds} | "
            "{mean_completion_seconds_when_affected} | "
            "{max_completion_seconds} |".format(
                **{key: fmt(value) for key, value in row.items()}
            )
        )

    lines.extend(
        [
            "",
            "## By failure time",
            "",
            "| topology | failure epoch | strategy | affected link scenarios | "
            "mean affected | mean failure-execution link-s (affected only) | "
            "mean completion s (affected only) | worst completion s |",
            "|---|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in by_time:
        lines.append(
            "| {topology} | {failure_epoch_zero_based} | {strategy} | "
            "{scenarios_with_affected_commodities} | "
            "{mean_affected_commodities} | "
            "{mean_failure_execution_backup_occupied_link_seconds_when_affected} | "
            "{mean_completion_seconds_when_affected} | "
            "{max_completion_seconds} |".format(
                **{key: fmt(value) for key, value in row.items()}
            )
        )

    lines.extend(
        [
            "",
            "## Audit",
            "",
            f"Passed {sum(bool(check['passed']) for check in checks)}/"
            f"{len(checks)} checks.",
            "",
        ]
    )
    for check in checks:
        state = "PASS" if check["passed"] else "FAIL"
        lines.append(
            f"- [{state}] {check['topology']}: {check['name']} "
            f"({check['detail']})"
        )
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the paired fixed AllGather schedules under every used "
            "single directed-link failure and activate backup per affected "
            "(source, chunk)."
        )
    )
    parser.parse_args()

    rows, checks = evaluate()
    overall = aggregate(rows, ("topology", "strategy"))
    by_time = aggregate(
        rows,
        ("topology", "failure_epoch_zero_based", "strategy"),
    )

    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(rows, RESULTS / "scenarios.csv")
    write_csv(overall, RESULTS / "overall.csv")
    write_csv(by_time, RESULTS / "by_failure_time.csv")
    write_markdown(
        rows,
        overall,
        by_time,
        checks,
        RESULTS / "summary.md",
    )
    (RESULTS / "results.json").write_text(
        json.dumps(
            {
                "scenarios": rows,
                "overall": overall,
                "by_failure_time": by_time,
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    for check in checks:
        state = "PASS" if check["passed"] else "FAIL"
        print(
            f"[{state}] {check['topology']}: {check['name']}: "
            f"{check['detail']}"
        )
    print(f"Wrote results to {RESULTS}")
    failed = [check for check in checks if not check["passed"]]
    if failed:
        raise SystemExit(f"Failure execution audit failed: {len(failed)} checks")
    print(f"Failure execution audit passed: {len(checks)} checks")


if __name__ == "__main__":
    main()
