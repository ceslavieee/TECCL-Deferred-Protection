# Methodology

> **Draft status:** current canonical methodology draft. This document replaces
> the earlier single-request preplanned-protection draft. It describes the
> implemented strict multicast tree-pair model and the online dynamic-admission
> experiment. Citation keys remain placeholders until the bibliography is
> finalized.

## 1. Methodological Overview

This work studies whether deferring protection reservations can reduce the
blocking of deadline-driven collective-communication requests. It combines
three established strands:

1. the timing principle of Deferred Dedicated Path Protection (DDPP), in which
   backup capacity is placed later within a request's deadline
   `\cite{Horota2020DeferredProtection}`; and
2. TE-CCL's time-expanded, integer multi-commodity-flow representation of
   collective communication `\cite{TECCL}`; and
3. survivable multicast provisioning with link-disjoint primary and backup
   trees `\cite{Singhal2003SurvivableMulticast,Zhang2011ScheduledMulticast}`.

The source DDPP work considers a unicast primary path and backup path. An
AllGather source chunk, however, must reach several destinations and may be
copied at intermediate nodes. Prior optical-network research already protects
multicast sessions with primary and backup trees, including scheduled traffic
and dynamic admission. This thesis does not claim to invent that structure. It
adapts it to TE-CCL by assigning one fixed rooted working tree and one fixed
rooted backup tree to every atomic source chunk, then applying DDPP-style timing
and online residual-capacity accounting. The resulting model is a DPP-inspired
multicast adaptation, not a literal reproduction of unicast DPP 1:1.

The main experiment is online and dynamic. Multiple AllGather requests arrive
over time. Each accepted request reserves both working and protection
link-epoch capacity. A later request is optimized against the residual capacity
left by earlier accepted requests. The two policies differ in the permitted
timing of their protection reservations:

```text
early DPP: protection opportunities lie in the early service window;
DDPP:      protection opportunities lie in the later remaining-deadline window.
```

The primary empirical outcome is request blocking. The method does not model
frequency-slot continuity or contiguity, and therefore does not claim elastic
optical spectrum fragmentation results.

## 2. Scope and Assumptions

The implemented model uses the following assumptions.

1. The network topology, directed-link capacities, and propagation delays are
   known.
2. Time is discretized into fixed-duration epochs.
3. Each `(source, chunk)` is indivisible. A node may copy a chunk only after it
   possesses the complete chunk.
4. At most one directed link fails at the beginning of an epoch.
5. A transmission that starts before the failure epoch remains valid even when
   its occupied interval crosses the failure time.
6. Working transmissions on surviving links continue after a failure.
7. The failure is detected after a fixed number of epochs.
8. Recovery uses only pre-reserved backup-tree capacity and must finish by the
   request deadline.
9. Node failures, simultaneous failures, correlated failures, physical-fiber
   failures in both directions, and partial link degradation are outside the
   evaluated scope.

All robustness statements in this thesis are conditional on these assumptions.

## 3. Network and Time Model

The inter-data-center network is a directed graph

```math
G=(V,E),
```

where `V` is the node set and `E` is the set of directed links. A bidirectional
physical connection is represented by two directed links. Each directed link
`e=(i,j)` has capacity `C_e` and propagation delay `alpha_e`.

Global time is divided into epochs of duration `Delta`. If one chunk occupies
link `e` for `b_e` epochs, then

```math
b_e = \beta_e+1.
```

A transmission that starts in epoch `k` occupies the link during

```math
k, k+1, ..., k+b_e-1.
```

Its data becomes available at the receiver only after both transmission and
propagation delay. Link occupation and data-arrival time are therefore kept as
separate quantities.

## 4. Online Request Model

A request `r` is represented by

```math
r=(a_r,S_r,B_r,K_r,\delta_r),
```

where:

- `a_r` is its global arrival epoch;
- `S_r` is the set of AllGather participants;
- `B_r` is the data volume owned by each source, represented by integer chunks;
- `K_r` is the relative service deadline in epochs; and
- `delta_r` is the configured failure-detection delay.

