import unittest

from teccl.input_data import TopologyParams
from teccl.topologies.dcn4wan import DCN4WAN
from teccl.topologies.interdc8 import InterDC8
from teccl.topologies.research_wan import (
    PhysicalLink,
    build_bidirectional_physical_topology,
)


def physical_edges(topology):
    return {
        tuple(sorted((i, j)))
        for i in range(len(topology.capacity))
        for j in range(len(topology.capacity))
        if topology.capacity[i][j] > 0
    }


def reachable_nodes(topology, removed_edge=None):
    edges = physical_edges(topology)
    if removed_edge is not None:
        edges.remove(tuple(sorted(removed_edge)))
    adjacency = {node: set() for node in range(len(topology.capacity))}
    for u, v in edges:
        adjacency[u].add(v)
        adjacency[v].add(u)
    reached = {0}
    frontier = [0]
    while frontier:
        node = frontier.pop()
        for neighbor in adjacency[node] - reached:
            reached.add(neighbor)
            frontier.append(neighbor)
    return reached


def minimum_directed_cut_into_subset(topology, root):
    """Return the minimum number of arcs entering a non-root node subset."""

    nodes = set(range(len(topology.capacity)))
    candidates = sorted(nodes - {root})
    minimum = None
    for mask in range(1, 1 << len(candidates)):
        subset = {
            candidates[index]
            for index in range(len(candidates))
            if mask & (1 << index)
        }
        entering = sum(
            topology.capacity[u][v] > 0
            for u in nodes - subset
            for v in subset
        )
        minimum = entering if minimum is None else min(minimum, entering)
    return minimum


class ResearchTopologyTest(unittest.TestCase):
    def build(self, cls, name, chunk_size=100.0, chassis=1):
        return cls(
            TopologyParams(
                name=name,
                chassis=chassis,
                chunk_size=chunk_size,
            )
        )

    def assert_matrix_invariants(self, topology):
        node_count = len(topology.capacity)
        self.assertEqual(node_count, len(topology.alpha))
        self.assertTrue(all(len(row) == node_count for row in topology.capacity))
        self.assertTrue(all(len(row) == node_count for row in topology.alpha))
        for i in range(node_count):
            self.assertEqual(0.0, topology.capacity[i][i])
            self.assertEqual(-1.0, topology.alpha[i][i])
            for j in range(node_count):
                self.assertEqual(topology.capacity[i][j], topology.capacity[j][i])
                self.assertEqual(topology.alpha[i][j], topology.alpha[j][i])
                self.assertEqual(
                    topology.capacity[i][j] > 0,
                    topology.alpha[i][j] >= 0,
                )

    def assert_no_physical_bridge(self, topology):
        all_nodes = set(range(len(topology.capacity)))
        self.assertEqual(all_nodes, reachable_nodes(topology))
        for edge in physical_edges(topology):
            self.assertEqual(all_nodes, reachable_nodes(topology, edge), edge)

    def test_dcn4wan_structure_and_units(self):
        topology = self.build(DCN4WAN, "DCN4WAN")
        self.assertEqual(9, len(topology.capacity))
        self.assertEqual(4, topology.node_per_chassis)
        self.assertEqual([4, 5, 6, 7, 8], topology.switch_indices)
        self.assertEqual(16, len(physical_edges(topology)))
        self.assertAlmostEqual(0.125, topology.capacity[0][4])
        self.assertAlmostEqual(0.0004, topology.alpha[0][4])
        self.assertAlmostEqual(0.075, topology.capacity[4][6])
        self.assertAlmostEqual(0.0012, topology.alpha[4][6])
        self.assert_matrix_invariants(topology)
        self.assert_no_physical_bridge(topology)

    def test_interdc8_structure_and_units(self):
        topology = self.build(InterDC8, "InterDC8")
        self.assertEqual(8, len(topology.capacity))
        self.assertEqual(8, topology.node_per_chassis)
        self.assertEqual([], topology.switch_indices)
        self.assertEqual(13, len(physical_edges(topology)))
        self.assertAlmostEqual(0.125, topology.capacity[0][1])
        self.assertAlmostEqual(0.0005, topology.alpha[0][1])
        self.assertAlmostEqual(0.0625, topology.capacity[3][4])
        self.assertAlmostEqual(0.0028, topology.alpha[3][4])
        self.assert_matrix_invariants(topology)
        self.assert_no_physical_bridge(topology)

    def test_capacity_scales_inversely_with_chunk_size(self):
        large = self.build(InterDC8, "InterDC8", chunk_size=100.0)
        small = self.build(InterDC8, "InterDC8", chunk_size=50.0)
        self.assertAlmostEqual(
            2.0 * large.capacity[0][1],
            small.capacity[0][1],
        )
        self.assertEqual(large.alpha, small.alpha)

    def test_every_participant_supports_two_directed_rooted_structures(self):
        cases = (
            (self.build(DCN4WAN, "DCN4WAN"), range(4)),
            (self.build(InterDC8, "InterDC8"), range(8)),
        )
        for topology, participants in cases:
            for root in participants:
                with self.subTest(topology=type(topology).__name__, root=root):
                    self.assertGreaterEqual(
                        minimum_directed_cut_into_subset(topology, root),
                        2,
                    )

    def test_fixed_wans_reject_chassis_multiplier(self):
        for cls, name in ((DCN4WAN, "DCN4WAN"), (InterDC8, "InterDC8")):
            with self.subTest(topology=name):
                with self.assertRaisesRegex(ValueError, "requires chassis=1"):
                    self.build(cls, name, chassis=2)

    def test_rejects_nonpositive_chunk_size(self):
        for cls, name in ((DCN4WAN, "DCN4WAN"), (InterDC8, "InterDC8")):
            with self.subTest(topology=name):
                with self.assertRaisesRegex(ValueError, "chunk_size must be positive"):
                    self.build(cls, name, chunk_size=0.0)

    def test_rejects_nonfinite_physical_values(self):
        valid_link = PhysicalLink(
            u=0,
            v=1,
            bandwidth_gbps=100.0,
            propagation_delay_ms=0.5,
        )
        for invalid_chunk_size in (float("nan"), float("inf")):
            with self.subTest(chunk_size=invalid_chunk_size):
                with self.assertRaisesRegex(ValueError, "chunk_size must be positive"):
                    build_bidirectional_physical_topology(
                        node_count=2,
                        chunk_size_gb=invalid_chunk_size,
                        links=(valid_link,),
                    )

        invalid_links = (
            PhysicalLink(
                u=0,
                v=1,
                bandwidth_gbps=float("nan"),
                propagation_delay_ms=0.5,
            ),
            PhysicalLink(
                u=0,
                v=1,
                bandwidth_gbps=100.0,
                propagation_delay_ms=float("inf"),
            ),
        )
        for invalid_link in invalid_links:
            with self.subTest(link=invalid_link):
                with self.assertRaisesRegex(ValueError, "must be finite"):
                    build_bidirectional_physical_topology(
                        node_count=2,
                        chunk_size_gb=100.0,
                        links=(invalid_link,),
                    )


if __name__ == "__main__":
    unittest.main()
