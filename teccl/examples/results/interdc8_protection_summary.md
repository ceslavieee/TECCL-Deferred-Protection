## Normal-Case Working Summary

This table focuses on the normal no-failure execution of the working collective.

| Strategy | Working Time | Working Epochs | Working Flows | Working Links | Solver Time |
| --- | --- | --- | --- | --- | --- |
| No Protection | 11.000 | 11 | 56 | 26 | 0.053 |
| Dedicated Protection | 11.000 | 11 | 56 | 25 | 21.125 |
| Shared Protection | 11.000 | 11 | 56 | 21 | 69.300 |
| Deferred Protection | 11.000 | 11.000 | 56 | 26 | 30.342 |

## Failure-Aware / Protection-Aware Summary

This table focuses on protection-related completion and coverage under the configured failure scenarios.

| Strategy | Final Time | Final Epochs | Protection Flows | Protection Links | Affected | Protected | Released | Unprotected | Failure Scenarios | Deadline Factor | Working Deadline | Deferred Start | Failure Time | Detection Delay | Recovery Start | Final Deadline |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| No Protection | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - |
| Dedicated Protection | 17.000 | - | 56 | 56 | 392 | 392 | 0 | 0 | 26 | - | - | - | - | - | - | - |
| Shared Protection | 17.000 | - | 56 | 15 | 392 | 392 | 0 | 0 | 26 | - | - | - | - | - | - | - |
| Deferred Protection | 30.000 | 30 | 8 | 8 | 4 | 4 | 10 | 0 | 26 | 1.500 | 18 | 10 | 9 | 1 | 10 | 30 |
