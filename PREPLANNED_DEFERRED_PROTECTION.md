# Preplanned Deferred Protection for TE-CCL

## Purpose

This formulation adapts the deferred-protection idea to TE-CCL's discrete
AllGather model. It is not a reproduction of an elastic-optical-network
spectrum simulator.

Before communication starts, the solver computes:

1. a working AllGather schedule;
2. a complete contingency AllGather schedule;
3. the future contingency transmissions that remain reserved after each
   failure-free working epoch.

The working and contingency schedules are both known before a failure. The
contingency schedule is placed after the working window and is executed only if
the corresponding source chunk has not completed normally.

## Protection Unit

TE-CCL's atomic multicast commodity is one `(source, chunk)` pair. A commodity
is complete only when every destination that demands the chunk has received it.

This definition preserves TE-CCL's in-network copy semantics. It deliberately
does not infer partial reservation release from reconstructed per-destination
paths. Finer release granularity is obtained by increasing the configured chunk
count.

## Time Model

Let:

- `K_w` be the last working epoch;
- `K_a` be the first contingency epoch;
- `K_d` be the final deadline.

The formulation enforces:

```text
K_a > K_w
working flow is zero for k > K_w
contingency flow is zero for k < K_a
all working demands are satisfied by K_w
all contingency demands are satisfiable by K_d
```

The contingency flow has its own buffer state. It is a failure-activation plan,
not data delivered during normal operation.

## Decision Variables

The formulation reuses TE-CCL variables:

- `flow_w[s,i,j,c,k]`: working transmission;
- `buffer_w[s,i,c,k]`: failure-free working buffer;
- `demand_w[s,d,c,k]`: working demand satisfied by epoch `k`;
- `flow_p[s,i,j,c,k]`: executable preplanned contingency transmission;
- `buffer_p[s,i,c,k]`: contingency-plan buffer;
- `demand_p[s,d,c,k]`: contingency demand satisfied by epoch `k`.

It adds:

- `complete_before[s,c,t]`: every destination has received `(s,c)` before
  observation epoch `t`;
- `reserve[t,s,i,j,c,k]`: the future contingency transmission
  `flow_p[s,i,j,c,k]` is still reserved at observation epoch `t`.

## Exact Completion And Release

For destination set `D(s,c)`, completion is the binary AND:

```text
complete_before[s,c,0] = 0

complete_before[s,c,t] <= demand_w[s,d,c,t-1]
    for every d in D(s,c)

complete_before[s,c,t] >=
    sum(demand_w[s,d,c,t-1] for d in D(s,c)) - |D(s,c)| + 1
```

With dynamic release enabled, reservation state is the exact binary product:

```text
reserve = flow_p AND (NOT complete_before)
```

It is linearized as:

```text
reserve <= flow_p
reserve <= 1 - complete_before
reserve >= flow_p - complete_before
```

Therefore a source chunk retains its complete future contingency plan until all
of its AllGather destinations have received it. It releases that plan at the
first observation epoch after completion.

## Protection Guarantee

For each source chunk, the existing TE-CCL link-use variables enforce:

```text
working_link_used[s,i,j,c] + backup_link_used[s,i,j,c] <= 1
```

The guarantee is for a single directed-link resource failure. The complete
contingency schedule avoids every directed link used by that source chunk's
working schedule and satisfies all destinations by the final deadline.

This guarantee is stronger than protecting only one configured failed link, but
it does not cover node failures, simultaneous failures, correlated failures, or
an undirected shared-risk link group.

## Objective

The solver uses lexicographic objectives:

1. minimize total reservation holding units across observation epochs;
2. minimize the number of contingency transmissions;
3. minimize working plus contingency completion time as a tie-breaker.

Reservation holding units are:

```text
sum(reserve[t,s,i,j,c,k])
```

This model objective counts reserved flow starts. It represents how many future
contingency actions remain booked as normal working progress is observed. It is
not blocking probability.

The common post-solve accounting separately weights every reserved flow by
`beta(i,j) + 1` and converts it to physical units.

`beta_weighted_holding_objective = false` preserves the legacy flow-start
objective. In this mode, physical results are measured outcomes of the
optimized schedules, not globally minimal beta-weighted holding.

`beta_weighted_holding_objective = true` changes the first expression to:

```text
sum((beta(i,j) + 1) * reserve[t,s,i,j,c,k])
```

Because epoch duration is constant within one instance, minimizing this
expression is equivalent to minimizing the reported holding
link-second-squared. It changes only the optimizer's preference among feasible
contingency schedules; protection constraints and failure scope are unchanged.
The formal Preplanned Deferred inputs use this beta-weighted mode. The legacy
flow-start mode is retained only for the objective-ablation experiment.

## Experimental Interpretation

The current TE-CCL solve contains one AllGather operation. Delaying reservation
does not by itself prove that another request can use the newly available
capacity.

The first experiment reports:

