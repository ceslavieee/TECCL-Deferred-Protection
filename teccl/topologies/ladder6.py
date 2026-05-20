from teccl.input_data import TopologyParams
from teccl.topologies.topology import Topology


class Ladder6(Topology):
    """
    A small 6-node ladder topology for illustrative resilient-routing experiments.

    Layout:
        0 -- 1 -- 2
        |    |    |
        3 -- 4 -- 5
    """

    def __init__(self, topo_input: TopologyParams):
        super().__init__(topo_input)

    def construct_topology(self, topo_input: TopologyParams):
        self.node_per_chassis = 6
        speed = 100 / self.chunk_size
        self.capacity = [
            # 0    1    2    3    4    5
            [0,   speed, 0,   speed, 0,     0],
            [speed, 0,   speed, 0,   speed, 0],
            [0,   speed, 0,   0,   0,   speed],
            [speed, 0,   0,   0,   speed, 0],
            [0,   speed, 0,   speed, 0,   speed],
            [0,   0,   speed, 0,   speed, 0],
        ]
        self.alpha = []
        for row in self.capacity:
            alpha_row = []
            for value in row:
                if value:
                    alpha_row.append(topo_input.alpha[0])
                else:
                    alpha_row.append(-1)
            self.alpha.append(alpha_row)

    def set_switch_indicies(self) -> None:
        super().set_switch_indicies()