Requests are processed in nondecreasing arrival order. Accepted requests are
not preempted. Each request is classified exactly once as accepted, blocked, or
unknown.

The current controlled experiment uses homogeneous requests so that the policy
comparison is not mixed with different data volumes, deadlines, or collective
types.

## 5. AllGather and the Atomic Protection Unit

For source `s` and chunk `c`, define the atomic multicast commodity

```math
q=(s,c).
```

Its destination set is

```math
D_q=\{d\in S_r: d\neq s\}.
```

Commodity `q` is complete only after every required destination has received
the chunk:

```math
\operatorname{complete}(q,k)=1
\Longleftrightarrow
\operatorname{delivered}(q,d,k)=1
\quad \forall d\in D_q.
```

The full `(source, chunk)` commodity is used as the protection and release
unit. Destination-level labels are not independent protection units because one
multicast transmission may support several destinations. This definition is
consistent with TE-CCL's integer-chunk and in-network-copy semantics.

## 6. Strict Working-Tree and Backup-Tree Pair

For every commodity `q`, the model selects:

- a rooted working tree `T_q^w`; and
- a rooted backup tree `T_q^p`.

Both trees are rooted at the source of `q` and must contain every terminal in
`D_q`. Each non-root node has at most one parent in a tree, and the selected
edges must be acyclic and source-reachable. These requirements prevent an empty
or disconnected structure from being counted as protection.

Let `z^w_{q,e}` and `z^p_{q,e}` indicate whether directed link `e` belongs to
the working or backup tree. Directed-link disjointness is enforced by

```math
z^w_{q,e}+z^p_{q,e}\leq 1
\qquad \forall q,e.
```

This is a per-commodity guarantee for one directed-link failure. It does not
imply node-disjointness or shared-risk-group disjointness.

The working tree carries data in failure-free operation. The backup tree is a
reserved recovery structure. A backup reservation is not normal-operation data
and does not create receiver buffer state before a failure.

## 7. Two-Stage Scenario-Robust Protection

The model separates pre-failure reservation decisions from post-failure
recovery execution.

### 7.1 First-stage variables

```math
x^w_{q,e,k}=1
```

if working commodity `q` starts transmission on link `e` in epoch `k`, and

```math
\rho_{q,e,k}=1
```

if the same link-epoch opportunity is reserved for backup recovery.

The reservation variable `rho` commits capacity but transmits no data during
normal operation.

### 7.2 Failure scenarios

A failure scenario is

```math
f=(e_f,\tau),
```

where directed link `e_f` fails at the beginning of epoch `tau`. Recovery may
start only after detection:

```math
a_f=\tau+\delta_r.
```

For scenario `f`, let `x^f_{q,e,k}` denote executed recovery. The model enforces

```math
x^f_{q,e,k}=0 \quad \forall k<a_f,
```

```math
x^f_{q,e_f,k}=0 \quad \forall k\geq\tau,
```

and reservation binding

```math
x^f_{q,e,k}\leq \rho_{q,e,k}.
```

Recovery is restricted to the fixed backup tree. An affected destination must
receive a genuine source-originated recovery flow; it cannot inherit a
downstream working transmission that became causally unreachable after an
upstream failure.

### 7.3 Exact failure exposure

For a fixed working schedule, the current `EXACT` mode determines exposure from
the temporal working path under scenario `(e_f,tau)`. Working starts on the
failed link at or after `tau` are removed, surviving transmissions are replayed
with their original timing, and a destination is affected only if its chunk no
longer arrives.

The implementation also checks exposure at the atomic `(source, chunk)` level.
Deterministic replay confirms that the current source-chunk activation set is
identical to the exact affected protection-unit set on both research
topologies. Destination-level approximations may overcount labels, but they do
not omit an affected atomic protection unit in the evaluated schedules.

### 7.4 Capacity constraints

In normal operation, working load plus reserved backup load must fit:

