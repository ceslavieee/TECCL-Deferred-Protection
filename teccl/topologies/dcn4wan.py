from teccl.input_data import TopologyParams
from teccl.topologies.research_wan import (
    PhysicalLink,
    build_bidirectional_physical_topology,
    validate_single_chassis,
)
from teccl.topologies.topology import Topology


class DCN4WAN(Topology):
    """
    Synthetic 4-DCN inter-DC WAN used for small, auditable experiments.

    Node mapping:
        0: DCN-1, top-left data center
        1: DCN-2, top-right data center
        2: DCN-3, bottom-left data center
        3: DCN-4, bottom-right data center
        4: WAN-L, left transit node
        5: WAN-T, top transit node
        6: WAN-C, central transit node
        7: WAN-B, bottom transit node
        8: WAN-R, right transit node

    Only DCN nodes participate in AllGather. WAN nodes are copy-capable
    switch/transit nodes under the experiment's ``switch_copy=True`` setting,
    so they forward traffic but do not originate or receive collective chunks.
    The graph has 16 symmetric physical connections (32 directed TE-CCL links)
    and no single physical bridge.

    Link specifications below are the source of truth.  Bandwidth is written in
    Gbit/s and propagation delay in milliseconds, then converted to TE-CCL's
    chunks/s and seconds by ``build_bidirectional_physical_topology``.
    """

    NODE_COUNT = 9
    PARTICIPANT_COUNT = 4
    SWITCH_INDICES = (4, 5, 6, 7, 8)
    PHYSICAL_LINKS = (
        # DC access links to the WAN fabric.
        PhysicalLink(u=0, v=4, bandwidth_gbps=100.0, propagation_delay_ms=0.4),
        PhysicalLink(u=0, v=5, bandwidth_gbps=100.0, propagation_delay_ms=0.5),
        PhysicalLink(u=0, v=6, bandwidth_gbps=80.0, propagation_delay_ms=0.8),
        PhysicalLink(u=1, v=5, bandwidth_gbps=100.0, propagation_delay_ms=0.5),
        PhysicalLink(u=1, v=6, bandwidth_gbps=80.0, propagation_delay_ms=0.8),
        PhysicalLink(u=1, v=8, bandwidth_gbps=100.0, propagation_delay_ms=0.4),
        PhysicalLink(u=2, v=4, bandwidth_gbps=100.0, propagation_delay_ms=0.4),
        PhysicalLink(u=2, v=6, bandwidth_gbps=80.0, propagation_delay_ms=0.8),
        PhysicalLink(u=2, v=7, bandwidth_gbps=100.0, propagation_delay_ms=0.5),
        PhysicalLink(u=3, v=6, bandwidth_gbps=80.0, propagation_delay_ms=0.8),
        PhysicalLink(u=3, v=7, bandwidth_gbps=100.0, propagation_delay_ms=0.5),
        PhysicalLink(u=3, v=8, bandwidth_gbps=100.0, propagation_delay_ms=0.4),
        # Lower-bandwidth WAN-core links.  WAN-C is a hub; the model does not
        # impose a separate aggregate node-capacity limit on that hub.
        PhysicalLink(u=4, v=6, bandwidth_gbps=60.0, propagation_delay_ms=1.2),
        PhysicalLink(u=5, v=6, bandwidth_gbps=60.0, propagation_delay_ms=1.2),
        PhysicalLink(u=6, v=7, bandwidth_gbps=60.0, propagation_delay_ms=1.2),
        PhysicalLink(u=6, v=8, bandwidth_gbps=60.0, propagation_delay_ms=1.2),
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
        self.switch_indices = list(self.SWITCH_INDICES)
