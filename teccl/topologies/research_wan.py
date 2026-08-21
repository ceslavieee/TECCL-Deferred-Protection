"""Physical-link helpers for the synthetic research WAN topologies.

TE-CCL represents a link capacity in chunks per second and propagation delay
in seconds.  The research topologies are easier to review in conventional WAN
units, so their source-of-truth link tables use Gbit/s and milliseconds and are
converted here exactly once.
"""

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple


@dataclass(frozen=True)
class PhysicalLink:
    """One symmetric physical connection expressed in reviewer-facing units."""

    u: int
    v: int
    bandwidth_gbps: float
    propagation_delay_ms: float

    def capacity_chunks_per_second(self, chunk_size_gb: float) -> float:
        bandwidth_gb_per_second = self.bandwidth_gbps / 8.0
        return bandwidth_gb_per_second / chunk_size_gb

    @property
    def propagation_delay_seconds(self) -> float:
        return self.propagation_delay_ms / 1000.0


def build_bidirectional_physical_topology(
    node_count: int,
    chunk_size_gb: float,
    links: Sequence[PhysicalLink],
) -> Tuple[List[List[float]], List[List[float]]]:
    """Build TE-CCL capacity/alpha matrices from a physical link table."""

    if node_count <= 0:
        raise ValueError("node_count must be positive")
    if not math.isfinite(chunk_size_gb) or chunk_size_gb <= 0:
        raise ValueError("chunk_size must be positive and expressed in GB")

    capacity = [[0.0 for _ in range(node_count)] for _ in range(node_count)]
    alpha = [[-1.0 for _ in range(node_count)] for _ in range(node_count)]
    seen = set()
    for link in links:
        if not 0 <= link.u < node_count or not 0 <= link.v < node_count:
            raise ValueError(f"link endpoint outside topology: {link}")
        if link.u == link.v:
            raise ValueError(f"self-links are not allowed: {link}")
        if not math.isfinite(link.bandwidth_gbps) or link.bandwidth_gbps <= 0:
            raise ValueError(f"bandwidth must be finite and positive: {link}")
        if (
            not math.isfinite(link.propagation_delay_ms)
            or link.propagation_delay_ms < 0
        ):
            raise ValueError(
                f"propagation delay must be finite and nonnegative: {link}"
            )

        physical_edge = tuple(sorted((link.u, link.v)))
        if physical_edge in seen:
            raise ValueError(f"duplicate physical link: {physical_edge}")
        seen.add(physical_edge)

        link_capacity = link.capacity_chunks_per_second(chunk_size_gb)
        link_alpha = link.propagation_delay_seconds
        capacity[link.u][link.v] = link_capacity
        capacity[link.v][link.u] = link_capacity
        alpha[link.u][link.v] = link_alpha
        alpha[link.v][link.u] = link_alpha

    return capacity, alpha


def validate_single_chassis(chassis: int, topology_name: str) -> None:
    """Reject a misleading chassis multiplier for fixed WAN graphs."""

    if chassis != 1:
        raise ValueError(
            f"{topology_name} is one fixed WAN graph and requires chassis=1; "
            f"received chassis={chassis}"
        )