```math
\operatorname{occ}(x^w,e,k)
+\operatorname{occ}(\rho,e,k)
\leq C_e.
```

In each failure scenario, surviving working load plus executed recovery must
also fit:

```math
\operatorname{occ}(x^{w,f},e,k)
+\operatorname{occ}(x^f,e,k)
\leq C_e.
```

The occupation operator expands every transmission start across all `b_e`
occupied epochs.

### 7.5 Protected completion

For every configured scenario and destination, the scenario-valid working and
recovery state must deliver the chunk before the request deadline. Scenario
feasibility is a hard constraint rather than an objective reward.

## 8. Early DPP and DDPP Timing Policies

Let `K_b` divide the request service horizon into an early and a late window.
Both policies use the same network, workload, strict tree-pair requirements,
failure model, detection delay, deadline, solver budget, and independent audit.
The intended policy difference is the reservation window.

For the early DPP baseline,

```math
\rho_{q,e,k}=0 \qquad \forall k>K_b.
```

For DDPP,

```math
\rho_{q,e,k}=0 \qquad \forall k\leq K_b,
```

with reservations permitted in the remaining interval before `K_r`.

All reservations are planned when the request is admitted. “Deferred” means
that their scheduled link-epoch opportunities occur later; it does not mean
that the system waits until a failure to compute the plan.

The current early baseline is accurately described as an early
scenario-robust multicast tree-pair DPP adaptation. It must not be described as
literal unicast DPP 1:1.

For the certified single-request pair, DPP and DDPP fix identical working-tree
and backup-tree edges, so only timing changes. During a dynamic trace, the two
policy histories may diverge after different admission decisions. Each policy
therefore reoptimizes against its own residual ledger, while retaining the same
structural constraints, workload, and compute budget.

## 9. Failure-Time Robustness Construction

Solving an independent reservation plan for every possible failure time would
not produce one deployable pre-failure plan. The dynamic fallback instead uses
a sufficient common-plan construction.

Let `C_q` be the nominal working completion epoch of commodity `q`. Every
backup reservation start `k` for that commodity is constrained by

```math
k\geq C_q+\delta_r-1.
```

For every relevant failure time `tau <= C_q-1`,

```math
\tau+\delta_r\leq C_q+\delta_r-1\leq k.
```

Therefore failure notification occurs no later than the first eligible backup
reservation. Because the working structure is a rooted tree with a unique
source-to-destination path, the affected set for a later failure time is a
subset of the affected set at `tau=0`. A certified `tau=0` recovery on the
fixed backup tree consequently covers a superset of later affected
commodities. Removing unaffected recovery branches cannot increase capacity or
break causality for an affected branch.

Each accepted fallback schedule exports a machine-audited certificate for this
construction. A complete outer sweep over failure times is retained as an
independent regression test of the implementation.

## 10. Global Capacity-Time Ledger

For global directed link `e` and epoch `t`, let `L_{e,t}` denote capacity
already committed by accepted requests. The residual capacity seen by a new
request is

```math
C^{res}_{e,t}=C_e-L_{e,t}.
```

For accepted request `r`, let `W_{r,e,t}` and `P_{r,e,t}` denote its committed
working and still-live protection occupancy. The ledger invariant is

```math
\sum_{r\in A}
\left(W_{r,e,t}+P_{r,e,t}\right)
\leq C_e
\qquad \forall e,t,
```

where `A` is the accepted-request set.

Every local schedule is shifted by its global arrival epoch before commitment.
Every transfer is expanded across its full link-occupation duration. Thus a
schedule is not admitted merely because its transmission start epochs do not
conflict.

The ledger is transactional: either every working and protection cell of an
accepted request is committed, or no cell is changed. A blocked or unknown
request cannot partially consume capacity.

## 11. Completion-Aware Release

Let the global working completion time of commodity `q` in request `r` be

```math
T^w_{r,q}=a_r+C_{r,q}.
```

At observation epoch `t`, future reservation capacity belonging exclusively to
that commodity may be released when

