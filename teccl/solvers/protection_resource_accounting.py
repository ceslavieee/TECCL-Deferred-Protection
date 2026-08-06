from typing import Dict, List, Mapping, Sequence, Tuple


Flow = Tuple[int, int, int, int, int]


def build_future_reservation_accounting(
    protection_flows: Sequence[Flow],
    chunk_completion_profile: Sequence[Dict[str, int]],
    occupancy_epochs_by_link: Mapping[Tuple[int, int], int] = None,
    epoch_duration_seconds: float = 1.0,
    chunk_size_gb: float = 1.0,
) -> Dict[str, object]:
    """Measure future backup commitments using occupied directed link-epochs.

    One flow start can occupy more than one epoch on a slow link. Its weight is
    ``beta(i,j) + 1``. At observation epoch ``t`` all of those occupied epochs
    remain a future commitment while ``t <= k`` and source chunk ``(s,c)`` has
    not completed normally.
    """
    occupancy_epochs_by_link = occupancy_epochs_by_link or {}

    def flow_weight(flow: Flow) -> int:
        weight = int(occupancy_epochs_by_link.get((flow[1], flow[2]), 1))
        if weight <= 0:
            raise ValueError(f"Invalid occupancy weight {weight} for link {flow[1]}->{flow[2]}")
        return weight

    completion_by_commodity = {
        (int(row["source"]), int(row["chunk"])): int(
            row["working_completion_epoch"]
        )
        for row in chunk_completion_profile
    }
    missing = sorted(
        {
            (source, chunk)
            for source, _, _, chunk, _ in protection_flows
            if (source, chunk) not in completion_by_commodity
        }
    )
    if missing:
        raise ValueError(
            "Missing working completion epochs for protected commodities: "
            + ", ".join(f"({source}, {chunk})" for source, chunk in missing)
        )

    last_completion = max(completion_by_commodity.values(), default=0)
    last_backup_epoch = max((flow[4] for flow in protection_flows), default=-1)
    last_observation = max(last_completion, last_backup_epoch + 1)
    profile: List[Dict[str, int]] = []

    for observation_epoch in range(last_observation + 1):
        active = 0
        released_before_slot = 0
        elapsed_before_completion = 0
        for flow in protection_flows:
            source, _, _, chunk, backup_epoch = flow
            weight = flow_weight(flow)
            completion_epoch = completion_by_commodity[(source, chunk)]
            if observation_epoch <= backup_epoch and observation_epoch < completion_epoch:
                active += weight
            if completion_epoch <= observation_epoch and completion_epoch <= backup_epoch:
                released_before_slot += weight
            if backup_epoch < observation_epoch and backup_epoch < completion_epoch:
                elapsed_before_completion += weight
        profile.append(
            {
                "observation_epoch": observation_epoch,
                "active_future_reserved_link_epochs": active,
                "released_before_scheduled_slot_link_epochs": released_before_slot,
                "scheduled_slot_elapsed_before_completion_link_epochs": elapsed_before_completion,
            }
        )

    released_link_epochs = sum(
        flow_weight(flow)
        for flow in protection_flows
        if completion_by_commodity[(flow[0], flow[3])] <= flow[4]
    )
    occupied_link_epochs = sum(flow_weight(flow) for flow in protection_flows)
    committed_link_epochs = occupied_link_epochs - released_link_epochs
    active_counts = [
        row["active_future_reserved_link_epochs"] for row in profile
    ]
    flow_count = len(protection_flows)
    epoch_duration_seconds = float(epoch_duration_seconds)
    chunk_size_gb = float(chunk_size_gb)
    link_weights = [
        {
            "link": f"{i}->{j}",
            "occupied_epochs_per_flow": int(weight),
        }
        for (i, j), weight in sorted(occupancy_epochs_by_link.items())
    ]

    return {
        "accounting_unit": "occupied directed link-epochs weighted by beta(i,j) + 1",
        "contingency_plan_link_epochs": occupied_link_epochs,
        "peak_future_reserved_link_epochs": max(active_counts, default=0),
        "future_reservation_holding_units": sum(active_counts),
        "released_before_scheduled_slot_link_epochs": released_link_epochs,
        "failure_free_committed_backup_link_epochs": committed_link_epochs,
        "failure_free_release_ratio": (
            released_link_epochs / occupied_link_epochs
            if occupied_link_epochs
            else 1.0
        ),
        "profile": profile,
        "chunk_working_completion_profile": list(chunk_completion_profile),
        "flow_occupancy_epochs_by_link": link_weights,
        "contingency_plan_transmissions": flow_count,
        "contingency_plan_payload_gb_links": flow_count * chunk_size_gb,
        "contingency_plan_occupied_link_seconds": (
            occupied_link_epochs * epoch_duration_seconds
        ),
        "future_reservation_holding_link_second_squared": (
            sum(active_counts) * epoch_duration_seconds * epoch_duration_seconds
        ),
        "released_before_scheduled_slot_link_seconds": (
            released_link_epochs * epoch_duration_seconds
        ),
        "failure_free_committed_backup_link_seconds": (
            committed_link_epochs * epoch_duration_seconds
        ),
        "epoch_duration_seconds": epoch_duration_seconds,
        "chunk_size_gb": chunk_size_gb,
    }


