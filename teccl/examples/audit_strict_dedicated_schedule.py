"""Audit invariants exported by a scenario-robust DPP or DDPP schedule."""

import argparse
from collections import defaultdict
import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple


RESERVATION_RE = re.compile(
    r"Chunk (?P<c>\d+) from (?P<s>\d+) reserves "
    r"(?P<i>\d+)->(?P<j>\d+) in epoch (?P<k>\d+)"
)
RECOVERY_RE = re.compile(
    r"Scenario (?P<scenario>\d+): chunk (?P<c>\d+) from (?P<s>\d+) "
    r"recovered over (?P<i>\d+)->(?P<j>\d+) in epoch (?P<k>\d+)"
)
WORKING_RE = re.compile(
    r"Chunk (?P<c>\d+) from (?P<s>\d+) traveled over "
    r"(?P<i>\d+)->(?P<j>\d+) in epoch (?P<k>\d+)"
)
Reservation = Tuple[int, int, int, int, int]
Recovery = Tuple[int, int, int, int, int, int]


def parse_reservations(data: Dict) -> Set[Reservation]:
    reservations = set()
    for row in data.get("11b-Reserved_Backup_Slots", []):
        match = RESERVATION_RE.fullmatch(str(row))
        if not match:
            raise ValueError(f"Could not parse reservation: {row}")
        reservations.add(
            (
                int(match.group("s")),
                int(match.group("i")),
                int(match.group("j")),
                int(match.group("c")),
                int(match.group("k")),
            )
        )
    return reservations


def parse_recovery(data: Dict) -> List[Recovery]:
    recovery = []
    for row in data.get("11a-Recovery_Flows_By_Scenario", []):
        match = RECOVERY_RE.fullmatch(str(row))
        if not match:
            raise ValueError(f"Could not parse recovery flow: {row}")
        recovery.append(
            (
                int(match.group("scenario")),
                int(match.group("s")),
                int(match.group("i")),
                int(match.group("j")),
                int(match.group("c")),
                int(match.group("k")),
            )
        )
    return recovery


def parse_working(data: Dict) -> Set[Reservation]:
    working = set()
    for row in data.get("10-Working_Flows", []):
        match = WORKING_RE.fullmatch(str(row))
        if not match:
            raise ValueError(f"Could not parse working flow: {row}")
        working.add(
            (
                int(match.group("s")),
                int(match.group("i")),
                int(match.group("j")),
                int(match.group("c")),
                int(match.group("k")),
            )
        )
    return working


def robust_reservation_timing_violations(
    reservations: Set[Reservation],
    completion_by_commodity: Dict[Tuple[int, int], int],
    detection_delay_epochs: int,
) -> List[Reservation]:
    """Return reservations too early for the all-failure-time construction."""

    return [
        reservation
        for reservation in reservations
        if reservation[4]
        < completion_by_commodity.get((reservation[0], reservation[3]), 0)
        + detection_delay_epochs
        - 1
    ]


def tree_structure_violations(flows: Set[Reservation]) -> List[Tuple]:
    """Return parent/root/reachability violations for serialized link sets."""

    by_commodity = defaultdict(set)
    for source, i, j, chunk, _ in flows:
        by_commodity[(source, chunk)].add((i, j))
    violations = []
    for (source, chunk), edges in by_commodity.items():
        parents = defaultdict(set)
        adjacency = defaultdict(set)
        nodes = {source}
        for i, j in edges:
            parents[j].add(i)
            adjacency[i].add(j)
            nodes.update((i, j))
        if parents[source]:
            violations.append((source, chunk, "source_has_parent", sorted(parents[source])))
        for node, incoming in parents.items():
            if node != source and len(incoming) > 1:
                violations.append((source, chunk, "multiple_parents", node, sorted(incoming)))
        reachable = {source}
        frontier = [source]
        while frontier:
            node = frontier.pop()
            for successor in adjacency[node].difference(reachable):
                reachable.add(successor)
                frontier.append(successor)
        disconnected = sorted(nodes.difference(reachable))
        if disconnected:
            violations.append((source, chunk, "not_rooted_at_source", disconnected))
    return violations


