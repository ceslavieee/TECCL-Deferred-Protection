# Deferred Protection Theory Logic Audit

This note is an internal correctness ledger for the protection idea. It is not
paper prose. Its job is to keep the theory, code semantics, and result claims
aligned.

## Core Model

The experiment compares three protection timing semantics on the same fixed
working schedule:

- Dedicated protection: static backup is pre-planned per source/chunk and held
  from the beginning.
- Shared protection: static backup is pre-planned, but reserved links can be
  shared across source/chunk backup paths in the resource accounting.
- Deferred protection: recovery is scenario-specific and starts only after a
  concrete failure time plus detection delay.

How to explain the experiment:

- A single deferred MILP solve is a fixed failure-time scenario evaluation, not a
  model that claims to know an unknown future failure time.
- Failure-time sensitivity is handled by an outer experiment script that sweeps
  possible failure epochs.
- Dedicated/shared remain static baselines throughout that comparison; they do
  not become time-aware when deferred uses a concrete failure epoch.
- Therefore `affected` means static path-exposed demands for dedicated/shared,
  and still-at-risk-after-failure demands for deferred.

The fair-comparison anchor is:

- same topology,
- same collective,
- same fixed working flows,
- same fixed working demand-link exposure,
- same directed single-link failure scenario set,
- same `EXACT` failure model for the main experiments.

Therefore the defensible claim is not "deferred protects the same static demand
set with less resource." The defensible claim is:

> Given the same working schedule and failure scenarios, deferred protection
> exploits failure-time information to recover only demands still at risk after
> the failure time, while dedicated/shared protection provision static backup
> for all path-exposed demands.

## Correctness Invariants Already Checked

`teccl/examples/audit_protection_correctness.py` verifies:

- shared/deferred schedules are fixed to the dedicated working schedule,
- shared/deferred working flows match dedicated exactly,
- shared/deferred working demand links match dedicated exactly,
- all three modes use the same failure scenarios,
- all reported affected demands are protected,
- unprotected demand count is zero,
- deferred recovery never uses the failed link after failure,
- deferred recovery starts after the configured detection delay,
- recovery scenario summaries partition the fixed demand set,
- dedicated/shared/deferred solver quality metadata is present,
- all main and sensitivity schedules currently pass the audit.

Current audit result:

```text
Protection correctness audit: PASS (454 checks, 0 errors, 0 warnings)
```

## Important Theoretical Boundaries

### 1. Deferred `EXACT` Exposure Uses Fixed Temporal Demand Paths

The deferred formulation has per-demand working path exposure through
`demand_link_used_w`. It now also exports and consumes
`Working_Demand_Link_Epochs` when the working schedule is fixed. The exported
epochs are reconstructed as temporal demand paths: for each demand, the solver
chooses a causally reachable sequence of working transmissions from source to
destination.

For fixed-schedule deferred runs, the time-aware exposure variable
`demand_link_used_w_after_failure` is fixed from:

- this demand's fixed path contains the failed link, and
- the exported demand-link epoch is at or after the failure time.

If the fixed schedule does not provide demand-link epochs, the solver falls back
to the older conservative per-demand-path plus source/chunk future-link
approximation.

Impact:

- Both InterDC8 and DCN4WAN currently pass demand-link-epoch audit checks with
  no ambiguity warnings.
- A small demand-path tie-breaker removes redundant demand-path edges that were
  feasible but not part of any temporal demand path.
- This should not understate protection requirements.

Possible future strengthening:

- Add true demand-specific time-expanded flow variables for the working phase.
- Then bind failure exposure to "demand d traverses failed link at epoch >= t_f"
  directly inside the MILP rather than during fixed-schedule epoch extraction.

### 2. Dedicated/Shared Are Intentionally Not Time-Aware

Dedicated and shared use the inherited static failure-scenario formulation with
`enable_real_failure_timing = False`.

This is not a bug. It encodes the baseline semantics:

- backup is provisioned before the failure,
- the failure time is not used to release static protection obligations,
- any demand whose fixed path can traverse the failed link must have backup.

Impact:

- Dedicated/shared affected counts are static path exposure counts.
- Deferred affected counts are time-aware risk counts.
- Figures must not imply that all modes protect the same affected set.

### 3. Resource Metrics Are Different Physical Commitments

Dedicated/shared resource counts represent reserved backup link-epochs. Deferred
resource counts represent post-failure recovery link-epochs.

These are comparable as "network capacity-time consumed by the protection
strategy," but they are not identical operational objects:

- static modes reserve capacity before knowing whether failure occurs,
- deferred mode consumes recovery capacity only in the realized failure scenario.

Impact:

- It is fair to report normalized link-epoch resource.
- It is not fair to describe deferred resource as equivalent pre-reserved backup
  capacity.

### 4. Failure Model Scope Is Single Directed Link

The current scenario set enumerates single directed link failures.

Out of scope:

- simultaneous multi-link failures,
- node failures,
- correlated datacenter-region failures,
- probabilistic failure rates,
- partial degradation instead of complete link failure.

Impact:

- Claims should say "single directed-link failure scenarios."
- Robustness to larger failure classes is future work, not an implied result.

### 5. Recovery Assumes Continuing Working Traffic On Nonfailed Links

Deferred recovery inherits scenario-valid working buffers and keeps nonfailed
working flows active after failure. Working arrivals over the failed link after
the failure time are excluded.

Impact:

- This matches a model where the original schedule continues on surviving links
  while extra recovery traffic is added.
- It is not a model of global restart or global abort after failure.
- The answer to "does everything retransmit?" is: no, only data not safely
  delivered under the failure scenario is recovered.

### 6. Detection Delay Is Exogenous

Detection delay is a fixed input. The solver does not model monitoring cost,
false positives, diagnosis uncertainty, or distributed detection protocol
dynamics.

Impact:

- Detection-delay sensitivity is necessary and already generated.
- Strong claims should condition on the chosen detection-delay value.

## Current Assessment

The core idea is theoretically coherent under the stated assumptions:

- same fixed working schedule,
- single directed-link failures,
- known failure time for deferred recovery,
- fixed detection delay,
- conservative exact path exposure,
- scenario-specific recovery after detection.

The biggest remaining theory risk is wording/claim scope, not a discovered
code-level contradiction. The implementation now removes the earlier DCN4WAN
multicast attribution warning through temporal demand-path reconstruction and a
small demand-path tie-breaker.