```math
t\geq T^w_{r,q}.
```

Only future reservation cells are removed. Past occupancy is unchanged. A
reservation shared by several atomic commodities can be released only after
all protected commodities have completed.

This rule is safe under the stated event model because a fully delivered
commodity no longer needs protection against a later failure. It is also the
mechanism through which a later DDPP reservation may be released before its
scheduled slot, making that capacity available to a subsequently arriving
request.

## 12. Per-Arrival Admission Algorithm

At arrival of request `r`, the implementation performs the following steps.

1. Release eligible future protection reservations from completed commodities.
2. Export the current request-local residual capacity from the global ledger.
3. Try the certified topology template at the request's arrival epoch.
4. If it conflicts, try the frozen library of certified working-tree and
   backup-tree candidates while reoptimizing all transmission epochs.
5. If no candidate succeeds, run unrestricted joint tree-and-timing
   optimization against residual capacity.
6. Independently audit any incumbent for tree validity, terminal coverage,
   deadline completion, reservation binding, directed-link failures, detection
   timing, failure-time robustness, and capacity.
7. Commit the complete schedule only if every audit passes.

The candidate library is a search accelerator, not an infeasibility proof.
Failure of every fixed-tree candidate never classifies the request as blocked;
only the unrestricted solver may establish global infeasibility.

The library and solver settings are frozen before held-out traces are
generated. Calibration seeds used to construct the library are excluded from
the final held-out statistics.

## 13. Admission Outcome Classification

Each request has one of three outcomes.

### Accepted

A request is accepted when a valid incumbent exists and all independent audits
pass. A time-limited solve may be accepted if it contains such an audited
incumbent; global optimality is not required for feasibility admission.

### Blocked

A request is blocked only when the unrestricted solver proves the
residual-capacity problem infeasible.

### Unknown

A timeout, process failure, missing solver sidecar, or unauditable incumbent is
unknown. Unknown is not equivalent to blocking and makes no ledger mutation.

The accounting identity is

```math
N_{accepted}+N_{blocked}+N_{unknown}=N_{total}.
```

## 14. Arrival Traces and Paired Experimental Design

The current evaluated trace family draws exponential interarrival gaps and
discretizes them as

```math
g_r=\max\left(1,\left\lceil
\operatorname{Exp}(\lambda)\right\rceil\right).
```

The trace is generated once per seed and reused byte-for-byte by both policies.
Request size, deadline, detection delay, and topology are fixed while the
arrival sequence changes across independent seeds.

This generator permits at most one request arrival per epoch. The limitation is
explicit: the current InterDC8 result applies to this trace family and must not
be silently combined with a future batched Poisson epoch-count workload.

The principal completed InterDC8 experiment uses:

- topology: InterDC8;
- arrival rates: `lambda in {0.03, 0.06, 0.10}` per epoch;
- offered arrivals per 40-epoch deadline: 1.2, 2.4, and 4.0;
- requests per trace: 8;
- endpoint-load seeds: 8 per load and 64 requests per policy per load;
- transition-load seeds: 16 and 128 requests per policy;
- candidate-library calibration seeds: excluded from the held-out sample;
- identical trace, solver budget, and audit rules for both policies.

The transition-load stopping rule was fixed before its final batch. The two
endpoint loads, integrity-gate seed, eight formal endpoint seeds, and reporting
rules were then frozen in `MULTILOAD_EXPERIMENT_PLAN.md` before the endpoint
traces were generated. The integrity-gate seed is excluded, and all formal
seeds are retained regardless of policy direction or significance.

DCN4WAN traces with 4, 8, and 12 requests are retained as current-implementation
correctness and load-location calibrations. Because both policies admitted all
requests, these runs do not support a DCN4WAN performance-advantage claim.

## 15. Metrics and Statistical Analysis

### 15.1 Admission metrics

For `N` total arrivals, the unknown-aware blocking bounds are

```math
p^{low}_{block}=\frac{N_{blocked}}{N},
```