- contingency-plan transmissions and payload GB-links;
- contingency-plan occupied link-seconds;
- peak future reserved occupied link-epochs;
- future reservation holding link-second-squared;
- backup link-seconds released before their scheduled slot;
- failure-free committed backup link-seconds whose slot arrives before normal
  commodity completion;
- working completion time;
- contingency completion time;
- final deadline;
- solver status and optimality gap.

The common accounting module applies the same rule to Dedicated and Preplanned
Deferred protection. A future backup link-epoch `(s,i,j,c,k)` remains active at
observation epoch `t` exactly when `t <= k` and `(s,c)` has not completed
normally before `t`.

TE-CCL's `beta(i,j) + 1` transmission duration is included in every occupied
resource metric. One flow start therefore contributes `beta(i,j) + 1` occupied
link-epochs, not one link-epoch. Physical-unit conversion uses:

```text
payload GB-links = number of flow starts * chunk size in GB
occupied link-seconds = occupied link-epochs * epoch duration
holding link-second-squared = holding units * epoch duration^2
```

This produces two distinct resource conclusions:

- reservation holding measures how long future commitments remain booked;
- failure-free committed backup link-epochs measure backup slots that arrive
  before normal completion and therefore cannot be released in advance.

Delayed backup can increase the first metric while reducing the second. Neither
metric should be renamed as blocking probability or total network utilization.

The paired experiment can be reproduced with:

```bash
python teccl/examples/compare_preplanned_protection.py --run
```

It first lets Dedicated protection generate a protectable working schedule,
then fixes those exact working flows in Preplanned Deferred protection. An
unprotected minimum-time schedule is not automatically a valid reference
because it may have no directed-link-disjoint contingency route.

The formal paired results use `DCN4WAN` (four data centers connected through
five WAN transit nodes) and `InterDC8`. `Ladder6` is not part of this result
set.

A fixed-total-data chunk-granularity experiment is available with:

```bash
python teccl/examples/chunk_granularity_sensitivity.py --run
```

It tests 1, 2, and 4 chunks while keeping 100 GB per source. Results are written
under `teccl/examples/results/chunk_granularity/`. Every row records solver
status, MIP gap, and working-schedule origin. A time-limit solution is exact
with respect to the MILP constraints but is only a feasible point, not a proof
of optimality. The serialized `InterDC8` four-chunk reference is reported
separately and must not be treated as a like-for-like monotonic trend point.

The objective-only A/B experiment can be reproduced with:

```bash
python teccl/examples/compare_preplanned_holding_objectives.py --run
```

It fixes identical working flows and protection constraints, changing only the
highest-priority holding expression. It writes solver status, physical metrics,
paired changes, and audit results under
`teccl/examples/results/preplanned_objective_ablation/`.

The fixed-schedule single-link failure execution experiment can be reproduced
without rerunning Gurobi:

```bash
python teccl/examples/evaluate_preplanned_failure_execution.py
```

For every directed link used by the common working schedule and every failure
epoch before working completion, the evaluator:

1. removes working transmissions that start on the failed link at or after the
   failure epoch;
2. propagates all remaining fixed working flows using the topology's exact
   alpha delay and `beta(i,j) + 1` transmission time;
3. marks a `(source, chunk)` as affected only if it no longer reaches every
   AllGather endpoint;
4. activates the corresponding contingency plan only for affected units; and
5. reports recovery completion and occupied backup link-seconds.

This follows the MILP's discrete failure convention: a transmission that
started before the failure epoch remains valid even if its occupied interval
extends across that epoch. Provisioned resource counts each strategy's complete
backup plan. Activated resource uses the same affected-only rule for both
strategies and counts only plans belonging to affected units. The detailed and
aggregated results are under
`teccl/examples/results/preplanned_failure_execution/`.

The thesis figure comparing provisioned resource, affected-only activated
resource, and completion time is generated with:

```bash
python teccl/examples/plot_preplanned_failure_execution.py
```

PNG, PDF, and SVG versions are written under
`teccl/examples/results/figures/preplanned_failure_execution/`. The figure uses
only failure scenarios that actually prevent at least one `(source, chunk)`
from completing AllGather. Separate panels report complete provisioned backup
resource and affected-only activated resource, using the same definition for
both strategies, followed by the protected-completion trade-off.

The serialized InterDC8 Dedicated schedule is a verified feasible
`TIME_LIMIT` solution with a relative MIP gap of approximately `0.7878%`.
Consequently, deterministic replay results are exact for that schedule, but
the InterDC8 comparison is not a proof of globally optimal Dedicated
performance.

A later multi-collective or time-varying-background-capacity experiment is
required before making claims about admission rate or blocking probability.

## Implemented Mode Boundary

`DeferredTimingMode.PREPLANNED` selects this formulation.

`DeferredTimingMode.POST_FAILURE` selects the existing scenario-specific
failure-time-aware recovery formulation. The two modes answer different
questions and must not be merged into one resource metric without labels.