def missing_tree_terminals(
    flows: Set[Reservation],
    commodities: Set[Tuple[int, int]],
    terminals: Set[int],
) -> List[Tuple]:
    """Return AllGather terminals absent from each rooted commodity tree."""

    nodes_by_commodity = defaultdict(set)
    for source, i, j, chunk, _ in flows:
        nodes_by_commodity[(source, chunk)].update((source, i, j))
    violations = []
    for source, chunk in commodities:
        present = nodes_by_commodity[(source, chunk)] | {source}
        missing = sorted(terminals - present)
        if missing:
            violations.append((source, chunk, "missing_terminals", missing))
    return violations


def audit(
    data: Dict,
    require_nonempty: bool = False,
    require_tree_pair: bool = False,
) -> List[Dict[str, object]]:
    working = parse_working(data)
    reservations = parse_reservations(data)
    recovery = parse_recovery(data)
    failed_links = [
        tuple(int(node) for node in label.split("->"))
        for label in data.get("9a-Failure_Scenarios", [])
    ]
    activation = int(data.get("7g-Recovery_Start_Epoch", 0)) - 1
    reservation_start = int(data.get("7w-Reservation_Start_Epoch", 1)) - 1
    reservation_deadline = int(
        data.get(
            "7x-Reservation_End_Epoch",
            data.get("7p-DPP_Reservation_Deadline_Epoch", 0),
        )
    ) - 1
    protected_deadline = int(
        data.get(
            "7s-Strict_Protected_Completion_Deadline_Epoch",
            reservation_deadline + 1,
        )
    )
    protected_completion = int(data.get("9f-Actual_Protected_Completion_Epoch", 0))
    service_deadline = int(data.get("8-Final_Deadline_Epoch", 0))
    occupancy_by_link = {
        tuple(int(node) for node in str(row["link"]).split("->")): int(
            row["occupied_epochs_per_flow"]
        )
        for row in data.get("14i-Flow_Occupancy_Epochs_By_Link", [])
    }
    deadline_occupancy_violations = []
    for kind, rows, pattern in (
        ("working", data.get("10-Working_Flows", []), WORKING_RE),
        (
            "reservation",
            data.get("11b-Reserved_Backup_Slots", []),
            RESERVATION_RE,
        ),
    ):
        for row in rows:
            match = pattern.fullmatch(str(row))
            if not match:
                raise ValueError(f"Could not parse {kind} flow: {row}")
            link = (int(match.group("i")), int(match.group("j")))
            start = int(match.group("k"))
            occupancy = occupancy_by_link.get(link)
            if occupancy is None or occupancy <= 0:
                raise ValueError(f"Missing occupancy for {kind} link {link}")
            if start + occupancy > service_deadline:
                deadline_occupancy_violations.append(
                    (kind, link[0], link[1], start, occupancy)
                )
    summaries = data.get("9b-Failure_Scenario_Summary", {})
    affected_scenarios = sum(
        bool(summary.get("affected_demands")) for summary in summaries.values()
    )

    missing_reservation = [
        flow
        for flow in recovery
        if (flow[1], flow[2], flow[3], flow[4], flow[5]) not in reservations
    ]
    early = [flow for flow in recovery if flow[5] < activation]
    outside_reservations = [
        reservation
        for reservation in reservations
        if not reservation_start <= reservation[4] <= reservation_deadline
    ]
    failed_link_use = [
        flow
        for flow in recovery
        if flow[0] >= len(failed_links)
        or (flow[2], flow[3]) == failed_links[flow[0]]
    ]
    unprotected = [
        (scenario, demand)
        for scenario, summary in summaries.items()
        for demand in summary.get("unprotected_demands", [])
    ]
    tree_pair_claimed = data.get("7aa-Multicast_Tree_Pair_Enforced") is True
    failure_time_robust_claimed = (
        data.get("7ab-Failure_Time_Robust_Reservation_Enforced") is True
    )
    completion_by_commodity = {
        (int(row["source"]), int(row["chunk"])): int(
            row["working_completion_epoch"]
        )
        for row in data.get("14h-Chunk_Working_Completion_Profile", [])
    }
    detection_delay = int(data.get("7f-Detection_Delay_Epochs", 0))
    early_robust_reservations = robust_reservation_timing_violations(
        reservations,
        completion_by_commodity,
        detection_delay,
    )
    working_commodities = {(source, chunk) for source, _, _, chunk, _ in working}
    allgather_terminals = {source for source, _ in working_commodities}
    tree_violations = {
        "working": tree_structure_violations(working)
        + missing_tree_terminals(
            working,
            working_commodities,
            allgather_terminals,
        ),
        "reservation": tree_structure_violations(reservations)
        + missing_tree_terminals(
            reservations,
            working_commodities,
            allgather_terminals,
        ),
    }
    tree_structure_safe = not any(tree_violations.values())

    return [
        {
            "name": "scenario-robust semantic marker",
            "passed": data.get("7m-Protection_Semantics")
            in {
                "strict DPP 1:1 fixed-time subproblem",
                "scenario-robust DPP fixed-time subproblem",
                "scenario-robust DDPP fixed-time subproblem",
            },
        },
        {
            "name": "failure-time scope is explicit",
            "passed": (
                data.get("7q-Failure_Time_Known_To_Optimizer") is True
                and (
                    data.get("7r-Robust_Across_Unknown_Failure_Time") is False
                    or failure_time_robust_claimed
                )
            ),
        },
        {
            "name": "no failure-free backup data",
            "passed": (
                data.get("7n-Normal_Operation_Backup_Data_Transmissions") == 0
                and not data.get("11-Protection_Flows")
            ),
        },
        {
            "name": "nonempty recovery exercised",
            "passed": not require_nonempty or bool(recovery and reservations),
            "detail": (
                f"reservations={len(reservations)}, recovery={len(recovery)}, "
                f"affected_scenarios={affected_scenarios}"
            ),
        },
        {
            "name": "recovery starts after detection",
            "passed": not early,
            "detail": f"activation={activation}, violations={early[:5]}",
        },
        {
            "name": "reservation stays inside declared policy window",
            "passed": (
                reservation_deadline >= reservation_start
                and not outside_reservations
            ),
            "detail": (
                f"window={reservation_start}..{reservation_deadline}, "
                f"violations={outside_reservations[:5]}"
            ),
        },
        {
            "name": "robust reservations follow commodity completion",
            "passed": (
                not failure_time_robust_claimed
                or (
                    bool(completion_by_commodity)
                    and not early_robust_reservations
                )
            ),
            "detail": f"violations={early_robust_reservations[:5]}",
        },
        {
            "name": "protected completion stays inside declared deadline",
            "passed": (
                protected_completion > 0
                and protected_completion <= protected_deadline
            ),
            "detail": (
                f"completion={protected_completion}, "
                f"deadline={protected_deadline}"
            ),
        },
        {
            "name": "full link occupancy stays inside service deadline",
            "passed": service_deadline > 0 and not deadline_occupancy_violations,
            "detail": (
                f"deadline={service_deadline}, "
                f"violations={deadline_occupancy_violations[:5]}"
            ),
        },
        {
            "name": "fixed multicast working/backup tree pair",
            "passed": (
                (not require_tree_pair and not tree_pair_claimed)
                or (tree_pair_claimed and tree_structure_safe)
            ),
            "detail": (
                f"required={require_tree_pair}, claimed={tree_pair_claimed}, "
                f"violations={tree_violations}"
            ),
        },
        {
            "name": "recovery is bound to reservation",
            "passed": not missing_reservation,
            "detail": f"violations={missing_reservation[:5]}",
        },
        {
            "name": "recovery avoids failed link",
            "passed": not failed_link_use,
            "detail": f"violations={failed_link_use[:5]}",
        },
        {
            "name": "all affected demands are protected",
            "passed": not unprotected,
            "detail": f"violations={unprotected[:5]}",
        },
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schedule", type=Path)
    parser.add_argument("--require-nonempty", action="store_true")
    parser.add_argument("--require-tree-pair", action="store_true")
    args = parser.parse_args()
    checks = audit(
        json.loads(args.schedule.read_text()),
        args.require_nonempty,
        args.require_tree_pair,
    )
    for check in checks:
        marker = "PASS" if check["passed"] else "FAIL"
        detail = f": {check['detail']}" if check.get("detail") else ""
        print(f"[{marker}] {check['name']}{detail}")
    if any(not check["passed"] for check in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
