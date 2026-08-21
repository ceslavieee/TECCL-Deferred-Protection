import logging
import json
import math
import re
import time
import heapq
from collections import defaultdict
from itertools import product
from pathlib import Path
from typing import Dict, List, Tuple

import gurobipy as gp
import numpy as np
from gurobipy import GRB

from teccl.input_data import FailureModel, ObjectiveType, SolutionMethod, UserInputParams
from teccl.solvers.base_formulation import BaseFormulation
from teccl.solvers.heuristics import estimate_allgather_shortest_path_completion
from teccl.topologies.topology import Topology


class DeferredProtectionFormulation(BaseFormulation):
    """
    MILP prototype for deferred protection of collective communication.

    The model adapts the DDPP/DDBS idea from deadline-driven inter-DC optical
    networks to AllGather-style demands:
    - Working phase: deliver the collective by a working deadline.
    - Failure event: a directed inter-DC link fails at a configured failure epoch.
    - Deferred recovery phase: each failure scenario has its own recovery flow,
      which can only start after failure detection.
    - Dynamic backup release: demands already delivered before the failure epoch
      do not require backup/recovery resources.

    A single solve is a fixed failure-time scenario evaluation. Broader claims
    about unknown failure timing should be made from an outer sweep over possible
    failure epochs, not from one configured failure epoch alone.

    This is still a flow-level CCL formulation rather than a spectrum-slot optical
    model, but it captures the paper's two key mechanisms: delayed backup
    scheduling and reduced backup holding for already delivered traffic.
    """

    def __init__(self, user_input: UserInputParams, topology: Topology) -> None:
        super().__init__(user_input, topology)
        self.solver_name = "DeferredProtection_MILP"
        self.required_flows = []
        self.flows_str_info = {}
        heuristic_summary = estimate_allgather_shortest_path_completion(
            topology=self.topology,
            epoch_duration=self.epoch_duration,
            alpha_threshold=self.user_input.instance.alpha_threshold,
            switch_indices=self.topology.switch_indices,
        )
        self.heuristic_working_finish_epochs = max(
            1.0,
            float(heuristic_summary["collective_finish_epochs"]),
        )
        # First deferred baseline: use a simple two-phase upper-bound heuristic.
        # Phase 1 approximates the working collective; Phase 2 budgets a similar
        # amount of time for deferred protection/recovery.
        self.heuristic_finish_epochs = 2.0 * self.heuristic_working_finish_epochs
        self.heuristic_finish_time = self.heuristic_finish_epochs * self.epoch_duration
        self.deadline_factor = max(1.0, float(self.user_input.instance.deferred_deadline_factor))
        derived_deadline_epochs = max(
            1,
            math.ceil(self.heuristic_finish_epochs * self.deadline_factor),
        )
        if derived_deadline_epochs > self.num_epochs:
            self.user_input.instance.num_epochs = derived_deadline_epochs
            self.set_num_epochs(derived_deadline_epochs)
        if self.user_input.instance.fixed_working_schedule:
            self.final_deadline = self.num_epochs - 1
        else:
            self.final_deadline = min(self.num_epochs - 1, derived_deadline_epochs - 1)
        self.working_deadline = min(
            self.final_deadline,
            max(0, int((self.final_deadline + 1) * self.user_input.instance.working_deadline_ratio) - 1),
        )
        # DedicatedProtectionFormulation and SharedProtectionFormulation inherit
        # this helper implementation, but their baseline semantics are static
        # pre-planned protection. Keep concrete failure-time logic exclusive to
        # the actual deferred formulation.
        self.real_failure_timing_enabled = (
            self.user_input.instance.enable_real_failure_timing
            and type(self) is DeferredProtectionFormulation
        )
        observation_ratio = self.user_input.instance.failure_observation_ratio
        if observation_ratio < 0:
            self.failure_observation_epoch = max(0, int((self.working_deadline + 1) * 0.5) - 1)
        else:
            self.failure_observation_epoch = min(
                self.final_deadline,
                max(0, int((self.final_deadline + 1) * observation_ratio) - 1),
            )
        failure_time_epoch = self.user_input.instance.failure_time_epoch
        if failure_time_epoch < 0:
            self.failure_time_epoch = max(0, int((self.working_deadline + 1) * 0.5) - 1)
        else:
            self.failure_time_epoch = min(self.final_deadline, max(0, int(failure_time_epoch)))
        self.detection_delay_epochs = max(0, int(self.user_input.instance.detection_delay_epochs))
        activation_ratio = self.user_input.instance.deferred_activation_ratio
        if activation_ratio < 0 and self.real_failure_timing_enabled:
            self.deferred_activation_epoch = min(
                self.final_deadline,
                self.failure_time_epoch + self.detection_delay_epochs,
            )
        elif activation_ratio < 0:
            self.deferred_activation_epoch = min(self.final_deadline, self.working_deadline + 1)
        else:
            self.deferred_activation_epoch = min(
                self.final_deadline,
                max(0, int((self.final_deadline + 1) * activation_ratio)),
            )
        self.recovery_start_epoch = min(
            self.final_deadline,
            max(self.deferred_activation_epoch, self.failure_time_epoch + self.detection_delay_epochs),
        )
        self.recovery_inherits_working_arrivals = True
        self.enable_dynamic_backup_release = self.user_input.instance.enable_dynamic_backup_release
        self.failure_scenarios = self._enumerate_failure_scenarios()
        self._fixed_demand_link_epoch_cache = None
        self.prior_link_epoch_occupancy = self._load_prior_link_epoch_occupancy()

    def _load_prior_link_epoch_occupancy(self) -> Dict[Tuple[int, int, int], int]:
        """Load already committed traffic in request-local link epochs."""

        occupancy_path = self.user_input.instance.prior_link_epoch_occupancy_file
        if not occupancy_path:
            return {}
        path = Path(occupancy_path)
        payload = json.loads(path.read_text())
        if payload.get("accounting_unit") != "occupied chunk-transfer units per directed link-epoch":
            raise ValueError(
                "Unsupported prior occupancy accounting unit in "
                f"{occupancy_path}"
            )
        occupancy: Dict[Tuple[int, int, int], int] = defaultdict(int)
        for row in payload.get("epochs", []):
            i = int(row["src"])
            j = int(row["dst"])
            k = int(row["epoch"])
            value = int(row.get("occupancy", 1))
            if not (0 <= i < self.num_nodes and 0 <= j < self.num_nodes):
                raise ValueError(f"Prior occupancy uses unknown link {i}->{j}")
            if self.topology.capacity[i][j] <= 0:
                raise ValueError(f"Prior occupancy uses absent link {i}->{j}")
            if not 0 <= k < self.num_epochs:
                raise ValueError(
                    f"Prior occupancy epoch {k} outside local horizon "
                    f"[0, {self.num_epochs})"
                )
            if value <= 0:
                raise ValueError(f"Prior occupancy must be positive: {row}")
            occupancy[(i, j, k)] += value

        for (i, j, k), value in occupancy.items():
            epoch_capacity = self.topology.capacity[i][j] * self.epoch_duration
            beta_num_back = max(0, int(np.ceil(1 / epoch_capacity)) - 1)
            nominal_bound = (beta_num_back + 1) * epoch_capacity
            if value > nominal_bound + 1e-9:
                raise ValueError(
                    f"Prior occupancy {value} exceeds nominal bound "
                    f"{nominal_bound} on {i}->{j} at epoch {k}"
                )
        return dict(occupancy)

    def _prior_occupancy(self, i: int, j: int, k: int) -> int:
        return int(self.prior_link_epoch_occupancy.get((i, j, k), 0))

    def _enumerate_failure_scenarios(self) -> List[Tuple[int, int]]:
        scenarios = []
        for i, j in product(self.nodes, self.nodes):
            if self.topology.capacity[i][j] > 0:
                scenarios.append((i, j))
        if self.user_input.instance.failure_scenario_offset > 0:
            scenarios = scenarios[self.user_input.instance.failure_scenario_offset :]
        if self.user_input.instance.max_failure_scenarios >= 0:
            scenarios = scenarios[:self.user_input.instance.max_failure_scenarios]
        return scenarios

    @staticmethod
    def _is_var(value) -> bool:
        return isinstance(value, gp.Var)

    def _expand_fixed_working_link(self, start_node: int, end_node: int, switches: str) -> List[Tuple[int, int]]:
        if not switches:
            return [(start_node, end_node)]
        switch_nodes = [int(node) for node in re.findall(r"\d+", switches)]
        nodes = [start_node] + switch_nodes + [end_node]
        return [(nodes[idx], nodes[idx + 1]) for idx in range(len(nodes) - 1)]

    def _load_fixed_working_flows(self) -> List[Tuple[int, int, int, int, int]]:
        schedule_path = self.user_input.instance.fixed_working_schedule
        if not schedule_path:
            return []
        data = json.loads(Path(schedule_path).read_text())
        flow_rows = (
            data.get("7a-Raw_Flows")
            or data.get("10-Working_Flows")
            or data.get("11-Working_Flows")
            or data.get("7-Flows")
            or []
        )
        flow_re = re.compile(
            r"Chunk (?P<c>\d+) from (?P<s>\d+) traveled over "
            r"(?P<i>\d+)->(?P<j>\d+) in epoch (?P<k>\d+)"
            r"(?: via switches (?P<switches>[\d,\- >]+))?"
        )
        fixed_flows = []
        for row in flow_rows:
            match = flow_re.search(row)
            if not match:
                raise ValueError(f"Could not parse fixed working flow: {row}")
            s = int(match.group("s"))
            c = int(match.group("c"))
            k = int(match.group("k"))
            start_node = int(match.group("i"))
            end_node = int(match.group("j"))
            switches = match.group("switches") or ""
            for i, j in self._expand_fixed_working_link(start_node, end_node, switches):
                fixed_flows.append((s, i, j, c, k))
        return fixed_flows

    def _load_fixed_working_demand_links(self) -> List[Tuple[int, int, int, int, int]]:
        schedule_path = self.user_input.instance.fixed_working_schedule
        if not schedule_path:
            return []
        data = json.loads(Path(schedule_path).read_text())
        demand_rows = data.get("10a-Working_Demand_Links") or data.get("11a-Working_Demand_Links") or []
        demand_links = []
        demand_link_re = re.compile(
            r"Demand (?P<s>\d+)->(?P<d>\d+) chunk (?P<c>\d+) uses (?P<i>\d+)->(?P<j>\d+)"
        )
        for row in demand_rows:
            if isinstance(row, dict):
                s = int(row["source"])
                d = int(row["destination"])
                c = int(row["chunk"])
                link = row["link"]
                i, j = [int(node) for node in link.split("->")]
            else:
                match = demand_link_re.search(str(row))
                if not match:
                    raise ValueError(f"Could not parse fixed working demand link: {row}")
                s = int(match.group("s"))
                d = int(match.group("d"))
                c = int(match.group("c"))
                i = int(match.group("i"))
                j = int(match.group("j"))
            demand_links.append((s, d, i, j, c))
        return demand_links

    def _load_fixed_working_demand_link_epochs(self) -> List[Tuple[int, int, int, int, int, int]]:
        if self._fixed_demand_link_epoch_cache is not None:
            return self._fixed_demand_link_epoch_cache
        schedule_path = self.user_input.instance.fixed_working_schedule
        if not schedule_path:
            self._fixed_demand_link_epoch_cache = []
            return []
        data = json.loads(Path(schedule_path).read_text())
        epoch_rows = (
            data.get("10b-Working_Demand_Link_Epochs")
            or data.get("11b-Working_Demand_Link_Epochs")
            or []
        )
        demand_link_epochs = []
        demand_link_epoch_re = re.compile(
            r"Demand (?P<s>\d+)->(?P<d>\d+) chunk (?P<c>\d+) uses "
            r"(?P<i>\d+)->(?P<j>\d+) in epoch (?P<k>\d+)"
        )
        for row in epoch_rows:
            if isinstance(row, dict):
                s = int(row["source"])
                d = int(row["destination"])
                c = int(row["chunk"])
                k = int(row["epoch"])
                i, j = [int(node) for node in row["link"].split("->")]
            else:
                match = demand_link_epoch_re.search(str(row))
                if not match:
                    raise ValueError(f"Could not parse fixed working demand link epoch: {row}")
                s = int(match.group("s"))
                d = int(match.group("d"))
                c = int(match.group("c"))
                i = int(match.group("i"))
                j = int(match.group("j"))
                k = int(match.group("k"))
            demand_link_epochs.append((s, d, i, j, c, k))
        self._fixed_demand_link_epoch_cache = demand_link_epochs
        return self._fixed_demand_link_epoch_cache

    def _fixed_demand_link_after_failure_values(self) -> Dict[Tuple[int, int, int, int, int], int]:
        demand_link_epochs = self._load_fixed_working_demand_link_epochs()
        if not demand_link_epochs:
            return {}
        fixed_values = {key: 0 for key in self._load_fixed_working_demand_links()}
        for s, d, i, j, c, k in demand_link_epochs:
            key = (s, d, i, j, c)
            if key in fixed_values and k >= self.failure_time_epoch:
                fixed_values[key] = 1
        return fixed_values

    def fixed_working_schedule_constraints(self) -> None:
        fixed_flows = set(self._load_fixed_working_flows())
        if not fixed_flows:
            return
        start = time.time()
        fixed_count = 0
        for s, i, j, c, k in product(self.nodes, self.nodes, self.nodes, self.chunks, self.epochs):
            if self.topology.capacity[i][j] <= 0:
                continue
            flow_var = self.flow_w[s][i][j][c][k]
            if not self._is_var(flow_var):
                if (s, i, j, c, k) in fixed_flows:
                    raise ValueError(
                        "Fixed working schedule references unavailable flow "
                        f"(source={s}, link={i}->{j}, chunk={c}, epoch={k})"
                    )
                continue
            value = 1 if (s, i, j, c, k) in fixed_flows else 0
            self.model.addConstr(
                flow_var == value,
                name="fixed_working_flow_%d_%d_%d_%d_%d" % (s, i, j, c, k),
            )
            fixed_count += value
        logging.debug(
            "Fixed %d working flows from %s in %s",
            fixed_count,
            self.user_input.instance.fixed_working_schedule,
            time.time() - start,
        )
        fixed_demand_links = set(self._load_fixed_working_demand_links())
        if not fixed_demand_links:
            return
        fixed_demand_count = 0
        for key, demand_var in self.demand_link_used_w.items():
            value = 1 if key in fixed_demand_links else 0
            self.model.addConstr(
                demand_var == value,
                name="fixed_working_demand_link_%d_%d_%d_%d_%d" % key,
            )
            fixed_demand_count += value
        unavailable = fixed_demand_links.difference(self.demand_link_used_w.keys())
        if unavailable:
            key = sorted(unavailable)[0]
            raise ValueError(
                "Fixed working schedule references unavailable demand link "
                f"(source={key[0]}, destination={key[1]}, link={key[2]}->{key[3]}, chunk={key[4]})"
            )
        logging.debug(
            "Fixed %d working demand links from %s in %s",
            fixed_demand_count,
            self.user_input.instance.fixed_working_schedule,
            time.time() - start,
        )

    def initialize_variables(self) -> None:
        logging.debug("Initializing deferred-protection variables")
        time_start = time.time()
        shape_flow = (self.num_nodes, self.num_nodes, self.num_nodes, self.num_chunks, self.num_epochs)
        shape_buffer = (self.num_nodes, self.num_nodes, self.num_chunks, self.num_epochs)
        self.flow_w = np.zeros(shape_flow).tolist()
        self.flow_p = np.zeros(shape_flow).tolist()
        self.buffer_w = np.zeros(shape_buffer).tolist()
        self.buffer_p = np.zeros(shape_buffer).tolist()
        self.total_demand_sat_w = np.zeros(shape_buffer).tolist()
        self.total_demand_sat_p = np.zeros(shape_buffer).tolist()
        self.link_used_w = np.zeros((self.num_nodes, self.num_nodes, self.num_nodes, self.num_chunks)).tolist()
        self.link_used_p = np.zeros((self.num_nodes, self.num_nodes, self.num_nodes, self.num_chunks)).tolist()
        self.flow_r = {}
        self.buffer_r = {}
        self.total_demand_sat_r = {}
        self.link_used_r = {}
        self.link_used_w_after_failure = {}
        self.demand_link_used_w = {}
        self.demand_link_used_w_after_failure = {}
        self.scenario_demand_sat = {}
        self.scenario_demand_fail = {}
        self.working_completion_epoch = self.model.addVar(
            0, self.num_epochs, vtype=GRB.CONTINUOUS, name="T_work"
        )
        self.protection_completion_epoch = self.model.addVar(
            0, self.num_epochs, vtype=GRB.CONTINUOUS, name="T_protection"
        )

        for i, j in product(self.nodes, self.nodes):
            if self.topology.capacity[i][j] <= 0:
                continue
            alpha_num_back = self.get_alpha_num_back(i, j)
            link_type = self.get_link_type(i, j)
            beta_num_back = self.get_beta_num_back(i, j)
            extra_epochs = alpha_num_back + beta_num_back
            if link_type == self.LinkType.GPU_SWITCH and not self.user_input.instance.switch_to_gpu_link_on:
                extra_epochs += 1
            not_necessary_k = [self.num_epochs - epoch - 1 for epoch in range(extra_epochs)]
            for s, c, k in product(self.nodes, self.chunks, self.epochs):
                if s == j:
                    continue
                if k in not_necessary_k:
                    continue
                self.flow_w[s][i][j][c][k] = self.model.addVar(
                    0, 1, vtype=GRB.INTEGER, name="flow_w_%d_%d_%d_%d_%d" % (s, i, j, c, k)
                )
                self.flow_p[s][i][j][c][k] = self.model.addVar(
                    0, 1, vtype=GRB.INTEGER, name="flow_p_%d_%d_%d_%d_%d" % (s, i, j, c, k)
                )
            for s, c in product(self.nodes, self.chunks):
                self.link_used_w[s][i][j][c] = self.model.addVar(
                    0, 1, vtype=GRB.BINARY, name="link_used_w_%d_%d_%d_%d" % (s, i, j, c)
                )
                self.link_used_w_after_failure[(s, i, j, c)] = self.model.addVar(
                    0,
                    1,
                    vtype=GRB.BINARY,
                    name="link_used_w_after_failure_%d_%d_%d_%d" % (s, i, j, c),
                )
                self.link_used_p[s][i][j][c] = self.model.addVar(
                    0, 1, vtype=GRB.BINARY, name="link_used_p_%d_%d_%d_%d" % (s, i, j, c)
                )

        for s, i, c, k in product(self.nodes, self.nodes, self.chunks, self.epochs):
            self.buffer_w[s][i][c][k] = self.model.addVar(
                0, 1, vtype=GRB.INTEGER, name="buffer_w_%d_%d_%d_%d" % (s, i, c, k)
            )
            self.buffer_p[s][i][c][k] = self.model.addVar(
                0, 1, vtype=GRB.INTEGER, name="buffer_p_%d_%d_%d_%d" % (s, i, c, k)
            )
            if self.demand[s][i][c]:
                self.total_demand_sat_w[s][i][c][k] = self.model.addVar(
                    0, 1, vtype=GRB.INTEGER, name="total_demand_w_%d_%d_%d_%d" % (s, i, c, k)
                )
                self.total_demand_sat_p[s][i][c][k] = self.model.addVar(
                    0, 1, vtype=GRB.INTEGER, name="total_demand_p_%d_%d_%d_%d" % (s, i, c, k)
                )
        for s, d, c, i, j in product(self.nodes, self.nodes, self.chunks, self.nodes, self.nodes):
            if not self.demand[s][d][c]:
                continue
            if self.topology.capacity[i][j] <= 0:
                continue
            self.demand_link_used_w[(s, d, i, j, c)] = self.model.addVar(
                0, 1, vtype=GRB.BINARY, name="demand_link_used_w_%d_%d_%d_%d_%d" % (s, d, i, j, c)
            )
            self.demand_link_used_w_after_failure[(s, d, i, j, c)] = self.model.addVar(
                0,
                1,
                vtype=GRB.BINARY,
                name="demand_link_used_w_after_failure_%d_%d_%d_%d_%d" % (s, d, i, j, c),
            )
        if self.user_input.instance.enable_failure_scenarios:
            for scenario_idx, _ in enumerate(self.failure_scenarios):
                for s, d, c in product(self.nodes, self.nodes, self.chunks):
                    if self.demand[s][d][c]:
                        self.scenario_demand_fail[(scenario_idx, s, d, c)] = self.model.addVar(
                            0, 1, vtype=GRB.BINARY, name="scenario_demand_fail_%d_%d_%d_%d" % (scenario_idx, s, d, c)
                        )
                        self.scenario_demand_sat[(scenario_idx, s, d, c)] = self.model.addVar(
                            0, 1, vtype=GRB.BINARY, name="scenario_demand_sat_%d_%d_%d_%d" % (scenario_idx, s, d, c)
                        )
        if self.real_failure_timing_enabled and self.user_input.instance.enable_failure_scenarios:
            for scenario_idx, _ in enumerate(self.failure_scenarios):
                for i, j in product(self.nodes, self.nodes):
                    if self.topology.capacity[i][j] <= 0:
                        continue
                    alpha_num_back = self.get_alpha_num_back(i, j)
                    link_type = self.get_link_type(i, j)
                    beta_num_back = self.get_beta_num_back(i, j)
                    extra_epochs = alpha_num_back + beta_num_back
                    if link_type == self.LinkType.GPU_SWITCH and not self.user_input.instance.switch_to_gpu_link_on:
                        extra_epochs += 1
                    not_necessary_k = [self.num_epochs - epoch - 1 for epoch in range(extra_epochs)]
                    for s, c, k in product(self.nodes, self.chunks, self.epochs):
                        if s == j:
                            continue
                        if k in not_necessary_k:
                            continue
                        self.flow_r[(scenario_idx, s, i, j, c, k)] = self.model.addVar(
                            0,
                            1,
                            vtype=GRB.INTEGER,
                            name="flow_r_%d_%d_%d_%d_%d_%d" % (scenario_idx, s, i, j, c, k),
                        )
                    for s, c in product(self.nodes, self.chunks):
                        self.link_used_r[(scenario_idx, s, i, j, c)] = self.model.addVar(
                            0,
                            1,
                            vtype=GRB.BINARY,
                            name="link_used_r_%d_%d_%d_%d_%d" % (scenario_idx, s, i, j, c),
                        )
                for s, i, c, k in product(self.nodes, self.nodes, self.chunks, self.epochs):
                    self.buffer_r[(scenario_idx, s, i, c, k)] = self.model.addVar(
                        0,
                        1,
                        vtype=GRB.INTEGER,
                        name="buffer_r_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                    )
                    if self.demand[s][i][c]:
                        self.total_demand_sat_r[(scenario_idx, s, i, c, k)] = self.model.addVar(
                            0,
                            1,
                            vtype=GRB.INTEGER,
                            name="total_demand_r_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                        )
        logging.debug("Finished initializing deferred-protection variables in %s", time.time() - time_start)

    def _add_phase_gating_constraints(self) -> None:
        for i, j in product(self.nodes, self.nodes):
            if self.topology.capacity[i][j] <= 0:
                continue
            for s, c, k in product(self.nodes, self.chunks, self.epochs):
                if self._is_var(self.flow_w[s][i][j][c][k]) and k > self.working_deadline:
                    self.model.addConstr(self.flow_w[s][i][j][c][k] == 0, name="working_window_%d_%d_%d_%d_%d" % (s, i, j, c, k))
                if self._is_var(self.flow_p[s][i][j][c][k]) and k < self.deferred_activation_epoch:
                    self.model.addConstr(
                        self.flow_p[s][i][j][c][k] == 0,
                        name="deferred_protection_window_%d_%d_%d_%d_%d" % (s, i, j, c, k),
                    )

    def _add_destination_constraints_for_phase(
        self,
        flow_vars: List[List[List[List[List[gp.Var]]]]],
        buffer_vars: List[List[List[List[gp.Var]]]],
        demand_vars: List[List[List[List[gp.Var]]]],
        phase_name: str,
        required_epoch: int,
        require_all_demands: bool = True,
    ) -> None:
        start = time.time()
        for s, d, c, k in product(self.nodes, self.nodes, self.chunks, self.epochs):
            if not self.demand[s][d][c]:
                continue
            if k + 1 < self.num_epochs:
                self.model.addConstr(
                    demand_vars[s][d][c][k] == buffer_vars[s][d][c][k + 1],
                    name="dest_%s_%d_%d_%d_%d" % (phase_name, s, d, c, k),
                )
            else:
                dem_sat_constr = gp.LinExpr(0.0)
                dem_sat_constr.add(buffer_vars[s][d][c][k])
                for i in self.nodes:
                    if self.topology.capacity[i][d] <= 0:
                        continue
                    alpha_num_back = self.get_alpha_num_back(i, d)
                    link_type = self.get_link_type(i, d)
                    if link_type != self.LinkType.SWITCH_GPU or self.user_input.instance.switch_to_gpu_link_on:
                        beta_num_back = self.get_beta_num_back(i, d)
                        if k - alpha_num_back - beta_num_back >= 0:
                            dem_sat_constr.add(flow_vars[s][i][d][c][k - alpha_num_back - beta_num_back])
                self.model.addConstr(
                    dem_sat_constr == demand_vars[s][d][c][k],
                    name="dest_last_%s_%d_%d_%d_%d" % (phase_name, s, d, c, k),
                )
            if k < self.num_epochs - 1:
                self.model.addConstr(
                    demand_vars[s][d][c][k] <= demand_vars[s][d][c][k + 1],
                    name="dest_monotonic_%s_%d_%d_%d_%d" % (phase_name, s, d, c, k),
                )
        if require_all_demands:
            for s, d, c in product(self.nodes, self.nodes, self.chunks):
                if self.demand[s][d][c]:
                    self.model.addConstr(
                        demand_vars[s][d][c][required_epoch] == 1,
                        name="dest_required_%s_%d_%d_%d" % (phase_name, s, d, c),
                    )
        logging.debug("Finished adding %s destination constraints in %s", phase_name, time.time() - start)

    def _add_completion_time_constraints_for_phase(
        self,
        demand_vars: List[List[List[List[gp.Var]]]],
        completion_var: gp.Var,
        phase_name: str,
    ) -> None:
        """Bind an explicit completion-time variable to first demand satisfaction.

        total_demand_sat variables are monotone in k. Therefore
        y[k] - y[k-1] is 1 only at the first epoch where a demand becomes
        satisfied. Minimizing completion_var then minimizes the maximum first
        satisfaction epoch across all relevant demands.
        """
        start = time.time()
        for s, d, c, k in product(self.nodes, self.nodes, self.chunks, self.epochs):
            if not self.demand[s][d][c]:
                continue
            first_satisfied_at_k = gp.LinExpr(0.0)
            first_satisfied_at_k.add(demand_vars[s][d][c][k])
            if k > 0:
                first_satisfied_at_k.add(demand_vars[s][d][c][k - 1], -1.0)
            self.model.addConstr(
                completion_var >= (k + 1) * first_satisfied_at_k,
                name="completion_time_%s_%d_%d_%d_%d" % (phase_name, s, d, c, k),
            )
        logging.debug("Finished adding %s completion-time constraints in %s", phase_name, time.time() - start)

    def completion_time_constraints(self) -> None:
        self._add_completion_time_constraints_for_phase(
            self.total_demand_sat_w,
            self.working_completion_epoch,
            "working",
        )
        self._add_completion_time_constraints_for_phase(
            self.total_demand_sat_p,
            self.protection_completion_epoch,
            "protection",
        )

    def _add_node_constraints_for_phase(
        self,
        flow_vars: List[List[List[List[List[gp.Var]]]]],
        buffer_vars: List[List[List[List[gp.Var]]]],
        phase_name: str,
    ) -> None:
        start = time.time()
        for i, s, c, k in product(self.nodes, self.nodes, self.chunks, self.epochs):
            buffer_constr = gp.LinExpr(0.0)
            if k == 0 and i != s:
                buffer_constr.add(buffer_vars[s][i][c][k])
                self.model.addConstr(
                    buffer_constr == 0,
                    name="node_first_buffer_%s_%d_%d_%d_%d" % (phase_name, s, i, c, k),
                )
            elif k == 0 and i == s:
                dem_c = max([self.demand[s][d][c] for d in self.nodes])
                buffer_constr.add(buffer_vars[s][s][c][0])
                self.model.addConstr(
                    buffer_constr == dem_c,
                    name="node_initial_buffer_%s_%d_%d_%d_%d" % (phase_name, s, i, c, k),
                )
            elif i not in self.topology.switch_indices:
                buffer_constr.add(buffer_vars[s][i][c][k - 1])
                for j in self.nodes:
                    if self.topology.capacity[j][i] <= 0:
                        continue
                    alpha_num_back = self.get_alpha_num_back(j, i)
                    beta_num_back = self.get_beta_num_back(j, i)
                    link_type = self.get_link_type(j, i)
                    if link_type != self.LinkType.SWITCH_GPU or self.user_input.instance.switch_to_gpu_link_on:
                        if k - alpha_num_back - 1 - beta_num_back >= 0:
                            buffer_constr.add(flow_vars[s][j][i][c][k - alpha_num_back - 1 - beta_num_back])
                    else:
                        if k - alpha_num_back >= 0:
                            buffer_constr.add(flow_vars[s][j][i][c][k - alpha_num_back])
                self.model.addConstr(
                    buffer_constr == buffer_vars[s][i][c][k],
                    name="node_buffer_%s_%d_%d_%d_%d" % (phase_name, s, i, c, k),
                )
            else:
                buffer_constr.add(buffer_vars[s][i][c][k])
                self.model.addConstr(
                    buffer_constr == 0,
                    name="node_switch_buffer_%s_%d_%d_%d_%d" % (phase_name, s, i, c, k),
                )

            if i not in self.topology.switch_indices:
                outgoing = [flow_vars[s][i][v][c][k] for v in range(self.num_nodes) if self.topology.capacity[i][v] > 0]
                self.aux_var.append(
                    self.model.addVar(0, GRB.INFINITY, vtype=GRB.CONTINUOUS, name="aux_var_%s_%d_%d_%d_%d" % (phase_name, s, i, c, k))
                )
                self.model.addConstr(
                    self.aux_var[-1] == gp.max_(outgoing),
                    name="node_aux_flow_%s_%d_%d_%d_%d" % (phase_name, s, i, c, k),
                )
                node_constr = gp.LinExpr(0.0)
                node_constr.add(buffer_vars[s][i][c][k])
                self.model.addConstr(
                    node_constr >= self.aux_var[-1],
                    name="node_flow_%s_%d_%d_%d_%d" % (phase_name, s, i, c, k),
                )
            else:
                switch_node_constr = gp.LinExpr(0.0)
                if k > 0:
                    for j in range(self.num_nodes):
                        if self.topology.capacity[j][i] > 0:
                            alpha_num_back = self.get_alpha_num_back(j, i)
                            beta_num_back = self.get_beta_num_back(j, i)
                            if k - alpha_num_back - 1 - beta_num_back >= 0:
                                switch_node_constr.add(flow_vars[s][j][i][c][k - alpha_num_back - 1 - beta_num_back])
                outgoing = gp.LinExpr(0.0)
                for v in range(self.num_nodes):
                    if self.topology.capacity[i][v] > 0:
                        outgoing.add(flow_vars[s][i][v][c][k])
                if not self.user_input.instance.switch_copy:
                    self.model.addConstr(
                        switch_node_constr >= outgoing,
                        name="switch_flow_%s_%d_%d_%d_%d" % (phase_name, s, i, c, k),
                    )
                else:
                    outgoing_list = [flow_vars[s][i][v][c][k] for v in range(self.num_nodes) if self.topology.capacity[i][v] > 0]
                    self.aux_var.append(
                        self.model.addVar(0, GRB.INFINITY, vtype=GRB.CONTINUOUS, name="aux_switch_%s_%d_%d_%d_%d" % (phase_name, s, i, c, k))
                    )
                    self.model.addConstr(
                        self.aux_var[-1] == gp.max_(outgoing_list),
                        name="node_switch_flow_%s_%d_%d_%d_%d" % (phase_name, s, i, c, k),
                    )
                    self.model.addConstr(
                        switch_node_constr >= self.aux_var[-1],
                        name="switch_copy_flow_%s_%d_%d_%d_%d" % (phase_name, s, i, c, k),
                    )
        logging.debug("Finished adding %s node constraints in %s", phase_name, time.time() - start)

    def capacity_constraints(self) -> None:
        logging.debug("Adding deferred-protection capacity constraints")
        start = time.time()
        for i, j, k in product(self.nodes, self.nodes, self.epochs):
            if self.topology.capacity[i][j] <= 0:
                continue
            cap_constr = gp.LinExpr(0.0)
            epoch_capacity = self.topology.capacity[i][j] * self.epoch_duration
            beta_num_back = max(0, int(np.ceil(1 / epoch_capacity)) - 1)
            for l in range(beta_num_back + 1):
                for s, c in product(self.nodes, self.chunks):
                    if k - l >= 0:
                        cap_constr.add(self.flow_w[s][i][j][c][k - l])
                        cap_constr.add(self.flow_p[s][i][j][c][k - l])
            cap_constr.addConstant(self._prior_occupancy(i, j, k))
            self.model.addConstr(
                cap_constr <= ((beta_num_back + 1) * epoch_capacity),
                name="capacity_%d_%d_%d" % (i, j, k),
            )
        logging.debug("Finished adding deferred-protection capacity constraints in %s", time.time() - start)

    def link_usage_constraints(self) -> None:
        start = time.time()
        for s, i, j, c in product(self.nodes, self.nodes, self.nodes, self.chunks):
            if self.topology.capacity[i][j] <= 0:
                continue
            working_vars = [self.flow_w[s][i][j][c][k] for k in self.epochs if self._is_var(self.flow_w[s][i][j][c][k])]
            protection_vars = [self.flow_p[s][i][j][c][k] for k in self.epochs if self._is_var(self.flow_p[s][i][j][c][k])]

            if working_vars:
                self.model.addConstr(
                    gp.quicksum(working_vars) >= self.link_used_w[s][i][j][c],
                    name="working_link_use_lb_%d_%d_%d_%d" % (s, i, j, c),
                )
                for k, flow_var in enumerate(working_vars):
                    self.model.addConstr(
                        flow_var <= self.link_used_w[s][i][j][c],
                        name="working_link_use_ub_%d_%d_%d_%d_%d" % (s, i, j, c, k),
                    )
            else:
                self.model.addConstr(
                    self.link_used_w[s][i][j][c] == 0,
                    name="working_link_unused_%d_%d_%d_%d" % (s, i, j, c),
                )

            after_failure_var = self.link_used_w_after_failure[(s, i, j, c)]
            working_after_failure_vars = [
                self.flow_w[s][i][j][c][k]
                for k in self.epochs
                if k >= self.failure_time_epoch and self._is_var(self.flow_w[s][i][j][c][k])
            ]
            if working_after_failure_vars:
                self.model.addConstr(
                    gp.quicksum(working_after_failure_vars) >= after_failure_var,
                    name="working_after_failure_link_use_lb_%d_%d_%d_%d" % (s, i, j, c),
                )
                for idx, flow_var in enumerate(working_after_failure_vars):
                    self.model.addConstr(
                        flow_var <= after_failure_var,
                        name="working_after_failure_link_use_ub_%d_%d_%d_%d_%d" % (s, i, j, c, idx),
                    )
            else:
                self.model.addConstr(
                    after_failure_var == 0,
                    name="working_after_failure_link_unused_%d_%d_%d_%d" % (s, i, j, c),
                )

            if protection_vars:
                self.model.addConstr(
                    gp.quicksum(protection_vars) >= self.link_used_p[s][i][j][c],
                    name="protection_link_use_lb_%d_%d_%d_%d" % (s, i, j, c),
                )
                for k, flow_var in enumerate(protection_vars):
                    self.model.addConstr(
                        flow_var <= self.link_used_p[s][i][j][c],
                        name="protection_link_use_ub_%d_%d_%d_%d_%d" % (s, i, j, c, k),
                    )
            else:
                self.model.addConstr(
                    self.link_used_p[s][i][j][c] == 0,
                    name="protection_link_unused_%d_%d_%d_%d" % (s, i, j, c),
                )

            if self.user_input.instance.protection_link_disjoint:
                self.model.addConstr(
                    self.link_used_w[s][i][j][c] + self.link_used_p[s][i][j][c] <= 1,
                    name="link_disjoint_%d_%d_%d_%d" % (s, i, j, c),
                )
        logging.debug("Finished adding link-usage constraints in %s", time.time() - start)

    def demand_path_constraints(self) -> None:
        start = time.time()
        fixed_after_failure_values = (
            self._fixed_demand_link_after_failure_values()
            if self.real_failure_timing_enabled and self.user_input.instance.fixed_working_schedule
            else {}
        )
        for s, d, c in product(self.nodes, self.nodes, self.chunks):
            if not self.demand[s][d][c]:
                continue
            for i, j in product(self.nodes, self.nodes):
                if self.topology.capacity[i][j] <= 0:
                    continue
                demand_var = self.demand_link_used_w[(s, d, i, j, c)]
                self.model.addConstr(
                    demand_var <= self.link_used_w[s][i][j][c],
                    name="demand_path_embed_%d_%d_%d_%d_%d" % (s, d, i, j, c),
                )
                demand_after_failure_var = self.demand_link_used_w_after_failure[(s, d, i, j, c)]
                future_link_var = self.link_used_w_after_failure[(s, i, j, c)]
                fixed_after_failure = fixed_after_failure_values.get((s, d, i, j, c))
                if fixed_after_failure is not None:
                    self.model.addConstr(
                        demand_after_failure_var == fixed_after_failure,
                        name="fixed_demand_path_after_failure_%d_%d_%d_%d_%d" % (s, d, i, j, c),
                    )
                else:
                    self.model.addConstr(
                        demand_after_failure_var <= demand_var,
                        name="demand_path_after_failure_path_ub_%d_%d_%d_%d_%d" % (s, d, i, j, c),
                    )
                    self.model.addConstr(
                        demand_after_failure_var <= future_link_var,
                        name="demand_path_after_failure_future_ub_%d_%d_%d_%d_%d" % (s, d, i, j, c),
                    )
                    self.model.addConstr(
                        demand_after_failure_var >= demand_var + future_link_var - 1,
                        name="demand_path_after_failure_lb_%d_%d_%d_%d_%d" % (s, d, i, j, c),
                    )

            for node in self.nodes:
                incoming = gp.quicksum(
                    self.demand_link_used_w[(s, d, i, node, c)]
                    for i in self.nodes
                    if self.topology.capacity[i][node] > 0 and (s, d, i, node, c) in self.demand_link_used_w
                )
                outgoing = gp.quicksum(
                    self.demand_link_used_w[(s, d, node, j, c)]
                    for j in self.nodes
                    if self.topology.capacity[node][j] > 0 and (s, d, node, j, c) in self.demand_link_used_w
                )
                if node == s:
                    self.model.addConstr(
                        outgoing - incoming == 1,
                        name="demand_path_source_%d_%d_%d_%d" % (s, d, c, node),
                    )
                elif node == d:
                    self.model.addConstr(
                        incoming - outgoing == 1,
                        name="demand_path_dest_%d_%d_%d_%d" % (s, d, c, node),
                    )
                else:
                    self.model.addConstr(
                        incoming - outgoing == 0,
                        name="demand_path_mid_%d_%d_%d_%d" % (s, d, c, node),
                    )
        logging.debug("Finished adding demand-path constraints in %s", time.time() - start)

    def _flow_r(self, scenario_idx: int, s: int, i: int, j: int, c: int, k: int):
        return self.flow_r.get((scenario_idx, s, i, j, c, k), 0)

    def _buffer_r(self, scenario_idx: int, s: int, i: int, c: int, k: int):
        return self.buffer_r[(scenario_idx, s, i, c, k)]

    def _demand_r(self, scenario_idx: int, s: int, d: int, c: int, k: int):
        return self.total_demand_sat_r[(scenario_idx, s, d, c, k)]

    def _add_valid_working_arrivals(
        self,
        buffer_constr: gp.LinExpr,
        s: int,
        i: int,
        c: int,
        k: int,
        failed_i: int,
        failed_j: int,
    ) -> None:
        for j in self.nodes:
            if self.topology.capacity[j][i] <= 0:
                continue
            alpha_num_back = self.get_alpha_num_back(j, i)
            beta_num_back = self.get_beta_num_back(j, i)
            link_type = self.get_link_type(j, i)
            if link_type != self.LinkType.SWITCH_GPU or self.user_input.instance.switch_to_gpu_link_on:
                send_k = k - alpha_num_back - 1 - beta_num_back
            else:
                send_k = k - alpha_num_back
            if send_k < 0:
                continue
            if (j, i) == (failed_i, failed_j) and send_k >= self.failure_time_epoch:
                continue
            flow_var = self.flow_w[s][j][i][c][send_k]
            if self._is_var(flow_var):
                buffer_constr.add(flow_var)

    def recovery_destination_constraints(self) -> None:
        start = time.time()
        for scenario_idx, _ in enumerate(self.failure_scenarios):
            for s, d, c, k in product(self.nodes, self.nodes, self.chunks, self.epochs):
                if not self.demand[s][d][c]:
                    continue
                demand_var = self._demand_r(scenario_idx, s, d, c, k)
                if k + 1 < self.num_epochs:
                    self.model.addConstr(
                        demand_var == self._buffer_r(scenario_idx, s, d, c, k + 1),
                        name="dest_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, d, c, k),
                    )
                else:
                    dem_sat_constr = gp.LinExpr(0.0)
                    dem_sat_constr.add(self._buffer_r(scenario_idx, s, d, c, k))
                    for i in self.nodes:
                        if self.topology.capacity[i][d] <= 0:
                            continue
                        alpha_num_back = self.get_alpha_num_back(i, d)
                        link_type = self.get_link_type(i, d)
                        if link_type != self.LinkType.SWITCH_GPU or self.user_input.instance.switch_to_gpu_link_on:
                            beta_num_back = self.get_beta_num_back(i, d)
                            if k - alpha_num_back - beta_num_back >= 0:
                                dem_sat_constr.add(
                                    self._flow_r(scenario_idx, s, i, d, c, k - alpha_num_back - beta_num_back)
                                )
                    self.model.addConstr(
                        dem_sat_constr == demand_var,
                        name="dest_last_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, d, c, k),
                    )
                if k < self.num_epochs - 1:
                    self.model.addConstr(
                        demand_var <= self._demand_r(scenario_idx, s, d, c, k + 1),
                        name="dest_monotonic_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, d, c, k),
                    )
        logging.debug("Finished adding recovery destination constraints in %s", time.time() - start)

    def recovery_node_constraints(self) -> None:
        start = time.time()
        for scenario_idx, (failed_i, failed_j) in enumerate(self.failure_scenarios):
            for i, s, c, k in product(self.nodes, self.nodes, self.chunks, self.epochs):
                buffer_var = self._buffer_r(scenario_idx, s, i, c, k)
                buffer_constr = gp.LinExpr(0.0)
                if k == 0 and i != s:
                    buffer_constr.add(buffer_var)
                    self.model.addConstr(
                        buffer_constr == 0,
                        name="node_first_buffer_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                    )
                elif k == 0 and i == s:
                    dem_c = max([self.demand[s][d][c] for d in self.nodes])
                    buffer_constr.add(buffer_var)
                    self.model.addConstr(
                        buffer_constr == dem_c,
                        name="node_initial_buffer_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                    )
                elif k <= self.recovery_start_epoch and i not in self.topology.switch_indices:
                    buffer_constr.add(self._buffer_r(scenario_idx, s, i, c, k - 1))
                    if self.recovery_inherits_working_arrivals:
                        self._add_valid_working_arrivals(
                            buffer_constr,
                            s,
                            i,
                            c,
                            k,
                            failed_i,
                            failed_j,
                        )
                    self.model.addConstr(
                        buffer_constr == buffer_var,
                        name="node_inherited_buffer_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                    )
                elif k <= self.recovery_start_epoch:
                    buffer_constr.add(buffer_var)
                    self.model.addConstr(
                        buffer_constr == 0,
                        name="node_switch_inherited_buffer_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                    )
                elif i not in self.topology.switch_indices:
                    buffer_constr.add(self._buffer_r(scenario_idx, s, i, c, k - 1))
                    if self.recovery_inherits_working_arrivals:
                        self._add_valid_working_arrivals(
                            buffer_constr,
                            s,
                            i,
                            c,
                            k,
                            failed_i,
                            failed_j,
                        )
                    for j in self.nodes:
                        if self.topology.capacity[j][i] <= 0:
                            continue
                        alpha_num_back = self.get_alpha_num_back(j, i)
                        beta_num_back = self.get_beta_num_back(j, i)
                        link_type = self.get_link_type(j, i)
                        if link_type != self.LinkType.SWITCH_GPU or self.user_input.instance.switch_to_gpu_link_on:
                            if k - alpha_num_back - 1 - beta_num_back >= 0:
                                buffer_constr.add(
                                    self._flow_r(scenario_idx, s, j, i, c, k - alpha_num_back - 1 - beta_num_back)
                                )
                        else:
                            if k - alpha_num_back >= 0:
                                buffer_constr.add(self._flow_r(scenario_idx, s, j, i, c, k - alpha_num_back))
                    self.model.addConstr(
                        buffer_constr == buffer_var,
                        name="node_buffer_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                    )
                else:
                    buffer_constr.add(buffer_var)
                    self.model.addConstr(
                        buffer_constr == 0,
                        name="node_switch_buffer_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                    )
                outgoing = [
                    self._flow_r(scenario_idx, s, i, v, c, k)
                    for v in range(self.num_nodes)
                    if self.topology.capacity[i][v] > 0 and self._is_var(self._flow_r(scenario_idx, s, i, v, c, k))
                ]
                if not outgoing:
                    continue
                if i not in self.topology.switch_indices:
                    self.aux_var.append(
                        self.model.addVar(
                            0,
                            GRB.INFINITY,
                            vtype=GRB.CONTINUOUS,
                            name="aux_var_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                        )
                    )
                    self.model.addConstr(
                        self.aux_var[-1] == gp.max_(outgoing),
                        name="node_aux_flow_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                    )
                    self.model.addConstr(
                        buffer_var >= self.aux_var[-1],
                        name="node_flow_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                    )
                else:
                    switch_node_constr = gp.LinExpr(0.0)
                    if k > 0:
                        for j in range(self.num_nodes):
                            if self.topology.capacity[j][i] > 0:
                                alpha_num_back = self.get_alpha_num_back(j, i)
                                beta_num_back = self.get_beta_num_back(j, i)
                                if k - alpha_num_back - 1 - beta_num_back >= 0:
                                    switch_node_constr.add(
                                        self._flow_r(scenario_idx, s, j, i, c, k - alpha_num_back - 1 - beta_num_back)
                                    )
                    outgoing_expr = gp.quicksum(outgoing)
                    if not self.user_input.instance.switch_copy:
                        self.model.addConstr(
                            switch_node_constr >= outgoing_expr,
                            name="switch_flow_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                        )
                    else:
                        self.aux_var.append(
                            self.model.addVar(
                                0,
                                GRB.INFINITY,
                                vtype=GRB.CONTINUOUS,
                                name="aux_switch_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                            )
                        )
                        self.model.addConstr(
                            self.aux_var[-1] == gp.max_(outgoing),
                            name="node_switch_flow_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                        )
                        self.model.addConstr(
                            switch_node_constr >= self.aux_var[-1],
                            name="switch_copy_flow_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, i, c, k),
                        )
        logging.debug("Finished adding recovery node constraints in %s", time.time() - start)

    def recovery_link_usage_constraints(self) -> None:
        start = time.time()
        for scenario_idx, (failed_i, failed_j) in enumerate(self.failure_scenarios):
            for s, i, j, c in product(self.nodes, self.nodes, self.nodes, self.chunks):
                if self.topology.capacity[i][j] <= 0:
                    continue
                link_var = self.link_used_r[(scenario_idx, s, i, j, c)]
                recovery_vars = [
                    self._flow_r(scenario_idx, s, i, j, c, k)
                    for k in self.epochs
                    if self._is_var(self._flow_r(scenario_idx, s, i, j, c, k))
                ]
                if recovery_vars:
                    self.model.addConstr(
                        gp.quicksum(recovery_vars) >= link_var,
                        name="recovery_link_use_lb_%d_%d_%d_%d_%d" % (scenario_idx, s, i, j, c),
                    )
                    for idx, flow_var in enumerate(recovery_vars):
                        self.model.addConstr(
                            flow_var <= link_var,
                            name="recovery_link_use_ub_%d_%d_%d_%d_%d_%d" % (scenario_idx, s, i, j, c, idx),
                        )
                else:
                    self.model.addConstr(
                        link_var == 0,
                        name="recovery_link_unused_%d_%d_%d_%d_%d" % (scenario_idx, s, i, j, c),
                    )
                if (i, j) == (failed_i, failed_j):
                    self.model.addConstr(
                        link_var == 0,
                        name="recovery_avoid_failed_link_%d_%d_%d_%d_%d" % (scenario_idx, s, i, j, c),
                    )
                if self.user_input.instance.protection_link_disjoint:
                    self.model.addConstr(
                        self.link_used_w[s][i][j][c] + link_var <= 1,
                        name="recovery_link_disjoint_%d_%d_%d_%d_%d" % (scenario_idx, s, i, j, c),
                    )
        logging.debug("Finished adding recovery link-usage constraints in %s", time.time() - start)

    def recovery_timing_constraints(self) -> None:
        for scenario_idx, (failed_i, failed_j) in enumerate(self.failure_scenarios):
            for s, i, j, c, k in product(self.nodes, self.nodes, self.nodes, self.chunks, self.epochs):
                flow_var = self._flow_r(scenario_idx, s, i, j, c, k)
                if not self._is_var(flow_var):
                    continue
                if k < self.recovery_start_epoch or ((i, j) == (failed_i, failed_j) and k >= self.failure_time_epoch):
                    self.model.addConstr(
                        flow_var == 0,
                        name="recovery_timing_%d_%d_%d_%d_%d_%d" % (scenario_idx, s, i, j, c, k),
                    )

    def recovery_capacity_constraints(self) -> None:
        start = time.time()
        for scenario_idx, (failed_i, failed_j) in enumerate(self.failure_scenarios):
            for i, j, k in product(self.nodes, self.nodes, self.epochs):
                if self.topology.capacity[i][j] <= 0:
                    continue
                if (i, j) == (failed_i, failed_j) and k >= self.failure_time_epoch:
                    continue
                cap_constr = gp.LinExpr(0.0)
                epoch_capacity = self.topology.capacity[i][j] * self.epoch_duration
                beta_num_back = max(0, int(np.ceil(1 / epoch_capacity)) - 1)
                for l in range(beta_num_back + 1):
                    active_k = k - l
                    if active_k < 0:
                        continue
                    for s, c in product(self.nodes, self.chunks):
                        if self._is_var(self.flow_w[s][i][j][c][active_k]):
                            cap_constr.add(self.flow_w[s][i][j][c][active_k])
                        recovery_var = self._flow_r(scenario_idx, s, i, j, c, active_k)
                        if self._is_var(recovery_var):
                            cap_constr.add(recovery_var)
                cap_constr.addConstant(self._prior_occupancy(i, j, k))
                self.model.addConstr(
                    cap_constr <= ((beta_num_back + 1) * epoch_capacity),
                    name="capacity_recovery_%d_%d_%d_%d" % (scenario_idx, i, j, k),
                )
        logging.debug("Finished adding recovery capacity constraints in %s", time.time() - start)

    def recovery_completion_time_constraints(self) -> None:
        start = time.time()
        for scenario_idx, _ in enumerate(self.failure_scenarios):
            for s, d, c, k in product(self.nodes, self.nodes, self.chunks, self.epochs):
                if not self.demand[s][d][c]:
                    continue
                first_satisfied_at_k = gp.LinExpr(0.0)
                first_satisfied_at_k.add(self._demand_r(scenario_idx, s, d, c, k))
                if k > 0:
                    first_satisfied_at_k.add(self._demand_r(scenario_idx, s, d, c, k - 1), -1.0)
                self.model.addConstr(
                    self.protection_completion_epoch >= (k + 1) * first_satisfied_at_k,
                    name="completion_time_recovery_%d_%d_%d_%d_%d" % (scenario_idx, s, d, c, k),
                )
        logging.debug("Finished adding recovery completion constraints in %s", time.time() - start)

    def failure_scenario_constraints(self) -> None:
        if not self.user_input.instance.enable_failure_scenarios:
            return
        start = time.time()
        for scenario_idx, (failed_i, failed_j) in enumerate(self.failure_scenarios):
            for s, d, c in product(self.nodes, self.nodes, self.chunks):
                if not self.demand[s][d][c]:
                    continue
                demand_failed = self.scenario_demand_fail[(scenario_idx, s, d, c)]
                scenario_sat = self.scenario_demand_sat[(scenario_idx, s, d, c)]
                if self.real_failure_timing_enabled:
                    protection_final = self._demand_r(scenario_idx, s, d, c, self.final_deadline)
                    if self.failure_time_epoch == 0:
                        delivered_before_failure = 0
                    else:
                        delivered_before_failure = self.total_demand_sat_w[s][d][c][self.failure_time_epoch - 1]
                    future_failed_link_use = self.link_used_w_after_failure[(s, failed_i, failed_j, c)]
                    if self.user_input.instance.failure_model == FailureModel.EXACT:
                        failed_link_on_path = self.demand_link_used_w[(s, d, failed_i, failed_j, c)]
                        future_failed_link_use = self.demand_link_used_w_after_failure[
                            (s, d, failed_i, failed_j, c)
                        ]
                        self.model.addConstr(
                            demand_failed <= failed_link_on_path,
                            name="scenario_real_fail_path_ub_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                        )
                        self.model.addConstr(
                            demand_failed <= future_failed_link_use,
                            name="scenario_real_fail_future_ub_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                        )
                        self.model.addConstr(
                            demand_failed <= 1 - delivered_before_failure,
                            name="scenario_real_fail_delivered_ub_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                        )
                        self.model.addConstr(
                            demand_failed >= failed_link_on_path + future_failed_link_use - delivered_before_failure - 1,
                            name="scenario_real_fail_lb_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                        )
                    else:
                        self.model.addConstr(
                            demand_failed <= future_failed_link_use,
                            name="scenario_real_fail_future_ub_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                        )
                        self.model.addConstr(
                            demand_failed <= 1 - delivered_before_failure,
                            name="scenario_real_fail_delivered_ub_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                        )
                        self.model.addConstr(
                            demand_failed >= future_failed_link_use - delivered_before_failure,
                            name="scenario_real_fail_lb_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                        )
                else:
                    protection_final = self.total_demand_sat_p[s][d][c][self.final_deadline]
                    if self.user_input.instance.failure_model == FailureModel.EXACT:
                        failed_link_on_path = self.demand_link_used_w[(s, d, failed_i, failed_j, c)]
                    else:
                        failed_link_on_path = self.link_used_w[s][failed_i][failed_j][c]
                    if self.enable_dynamic_backup_release:
                        delivered_before_observation = self.total_demand_sat_w[s][d][c][self.failure_observation_epoch]
                        self.model.addConstr(
                            demand_failed <= failed_link_on_path,
                            name="scenario_fail_link_ub_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                        )
                        self.model.addConstr(
                            demand_failed <= 1 - delivered_before_observation,
                            name="scenario_fail_release_ub_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                        )
                        self.model.addConstr(
                            demand_failed >= failed_link_on_path - delivered_before_observation,
                            name="scenario_fail_release_lb_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                        )
                    else:
                        self.model.addConstr(
                            demand_failed == failed_link_on_path,
                            name="scenario_demand_fail_bind_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                        )
                    self.model.addConstr(
                        self.link_used_p[s][failed_i][failed_j][c] <= 1 - demand_failed,
                        name="scenario_avoid_failed_link_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                    )
                self.model.addConstr(
                    scenario_sat >= 1 - demand_failed,
                    name="scenario_survive_if_unaffected_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                )
                self.model.addConstr(
                    scenario_sat >= protection_final + demand_failed - 1,
                    name="scenario_survive_if_failed_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                )
                self.model.addConstr(
                    scenario_sat <= 1 - demand_failed + protection_final,
                    name="scenario_survive_cap_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                )
                self.model.addConstr(
                    protection_final >= demand_failed,
                    name="scenario_protection_needed_%d_%d_%d_%d" % (scenario_idx, s, d, c),
                )
        logging.debug("Finished adding failure-scenario constraints in %s", time.time() - start)

    def _add_demand_path_tiebreaker(self, objective: gp.LinExpr, weight: float = 0.001) -> None:
        if self.user_input.instance.failure_model != FailureModel.EXACT:
            return
        for demand_var in self.demand_link_used_w.values():
            objective.add(demand_var, weight)

    def objective_formulation(self, objective_type: ObjectiveType = ObjectiveType.PAPER) -> gp.LinExpr:
        del objective_type
        logging.debug("Adding deferred-protection objective")
        objective = gp.LinExpr(0.0)
        working_completion_weight = 1000.0
        protection_completion_tiebreak = 1.0
        protection_usage_penalty = 0.05
        backup_holding_penalty = 1.0

        objective.add(self.working_completion_epoch, working_completion_weight)
        objective.add(self.protection_completion_epoch, protection_completion_tiebreak)
        self._add_demand_path_tiebreaker(objective)
        if self.real_failure_timing_enabled:
            for (scenario_idx, s, i, j, c, k), flow_var in self.flow_r.items():
                del scenario_idx, s, i, j, c
                holding_epochs = max(1, self.final_deadline - k + 1)
                objective.add(
                    flow_var,
                    protection_usage_penalty + backup_holding_penalty * holding_epochs,
                )
            return objective
        for s, i, j, c, k in product(self.nodes, self.nodes, self.nodes, self.chunks, self.epochs):
            if self.topology.capacity[i][j] <= 0:
                continue
            if self._is_var(self.flow_p[s][i][j][c][k]):
                holding_epochs = max(1, self.final_deadline - k + 1)
                objective.add(
                    self.flow_p[s][i][j][c][k],
                    protection_usage_penalty + backup_holding_penalty * holding_epochs,
                )
        return objective

    def encode_problem(self, use_one_less_epoch: bool = False, previous_buffers: List[List[int]] = []) -> int:
        del use_one_less_epoch, previous_buffers
        setup_start = time.time()
        self.model = gp.Model(self.solver_name)
        self.initialize_variables()
        self.fixed_working_schedule_constraints()
        if self.real_failure_timing_enabled:
            self._add_destination_constraints_for_phase(
                self.flow_w,
                self.buffer_w,
                self.total_demand_sat_w,
                "working",
                self.working_deadline,
                True,
            )
            self._add_completion_time_constraints_for_phase(
                self.total_demand_sat_w,
                self.working_completion_epoch,
                "working",
            )
            self._add_node_constraints_for_phase(self.flow_w, self.buffer_w, "working")
            self.capacity_constraints()
            self.link_usage_constraints()
            if self.user_input.instance.failure_model == FailureModel.EXACT:
                self.demand_path_constraints()
            self.recovery_destination_constraints()
            self.recovery_completion_time_constraints()
            self.recovery_node_constraints()
            self.recovery_timing_constraints()
            self.recovery_link_usage_constraints()
            self.recovery_capacity_constraints()
            self.failure_scenario_constraints()
        else:
            self._add_phase_gating_constraints()
            self._add_destination_constraints_for_phase(
                self.flow_w, self.buffer_w, self.total_demand_sat_w, "working", self.working_deadline, True
            )
            self._add_destination_constraints_for_phase(
                self.flow_p, self.buffer_p, self.total_demand_sat_p, "protection", self.final_deadline, False
            )
            self.completion_time_constraints()
            self._add_node_constraints_for_phase(self.flow_w, self.buffer_w, "working")
            self._add_node_constraints_for_phase(self.flow_p, self.buffer_p, "protection")
            self.capacity_constraints()
            self.link_usage_constraints()
            if self.user_input.instance.failure_model == FailureModel.EXACT:
                self.demand_path_constraints()
            self.failure_scenario_constraints()
        self.model.setObjective(self.objective_formulation(self.user_input.instance.objective_type))

        log_file = (
            f"Logs/{self.solver_name}_{self.user_input.topology.name}_{self.num_nodes}-nodes_"
            f"{self.num_chunks}-chunks_{self.num_epochs}-epochs_{self.epoch_duration}-epochduration"
        )
        if self.user_input.gurobi.output_flag == 1 or self.user_input.instance.debug:
            if self.user_input.gurobi.log_file:
                log_file += self.user_input.gurobi.log_file
            self.model.setParam("LogFile", log_file + ".log")
            self.model.Params.LogToConsole = 0

        self.set_gurobi_params()
        logging.debug("Total time for deferred-protection setup %s", time.time() - setup_start)
        logging.debug("Starting deferred-protection optimization %s", log_file)

        if self.user_input.instance.warmstart and self.user_input.instance.solution_method == SolutionMethod.ONE_SHOT:
            self.model.update()
            self.model.read(self.user_input.instance.warmstart)

        self.model.optimize()
        if self.model.Status != GRB.OPTIMAL:
            logging.warning("Deferred protection finished with non-optimal status %s", self.model.Status)
            return self.model.Status
        return self.model.Status

    def _extract_phase_flows(self, prefix: str) -> List[Tuple[int, int, int, int, int]]:
        flows = []
        for var in self.model.getVars():
            if not var.varName.startswith(prefix) or var.x <= 0.9:
                continue
            suffix = var.varName[len(prefix):]
            s, i, j, c, k = suffix.split("_")
            flows.append((int(s), int(i), int(j), int(c), int(k)))
        flows.sort(key=lambda item: item[4])
        return flows

    def _extract_chunk_working_completion_profile(self) -> List[Dict[str, int]]:
        profile = []
        for s, c in product(self.nodes, self.chunks):
            destinations = [d for d in self.nodes if self.demand[s][d][c]]
            if not destinations:
                continue
            completion_epoch = self.final_deadline + 1
            for observation_epoch in range(1, self.final_deadline + 2):
                demand_epoch = observation_epoch - 1
                if all(
                    self.total_demand_sat_w[s][d][c][demand_epoch].X > 0.5
                    for d in destinations
                ):
                    completion_epoch = observation_epoch
                    break
            profile.append(
                {
                    "source": s,
                    "chunk": c,
                    "working_completion_epoch": completion_epoch,
                }
            )
        profile.sort(key=lambda row: (row["source"], row["chunk"]))
        return profile

    def _extract_link_occupancy_epochs(self) -> Dict[Tuple[int, int], int]:
        return {
            (i, j): self.get_beta_num_back(i, j) + 1
            for i, j in product(self.nodes, self.nodes)
            if self.topology.capacity[i][j] > 0
        }

    def _extract_recovery_flows(self) -> List[Tuple[int, int, int, int, int, int]]:
        flows = []
        for var in self.model.getVars():
            if not var.varName.startswith("flow_r_") or var.x <= 0.9:
                continue
            suffix = var.varName[len("flow_r_"):]
            scenario_idx, s, i, j, c, k = suffix.split("_")
            flows.append((int(scenario_idx), int(s), int(i), int(j), int(c), int(k)))
        flows.sort(key=lambda item: (item[0], item[5]))
        return flows

    def find_demand_satisfied_k(self) -> int:
        if self.real_failure_timing_enabled:
            satisfied_epochs = {}
            for var in self.model.getVars():
                if not var.varName.startswith("total_demand_r_") or var.x <= 0.9:
                    continue
                suffix = var.varName[len("total_demand_r_"):]
                scenario_idx, s, i, c, k = suffix.split("_")
                key = (int(scenario_idx), int(s), int(i), int(c))
                epoch = int(k)
                if key not in satisfied_epochs or epoch < satisfied_epochs[key]:
                    satisfied_epochs[key] = epoch
            if not satisfied_epochs:
                return int(self.working_completion_epoch.X) - 1
            return max(satisfied_epochs.values())
        satisfied_epochs = {}
        for var in self.model.getVars():
            if not var.varName.startswith("total_demand_p_") or var.x <= 0.9:
                continue
            suffix = var.varName[len("total_demand_p_"):]
            s, i, c, k = suffix.split("_")
            key = (int(s), int(i), int(c))
            epoch = int(k)
            if key not in satisfied_epochs or epoch < satisfied_epochs[key]:
                satisfied_epochs[key] = epoch
        if not satisfied_epochs:
            return self.final_deadline
        return max(satisfied_epochs.values())

    def _extract_failure_scenario_summary(self) -> Dict:
        if not self.user_input.instance.enable_failure_scenarios:
            return {}

        summary = {}
        for scenario_idx, (failed_i, failed_j) in enumerate(self.failure_scenarios):
            affected_demands = []
            protected_demands = []
            unaffected_demands = []
            unprotected_demands = []
            dynamically_released_demands = []

            for s, d, c in product(self.nodes, self.nodes, self.chunks):
                if not self.demand[s][d][c]:
                    continue

                failed_var = self.scenario_demand_fail[(scenario_idx, s, d, c)]
                sat_var = self.scenario_demand_sat[(scenario_idx, s, d, c)]
                if self.real_failure_timing_enabled:
                    protection_final = self._demand_r(scenario_idx, s, d, c, self.final_deadline)
                    if self.failure_time_epoch == 0:
                        delivered_before_cutoff = None
                        delivered_before_cutoff_value = 0.0
                    else:
                        delivered_before_cutoff = self.total_demand_sat_w[s][d][c][self.failure_time_epoch - 1]
                        delivered_before_cutoff_value = delivered_before_cutoff.X
                    if self.user_input.instance.failure_model == FailureModel.EXACT:
                        failed_link_on_path = self.demand_link_used_w_after_failure[
                            (s, d, failed_i, failed_j, c)
                        ]
                    else:
                        failed_link_on_path = self.link_used_w_after_failure[(s, failed_i, failed_j, c)]
                else:
                    protection_final = self.total_demand_sat_p[s][d][c][self.final_deadline]
                    delivered_before_cutoff = self.total_demand_sat_w[s][d][c][self.failure_observation_epoch]
                    delivered_before_cutoff_value = delivered_before_cutoff.X
                    if self.user_input.instance.failure_model == FailureModel.EXACT:
                        failed_link_on_path = self.demand_link_used_w[(s, d, failed_i, failed_j, c)]
                    else:
                        failed_link_on_path = self.link_used_w[s][failed_i][failed_j][c]

                demand_label = f"Demand ({s}->{d}, chunk {c})"

                if failed_var.X > 0.5:
                    affected_demands.append(demand_label)
                    if protection_final.X > 0.5 and sat_var.X > 0.5:
                        protected_demands.append(demand_label)
                    else:
                        unprotected_demands.append(demand_label)
                elif (
                    self.enable_dynamic_backup_release
                    and failed_link_on_path.X > 0.5
                    and delivered_before_cutoff_value > 0.5
                ):
                    dynamically_released_demands.append(demand_label)
                    unaffected_demands.append(demand_label)
                else:
                    unaffected_demands.append(demand_label)

            summary[f"Failure {scenario_idx}: {failed_i}->{failed_j}"] = {
                "failed_link": f"{failed_i}->{failed_j}",
                "affected_demands": affected_demands,
                "protected_demands": protected_demands,
                "unaffected_demands": unaffected_demands,
                "unprotected_demands": unprotected_demands,
                "dynamically_released_demands": dynamically_released_demands,
            }
        return summary

    def _extract_working_demand_links(self) -> List[Dict[str, int]]:
        demand_links = []
        for (s, d, i, j, c), var in self.demand_link_used_w.items():
            if var.X <= 0.5:
                continue
            demand_links.append(
                {
                    "source": s,
                    "destination": d,
                    "chunk": c,
                    "link": f"{i}->{j}",
                }
            )
        demand_links.sort(key=lambda row: (row["source"], row["destination"], row["chunk"], row["link"]))
        return demand_links

    def _flow_arrival_epoch(self, i: int, j: int, send_epoch: int) -> int:
        alpha_num_back = self.get_alpha_num_back(i, j)
        link_type = self.get_link_type(i, j)
        if link_type == self.LinkType.SWITCH_GPU and not self.user_input.instance.switch_to_gpu_link_on:
            return send_epoch + alpha_num_back
        beta_num_back = self.get_beta_num_back(i, j)
        return send_epoch + alpha_num_back + 1 + beta_num_back

    def _extract_temporal_demand_path_epochs(self, s: int, d: int, c: int) -> List[Dict[str, int]]:
        edges_by_node = defaultdict(list)
        send_epochs_by_edge = {}
        for (path_s, path_d, i, j, path_c), var in self.demand_link_used_w.items():
            if (path_s, path_d, path_c) != (s, d, c) or var.X <= 0.5:
                continue
            epochs = [
                k
                for k in self.epochs
                if self._is_var(self.flow_w[s][i][j][c][k]) and self.flow_w[s][i][j][c][k].X > 0.5
            ]
            if not epochs:
                continue
            edge = (i, j)
            edges_by_node[i].append(edge)
            send_epochs_by_edge[edge] = sorted(epochs)

        queue = [(0, s)]
        best_arrival = {s: 0}
        predecessor = {}
        while queue:
            available_epoch, node = heapq.heappop(queue)
            if available_epoch != best_arrival.get(node):
                continue
            if node == d:
                break
            for edge in edges_by_node.get(node, []):
                i, j = edge
                send_epoch = next(
                    (epoch for epoch in send_epochs_by_edge[edge] if epoch >= available_epoch),
                    None,
                )
                if send_epoch is None:
                    continue
                arrival_epoch = self._flow_arrival_epoch(i, j, send_epoch)
                if arrival_epoch < best_arrival.get(j, math.inf):
                    best_arrival[j] = arrival_epoch
                    predecessor[j] = (node, edge, send_epoch)
                    heapq.heappush(queue, (arrival_epoch, j))

        if d not in predecessor:
            return []

        path = []
        node = d
        while node != s:
            previous_node, (i, j), send_epoch = predecessor[node]
            path.append(
                {
                    "source": s,
                    "destination": d,
                    "chunk": c,
                    "link": f"{i}->{j}",
                    "epoch": send_epoch,
                }
            )
            node = previous_node
        path.reverse()
        return path

    def _extract_working_demand_link_epochs(self) -> List[Dict[str, int]]:
        demand_link_epochs = []
        for s, d, c in product(self.nodes, self.nodes, self.chunks):
            if not self.demand[s][d][c]:
                continue
            temporal_path = self._extract_temporal_demand_path_epochs(s, d, c)
            if temporal_path:
                demand_link_epochs.extend(temporal_path)
                continue
            for (path_s, path_d, i, j, path_c), var in self.demand_link_used_w.items():
                if (path_s, path_d, path_c) != (s, d, c) or var.X <= 0.5:
                    continue
                for k in self.epochs:
                    flow_var = self.flow_w[s][i][j][c][k]
                    if self._is_var(flow_var) and flow_var.X > 0.5:
                        demand_link_epochs.append(
                            {
                                "source": s,
                                "destination": d,
                                "chunk": c,
                                "link": f"{i}->{j}",
                                "epoch": k,
                            }
                        )
        demand_link_epochs.sort(
            key=lambda row: (
                row["source"],
                row["destination"],
                row["chunk"],
                row["link"],
                row["epoch"],
            )
        )
        return demand_link_epochs

    def get_schedule(self) -> Tuple[List[Tuple[int, int, int, int, int]], Dict]:
        if self.model.SolCount <= 0:
            return [], {}
        working_flows = self._extract_phase_flows("flow_w_")
        working_demand_links = self._extract_working_demand_links()
        working_demand_link_epochs = self._extract_working_demand_link_epochs()
        if self.real_failure_timing_enabled:
            recovery_flows = self._extract_recovery_flows()
            protection_flows = [(s, i, j, c, k) for _, s, i, j, c, k in recovery_flows]
        else:
            recovery_flows = []
            protection_flows = self._extract_phase_flows("flow_p_")
        failure_summary = self._extract_failure_scenario_summary()
        schedule_json = {
            "1-Epoch_Duration": self.epoch_duration,
            "2-Expected_Epoch_Duration": self.expected_epoch_duration,
            "3-Heuristic_Working_Finish_Epochs": self.heuristic_working_finish_epochs,
            "4-Heuristic_Deferred_Finish_Epochs": self.heuristic_finish_epochs,
            "5-Heuristic_Deferred_Finish_Time": self.heuristic_finish_time,
            "6-Deferred_Deadline_Factor": self.deadline_factor,
            "7-Working_Deadline_Epoch": self.working_deadline + 1,
            "7a-Deferred_Activation_Epoch": self.deferred_activation_epoch + 1,
            "7b-Failure_Observation_Epoch": self.failure_observation_epoch + 1,
            "7c-Dynamic_Backup_Release": self.enable_dynamic_backup_release,
            "7d-Real_Failure_Timing": self.real_failure_timing_enabled,
            "7e-Failure_Time_Epoch": self.failure_time_epoch + 1,
            "7f-Detection_Delay_Epochs": self.detection_delay_epochs,
            "7g-Recovery_Start_Epoch": self.recovery_start_epoch + 1,
            "7h-Recovery_Inherits_Scenario_Valid_Working_Buffers": (
                self.real_failure_timing_enabled
                and self.recovery_inherits_working_arrivals
            ),
            "7i-Post_Failure_Working_Flows_Continue": self.real_failure_timing_enabled,
            "7j-Failure_Model": self.user_input.instance.failure_model.name,
            "7k-Failure_Exposure_Granularity": (
                "fixed embedded demand-link-epoch exposure"
                if (
                    self.user_input.instance.failure_model == FailureModel.EXACT
                    and self._load_fixed_working_demand_link_epochs()
                )
                else "solver-selected embedded demand-path exposure (not replay-exact)"
                if self.user_input.instance.failure_model == FailureModel.EXACT
                else "source-chunk future link exposure"
            ),
            "7l-Fixed_Working_Schedule": self.user_input.instance.fixed_working_schedule,
            "8-Final_Deadline_Epoch": self.final_deadline + 1,
            "9-Epochs_Required": self.find_demand_satisfied_k() + 1,
            "9c-Objective_Working_Completion_Epoch": self.working_completion_epoch.X,
            "9d-Objective_Protection_Completion_Epoch": self.protection_completion_epoch.X,
            "9a-Failure_Scenarios": [f"{i}->{j}" for i, j in self.failure_scenarios],
            "9b-Failure_Scenario_Summary": failure_summary,
            "10-Working_Flows": [
                f"Chunk {c} from {s} traveled over {i}->{j} in epoch {k}"
                for s, i, j, c, k in working_flows
            ],
            "10a-Working_Demand_Links": working_demand_links,
            "10b-Working_Demand_Link_Epochs": working_demand_link_epochs,
            "11-Protection_Flows": [
                f"Chunk {c} from {s} traveled over {i}->{j} in epoch {k}"
                for s, i, j, c, k in protection_flows
            ],
        }
        if self.real_failure_timing_enabled:
            schedule_json["11a-Recovery_Flows_By_Scenario"] = [
                f"Scenario {scenario_idx}: chunk {c} from {s} recovered over {i}->{j} in epoch {k}"
                for scenario_idx, s, i, j, c, k in recovery_flows
            ]
        return working_flows + protection_flows, schedule_json
