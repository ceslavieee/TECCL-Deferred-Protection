# Topology

Add new topologies in this module following the existing TE-CCL matrix format.

## Research WAN unit convention

`DCN4WAN` and `InterDC8` are synthetic research topologies, not measured
production networks. Their physical link tables are the source of truth and use:

- link bandwidth in Gbit/s;
- propagation delay in milliseconds;
- chunk size in GB.

Each bandwidth value is the usable capacity of one direction of a full-duplex
connection. The two directions are represented by separate TE-CCL links with
the same capacity and delay.

`research_wan.py` converts these values to the units consumed by TE-CCL:

```text
capacity_chunks_per_second = bandwidth_gbps / 8 / chunk_size_gb
alpha_seconds = propagation_delay_ms / 1000
```

Both graphs are fixed single-chassis WANs. `chassis` must equal `1`; topology
classes reject other values instead of silently multiplying output metrics.

The current protection scope is one directed-link failure. A bidirectional
physical-fiber failure is a different scenario and requires coupling both
directions in the protection constraints.
