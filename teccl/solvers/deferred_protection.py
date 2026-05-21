import logging
import math
import time
from itertools import product
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
    Preliminary MILP formulation for deferred protection.

    The model separates the communication horizon into two windows:
    - Working phase: deliver the collective by the working deadline.
    - Protection phase: send a deferred backup copy after the working deadline and
      complete it by the final deadline.

    This keeps the original TE-CCL baseline intact while giving us a clean place to
    iterate on stronger resiliency constraints later.
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
        self.final_deadline = min(self.num_epochs - 1, derived_deadline_epochs - 1)
        self.working_deadline = min(
            self.final_deadline,
            max(0, int((self.final_deadline + 1) * self.user_input.instance.working_deadline_ratio) - 1),
        )
        self.failure_scenarios = self._enumerate_failure_scenarios()

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
        self.demand_link_used_w = {}
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
                if link_type in [self.LinkType.GPU_SWITCH, self.LinkType.GPU_GPU] and k in not_necessary_k:
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
        logging.debug("Finished initializing deferred-protection variables in %s", time.time() - time_start)

    def _add_phase_gating_constraints(self) -> None:
        for i, j in product(self.nodes, self.nodes):
            if self.topology.capacity[i][j] <= 0:
                continue
            for s, c, k in product(self.nodes, self.chunks, self.epochs):
                if self._is_var(self.flow_w[s][i][j][c][k]) and k > self.working_deadline:
                    self.model.addConstr(self.flow_w[s][i][j][c][k] == 0, name="working_window_%d_%d_%d_%d_%d" % (s, i, j, c, k))
                if self._is_var(self.flow_p[s][i][j][c][k]) and k <= self.working_deadline:
                    self.model.addConstr(self.flow_p[s][i][j][c][k] == 0, name="protection_window_%d_%d_%d_%d_%d" % (s, i, j, c, k))

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
            if k - beta_num_back < 0:
                continue
            for l in range(beta_num_back + 1):
                for s, c in product(self.nodes, self.chunks):
                    if k - l >= 0:
                        cap_constr.add(self.flow_w[s][i][j][c][k - l])
                        cap_constr.add(self.flow_p[s][i][j][c][k - l])
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
                protection_final = self.total_demand_sat_p[s][d][c][self.final_deadline]
                if self.user_input.instance.failure_model == FailureModel.EXACT:
                    failed_link_on_path = self.demand_link_used_w[(s, d, failed_i, failed_j, c)]
                else:
                    failed_link_on_path = self.link_used_w[s][failed_i][failed_j][c]
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

    def objective_formulation(self, objective_type: ObjectiveType = ObjectiveType.PAPER) -> gp.LinExpr:
        del objective_type
        logging.debug("Adding deferred-protection objective")
        objective = gp.LinExpr(0.0)
        completion_weight = 1000.0
        working_completion_weight = 100.0
        protection_penalty = 0.05
        lateness_penalty = 0.01
        objective.add(self.protection_completion_epoch, completion_weight)
        objective.add(self.working_completion_epoch, working_completion_weight)
        for s, i, j, c, k in product(self.nodes, self.nodes, self.nodes, self.chunks, self.epochs):
            if self.topology.capacity[i][j] <= 0:
                continue
            if self._is_var(self.flow_p[s][i][j][c][k]):
                objective.add(self.flow_p[s][i][j][c][k], protection_penalty + lateness_penalty * k)
        return objective

    def encode_problem(self, use_one_less_epoch: bool = False, previous_buffers: List[List[int]] = []) -> int:
        del use_one_less_epoch, previous_buffers
        setup_start = time.time()
        self.model = gp.Model(self.solver_name)
        self.initialize_variables()
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

    def find_demand_satisfied_k(self) -> int:
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

            for s, d, c in product(self.nodes, self.nodes, self.chunks):
                if not self.demand[s][d][c]:
                    continue

                failed_var = self.scenario_demand_fail[(scenario_idx, s, d, c)]
                sat_var = self.scenario_demand_sat[(scenario_idx, s, d, c)]
                protection_final = self.total_demand_sat_p[s][d][c][self.final_deadline]

                demand_label = f"Demand ({s}->{d}, chunk {c})"

                if failed_var.X > 0.5:
                    affected_demands.append(demand_label)
                    if protection_final.X > 0.5 and sat_var.X > 0.5:
                        protected_demands.append(demand_label)
                    else:
                        unprotected_demands.append(demand_label)
                else:
                    unaffected_demands.append(demand_label)

            summary[f"Failure {scenario_idx}: {failed_i}->{failed_j}"] = {
                "failed_link": f"{failed_i}->{failed_j}",
                "affected_demands": affected_demands,
                "protected_demands": protected_demands,
                "unaffected_demands": unaffected_demands,
                "unprotected_demands": unprotected_demands,
            }
        return summary

    def get_schedule(self) -> Tuple[List[Tuple[int, int, int, int, int]], Dict]:
        if self.model.SolCount <= 0:
            return [], {}
        working_flows = self._extract_phase_flows("flow_w_")
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
            "11-Protection_Flows": [
                f"Chunk {c} from {s} traveled over {i}->{j} in epoch {k}"
                for s, i, j, c, k in protection_flows
            ],
        }
        return working_flows + protection_flows, schedule_json
