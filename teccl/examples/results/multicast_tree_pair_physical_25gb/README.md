# Physical-unit 25 GB multicast tree-pair experiments

This directory is the post-topology-reset workspace for the strict rooted
multicast working-tree / backup-tree DPP analogue.

The `probe_4fail` inputs use:

- 25 GB per source;
- one integer chunk per source;
- fastest-link epochs (2 seconds on both research topologies);
- exact per-destination failure exposure;
- the first four directed-link failure scenarios;
- one directed-link failure at a time.

The probe is only an integration and feasibility check. It is not a full
single-link-failure certificate and must not be reported as a thesis result.
Old schedules under `results/multicast_tree_pair/` are not inputs to this run.

## Probe outcome

The probe found and closed two strict-tree modeling loopholes before producing
the results below:

1. both trees now explicitly contain every demanded AllGather terminal;
2. an affected destination must use a real source-originated recovery flow,
   rather than inheriting a downstream working send disconnected by the failed
   upstream tree edge.

| Topology / policy | Solver status | Working completion | Protected completion | Reserved slots | Recovery flows |
| --- | --- | ---: | ---: | ---: | ---: |
| DCN4WAN DPP | OPTIMAL | 4 | 15 | 24 | 16 |
| DCN4WAN DDPP | OPTIMAL | 4 | 30 | 24 | 16 |
| InterDC8 DPP | TIME_LIMIT, feasible | 6 | 20 | 56 | 28 |
| InterDC8 DDPP | OPTIMAL with fixed tree pair | 6 | 40 | 56 | 28 |

All four schedules pass the independent strict-schedule audit, including
terminal coverage, nonempty recovery, reservation binding, failed-link
avoidance, deadline, and tree-structure checks. Within each topology, DPP and
DDPP have identical working-tree edges, backup-tree edges, and failure-scenario
sets; only their reservation timing policy differs.

Completion values in the table are epoch counts, and one epoch is 2 seconds.
InterDC8 DPP is not an optimality result because its 18-second probe limit was
reached. No performance conclusion may be drawn from this four-scenario probe.

The `full_directed_failures` inputs fix the audited tree-pair candidate from the
probe and expand the scenario set to every directed topology link. They still
evaluate only failure epoch zero; robustness across failure times requires a
separate outer sweep.

## Full directed-link coverage outcome

| Topology / policy | Status | Scenarios | Scenarios affecting the working trees | Working completion | Protected completion | Reserved slots | Recovery flows |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DCN4WAN DPP | OPTIMAL | 32 | 24 | 4 | 15 | 24 | 96 |
| DCN4WAN DDPP | OPTIMAL | 32 | 24 | 4 | 30 | 24 | 96 |
| InterDC8 DPP | OPTIMAL | 26 | 25 | 6 | 20 | 56 | 209 |
| InterDC8 DDPP | OPTIMAL | 26 | 25 | 6 | 40 | 56 | 209 |

Every schedule passes all 13 current independent strict-schedule checks. DPP and DDPP
within each topology have identical working-tree edges, backup-tree edges, and
failure-scenario sets. Scenarios that do not affect a working-tree edge require
no recovery and are retained in the scenario count.

This is a complete directed-link-coverage certificate at failure epoch zero,
not yet a certificate for every possible failure time.

## Fixed-plan failure-time certificate

The same working schedule and complete reservation schedule were then fixed
while the failure epoch changed. With one epoch of detection delay:

- DCN4WAN DPP and DDPP certify failure epochs 0--3; epoch 4 is the
  post-working-completion control;
- InterDC8 DPP and DDPP certify failure epochs 0--5; epoch 6 is the
  post-working-completion control;
- all 24 rows are `OPTIMAL`, use `EXACT` failure exposure, cover every directed
  topology link, and pass the independent audit;
- affected scenario counts decrease monotonically from `24, 16, 12, 4, 0` on
  DCN4WAN and `25, 23, 22, 18, 16, 3, 0` on InterDC8;
- recovery-flow counts decrease to zero at the post-completion control.

The machine-readable certificate is under
`failure_time_sweep/certificate.json`; the complete table is under
`failure_time_sweep/README.md`.

## Fixed-plan detection-delay boundary

The same fixed plans were then evaluated at their analytically derived maximum
detection delay for every affected failure epoch. The adjacent larger delay was
also solved at a tight witness and had to return `INFEASIBLE` rather than merely
timing out.

| Topology | Policy | Maximum delay (epochs) | Maximum delay (seconds) | First infeasible delay | Witness failure epoch |
| --- | --- | ---: | ---: | ---: | ---: |
| DCN4WAN | DPP | 6 | 12 | 7 | 3 |
| DCN4WAN | DDPP | 21 | 42 | 22 | 3 |
| InterDC8 | DPP | 2 | 4 | 3 | 5 |
| InterDC8 | DDPP | 22 | 44 | 23 | 5 |

All 20 feasible endpoint subproblems are `OPTIMAL`, use all 32 or 26 directed
link scenarios, preserve the fixed working and reservation schedules, and pass
the independent strict-tree audit. All four adjacent-delay witnesses are
`INFEASIBLE`. The machine-readable certificate is under
`detection_delay_boundary/certificate.json`.

These are tolerance limits of the exported fixed plans, not upper bounds over
all alternative plans. DDPP's larger numerical tolerance reflects its later
reservation and protected-completion window; it does not mean faster recovery.
