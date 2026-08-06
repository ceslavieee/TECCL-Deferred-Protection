import argparse
import copy
import csv
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.examples.compare_preplanned_protection import (  # noqa: E402
    check_disjoint,
    completion_epoch,
    load,
    percent_change,
    protection_flows,
    run_solver,
    verify_accounting,
    working_flows,
)


EXAMPLES = ROOT / "teccl" / "examples"
RESULTS = EXAMPLES / "results" / "chunk_granularity"
INPUTS = RESULTS / "inputs"
SCHEDULES = RESULTS / "schedules"
WARMSTARTS = RESULTS / "warmstarts"
TOTAL_DATA_GB_PER_SOURCE = 100.0

TOPOLOGIES = {
    "DCN4WAN": {
        "slug": "dcn4wan",
        "dedicated_base_epochs": 18,
        "deferred_base_epochs": 30,
        "dedicated_template": EXAMPLES
        / "sample_inputs"
        / "dcn4wan_dedicated_comparison.json",
        "deferred_template": EXAMPLES
        / "sample_inputs"
        / "dcn4wan_preplanned_deferred_protection.json",
    },
    "InterDC8": {
        "slug": "interdc8",
        "dedicated_base_epochs": 20,
        "deferred_base_epochs": 40,
        "dedicated_template": EXAMPLES
        / "sample_inputs"
        / "interdc8_dedicated_comparison.json",
        "deferred_template": EXAMPLES
        / "sample_inputs"
        / "interdc8_preplanned_deferred_protection.json",
    },
}


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def paths_for(topology: str, chunks: int) -> Dict[str, Path]:
    slug = TOPOLOGIES[topology]["slug"]
    stem = f"{slug}_{chunks}chunks"
    return {
        "dedicated_input": INPUTS / f"{stem}_dedicated.json",
        "deferred_input": INPUTS / f"{stem}_preplanned_deferred.json",
        "dedicated_schedule": SCHEDULES / f"{stem}_dedicated_schedule.json",
        "deferred_schedule": SCHEDULES
        / f"{stem}_preplanned_deferred_schedule.json",
        "dedicated_warmstart": WARMSTARTS / f"{stem}_dedicated.mst",
        "deferred_warmstart": WARMSTARTS
        / f"{stem}_preplanned_deferred.mst",
        "scaled_working_reference": WARMSTARTS
        / f"{stem}_scaled_working_reference.json",
    }


def write_scaled_flow_warmstart(
    source_schedule: Path,
    target_path: Path,
    chunk_multiplier: int,
    serialize_copies: bool = False,
) -> int:
    source = load(source_schedule)
    working = working_flows(source)
    backup = protection_flows(source)
    max_source_epoch = max(
        (flow[4] for flow in working + backup),
        default=0,
    )
    block_shift = (
        (max_source_epoch + 1) * chunk_multiplier
        if serialize_copies
        else 1
    )
    max_target_epoch = 0
    rows = ["# Scaled feasible-flow hint from a coarser chunk schedule"]
    for prefix, flows in (("flow_w", working), ("flow_p", backup)):
        for source_node, i, j, source_chunk, epoch in flows:
            for offset in range(chunk_multiplier):
                target_chunk = source_chunk * chunk_multiplier + offset
                target_epoch = (
                    epoch * chunk_multiplier
                    + offset * block_shift
                    if serialize_copies
                    else epoch * chunk_multiplier + offset
                )
                max_target_epoch = max(max_target_epoch, target_epoch)
                rows.append(
                    f"{prefix}_{source_node}_{i}_{j}_{target_chunk}_{target_epoch} 1"
                )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("\n".join(rows) + "\n")
    return max_target_epoch


