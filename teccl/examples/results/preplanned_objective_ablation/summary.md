# Preplanned holding-objective ablation

Both objective variants fix the same Dedicated working schedule and use identical protection constraints.
Only the highest-priority Preplanned Deferred holding objective changes.

| topology | objective_mode | optimization_quality | normal_completion_seconds | protected_completion_seconds | flow_start_holding_units | beta_weighted_holding_link_second_squared | contingency_plan_transmissions | contingency_plan_occupied_link_seconds |
|---|---|---|---|---|---|---|---|---|
| DCN4WAN | LEGACY_FLOW_START | OPTIMAL | 8.0000 | 27.0000 | 128 | 256.0000 | 16 | 32.0000 |
| DCN4WAN | BETA_WEIGHTED_HOLDING | OPTIMAL | 8.0000 | 27.0000 | 128 | 256.0000 | 16 | 32.0000 |
| InterDC8 | LEGACY_FLOW_START | OPTIMAL | 11.0000 | 31.0000 | 602 | 979.0000 | 56 | 91.0000 |
| InterDC8 | BETA_WEIGHTED_HOLDING | OPTIMAL | 11.0000 | 35.0000 | 602 | 936.0000 | 56 | 87.0000 |

## Paired changes

| topology | comparison_quality | physical_holding_reduction_percent | occupied_link_seconds_reduction_percent | protected_completion_delta_seconds | transmission_count_delta |
|---|---|---|---|---|---|
| DCN4WAN | OPTIMAL_PAIR | 0.0000 | 0.0000 | 0.0000 | 0 |
| InterDC8 | OPTIMAL_PAIR | 4.3922 | 4.3956 | 4.0000 | 0 |

## Audit

Passed 16/16 checks.

- [PASS] DCN4WAN: Legacy solver returned a solution (status=OPTIMAL, gap=None)
- [PASS] DCN4WAN: Legacy accounting recomputes exactly (holding_link_second_squared=256.0)
- [PASS] DCN4WAN: Legacy is directed-link disjoint (working=24, backup=16)
- [PASS] DCN4WAN: Beta weighted solver returned a solution (status=OPTIMAL, gap=None)
- [PASS] DCN4WAN: Beta weighted accounting recomputes exactly (holding_link_second_squared=256.0)
- [PASS] DCN4WAN: Beta weighted is directed-link disjoint (working=24, backup=16)
- [PASS] DCN4WAN: objective variants use identical working flows (legacy=24, weighted=24)
- [PASS] DCN4WAN: weighted schedule reports the selected primary objective (label=beta-weighted occupied link-epoch holding, value=256, accounting=256.0)
- [PASS] InterDC8: Legacy solver returned a solution (status=OPTIMAL, gap=None)
- [PASS] InterDC8: Legacy accounting recomputes exactly (holding_link_second_squared=979.0)
- [PASS] InterDC8: Legacy is directed-link disjoint (working=56, backup=56)
- [PASS] InterDC8: Beta weighted solver returned a solution (status=OPTIMAL, gap=None)
- [PASS] InterDC8: Beta weighted accounting recomputes exactly (holding_link_second_squared=936.0)
- [PASS] InterDC8: Beta weighted is directed-link disjoint (working=56, backup=56)
- [PASS] InterDC8: objective variants use identical working flows (legacy=56, weighted=56)
- [PASS] InterDC8: weighted schedule reports the selected primary objective (label=beta-weighted occupied link-epoch holding, value=936, accounting=936.0)
