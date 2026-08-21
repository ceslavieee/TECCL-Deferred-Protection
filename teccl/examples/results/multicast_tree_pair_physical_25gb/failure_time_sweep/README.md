# Physical tree-pair failure-time sweep

| Topology | Policy | Failure epoch | Status | Affected scenarios | Affected demands | Recovery flows | Audit |
| --- | --- | ---: | --- | ---: | ---: | ---: | --- |
| DCN4WAN | DPP | 0 | OPTIMAL | 24 | 32 | 96 | PASS |
| DCN4WAN | DPP | 1 | OPTIMAL | 16 | 20 | 64 | PASS |
| DCN4WAN | DPP | 2 | OPTIMAL | 12 | 12 | 40 | PASS |
| DCN4WAN | DPP | 3 | OPTIMAL | 4 | 4 | 16 | PASS |
| DCN4WAN | DPP | 4 | OPTIMAL | 0 | 0 | 0 | PASS |
| DCN4WAN | DDPP | 0 | OPTIMAL | 24 | 32 | 96 | PASS |
| DCN4WAN | DDPP | 1 | OPTIMAL | 16 | 20 | 64 | PASS |
| DCN4WAN | DDPP | 2 | OPTIMAL | 12 | 12 | 40 | PASS |
| DCN4WAN | DDPP | 3 | OPTIMAL | 4 | 4 | 16 | PASS |
| DCN4WAN | DDPP | 4 | OPTIMAL | 0 | 0 | 0 | PASS |
| InterDC8 | DPP | 0 | OPTIMAL | 25 | 127 | 209 | PASS |
| InterDC8 | DPP | 1 | OPTIMAL | 23 | 71 | 144 | PASS |
| InterDC8 | DPP | 2 | OPTIMAL | 22 | 65 | 137 | PASS |
| InterDC8 | DPP | 3 | OPTIMAL | 18 | 28 | 79 | PASS |
| InterDC8 | DPP | 4 | OPTIMAL | 16 | 20 | 61 | PASS |
| InterDC8 | DPP | 5 | OPTIMAL | 3 | 3 | 11 | PASS |
| InterDC8 | DPP | 6 | OPTIMAL | 0 | 0 | 0 | PASS |
| InterDC8 | DDPP | 0 | OPTIMAL | 25 | 127 | 209 | PASS |
| InterDC8 | DDPP | 1 | OPTIMAL | 23 | 71 | 144 | PASS |
| InterDC8 | DDPP | 2 | OPTIMAL | 22 | 65 | 137 | PASS |
| InterDC8 | DDPP | 3 | OPTIMAL | 18 | 28 | 79 | PASS |
| InterDC8 | DDPP | 4 | OPTIMAL | 16 | 20 | 61 | PASS |
| InterDC8 | DDPP | 5 | OPTIMAL | 3 | 3 | 11 | PASS |
| InterDC8 | DDPP | 6 | OPTIMAL | 0 | 0 | 0 | PASS |

Each policy fixes one previously certified working schedule and its complete reservation schedule across every row. The final row for each topology/policy is a post-working-completion control.
