# Physical tree-pair detection-delay boundary

| Topology | Policy | Maximum delay | Seconds | Next delay | Infeasible witness τ | Certified |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| DCN4WAN | DPP | 6 | 12.0 | 7 | 3 | PASS |
| DCN4WAN | DDPP | 21 | 42.0 | 22 | 3 | PASS |
| InterDC8 | DPP | 2 | 4.0 | 3 | 5 | PASS |
| InterDC8 | DDPP | 22 | 44.0 | 23 | 5 | PASS |

Each row fixes the exported working schedule and the complete reservation schedule. The boundary is feasible for every affected failure epoch under all directed-link scenarios; boundary + 1 is proved infeasible at an analytically identified witness.

Epochs and reservation labels are zero-based in this report. One epoch is 2 seconds.
