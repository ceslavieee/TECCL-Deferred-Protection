import heapq
import math
from typing import Dict, List, Tuple

from teccl.topologies.topology import Topology


INF = float("inf")


def _edge_cost_in_epochs(
    topology: Topology,
    epoch_duration: float,
    alpha_threshold: float,
    i: int,
    j: int,
) -> float:
    capacity = topology.capacity[i][j]
    if capacity <= 0:
        return INF
    transmission_epochs = max(1.0, 1.0 / (capacity * epoch_duration))
    alpha = topology.alpha[i][j]
    alpha_epochs = 0
    if alpha >= 0 and epoch_duration > 0 and (alpha / epoch_duration) > alpha_threshold:
        alpha_epochs = math.ceil(alpha / epoch_duration)
    return transmission_epochs + alpha_epochs


def _dijkstra_from(
    topology: Topology,
    epoch_duration: float,
    alpha_threshold: float,
    src: int,
) -> List[float]:
    n = len(topology.capacity)
    dist = [INF] * n
    dist[src] = 0.0
    heap: List[Tuple[float, int]] = [(0.0, src)]

    while heap:
        cur_dist, u = heapq.heappop(heap)
        if cur_dist > dist[u]:
            continue
        for v in range(n):
            edge_cost = _edge_cost_in_epochs(topology, epoch_duration, alpha_threshold, u, v)
            if edge_cost == INF:
                continue
            cand = cur_dist + edge_cost
            if cand < dist[v]:
                dist[v] = cand
                heapq.heappush(heap, (cand, v))
    return dist


def all_pairs_shortest_epoch_distances(
    topology: Topology,
    epoch_duration: float,
    alpha_threshold: float,
) -> List[List[float]]:
    return [
        _dijkstra_from(topology, epoch_duration, alpha_threshold, src)
        for src in range(len(topology.capacity))
    ]


def estimate_allgather_shortest_path_completion(
    topology: Topology,
    epoch_duration: float,
    alpha_threshold: float,
    switch_indices: List[int],
) -> Dict[str, object]:
    """
    Greedy shortest-path dissemination heuristic.

    For each source, we grow a dissemination tree over the shortest-path metric.
    Each step chooses the next uninformed node with the earliest possible arrival
    time from the already-informed set.
    """
    participants = [n for n in range(len(topology.capacity)) if n not in switch_indices]
    all_pairs = all_pairs_shortest_epoch_distances(topology, epoch_duration, alpha_threshold)
    per_source_finish: Dict[int, float] = {}

    for src in participants:
        informed = {src}
        arrival = {src: 0.0}
        uninformed = set(participants) - {src}

        while uninformed:
            best_node = None
            best_arrival = INF
            for u in informed:
                for v in uninformed:
                    dist = all_pairs[u][v]
                    if dist == INF:
                        continue
                    cand = arrival[u] + dist
                    if cand < best_arrival:
                        best_arrival = cand
                        best_node = v
            if best_node is None:
                best_arrival = INF
                break
            arrival[best_node] = best_arrival
            informed.add(best_node)
            uninformed.remove(best_node)

        per_source_finish[src] = 0.0 if len(arrival) == 1 else max(arrival.values())

    collective_finish = max(per_source_finish.values()) if per_source_finish else 0.0
    return {
        "per_source_finish_epochs": per_source_finish,
        "collective_finish_epochs": collective_finish,
    }
