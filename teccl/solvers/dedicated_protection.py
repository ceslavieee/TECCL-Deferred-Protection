import logging
import time

import gurobipy as gp
from gurobipy import GRB

from teccl.input_data import FailureModel, ObjectiveType, SolutionMethod, UserInputParams
from teccl.solvers.deferred_protection import DeferredProtectionFormulation
from teccl.topologies.topology import Topology


class DedicatedProtectionFormulation(DeferredProtectionFormulation):
    """
    Dedicated protection baseline.

    Working and protection flows are both planned before failure and share the same
    time horizon. Unlike deferred protection, the backup is not postponed to a later
    phase: it is provisioned together with the working communication and consumes
    capacity in the same static schedule.

    Although this class inherits shared helper methods from
    DeferredProtectionFormulation, it intentionally does not use concrete
    failure-time information. Any demand whose working path is exposed to a failed
    link is treated as requiring pre-planned backup.
    """

    def __init__(self, user_input: UserInputParams, topology: Topology) -> None:
        super().__init__(user_input, topology)
        self.solver_name = "DedicatedProtection_MILP"
        # Dedicated protection keeps backup resources from the beginning, so the
        # deferred dynamic-release approximation must not change its coverage.
        self.enable_dynamic_backup_release = False

    def objective_formulation(self, objective_type: ObjectiveType = ObjectiveType.PAPER):
        del objective_type
        logging.debug("Adding dedicated-protection objective")
        objective = gp.LinExpr(0.0)
        completion_weight = 1000.0
        protection_completion_weight = 100.0
        protection_flow_penalty = 0.05
        protection_link_penalty = 0.1

        objective += completion_weight * self.working_completion_epoch
        objective += protection_completion_weight * self.protection_completion_epoch
        self._add_demand_path_tiebreaker(objective)

        for s in self.nodes:
            for i in self.nodes:
                for j in self.nodes:
                    if self.topology.capacity[i][j] <= 0:
                        continue
                    for c in self.chunks:
                        objective += protection_link_penalty * self.link_used_p[s][i][j][c]
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
        logging.debug("Total time for dedicated-protection setup %s", time.time() - setup_start)
        logging.debug("Starting dedicated-protection optimization %s", log_file)

        if self.user_input.instance.warmstart and self.user_input.instance.solution_method == SolutionMethod.ONE_SHOT:
            self.model.update()
            self.model.read(self.user_input.instance.warmstart)

        self.model.optimize()
        if self.model.Status != GRB.OPTIMAL:
            logging.warning("Dedicated protection finished with non-optimal status %s", self.model.Status)
            return self.model.Status
        return self.model.Status

    def find_demand_satisfied_k(self) -> int:
        satisfied_epochs = {}
        for var in self.model.getVars():
            if not var.varName.startswith("total_demand_w_") or var.x <= 0.9:
                continue
            suffix = var.varName[len("total_demand_w_"):]
            s, i, c, k = suffix.split("_")
            key = (int(s), int(i), int(c))
            epoch = int(k)
            if key not in satisfied_epochs or epoch < satisfied_epochs[key]:
                satisfied_epochs[key] = epoch
        if not satisfied_epochs:
            return self.final_deadline
        return max(satisfied_epochs.values())

    def _extract_link_usage(self, prefix: str):
        links = []
        for var in self.model.getVars():
            if not var.varName.startswith(prefix) or var.x <= 0.5:
                continue
            suffix = var.varName[len(prefix):]
            parts = suffix.split("_")
            if len(parts) != 4:
                continue
            s, i, j, c = parts
            links.append((int(s), int(i), int(j), int(c)))
        links.sort()
        return links

    def get_schedule(self):
        if self.model.SolCount <= 0:
            return [], {}
        working_flows = self._extract_phase_flows("flow_w_")
        protection_flows = self._extract_phase_flows("flow_p_")
        working_demand_links = self._extract_working_demand_links()
        working_demand_link_epochs = self._extract_working_demand_link_epochs()
        working_links = self._extract_link_usage("link_used_w_")
        protection_links = self._extract_link_usage("link_used_p_")
        failure_summary = self._extract_failure_scenario_summary()
        working_epochs = self.find_demand_satisfied_k() + 1
        schedule_json = {
            "1-Epoch_Duration": self.epoch_duration,
            "2-Expected_Epoch_Duration": self.expected_epoch_duration,
            "3-Working_Epochs_Required": working_epochs,
            "4-Collective_Finish_Time": self.epoch_duration * working_epochs,
            "4a-Objective_Working_Completion_Epoch": self.working_completion_epoch.X,
            "4b-Objective_Protection_Completion_Epoch": self.protection_completion_epoch.X,
            "5-Working_Flow_Count": len(working_flows),
            "6-Protection_Flow_Count": len(protection_flows),
            "7-Working_Link_Count": len(working_links),
            "8-Protection_Link_Count": len(protection_links),
            "8b-Failure_Model": self.user_input.instance.failure_model.name,
            "8c-Failure_Exposure_Granularity": (
                "per-demand path exposure"
                if self.user_input.instance.failure_model == FailureModel.EXACT
                else "source-chunk path exposure"
            ),
            "8d-Protection_Timing_Model": "pre-planned protection",
            "8e-Fixed_Working_Schedule": self.user_input.instance.fixed_working_schedule,
            "9-Failure_Scenarios": [f"{i}->{j}" for i, j in self.failure_scenarios],
            "10-Failure_Scenario_Summary": failure_summary,
            "11-Working_Flows": [
                f"Chunk {c} from {s} traveled over {i}->{j} in epoch {k}"
                for s, i, j, c, k in working_flows
            ],
            "11a-Working_Demand_Links": working_demand_links,
            "11b-Working_Demand_Link_Epochs": working_demand_link_epochs,
            "12-Protection_Flows": [
                f"Chunk {c} from {s} traveled over {i}->{j} in epoch {k}"
                for s, i, j, c, k in protection_flows
            ],
            "13-Protection_Links": [
                f"Chunk {c} from {s} reserves {i}->{j}"
                for s, i, j, c in protection_links
            ],
        }
        return working_flows + protection_flows, schedule_json
