# Directed-link failure execution

A failure occurs at the beginning of the listed zero-based epoch. Working transmissions on that directed link at or after the failure are removed, and the remaining fixed working schedule is replayed.
A `(source, chunk)` is affected only when it can no longer reach every AllGather endpoint. Only affected units activate a backup plan.

Provisioned resource counts each strategy's complete backup plan. Activated resource uses the same affected-only rule for both strategies: only backup plans belonging to affected `(source, chunk)` units count.

## Overall

| topology | strategy | link-time scenarios | affected scenarios | mean affected | provisioned backup link-s | mean activated link-s (affected only) | worst activated link-s | mean completion s (affected only) | worst completion s |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| DCN4WAN | Dedicated Protection | 128 | 72 | 0.69 | 34.00 | 10.56 | 18.00 | 8.67 | 9.00 |
| DCN4WAN | Preplanned Deferred Protection | 128 | 72 | 0.69 | 32.00 | 9.78 | 16.00 | 24.44 | 27.00 |
| InterDC8 | Dedicated Protection | 264 | 154 | 1.06 | 93.00 | 21.25 | 48.00 | 17.19 | 18.00 |
| InterDC8 | Preplanned Deferred Protection | 264 | 154 | 1.06 | 87.00 | 19.92 | 45.00 | 33.49 | 35.00 |

## By failure time

| topology | failure epoch | strategy | affected link scenarios | mean affected | mean activated link-s (affected only) | mean completion s (affected only) | worst completion s |
|---|---:|---|---:|---:|---:|---:|---:|
| DCN4WAN | 0 | Dedicated Protection | 16 | 1.50 | 12.75 | 8.75 | 9.00 |
| DCN4WAN | 0 | Preplanned Deferred Protection | 16 | 1.50 | 12.00 | 24.75 | 27.00 |
| DCN4WAN | 1 | Dedicated Protection | 13 | 1.06 | 11.23 | 8.69 | 9.00 |
| DCN4WAN | 1 | Preplanned Deferred Protection | 13 | 1.06 | 10.46 | 24.54 | 27.00 |
| DCN4WAN | 2 | Dedicated Protection | 13 | 1.06 | 11.23 | 8.69 | 9.00 |
| DCN4WAN | 2 | Preplanned Deferred Protection | 13 | 1.06 | 10.46 | 24.54 | 27.00 |
| DCN4WAN | 3 | Dedicated Protection | 10 | 0.62 | 8.80 | 8.60 | 9.00 |
| DCN4WAN | 3 | Preplanned Deferred Protection | 10 | 0.62 | 8.00 | 24.20 | 27.00 |
| DCN4WAN | 4 | Dedicated Protection | 10 | 0.62 | 8.80 | 8.60 | 9.00 |
| DCN4WAN | 4 | Preplanned Deferred Protection | 10 | 0.62 | 8.00 | 24.20 | 27.00 |
| DCN4WAN | 5 | Dedicated Protection | 5 | 0.31 | 8.80 | 8.60 | 9.00 |
| DCN4WAN | 5 | Preplanned Deferred Protection | 5 | 0.31 | 8.00 | 24.20 | 27.00 |
| DCN4WAN | 6 | Dedicated Protection | 5 | 0.31 | 8.80 | 8.60 | 9.00 |
| DCN4WAN | 6 | Preplanned Deferred Protection | 5 | 0.31 | 8.00 | 24.20 | 27.00 |
| DCN4WAN | 7 | Dedicated Protection | 0 | 0.00 | 0.00 | - | 8.00 |
| DCN4WAN | 7 | Preplanned Deferred Protection | 0 | 0.00 | 0.00 | - | 8.00 |
| InterDC8 | 0 | Dedicated Protection | 24 | 2.33 | 27.12 | 17.58 | 18.00 |
| InterDC8 | 0 | Preplanned Deferred Protection | 24 | 2.33 | 25.38 | 33.75 | 35.00 |
| InterDC8 | 1 | Dedicated Protection | 21 | 1.75 | 23.24 | 17.19 | 18.00 |
| InterDC8 | 1 | Preplanned Deferred Protection | 21 | 1.75 | 21.86 | 33.57 | 35.00 |
| InterDC8 | 2 | Dedicated Protection | 21 | 1.75 | 23.24 | 17.19 | 18.00 |
| InterDC8 | 2 | Preplanned Deferred Protection | 21 | 1.75 | 21.86 | 33.57 | 35.00 |
| InterDC8 | 3 | Dedicated Protection | 18 | 1.46 | 22.56 | 17.28 | 18.00 |
| InterDC8 | 3 | Preplanned Deferred Protection | 18 | 1.46 | 21.11 | 33.56 | 35.00 |
| InterDC8 | 4 | Dedicated Protection | 18 | 1.42 | 21.89 | 17.28 | 18.00 |
| InterDC8 | 4 | Preplanned Deferred Protection | 18 | 1.42 | 20.50 | 33.56 | 35.00 |
| InterDC8 | 5 | Dedicated Protection | 17 | 1.21 | 19.88 | 17.00 | 18.00 |
| InterDC8 | 5 | Preplanned Deferred Protection | 17 | 1.21 | 18.65 | 33.53 | 35.00 |
| InterDC8 | 6 | Dedicated Protection | 16 | 0.83 | 14.69 | 16.75 | 18.00 |
| InterDC8 | 6 | Preplanned Deferred Protection | 16 | 0.83 | 13.88 | 33.12 | 35.00 |
| InterDC8 | 7 | Dedicated Protection | 12 | 0.62 | 14.83 | 17.08 | 18.00 |
| InterDC8 | 7 | Preplanned Deferred Protection | 12 | 0.62 | 13.83 | 33.25 | 35.00 |
| InterDC8 | 8 | Dedicated Protection | 4 | 0.21 | 14.75 | 17.00 | 18.00 |
| InterDC8 | 8 | Preplanned Deferred Protection | 4 | 0.21 | 13.50 | 32.75 | 35.00 |
| InterDC8 | 9 | Dedicated Protection | 3 | 0.12 | 11.67 | 17.00 | 18.00 |
| InterDC8 | 9 | Preplanned Deferred Protection | 3 | 0.12 | 10.67 | 33.00 | 35.00 |
| InterDC8 | 10 | Dedicated Protection | 0 | 0.00 | 0.00 | - | 11.00 |
| InterDC8 | 10 | Preplanned Deferred Protection | 0 | 0.00 | 0.00 | - | 11.00 |

