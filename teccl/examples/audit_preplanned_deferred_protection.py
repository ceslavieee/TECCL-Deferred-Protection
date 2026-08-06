import argparse
import json
import math
import re
from pathlib import Path
from typing import Dict, List, Tuple


FLOW_RE = re.compile(
    r"Chunk (?P<chunk>\d+) from (?P<source>\d+) traveled over "
    r"(?P<link>\d+->\d+) in epoch (?P<epoch>\d+)"
)


def _parse_flows(rows: List[str]) -> List[Tuple[int, int, str, int]]:
    flows = []
    for row in rows:
        match = FLOW_RE.fullmatch(row)
        if not match:
            raise ValueError(f"Could not parse flow row: {row}")
        flows.append(
            (
                int(match.group("source")),
                int(match.group("chunk")),
                match.group("link"),
                int(match.group("epoch")),
            )
        )
    return flows


def audit(schedule_path: Path) -> List[Dict[str, object]]:
    data = json.loads(schedule_path.read_text())
    working = _parse_flows(data.get("10-Working_Flows", []))
    backup = _parse_flows(data.get("11-Protection_Flows", []))
    release_profile = data.get("12-Reservation_Release_Profile", [])
    checks = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    add(
        "preplanned timing mode",
        data.get("7m-Deferred_Timing_Mode") == "PREPLANNED",
        str(data.get("7m-Deferred_Timing_Mode")),
    )
    add(
        "solver has a solution",
        int(data.get("Solver_Solution_Count", 0)) > 0,
        f"status={data.get('Solver_Status_Name')}, solutions={data.get('Solver_Solution_Count')}",
    )
    add(
        "preplanned mode does not depend on realized failure scenarios",
        not data.get("9a-Failure_Scenarios"),
        f"scenarios={data.get('9a-Failure_Scenarios')}",
    )

    fixed_working_path = data.get("7l-Fixed_Working_Schedule")
    if fixed_working_path:
        fixed_working = json.loads(Path(fixed_working_path).read_text())
        reference_rows = (
            fixed_working.get("11-Working_Flows")
            or fixed_working.get("10-Working_Flows")
            or fixed_working.get("7a-Raw_Flows")
            or fixed_working.get("7-Flows")
            or []
        )
        reference_working = set(_parse_flows(reference_rows))
        actual_working = set(working)
        add(
            "working flow matches fixed reference schedule",
            actual_working == reference_working,
            (
                f"missing={len(reference_working - actual_working)}, "
                f"extra={len(actual_working - reference_working)}"
            ),
        )

    working_deadline = int(data["7-Working_Deadline_Epoch"]) - 1
    activation_epoch = int(data["7a-Deferred_Activation_Epoch"]) - 1
    max_working_epoch = max((row[3] for row in working), default=-1)
    min_backup_epoch = min((row[3] for row in backup), default=activation_epoch)
    add(
        "working flow respects working deadline",
        max_working_epoch <= working_deadline,
        f"max_working_epoch={max_working_epoch}, deadline={working_deadline}",
    )
    add(
        "backup plan starts after working window",
        activation_epoch > working_deadline and min_backup_epoch >= activation_epoch,
        (
            f"working_deadline={working_deadline}, activation={activation_epoch}, "
            f"min_backup_epoch={min_backup_epoch}"
        ),
    )

    working_links = {}
    backup_links = {}
    for source, chunk, link, _ in working:
        working_links.setdefault((source, chunk), set()).add(link)
    for source, chunk, link, _ in backup:
        backup_links.setdefault((source, chunk), set()).add(link)
    overlaps = {
        commodity: sorted(links & backup_links.get(commodity, set()))
        for commodity, links in working_links.items()
        if links & backup_links.get(commodity, set())
    }
    add(
        "working and backup plans are directed-link disjoint",
        not overlaps,
        f"overlaps={overlaps}",
    )

    active_counts = [int(row["active_reservations"]) for row in release_profile]
    monotone = all(
        active_counts[index + 1] <= active_counts[index]
        for index in range(len(active_counts) - 1)
    )
    add(
        "flow-start reservation release is monotone",
        bool(active_counts) and monotone,
        f"active_flow_starts={active_counts}",
    )
    initial_count = int(data.get("12b-Initial_Backup_Reservation_Count", -1))
    final_count = int(data.get("12c-Final_Backup_Reservation_Count", -1))
    holding_units = int(data.get("12d-Reservation_Holding_Units", -1))
    add(
        "flow-start reservation summary matches model profile",
        (
            bool(active_counts)
            and initial_count == active_counts[0]
            and final_count == active_counts[-1]
            and holding_units == sum(active_counts)
        ),
        (
            f"initial={initial_count}, final={final_count}, "
            f"flow_start_holding={holding_units}, "
            f"profile_sum={sum(active_counts)}"
        ),
    )
    add(
        "failure-free completion releases all backup reservations",
        final_count == 0,
        f"final={final_count}",
    )
    add(
        "initial reservations cover the complete contingency plan",
        initial_count == len(backup),
        f"initial={initial_count}, backup_transmissions={len(backup)}",
    )

    occupied_profile = data.get("14g-Future_Reservation_Profile", [])
    occupied_active = [
        int(row["active_future_reserved_link_epochs"])
        for row in occupied_profile
    ]
    occupied_monotone = all(
        occupied_active[index + 1] <= occupied_active[index]
        for index in range(len(occupied_active) - 1)
    )
    add(
        "beta-weighted occupied reservation release is monotone",
        bool(occupied_active) and occupied_monotone,
        f"active_occupied_link_epochs={occupied_active}",
    )
    occupied_holding = int(
        data.get("14c-Future_Reservation_Holding_Units", -1)
    )
    epoch_duration = float(
        data.get("14p-Accounting_Epoch_Duration_Seconds", 0.0)
    )
    physical_holding = float(
        data.get(
            "14m-Future_Reservation_Holding_Link_Second_Squared",
            -1.0,
        )
    )
    add(
        "beta-weighted physical accounting matches occupied profile",
        (
            bool(occupied_active)
            and occupied_holding == sum(occupied_active)
            and math.isclose(
                physical_holding,
                occupied_holding * epoch_duration**2,
            )
        ),
        (
            f"occupied_holding={occupied_holding}, "
            f"profile_sum={sum(occupied_active)}, "
            f"holding_link_second_squared={physical_holding}"
        ),
    )
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit one preplanned deferred-protection schedule."
    )
    parser.add_argument(
        "schedule",
        nargs="?",
        default=(
            "teccl/examples/schedules/"
            "dcn4wan_preplanned_deferred_protection_schedule.json"
        ),
    )
    args = parser.parse_args()
    checks = audit(Path(args.schedule))
    failed = [check for check in checks if not check["passed"]]
    for check in checks:
        state = "PASS" if check["passed"] else "FAIL"
        print(f"[{state}] {check['name']}: {check['detail']}")
    if failed:
        raise SystemExit(1)
    print(f"Preplanned deferred-protection audit: PASS ({len(checks)} checks)")


if __name__ == "__main__":
    main()