def add_common_resource_fields(
    schedule_json: Dict[str, object],
    accounting: Dict[str, object],
) -> None:
    schedule_json["14-Resource_Accounting_Unit"] = accounting["accounting_unit"]
    schedule_json["14a-Contingency_Plan_Link_Epochs"] = accounting[
        "contingency_plan_link_epochs"
    ]
    schedule_json["14b-Peak_Future_Reserved_Link_Epochs"] = accounting[
        "peak_future_reserved_link_epochs"
    ]
    schedule_json["14c-Future_Reservation_Holding_Units"] = accounting[
        "future_reservation_holding_units"
    ]
    schedule_json["14d-Released_Before_Scheduled_Slot_Link_Epochs"] = accounting[
        "released_before_scheduled_slot_link_epochs"
    ]
    schedule_json["14e-Failure_Free_Committed_Backup_Link_Epochs"] = accounting[
        "failure_free_committed_backup_link_epochs"
    ]
    schedule_json["14f-Failure_Free_Release_Ratio"] = accounting[
        "failure_free_release_ratio"
    ]
    schedule_json["14g-Future_Reservation_Profile"] = accounting["profile"]
    schedule_json["14h-Chunk_Working_Completion_Profile"] = accounting[
        "chunk_working_completion_profile"
    ]
    schedule_json["14i-Flow_Occupancy_Epochs_By_Link"] = accounting[
        "flow_occupancy_epochs_by_link"
    ]
    schedule_json["14j-Contingency_Plan_Transmissions"] = accounting[
        "contingency_plan_transmissions"
    ]
    schedule_json["14k-Contingency_Plan_Payload_GB_Links"] = accounting[
        "contingency_plan_payload_gb_links"
    ]
    schedule_json["14l-Contingency_Plan_Occupied_Link_Seconds"] = accounting[
        "contingency_plan_occupied_link_seconds"
    ]
    schedule_json[
        "14m-Future_Reservation_Holding_Link_Second_Squared"
    ] = accounting["future_reservation_holding_link_second_squared"]
    schedule_json[
        "14n-Released_Before_Scheduled_Slot_Link_Seconds"
    ] = accounting["released_before_scheduled_slot_link_seconds"]
    schedule_json[
        "14o-Failure_Free_Committed_Backup_Link_Seconds"
    ] = accounting["failure_free_committed_backup_link_seconds"]
    schedule_json["14p-Accounting_Epoch_Duration_Seconds"] = accounting[
        "epoch_duration_seconds"
    ]
    schedule_json["14q-Accounting_Chunk_Size_GB"] = accounting["chunk_size_gb"]