def write_scaled_working_reference(
    source_schedule: Path,
    target_path: Path,
    chunk_multiplier: int,
    serialize_copies: bool = False,
) -> None:
    source = load(source_schedule)
    source_working = working_flows(source)
    source_backup = protection_flows(source)
    max_source_epoch = max(
        (flow[4] for flow in source_working + source_backup),
        default=0,
    )
    block_shift = (
        (max_source_epoch + 1) * chunk_multiplier
        if serialize_copies
        else 1
    )
    scaled_rows = []
    for source_node, i, j, source_chunk, epoch in source_working:
        for offset in range(chunk_multiplier):
            target_chunk = source_chunk * chunk_multiplier + offset
            target_epoch = (
                epoch * chunk_multiplier + offset * block_shift
                if serialize_copies
                else epoch * chunk_multiplier + offset
            )
            scaled_rows.append(
                f"Chunk {target_chunk} from {source_node} traveled over "
                f"{i}->{j} in epoch {target_epoch}"
            )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps({"10-Working_Flows": scaled_rows}, indent=2, sort_keys=True)
        + "\n"
    )


def write_serial_preplanned_warmstart(
    coarse_dedicated_schedule: Path,
    coarse_deferred_schedule: Path,
    target_path: Path,
    chunk_multiplier: int,
    target_activation_epoch: int = None,
) -> Dict[str, int]:
    coarse_dedicated = load(coarse_dedicated_schedule)
    coarse_deferred = load(coarse_deferred_schedule)
    coarse_working = working_flows(coarse_dedicated)
    coarse_dedicated_backup = protection_flows(coarse_dedicated)
    coarse_deferred_backup = protection_flows(coarse_deferred)
    working_block = (
        max(
            (flow[4] for flow in coarse_working + coarse_dedicated_backup),
            default=0,
        )
        + 1
    ) * chunk_multiplier

    rows = ["# Serialized exact-flow hint from a coarser preplanned schedule"]
    max_working_epoch = 0
    for source_node, i, j, source_chunk, epoch in coarse_working:
        for offset in range(chunk_multiplier):
            target_chunk = source_chunk * chunk_multiplier + offset
            target_epoch = epoch * chunk_multiplier + offset * working_block
            max_working_epoch = max(max_working_epoch, target_epoch)
            rows.append(
                f"flow_w_{source_node}_{i}_{j}_{target_chunk}_{target_epoch} 1"
            )

    source_activation = int(
        coarse_deferred["7a-Deferred_Activation_Epoch"]
    ) - 1
    max_source_backup_epoch = max(
        (flow[4] for flow in coarse_deferred_backup),
        default=source_activation,
    )
    backup_block = (
        max_source_backup_epoch - source_activation + 1
    ) * chunk_multiplier
    target_activation = (
        int(target_activation_epoch)
        if target_activation_epoch is not None
        else max_working_epoch + 1
    )
    max_backup_epoch = target_activation
    for source_node, i, j, source_chunk, epoch in coarse_deferred_backup:
        relative_epoch = epoch - source_activation
        for offset in range(chunk_multiplier):
            target_chunk = source_chunk * chunk_multiplier + offset
            target_epoch = (
                target_activation
                + relative_epoch * chunk_multiplier
                + offset * backup_block
            )
            max_backup_epoch = max(max_backup_epoch, target_epoch)
            rows.append(
                f"flow_p_{source_node}_{i}_{j}_{target_chunk}_{target_epoch} 1"
            )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("\n".join(rows) + "\n")
    return {
        "working_deadline": max_working_epoch,
        "activation_epoch": target_activation,
        "max_backup_epoch": max_backup_epoch,
    }