## Audit

Passed 12/12 checks.

- [PASS] DCN4WAN: paired working schedules are identical (dedicated=24, deferred=24)
- [PASS] DCN4WAN: paired working completion profiles are identical (commodities=4)
- [PASS] DCN4WAN: paired link occupancy maps are identical (links=32)
- [PASS] DCN4WAN: working schedule replay has no causality drops (drops=0)
- [PASS] DCN4WAN: working replay reproduces reported completion profile (reported={(0, 0): 8, (1, 0): 8, (2, 0): 8, (3, 0): 8}, replayed={(1, 0): 8, (2, 0): 8, (3, 0): 8, (0, 0): 8})
- [PASS] DCN4WAN: every affected commodity is recovered by both strategies (scenarios=256, failed=0)
- [PASS] InterDC8: paired working schedules are identical (dedicated=56, deferred=56)
- [PASS] InterDC8: paired working completion profiles are identical (commodities=8)
- [PASS] InterDC8: paired link occupancy maps are identical (links=26)
- [PASS] InterDC8: working schedule replay has no causality drops (drops=0)
- [PASS] InterDC8: working replay reproduces reported completion profile (reported={(0, 0): 11, (1, 0): 10, (2, 0): 11, (3, 0): 11, (4, 0): 11, (5, 0): 11, (6, 0): 10, (7, 0): 11}, replayed={(6, 0): 10, (1, 0): 10, (4, 0): 11, (0, 0): 11, (7, 0): 11, (2, 0): 11, (3, 0): 11, (5, 0): 11})
- [PASS] InterDC8: every affected commodity is recovered by both strategies (scenarios=528, failed=0)
