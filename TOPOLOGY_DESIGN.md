# Research Topology Design

## Status

`DCN4WAN` and `InterDC8` are synthetic research topologies. They are designed
to make the protection formulation auditable and experimentally discriminative;
they are not claimed to reproduce a measured production WAN.

The Python physical-link tables are the only source of truth. Old presentation
figures and all schedules generated before the physical-unit conversion are
historical artifacts and must not be used as final thesis evidence.

## TE-CCL unit contract

TE-CCL consumes link capacity in chunks per second and propagation delay in
seconds. The topology source tables use conventional physical units and convert
them as follows:

```text
capacity_chunks_per_second = bandwidth_gbps / 8 / chunk_size_gb
alpha_seconds = propagation_delay_ms / 1000
```

This keeps the topology readable while matching the formulation in the TE-CCL
paper. Changing the chunk size changes chunks per second, but does not change the
underlying physical link rate or propagation delay.

Bandwidth denotes usable full-duplex capacity per direction. Each symmetric
physical connection therefore becomes two independently capacitated directed
TE-CCL links.

## DCN4WAN

Purpose: small validation topology whose tree structures and failure cases can
be inspected manually.

- 4 AllGather participants;
- 5 copy-capable WAN transit nodes;
- 16 symmetric physical connections / 32 directed model links;
- 100 Gbit/s access, 80 Gbit/s metro, and 60 Gbit/s core links;
- 0.4--1.2 ms propagation delays;
- no physical bridge.

The topology is deliberately redundant and may produce DPP/DDPP ties. Its role
is correctness validation, not proving a performance advantage.

## InterDC8

Purpose: sparse heterogeneous stress topology for dynamic-admission experiments.

- 8 AllGather participants and no separate transit nodes;
- 13 symmetric physical connections / 26 directed model links;
- 100 Gbit/s local, 80 Gbit/s regional, 60 Gbit/s bypass, and 50 Gbit/s
  backbone links;
- 0.5--2.8 ms propagation delays;
- minimum physical degree 2 and no physical bridge.

The graph supports single directed-link protection. It does not contain enough
physical edges for two fully physical-edge-disjoint spanning trees over all
eight participants, so it must not be used to claim bidirectional-fiber-failure
protection without a topology and formulation change.

## Failure and forwarding assumptions

- One directed link fails at a time.
- The reverse direction remains available.
- DCN4WAN WAN nodes may copy a chunk when `switch_copy=True`.
- There is no aggregate node-capacity constraint; capacity is enforced per
  directed link.
- Both topologies are fixed graphs and require `chassis=1`.

## Workload and time-discretization decision

The reference workload is **25 GB of AllGather data per source**. This value is
inside the 12.5--62.5 GB bulk-transfer range evaluated by the deferred-
protection paper and is treated as a controlled synthetic workload, not as a
claim about every distributed-training job.

TE-CCL's fine-grained rule is used: one epoch is the time required for the
fastest link to transmit one complete integer chunk. The fixed-total-data
configurations are therefore:

| Chunks per source | Chunk size | Epoch duration |
| ---: | ---: | ---: |
| 1 | 25 GB | 2 s |
| 2 | 12.5 GB | 1 s |
| 4 | 6.25 GB | 0.5 s |

The one-chunk case is the primary exact/certification configuration. The two-
and four-chunk cases test whether integer-chunk pipelining and whole-chunk
protection release change the conclusion while total data remains fixed.

With `alpha_threshold=0.1`, physical propagation delay is below the TE-CCL
discretization threshold in all three configurations. In the finest four-chunk
case, the maximum `alpha / epoch_duration` is 0.0024 on DCN4WAN and 0.0056 on
InterDC8. Rounding a 2.8 ms delay up to a full 0.5 s epoch would introduce a
larger error than omitting it. The main experiments therefore treat propagation
as negligible relative to bulk serialization time and must not claim a
latency-sensitive result.

Fair comparisons across chunk counts must preserve physical quantities:

- total data remains 25 GB per source;
- a deadline in seconds is converted to epochs separately for each chunk count;
- arrival times are generated in seconds and then converted to local epochs;
- bandwidth, failure scope, and the paired request trace remain unchanged;
- solver status and MIP gap are reported for every point.

Data-volume sensitivity may subsequently evaluate 12.5, 50, and 62.5 GB per
source, matching the range used by the deferred-protection reference. It is
separate from chunk-granularity sensitivity and must not change both total data
and chunk count without labeling both factors.

## Experiment gate

Before final long-running experiments:

1. strict DPP/DDPP tree-pair schedules under these physical units are complete;
2. independent structure, capacity, deadline, full directed-link, failure-time,
   and fixed-plan detection-boundary audits are complete;
3. the 25 GB fixed-total-data settings are encoded in fresh experiment inputs;
4. rerun the load locator before selecting final load points;
5. add a public standard topology for external-validity evidence.