def refresh_deferred_after_dedicated(
    topology: str,
    chunks: int,
    paths: Dict[str, Path],
) -> None:
    if topology != "InterDC8" or chunks < 4:
        return
    dedicated = load(paths["dedicated_schedule"])
    actual_working_completion = int(
        math.ceil(float(completion_epoch(dedicated, "working")))
    )
    coarse_paths = paths_for(topology, chunks // 2)
    timing = write_serial_preplanned_warmstart(
        coarse_paths["dedicated_schedule"],
        coarse_paths["deferred_schedule"],
        paths["deferred_warmstart"],
        2,
        actual_working_completion,
    )
    deferred = load(paths["deferred_input"])
    deferred["InstanceParams"]["num_epochs"] = max(
        int(deferred["InstanceParams"]["num_epochs"]),
        timing["max_backup_epoch"] + 2,
    )
    timing_ratio = timing["activation_epoch"] / deferred["InstanceParams"][
        "num_epochs"
    ]
    deferred["InstanceParams"]["working_deadline_ratio"] = timing_ratio
    deferred["InstanceParams"]["deferred_activation_ratio"] = timing_ratio
    deferred["InstanceParams"]["fixed_working_schedule"] = relative(
        paths["dedicated_schedule"]
    )
    deferred["InstanceParams"]["warmstart"] = relative(
        paths["deferred_warmstart"]
    )
    paths["deferred_input"].write_text(
        json.dumps(deferred, indent=2, sort_keys=True) + "\n"
    )


def build_inputs(topology: str, chunks: int) -> Dict[str, Path]:
    if chunks <= 0:
        raise ValueError("Chunk count must be positive")
    config = TOPOLOGIES[topology]
    paths = paths_for(topology, chunks)
    chunk_size_gb = TOTAL_DATA_GB_PER_SOURCE / chunks
    dedicated_epochs = int(config["dedicated_base_epochs"]) * chunks
    deferred_epochs = int(config["deferred_base_epochs"]) * chunks

    dedicated = copy.deepcopy(load(config["dedicated_template"]))
    deferred = copy.deepcopy(load(config["deferred_template"]))
    for data in (dedicated, deferred):
        data["TopologyParams"]["chunk_size"] = chunk_size_gb
        data["InstanceParams"]["num_chunks"] = chunks
    dedicated["InstanceParams"]["num_epochs"] = dedicated_epochs
    deferred["InstanceParams"]["num_epochs"] = deferred_epochs
    deferred["InstanceParams"]["beta_weighted_holding_objective"] = True

    dedicated["InstanceParams"]["schedule_output_file"] = relative(
        paths["dedicated_schedule"]
    )
    deferred["InstanceParams"]["fixed_working_schedule"] = relative(
        paths["dedicated_schedule"]
    )
    deferred["InstanceParams"]["schedule_output_file"] = relative(
        paths["deferred_schedule"]
    )

    INPUTS.mkdir(parents=True, exist_ok=True)
    SCHEDULES.mkdir(parents=True, exist_ok=True)
    if chunks > 1 and chunks % 2 == 0:
        coarse_paths = paths_for(topology, chunks // 2)
        if coarse_paths["dedicated_schedule"].exists():
            serialize_dedicated = topology == "InterDC8" and chunks >= 4
            warmstart_max_epoch = write_scaled_flow_warmstart(
                coarse_paths["dedicated_schedule"],
                paths["dedicated_warmstart"],
                2,
                serialize_dedicated,
            )
            dedicated["InstanceParams"]["warmstart"] = relative(
                paths["dedicated_warmstart"]
            )
            if serialize_dedicated:
                write_scaled_working_reference(
                    coarse_paths["dedicated_schedule"],
                    paths["scaled_working_reference"],
                    2,
                    serialize_dedicated,
                )
                dedicated["InstanceParams"]["fixed_working_schedule"] = relative(
                    paths["scaled_working_reference"]
                )
                if serialize_dedicated:
                    dedicated["InstanceParams"]["num_epochs"] = max(
                        dedicated["InstanceParams"]["num_epochs"],
                        warmstart_max_epoch + 2,
                    )
        if coarse_paths["deferred_schedule"].exists():
            if topology == "InterDC8" and chunks >= 4:
                timing = write_serial_preplanned_warmstart(
                    coarse_paths["dedicated_schedule"],
                    coarse_paths["deferred_schedule"],
                    paths["deferred_warmstart"],
                    2,
                )
                deferred["InstanceParams"]["num_epochs"] = max(
                    deferred["InstanceParams"]["num_epochs"],
                    timing["max_backup_epoch"] + 2,
                )
                timing_ratio = timing["activation_epoch"] / deferred[
                    "InstanceParams"
                ]["num_epochs"]
                deferred["InstanceParams"][
                    "working_deadline_ratio"
                ] = timing_ratio
                deferred["InstanceParams"][
                    "deferred_activation_ratio"
                ] = timing_ratio
            else:
                write_scaled_flow_warmstart(
                    coarse_paths["deferred_schedule"],
                    paths["deferred_warmstart"],
                    2,
                )
            deferred["InstanceParams"]["warmstart"] = relative(
                paths["deferred_warmstart"]
            )

    paths["dedicated_input"].write_text(
        json.dumps(dedicated, indent=2, sort_keys=True) + "\n"
    )
    paths["deferred_input"].write_text(
        json.dumps(deferred, indent=2, sort_keys=True) + "\n"
    )
    return paths


def run_pair(
    topology: str,
    chunks: int,
    run_dedicated: bool = True,
) -> None:
    paths = build_inputs(topology, chunks)
    if run_dedicated:
        paths["dedicated_schedule"].unlink(missing_ok=True)
    paths["deferred_schedule"].unlink(missing_ok=True)
    if run_dedicated:
        print(f"Running {topology}, {chunks} chunks: Dedicated")
        run_solver(paths["dedicated_input"])
    if not paths["dedicated_schedule"].exists():
        raise RuntimeError(
            f"Dedicated did not produce {paths['dedicated_schedule']}"
        )
    refresh_deferred_after_dedicated(topology, chunks, paths)
    print(f"Running {topology}, {chunks} chunks: Preplanned Deferred")
    run_solver(paths["deferred_input"])
    if not paths["deferred_schedule"].exists():
        raise RuntimeError(
            f"Preplanned Deferred did not produce {paths['deferred_schedule']}"
        )


def physical_metric_row(
    topology: str,
    chunks: int,
    strategy: str,
    data: Dict,
    schedule_origin: str,
) -> Dict[str, object]:
    epoch_duration = float(data["14p-Accounting_Epoch_Duration_Seconds"])
    working_epoch = float(completion_epoch(data, "working"))
    protected_epoch = float(completion_epoch(data, "protection"))
    solver_status = data.get("Solver_Status_Name", "")
    return {
        "topology": topology,
        "chunks_per_source": chunks,
        "chunk_size_gb": float(data["14q-Accounting_Chunk_Size_GB"]),
        "total_data_gb_per_source": chunks
        * float(data["14q-Accounting_Chunk_Size_GB"]),
        "strategy": strategy,
        "schedule_origin": schedule_origin,
        "optimization_quality": (
            "OPTIMAL" if solver_status == "OPTIMAL" else "FEASIBLE_TIME_LIMIT"
        ),
        "solver_status": solver_status,
        "solver_mip_gap": data.get("Solver_MIP_Gap", ""),
        "solver_runtime_seconds": data.get("Solver_Runtime", ""),
        "epoch_duration_seconds": epoch_duration,
        "normal_completion_seconds": working_epoch * epoch_duration,
        "protected_completion_seconds": protected_epoch * epoch_duration,
        "contingency_plan_transmissions": data[
            "14j-Contingency_Plan_Transmissions"
        ],
        "contingency_plan_payload_gb_links": data[
            "14k-Contingency_Plan_Payload_GB_Links"
        ],
        "contingency_plan_occupied_link_seconds": data[
            "14l-Contingency_Plan_Occupied_Link_Seconds"
        ],
        "future_reservation_holding_link_second_squared": data[
            "14m-Future_Reservation_Holding_Link_Second_Squared"
        ],
        "failure_free_committed_backup_link_seconds": data[
            "14o-Failure_Free_Committed_Backup_Link_Seconds"
        ],
        "failure_free_release_ratio": data["14f-Failure_Free_Release_Ratio"],
    }


def summarize(topologies: Sequence[str], chunk_counts: Sequence[int]):
    rows: List[Dict[str, object]] = []
    comparisons: List[Dict[str, object]] = []
    checks: List[Dict[str, object]] = []

    def check(
        topology: str,
        chunks: int,
        name: str,
        passed: bool,
        detail: str,
    ) -> None:
        checks.append(
            {
                "topology": topology,
                "chunks_per_source": chunks,
                "name": name,
                "passed": passed,
                "detail": detail,
            }
        )

    for topology in topologies:
        for chunks in chunk_counts:
            paths = paths_for(topology, chunks)
            dedicated = load(paths["dedicated_schedule"])
            deferred = load(paths["deferred_schedule"])
            dedicated_working = working_flows(dedicated)
            deferred_working = working_flows(deferred)
            dedicated_backup = protection_flows(dedicated)
            deferred_backup = protection_flows(deferred)
            fixed_working_reference = dedicated.get(
                "8e-Fixed_Working_Schedule", ""
            )
            schedule_origin = (
                "SERIALIZED_COARSE_REFERENCE"
                if fixed_working_reference
                else "DIRECT_MILP"
            )

            for label, data in (
                ("Dedicated", dedicated),
                ("Preplanned Deferred", deferred),
            ):
                check(
                    topology,
                    chunks,
                    f"{label} has a solver solution",
                    int(data.get("Solver_Solution_Count", 0)) > 0,
                    (
                        f"status={data.get('Solver_Status_Name')}, "
                        f"gap={data.get('Solver_MIP_Gap')}"
                    ),
                )
            check(
                topology,
                chunks,
                "working flows match exactly",
                dedicated_working == deferred_working,
                (
                    f"dedicated={len(dedicated_working)}, "
                    f"deferred={len(deferred_working)}"
                ),
            )
            check(
                topology,
                chunks,
                "total data per source is fixed",
                math.isclose(
                    chunks
                    * float(deferred["14q-Accounting_Chunk_Size_GB"]),
                    TOTAL_DATA_GB_PER_SOURCE,
                ),
                (
                    f"total={chunks * float(deferred['14q-Accounting_Chunk_Size_GB'])}"
                ),
            )
            check(
                topology,
                chunks,
                "Deferred uses beta-weighted holding objective",
                (
                    deferred.get("12f-Primary_Holding_Objective")
                    == "beta-weighted occupied link-epoch holding"
                ),
                str(deferred.get("12f-Primary_Holding_Objective")),
            )
            for label, working, backup, data in (
                ("Dedicated", dedicated_working, dedicated_backup, dedicated),
                (
                    "Preplanned Deferred",
                    deferred_working,
                    deferred_backup,
                    deferred,
                ),
            ):
                working_commodities = {
                    (source, chunk) for source, _, _, chunk, _ in working
                }
                backup_commodities = {
                    (source, chunk) for source, _, _, chunk, _ in backup
                }
                check(
                    topology,
                    chunks,
                    f"{label} covers every commodity",
                    bool(backup) and working_commodities == backup_commodities,
                    (
                        f"working={len(working_commodities)}, "
                        f"backup={len(backup_commodities)}"
                    ),
                )
                check(
                    topology,
                    chunks,
                    f"{label} is directed-link disjoint",
                    check_disjoint(working, backup),
                    f"working_flows={len(working)}, backup_flows={len(backup)}",
                )
                check(
                    topology,
                    chunks,
                    f"{label} accounting recomputes exactly",
                    verify_accounting(data, backup),
                    (
                        "occupied_link_seconds="
                        f"{data.get('14l-Contingency_Plan_Occupied_Link_Seconds')}"
                    ),
                )

            dedicated_row = physical_metric_row(
                topology,
                chunks,
                "Dedicated Protection",
                dedicated,
                schedule_origin,
            )
            deferred_row = physical_metric_row(
                topology,
                chunks,
                "Preplanned Deferred Protection",
                deferred,
                schedule_origin,
            )
            rows.extend([dedicated_row, deferred_row])
            pair_is_optimal = all(
                row["solver_status"] == "OPTIMAL"
                for row in (dedicated_row, deferred_row)
            )
            quality_prefix = (
                "FIXED_REFERENCE"
                if schedule_origin == "SERIALIZED_COARSE_REFERENCE"
                else "DIRECT"
            )
            quality_suffix = (
                "OPTIMAL_PAIR"
                if pair_is_optimal
                else "FEASIBLE_TIME_LIMIT_PAIR"
            )
            comparison_quality = f"{quality_prefix}_{quality_suffix}"
            comparisons.append(
                {
                    "topology": topology,
                    "chunks_per_source": chunks,
                    "schedule_origin": schedule_origin,
                    "comparison_quality": comparison_quality,
                    "normal_completion_delta_seconds": (
                        float(deferred_row["normal_completion_seconds"])
                        - float(dedicated_row["normal_completion_seconds"])
                    ),
                    "protected_completion_delay_seconds": (
                        float(deferred_row["protected_completion_seconds"])
                        - float(dedicated_row["protected_completion_seconds"])
                    ),
                    "payload_gb_link_reduction_percent": percent_change(
                        float(
                            dedicated_row[
                                "contingency_plan_payload_gb_links"
                            ]
                        ),
                        float(
                            deferred_row[
                                "contingency_plan_payload_gb_links"
                            ]
                        ),
                    ),
                    "occupied_link_seconds_reduction_percent": percent_change(
                        float(
                            dedicated_row[
                                "contingency_plan_occupied_link_seconds"
                            ]
                        ),
                        float(
                            deferred_row[
                                "contingency_plan_occupied_link_seconds"
                            ]
                        ),
                    ),
                    "holding_link_second_squared_reduction_percent": (
                        percent_change(
                            float(
                                dedicated_row[
                                    "future_reservation_holding_link_second_squared"
                                ]
                            ),
                            float(
                                deferred_row[
                                    "future_reservation_holding_link_second_squared"
                                ]
                            ),
                        )
                    ),
                    "committed_link_seconds_reduction_percent": percent_change(
                        float(
                            dedicated_row[
                                "failure_free_committed_backup_link_seconds"
                            ]
                        ),
                        float(
                            deferred_row[
                                "failure_free_committed_backup_link_seconds"
                            ]
                        ),
                    ),
                    "release_ratio_gain_percentage_points": 100.0
                    * (
                        float(deferred_row["failure_free_release_ratio"])
                        - float(dedicated_row["failure_free_release_ratio"])
                    ),
                }
            )
    return rows, comparisons, checks


def write_csv(rows: Sequence[Dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def write_markdown(
    rows: Sequence[Dict[str, object]],
    comparisons: Sequence[Dict[str, object]],
    checks: Sequence[Dict[str, object]],
) -> None:
    row_columns = [
        "topology",
        "chunks_per_source",
        "strategy",
        "schedule_origin",
        "optimization_quality",
        "solver_mip_gap",
        "normal_completion_seconds",
        "protected_completion_seconds",
        "contingency_plan_payload_gb_links",
        "contingency_plan_occupied_link_seconds",
        "future_reservation_holding_link_second_squared",
        "failure_free_committed_backup_link_seconds",
        "failure_free_release_ratio",
        "solver_status",
    ]
    lines = [
        "# Chunk granularity sensitivity",
        "",
        "Each source always owns 100 GB. Chunk size is 100 GB divided by the chunk count.",
        "Within each row pair, Deferred fixes the exact working flows used by Dedicated.",
        "",
        "Result scope:",
        "",
        "- `DIRECT_OPTIMAL_PAIR` is suitable for direct paired conclusions.",
        "- `DIRECT_FEASIBLE_TIME_LIMIT_PAIR` satisfies the exact MILP constraints but is not proven optimal.",
        "- `FIXED_REFERENCE_OPTIMAL_PAIR` is optimal conditional on a serialized working reference and is not a like-for-like chunk-granularity trend point.",
        "- `FIXED_REFERENCE_FEASIBLE_TIME_LIMIT_PAIR` is conditional on that reference and is also not proven optimal.",
        "- Passing the audit proves feasibility, fairness, disjointness, and accounting consistency; it does not replace an optimality proof.",
        "",
        "| " + " | ".join(row_columns) + " |",
        "|" + "|".join(["---"] * len(row_columns)) + "|",
    ]
    for row in rows:
        lines.append(
            "| " + " | ".join(fmt(row[column]) for column in row_columns) + " |"
        )

    comparison_columns = [
        "topology",
        "chunks_per_source",
        "schedule_origin",
        "comparison_quality",
        "normal_completion_delta_seconds",
        "protected_completion_delay_seconds",
        "payload_gb_link_reduction_percent",
        "occupied_link_seconds_reduction_percent",
        "holding_link_second_squared_reduction_percent",
        "committed_link_seconds_reduction_percent",
        "release_ratio_gain_percentage_points",
    ]
    lines.extend(
        [
            "",
            "## Paired changes",
            "",
            "| " + " | ".join(comparison_columns) + " |",
            "|" + "|".join(["---"] * len(comparison_columns)) + "|",
        ]
    )
    for row in comparisons:
        lines.append(
            "| "
            + " | ".join(fmt(row[column]) for column in comparison_columns)
            + " |"
        )

    lines.extend(
        [
            "",
            "## Audit",
            "",
            f"Passed {sum(1 for item in checks if item['passed'])}/{len(checks)} checks.",
            "",
        ]
    )
    for item in checks:
        state = "PASS" if item["passed"] else "FAIL"
        lines.append(
            f"- [{state}] {item['topology']} {item['chunks_per_source']} chunks: "
            f"{item['name']} ({item['detail']})"
        )
    (RESULTS / "summary.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run fixed-total-data chunk granularity sensitivity."
    )
    parser.add_argument("--run", action="store_true")
    parser.add_argument(
        "--deferred-only",
        action="store_true",
        help="Reuse existing Dedicated schedules and rerun only Deferred.",
    )
    parser.add_argument(
        "--chunks",
        nargs="+",
        type=int,
        default=[1, 2, 4],
    )
    parser.add_argument(
        "--topologies",
        nargs="+",
        choices=sorted(TOPOLOGIES),
        default=list(TOPOLOGIES),
    )
    args = parser.parse_args()
    chunk_counts = sorted(set(args.chunks))
    topologies = list(dict.fromkeys(args.topologies))

    if args.run:
        for topology in topologies:
            for chunks in chunk_counts:
                run_pair(
                    topology,
                    chunks,
                    run_dedicated=not args.deferred_only,
                )

    rows, comparisons, checks = summarize(topologies, chunk_counts)
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(rows, RESULTS / "metrics.csv")
    write_csv(comparisons, RESULTS / "paired_changes.csv")
    write_csv(checks, RESULTS / "audit.csv")
    write_markdown(rows, comparisons, checks)
    (RESULTS / "summary.json").write_text(
        json.dumps(
            {
                "total_data_gb_per_source": TOTAL_DATA_GB_PER_SOURCE,
                "rows": rows,
                "paired_changes": comparisons,
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    failed = [item for item in checks if not item["passed"]]
    print(
        f"Chunk sensitivity audit: {len(checks) - len(failed)}/{len(checks)} passed"
    )
    print(f"Wrote results to {RESULTS}")
    if failed:
        for item in failed:
            print(
                f"[FAIL] {item['topology']} {item['chunks_per_source']} chunks: "
                f"{item['name']}: {item['detail']}"
            )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
