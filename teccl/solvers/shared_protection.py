import logging
import time

import gurobipy as gp

from teccl.input_data import FailureModel, ObjectiveType, SolutionMethod, UserInputParams
from teccl.solvers.dedicated_protection import DedicatedProtectionFormulation
from teccl.topologies.topology import Topology


class SharedProtectionFormulation(DedicatedProtectionFormulation):
    """
    Shared protection baseline.

    This formulation keeps the same pre-planned working/protection structure as the
    dedicated baseline, but accounts for backup resources at the shared-segment level.
    Multiple source-chunk backup paths can reuse the same reserved protection link.
    """

    def __init__(self, user_input: UserInputParams, topology: Topology) -> None:
        super().__init__(user_input, topology)
        self.solver_name = "SharedProtection_MILP"
        self.shared_link_used_p = []

    def initialize_variables(self) -> None:
        super().initialize_variables()
        self.shared_link_used_p = [[0 for _ in self.nodes] for _ in self.nodes]
        for i in self.nodes:
            for j in self.nodes:
                if self.topology.capacity[i][j] <= 0:
                    continue
                self.shared_link_used_p[i][j] = self.model.addVar(
                    0, 1, vtype=gp.GRB.BINARY, name="shared_link_used_p_%d_%d" % (i, j)
                )

    def shared_link_constraints(self) -> None:
        for i in self.nodes:
            for j in self.nodes:
                if self.topology.capacity[i][j] <= 0:
                    continue
                using_demands = []
                for s in self.nodes:
                    for c in self.chunks:
                        link_var = self.link_used_p[s][i][j][c]
                        using_demands.append(link_var)
                        self.model.addConstr(
                            link_var <= self.shared_link_used_p[i][j],
                            name="shared_link_embed_%d_%d_%d_%d" % (s, i, j, c),
                        )
                self.model.addConstr(
                    gp.quicksum(using_demands) >= self.shared_link_used_p[i][j],
                    name="shared_link_activate_%d_%d" % (i, j),
                )

    def objective_formulation(self, objective_type: ObjectiveType = ObjectiveType.PAPER):
        del objective_type
        logging.debug("Adding shared-protection objective")
        objective = gp.LinExpr(0.0)
        completion_weight = 1000.0
        protection_completion_weight = 100.0
        protection_flow_penalty = 0.05
        shared_link_penalty = 0.2

        objective += completion_weight * self.working_completion_epoch
        objective += protection_completion_weight * self.protection_completion_epoch

        for i in self.nodes:
            for j in self.nodes:
                if self.topology.capacity[i][j] <= 0:
                    continue
                objective += shared_link_penalty * self.shared_link_used_p[i][j]
                for s in self.nodes:
                    for c in self.chunks:
                        for k in self.epochs:
                            flow_var = self.flow_p[s][i][j][c][k]
                            if self._is_var(flow_var):
                                objective += protection_flow_penalty * flow_var

        return objective

    def encode_problem(self, use_one_less_epoch: bool = False, previous_buffers=None) -> int:
        del use_one_less_epoch, previous_buffers
        setup_start = time.time()
        self.model = gp.Model(self.solver_name)
        self.initialize_variables()
        self.fixed_working_schedule_constraints()
        self._add_destination_constraints_for_phase(
            self.flow_w,
            self.buffer_w,
            self.total_demand_sat_w,
            "working",
            self.final_deadline,
            True,
        )
        self._add_destination_constraints_for_phase(
            self.flow_p,
            self.buffer_p,
            self.total_demand_sat_p,
            "protection",
            self.final_deadline,
            False,
        )
        self.completion_time_constraints()
        self._add_node_constraints_for_phase(self.flow_w, self.buffer_w, "working")
        self._add_node_constraints_for_phase(self.flow_p, self.buffer_p, "protection")
        self.capacity_constraints()
        self.link_usage_constraints()
        self.shared_link_constraints()
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
        logging.debug("Total time for shared-protection setup %s", time.time() - setup_start)
        logging.debug("Starting shared-protection optimization %s", log_file)

        if self.user_input.instance.warmstart and self.user_input.instance.solution_method == SolutionMethod.ONE_SHOT:
            self.model.update()
            self.model.read(self.user_input.instance.warmstart)

        self.model.optimize()
        if self.model.Status != gp.GRB.OPTIMAL:
            logging.warning("Shared protection finished with non-optimal status %s", self.model.Status)
            return self.model.Status
        return self.model.Status

    def _extract_shared_link_usage(self):
        links = []
        for var in self.model.getVars():
            if not var.varName.startswith("shared_link_used_p_") or var.x <= 0.5:
                continue
            suffix = var.varName[len("shared_link_used_p_"):]
            i, j = suffix.split("_")
            links.append((int(i), int(j)))
        links.sort()
        return links

    def get_schedule(self):
        flows, schedule_json = super().get_schedule()
        if not schedule_json:
            return flows, schedule_json
        shared_links = self._extract_shared_link_usage()
        schedule_json["8-Protection_Link_Count"] = len(shared_links)
        schedule_json["8a-Per_Demand_Protection_Link_Count"] = len(
            self._extract_link_usage("link_used_p_")
        )
        schedule_json["13-Protection_Links"] = [
            f"Shared protection reserves {i}->{j}" for i, j in shared_links
        ]
        schedule_json["13a-Shared_Protection_Links"] = schedule_json["13-Protection_Links"]
        return flows, schedule_json
