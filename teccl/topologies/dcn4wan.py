from teccl.input_data import TopologyParams
from teccl.topologies.topology import Topology


class DCN4WAN(Topology):
    """
    Illustrative 4-DCN inter-DC WAN topology.

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

    Only DCN nodes participate in AllGather. WAN nodes are switch/transit nodes,
    so they forward traffic but do not originate or receive collective chunks.
    """

    def __init__(self, topo_input: TopologyParams):
        super().__init__(topo_input)

    def construct_topology(self, topo_input: TopologyParams):
        self.node_per_chassis = 9

        access = 100 / self.chunk_size
        metro = 80 / self.chunk_size
        core = 60 / self.chunk_size

        n = self.node_per_chassis
        self.capacity = [[0 for _ in range(n)] for _ in range(n)]
        self.alpha = [[-1 for _ in range(n)] for _ in range(n)]

        def add_bidirectional_link(u: int, v: int, capacity: float, alpha: float) -> None:
            self.capacity[u][v] = capacity
            self.capacity[v][u] = capacity
            self.alpha[u][v] = alpha
            self.alpha[v][u] = alpha

        # DC access links to the WAN fabric.
        add_bidirectional_link(0, 4, access, 0.4)  # DCN-1 <-> WAN-L
        add_bidirectional_link(0, 5, access, 0.5)  # DCN-1 <-> WAN-T
        add_bidirectional_link(0, 6, metro, 0.8)   # DCN-1 <-> WAN-C
        add_bidirectional_link(1, 5, access, 0.5)  # DCN-2 <-> WAN-T
        add_bidirectional_link(1, 6, metro, 0.8)   # DCN-2 <-> WAN-C
        add_bidirectional_link(1, 8, access, 0.4)  # DCN-2 <-> WAN-R
        add_bidirectional_link(2, 4, access, 0.4)  # DCN-3 <-> WAN-L
        add_bidirectional_link(2, 6, metro, 0.8)   # DCN-3 <-> WAN-C
        add_bidirectional_link(2, 7, access, 0.5)  # DCN-3 <-> WAN-B
        add_bidirectional_link(3, 6, metro, 0.8)   # DCN-4 <-> WAN-C
        add_bidirectional_link(3, 7, access, 0.5)  # DCN-4 <-> WAN-B
        add_bidirectional_link(3, 8, access, 0.4)  # DCN-4 <-> WAN-R

        # WAN transit links visible in the illustrative figure. The central WAN
        # node is a deliberate bottleneck; alternative routes should come from
        # visible DCN-WAN and WAN-core connections, not from hidden side links.
        add_bidirectional_link(4, 6, core, 1.2)    # WAN-L <-> WAN-C
        add_bidirectional_link(5, 6, core, 1.2)    # WAN-T <-> WAN-C
        add_bidirectional_link(6, 7, core, 1.2)    # WAN-C <-> WAN-B
        add_bidirectional_link(6, 8, core, 1.2)    # WAN-C <-> WAN-R

    def set_switch_indicies(self) -> None:
        self.switch_indices = [4, 5, 6, 7, 8]
