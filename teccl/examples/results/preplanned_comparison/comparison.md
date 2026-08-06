# Preplanned protection comparison

Both protected results use the exact same working flows.
Dedicated first generates a protectable working reference; Preplanned Deferred then fixes those flows exactly.
An `OPTIMAL_PAIR` is proven optimal to the configured solver tolerance. A `FEASIBLE_TIME_LIMIT_PAIR` passes every exact constraint audit but is not proven optimal.
The audit establishes fairness and feasibility; it does not replace an optimality proof.

| topology | strategy | primary_holding_objective | optimization_quality | solver_status | solver_mip_gap | normal_completion_epoch | protected_completion_epoch | normal_completion_seconds | protected_completion_seconds | contingency_plan_transmissions | contingency_plan_link_epochs | contingency_plan_payload_gb_links | contingency_plan_occupied_link_seconds | peak_future_reserved_link_epochs | future_reservation_holding_link_second_squared | failure_free_committed_backup_link_seconds |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| DCN4WAN | Dedicated Protection | not applicable | OPTIMAL | OPTIMAL | 0.000 | 8.000 | 9.000 | 8.000 | 9.000 | 19 | 34 | 1900.000 | 34.000 | 34 | 146.000 | 34.000 |
| DCN4WAN | Preplanned Deferred Protection | beta-weighted occupied link-epoch holding | OPTIMAL | OPTIMAL | - | 8.000 | 27.000 | 8.000 | 27.000 | 16 | 32 | 1600.000 | 32.000 | 32 | 256.000 | 0.000 |
| InterDC8 | Dedicated Protection | not applicable | FEASIBLE_TIME_LIMIT | TIME_LIMIT | 0.008 | 11.000 | 18.000 | 11.000 | 18.000 | 56 | 93 | 5600.000 | 93.000 | 93 | 654.000 | 66.000 |
| InterDC8 | Preplanned Deferred Protection | beta-weighted occupied link-epoch holding | OPTIMAL | OPTIMAL | - | 11.000 | 35.000 | 11.000 | 35.000 | 56 | 87 | 5600.000 | 87.000 | 87 | 936.000 | 0.000 |

## Paired changes

| topology | quality | normal delta (s) | protected delay (s) | occupied-time reduction (%) | physical holding reduction (%) | committed-slot reduction (%) |
|---|---|---:|---:|---:|---:|---:|
| DCN4WAN | OPTIMAL_PAIR | 0.000 | 18.000 | 5.882 | -75.342 | 100.000 |
| InterDC8 | FEASIBLE_TIME_LIMIT_PAIR | 0.000 | 17.000 | 6.452 | -43.119 | 100.000 |

## Audit

Passed 24/24 checks.

- [PASS] DCN4WAN: Dedicated solver returned a solution (status=OPTIMAL, solutions=10)
- [PASS] DCN4WAN: Preplanned Deferred solver returned a solution (status=OPTIMAL, solutions=7)
- [PASS] DCN4WAN: Preplanned Deferred working schedule equals Dedicated reference (dedicated=24, deferred=24)
- [PASS] DCN4WAN: Preplanned Deferred uses beta-weighted holding objective (beta-weighted occupied link-epoch holding)
- [PASS] DCN4WAN: Dedicated contingency plan covers every working commodity (working_commodities=4, backup_commodities=4)
- [PASS] DCN4WAN: Dedicated working/backup directed-link disjointness (working=24, backup=19)
- [PASS] DCN4WAN: Dedicated common resource accounting is reproducible (holding=146)
- [PASS] DCN4WAN: Preplanned Deferred contingency plan covers every working commodity (working_commodities=4, backup_commodities=4)
- [PASS] DCN4WAN: Preplanned Deferred working/backup directed-link disjointness (working=24, backup=16)
- [PASS] DCN4WAN: Preplanned Deferred common resource accounting is reproducible (holding=256)
- [PASS] DCN4WAN: Preplanned Deferred backup starts after working traffic (max_working=6, min_backup=15)
- [PASS] DCN4WAN: Preplanned Deferred releases every future slot in failure-free execution (ratio=1.0)
- [PASS] InterDC8: Dedicated solver returned a solution (status=TIME_LIMIT, solutions=10)
- [PASS] InterDC8: Preplanned Deferred solver returned a solution (status=OPTIMAL, solutions=7)
- [PASS] InterDC8: Preplanned Deferred working schedule equals Dedicated reference (dedicated=56, deferred=56)
- [PASS] InterDC8: Preplanned Deferred uses beta-weighted holding objective (beta-weighted occupied link-epoch holding)
- [PASS] InterDC8: Dedicated contingency plan covers every working commodity (working_commodities=8, backup_commodities=8)
- [PASS] InterDC8: Dedicated working/backup directed-link disjointness (working=56, backup=56)
- [PASS] InterDC8: Dedicated common resource accounting is reproducible (holding=654)
- [PASS] InterDC8: Preplanned Deferred contingency plan covers every working commodity (working_commodities=8, backup_commodities=8)
- [PASS] InterDC8: Preplanned Deferred working/backup directed-link disjointness (working=56, backup=56)
- [PASS] InterDC8: Preplanned Deferred common resource accounting is reproducible (holding=936)
- [PASS] InterDC8: Preplanned Deferred backup starts after working traffic (max_working=9, min_backup=16)
- [PASS] InterDC8: Preplanned Deferred releases every future slot in failure-free execution (ratio=1.0)
