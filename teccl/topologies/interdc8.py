from teccl.input_data import TopologyParams
from teccl.topologies.topology import Topology


class InterDC8(Topology):
    """
    An 8-node inter-data-center WAN topology for resilient collective-communication studies.

    Layout (conceptual):

        DC0 -- DC1 -- DC2
          |      /     /
          |     /     /
                /     DC6 -- DC7
               DC3 -- DC4 -- DC5

    Design goals:
    - node = one data center
    - heterogeneous link capacities and delays
    - sparse multi-hop WAN, not a fully connected graph
    - clear backbone bottlenecks (DC3 <-> DC4)
    - every edge data center has at least two WAN exits so single-link protection is meaningful
    """

    def __init__(self, topo_input: TopologyParams):
        super().__init__(topo_input)

    def construct_topology(self, topo_input: TopologyParams):
        self.node_per_chassis = 8

        # Normalize capacities against the chunk size so the topology keeps the same
        # style as the existing TE-CCL built-in examples.
        local = 100 / self.chunk_size
        regional = 80 / self.chunk_size
        backbone = 50 / self.chunk_size
        bypass = 60 / self.chunk_size

        n = self.node_per_chassis
        self.capacity = [[0 for _ in range(n)] for _ in range(n)]
        self.alpha = [[-1 for _ in range(n)] for _ in range(n)]

        def add_bidirectional_link(u: int, v: int, capacity: float, alpha: float) -> None:
            self.capacity[u][v] = capacity
            self.capacity[v][u] = capacity
            self.alpha[u][v] = alpha
            self.alpha[v][u] = alpha

        # Local / metro links: higher capacity, lower propagation delay.
        add_bidirectional_link(0, 1, local, 0.5)
        add_bidirectional_link(1, 2, local, 0.6)
        add_bidirectional_link(4, 5, local, 0.6)
        add_bidirectional_link(6, 7, local, 0.5)

        # Regional aggregation links.
        add_bidirectional_link(1, 3, regional, 1.2)
        add_bidirectional_link(2, 3, regional, 1.5)
        add_bidirectional_link(4, 6, regional, 1.2)
        add_bidirectional_link(5, 6, regional, 1.4)

        # Backbone and cross-region links: lower capacity, larger delay.
        add_bidirectional_link(3, 4, backbone, 2.8)
        add_bidirectional_link(2, 6, bypass, 2.3)
        add_bidirectional_link(1, 5, bypass, 2.0)

        # Edge resiliency links so DC0/DC7 are not single-homed. These keep the WAN sparse
        # while making any single-link failure protection study meaningful.
        add_bidirectional_link(0, 3, bypass, 1.8)
        add_bidirectional_link(5, 7, bypass, 1.8)

    def set_switch_indicies(self) -> None:
        super().set_switch_indicies()
