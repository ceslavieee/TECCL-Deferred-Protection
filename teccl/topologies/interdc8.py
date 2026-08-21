from teccl.input_data import TopologyParams
from teccl.topologies.research_wan import (
    PhysicalLink,
    build_bidirectional_physical_topology,
    validate_single_chassis,
)
from teccl.topologies.topology import Topology


class InterDC8(Topology):
    """
    Synthetic 8-data-center WAN for resilient collective-communication studies.

    All eight nodes participate in AllGather.  The graph has 13 symmetric
    physical connections (26 directed TE-CCL links), heterogeneous link rates,
    a low-capacity 3--4 backbone, and two bypasses across the regional cut.
    Every node has at least two physical exits, so every single directed-link
    failure leaves the graph connected.  The evaluated failure scope remains a
    directed link, not both directions of a physical fiber.

    Link specifications below are the source of truth.  Bandwidth is written in
    Gbit/s and propagation delay in milliseconds, then converted to TE-CCL's
    chunks/s and seconds by ``build_bidirectional_physical_topology``.
    """

    NODE_COUNT = 8
    PARTICIPANT_COUNT = 8
    PHYSICAL_LINKS = (
        # Local / metro links.
        PhysicalLink(u=0, v=1, bandwidth_gbps=100.0, propagation_delay_ms=0.5),
        PhysicalLink(u=1, v=2, bandwidth_gbps=100.0, propagation_delay_ms=0.6),
        PhysicalLink(u=4, v=5, bandwidth_gbps=100.0, propagation_delay_ms=0.6),
        PhysicalLink(u=6, v=7, bandwidth_gbps=100.0, propagation_delay_ms=0.5),
        # Regional aggregation links.
        PhysicalLink(u=1, v=3, bandwidth_gbps=80.0, propagation_delay_ms=1.2),
        PhysicalLink(u=2, v=3, bandwidth_gbps=80.0, propagation_delay_ms=1.5),
        PhysicalLink(u=4, v=6, bandwidth_gbps=80.0, propagation_delay_ms=1.2),
        PhysicalLink(u=5, v=6, bandwidth_gbps=80.0, propagation_delay_ms=1.4),
        # Backbone and cross-region bypass links.
        PhysicalLink(u=3, v=4, bandwidth_gbps=50.0, propagation_delay_ms=2.8),
        PhysicalLink(u=2, v=6, bandwidth_gbps=60.0, propagation_delay_ms=2.3),
        PhysicalLink(u=1, v=5, bandwidth_gbps=60.0, propagation_delay_ms=2.0),
        # Edge redundancy links.
        PhysicalLink(u=0, v=3, bandwidth_gbps=60.0, propagation_delay_ms=1.8),
        PhysicalLink(u=5, v=7, bandwidth_gbps=60.0, propagation_delay_ms=1.8),
    )

    def __init__(self, topo_input: TopologyParams):
        super().__init__(topo_input)

    def construct_topology(self, topo_input: TopologyParams):
        validate_single_chassis(topo_input.chassis, self.__class__.__name__)
        self.node_per_chassis = self.PARTICIPANT_COUNT
        self.physical_links = self.PHYSICAL_LINKS
        self.capacity, self.alpha = build_bidirectional_physical_topology(
            self.NODE_COUNT,
            self.chunk_size,
            self.physical_links,
        )

    def set_switch_indicies(self) -> None:
        self.switch_indices = []