```math
p^{high}_{block}=\frac{N_{blocked}+N_{unknown}}{N}.
```

When `N_unknown=0`, the two bounds coincide. The unknown rate is

```math
p_{unknown}=\frac{N_{unknown}}{N}.
```

The implementation also records the blocking probability among conclusive
arrivals, but the main figure uses the conservative lower/upper convention so
the single unknown remains visible.

### 15.2 Paired statistics

For seed `s`, define

```math
d_s=A^{DDPP}_s-A^{DPP}_s,
```

where `A_s` is the number of certified admissions on the same request trace.
The report includes:

- DDPP wins, ties, and losses across seeds;
- the mean paired admission difference;
- a 95% t interval for the mean paired difference; and
- a two-sided exact sign test over non-tied seeds.

These statistics describe the evaluated arrival distribution. They are not a
theorem that DDPP dominates DPP on every trace.

### 15.3 Supporting protection metrics

The single-request certificate separately reports:

- beta-weighted reserved occupied link-epochs;
- protected completion epoch; and
- maximum certified detection delay of the fixed plan.

These metrics explain the protection timing trade-off. They are not substitutes
for online blocking measurements.

## 16. Verification Procedure

An experimental result is considered valid only if all applicable obligations
pass.

1. **Tree validity:** every commodity has a rooted, acyclic working tree and
   backup tree covering all terminals.
2. **Directed-link disjointness:** a commodity's two trees share no directed
   link.
3. **Ledger safety:** no accepted request set exceeds any link-epoch capacity.
4. **Atomic admission:** blocked and unknown requests make no ledger mutation.
5. **Deadline completion:** all normal and protected deliveries finish by their
   declared deadlines.
6. **Scenario robustness:** affected commodities recover for every configured
   single directed-link failure.
7. **Detection observability:** recovery begins only after failure detection.
8. **Reservation binding:** executed recovery uses only reserved backup slots.
9. **Release safety:** only eligible future protection capacity is released.
10. **Trace identity:** both policies use the identical serialized request
    trace.
11. **Accounting identity:** accepted, blocked, and unknown counts sum to all
    arrivals.
12. **Reproducibility:** reports retain trace hashes, implementation hashes,
    source schedules, solver status, and audit certificates.

The current automated suite contains 22 tests covering ledger atomicity,
residual occupancy, release, provenance, topology structure, tree validity,
terminal coverage, and detection-delay-aware reservations. Accepted production
schedules additionally pass the independent serialized schedule audit.

## 17. Claim Boundary

The current methodology and evidence support the following statement:

> A strict rooted working-tree/backup-tree protection model can be integrated
> with TE-CCL's AllGather formulation and an online residual-capacity ledger.
> Under the evaluated single-directed-link failure model, accepted schedules
> pass independent protection and capacity audits. Across the three
> predeclared InterDC8 loads, early-DPP observed blocking increased from 9.375%
> to 15.625% and 26.5625%, while the DDPP unknown-aware upper bounds were 0%,
> 3.90625%, and 6.25%. The mean paired DDPP admission gain increased from 0.75
> to 0.9375 and 1.625 requests per eight-request trace. DDPP recorded no paired
> seed loss at any evaluated load.

The current work does not claim:

- invention of primary/backup multicast trees or survivable multicast
  provisioning;
- the first dynamic multicast admission or blocking-probability study;
- the first fault-tolerant collective-communication system;
- universal DDPP dominance across every topology, trace, or load;
- literal equivalence to unicast DPP 1:1;
- robustness to node, fiber-pair, correlated, or multiple failures;
- elastic-spectrum continuity, contiguity, or fragmentation performance;
- globally optimal schedules when a time-limited audited incumbent is accepted;
  or
- hardware execution performance.

DCN4WAN currently provides a second-topology correctness check, while the
repeated blocking evidence is limited to three loads on InterDC8 under one
arrival family. Broader cross-topology saturation experiments remain future
work unless required for the final thesis scope.
