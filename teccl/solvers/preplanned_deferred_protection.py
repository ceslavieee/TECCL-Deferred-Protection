import json
import logging
import time
from itertools import product
from pathlib import Path
from typing import Dict, List, Tuple

import gurobipy as gp
from gurobipy import GRB

from teccl.input_data import FailureModel, SolutionMethod, UserInputParams
from teccl.solvers.deferred_protection import DeferredProtectionFormulation
from teccl.solvers.protection_resource_accounting import (
    add_common_resource_fields,
    build_future_reservation_accounting,
)
from teccl.topologies.topology import Topology


class PreplannedDeferredProtectionFormulation(DeferredProtectionFormulation):
    """Pre-failure deferred backup planning for atomic TE-CCL commodities.

    A commodity is one ``(source, chunk)`` pair. The model computes a complete
    working schedule and a link-disjoint contingency schedule before execution.
    The contingency schedule is placed after the working window. Its future
    link-epoch transmissions remain reserved only until the working schedule has
    delivered that commodity to every destination.

    ``flow_p`` is the executable contingency plan. ``backup_reservation`` is the
    time-indexed reservation state under failure-free working progress; it never
    contributes data to the working buffers.
    """

    def __init__(self, user_input: UserInputParams, topology: Topology) -> None:
        # Preplanned protection proves survivability through a link-disjoint
        # contingency schedule, rather than per-failure recovery variables.
        user_input.instance.enable_failure_scenarios = False
        user_input.instance.enable_real_failure_timing = False
        super().__init__(user_input, topology)
        self.solver_name = "PreplannedDeferredProtection_MILP"
        self.real_failure_timing_enabled = False
        self.failure_scenarios = []
        self.final_deadline = self.num_epochs - 1
        self.working_deadline = min(
            self.final_deadline,
            max(
                0,
                int(
                    (self.final_deadline + 1)
                    * self.user_input.instance.working_deadline_ratio
                )
                - 1,
            ),
        )
        fixed_working = self.user_input.instance.fixed_working_schedule
        if fixed_working:
            fixed_schedule = json.loads(Path(fixed_working).read_text())
            strict_window = fixed_schedule.get(
                "7p-DPP_Reservation_Deadline_Epoch"
            )
            if strict_window is not None:
                self.working_deadline = min(
                    self.final_deadline,
                    max(0, int(strict_window) - 1),
                )
        self.deferred_activation_epoch = min(
            self.final_deadline,
            self.working_deadline + 1,
        )
        self.observation_epochs = list(range(self.working_deadline + 2))
        self.chunk_complete_before: Dict[Tuple[int, int, int], gp.Var] = {}
        self.backup_reservation: Dict[Tuple[int, int, int, int, int, int], gp.Var] = {}
        self.active_commodities: List[Tuple[int, int, List[int]]] = []

    def initialize_variables(self) -> None:
        super().initialize_variables()
        self.active_commodities = []
        for s, c in product(self.nodes, self.chunks):
            destinations = [d for d in self.nodes if self.demand[s][d][c]]
            if not destinations:
                continue
            self.active_commodities.append((s, c, destinations))
            for observation_epoch in self.observation_epochs:
                self.chunk_complete_before[(s, c, observation_epoch)] = self.model.addVar(
                    0,
                    1,
                    vtype=GRB.BINARY,
                    name="chunk_complete_before_%d_%d_%d" % (s, c, observation_epoch),
                )

        active_commodity_keys = {
            (source, chunk) for source, chunk, _ in self.active_commodities
        }
        for s, i, j, c, backup_epoch in product(
            self.nodes,
            self.nodes,
            self.nodes,
            self.chunks,
            self.epochs,
        ):
            if (s, c) not in active_commodity_keys:
                continue
            flow_var = self.flow_p[s][i][j][c][backup_epoch]
            if not self._is_var(flow_var) or backup_epoch < self.deferred_activation_epoch:
                continue
            for observation_epoch in self.observation_epochs:
                if observation_epoch > backup_epoch:
                    continue
                self.backup_reservation[
                    (observation_epoch, s, i, j, c, backup_epoch)
                ] = self.model.addVar(
                    0,
                    1,
                    vtype=GRB.BINARY,
                    name="backup_reservation_%d_%d_%d_%d_%d_%d"
                    % (observation_epoch, s, i, j, c, backup_epoch),
                )

    def chunk_completion_constraints(self) -> None:
        """Track exact failure-free completion of each atomic source chunk."""
        for s, c, destinations in self.active_commodities:
            for observation_epoch in self.observation_epochs:
                complete_var = self.chunk_complete_before[(s, c, observation_epoch)]
                if observation_epoch == 0:
                    self.model.addConstr(
                        complete_var == 0,
                        name="chunk_not_complete_at_admission_%d_%d" % (s, c),
                    )
                    continue

                delivered = [
                    self.total_demand_sat_w[s][d][c][observation_epoch - 1]
                    for d in destinations
                ]
                for destination, delivered_var in zip(destinations, delivered):
                    self.model.addConstr(
                        complete_var <= delivered_var,
                        name="chunk_complete_ub_%d_%d_%d_%d"
                        % (s, c, destination, observation_epoch),
                    )
                self.model.addConstr(
                    complete_var >= gp.quicksum(delivered) - len(delivered) + 1,
                    name="chunk_complete_lb_%d_%d_%d" % (s, c, observation_epoch),
                )

    def backup_reservation_constraints(self) -> None:
        """Bind each reservation snapshot to plan usage and working progress."""
        for (
            observation_epoch,
            s,
            i,
            j,
            c,
            backup_epoch,
        ), reservation_var in self.backup_reservation.items():
            flow_var = self.flow_p[s][i][j][c][backup_epoch]
            if not self.enable_dynamic_backup_release:
                self.model.addConstr(
                    reservation_var == flow_var,
                    name="backup_reservation_static_%d_%d_%d_%d_%d_%d"
                    % (observation_epoch, s, i, j, c, backup_epoch),
                )
                continue

            complete_var = self.chunk_complete_before[(s, c, observation_epoch)]
            self.model.addConstr(
                reservation_var <= flow_var,
                name="backup_reservation_plan_ub_%d_%d_%d_%d_%d_%d"
                % (observation_epoch, s, i, j, c, backup_epoch),
            )
            self.model.addConstr(
                reservation_var <= 1 - complete_var,
                name="backup_reservation_progress_ub_%d_%d_%d_%d_%d_%d"
                % (observation_epoch, s, i, j, c, backup_epoch),
            )
            self.model.addConstr(
                reservation_var >= flow_var - complete_var,
                name="backup_reservation_exact_lb_%d_%d_%d_%d_%d_%d"
                % (observation_epoch, s, i, j, c, backup_epoch),
            )

    def objective_expressions(
        self,
    ) -> Tuple[gp.LinExpr, gp.LinExpr, gp.LinExpr, gp.LinExpr]:
        reservation_holding = gp.LinExpr(0.0)
        for key, reservation_var in self.backup_reservation.items():
            _, _, i, j, _, _ = key
            weight = (
                self.get_beta_num_back(i, j) + 1
                if self.user_input.instance.beta_weighted_holding_objective
                else 1
            )
            reservation_holding.add(reservation_var, weight)
        backup_capacity = gp.LinExpr(0.0)
        flow_tiebreaker = gp.LinExpr(0.0)
        for s, i, j, c, k in product(
            self.nodes,
            self.nodes,
            self.nodes,
            self.chunks,
            self.epochs,
        ):
            flow_var = self.flow_p[s][i][j][c][k]
            if self._is_var(flow_var):
                backup_capacity.add(
                    flow_var,
                    self.get_beta_num_back(i, j) + 1,
                )
                flow_tiebreaker.add(flow_var)
            working_var = self.flow_w[s][i][j][c][k]
            if self._is_var(working_var):
                flow_tiebreaker.add(working_var)

        return (
            backup_capacity,
            reservation_holding,
            self.protection_completion_epoch,
            flow_tiebreaker,
        )

    def encode_problem(self, use_one_less_epoch: bool = False, previous_buffers=None) -> int:
        del use_one_less_epoch, previous_buffers
        setup_start = time.time()
        self.model = gp.Model(self.solver_name)
        self.initialize_variables()
        self.fixed_working_schedule_constraints()
        self._add_phase_gating_constraints()
        self._add_destination_constraints_for_phase(
            self.flow_w,
            self.buffer_w,
            self.total_demand_sat_w,
            "working",
            self.working_deadline,
            True,
        )
        self._add_destination_constraints_for_phase(
            self.flow_p,
            self.buffer_p,
            self.total_demand_sat_p,
            "preplanned_backup",
            self.final_deadline,
            True,
        )
        self.completion_time_constraints()
        self._add_node_constraints_for_phase(self.flow_w, self.buffer_w, "working")
        self._add_node_constraints_for_phase(self.flow_p, self.buffer_p, "preplanned_backup")
        self.capacity_constraints()
        self.link_usage_constraints()
        if self.user_input.instance.failure_model == FailureModel.EXACT:
            self.demand_path_constraints()
        self.chunk_completion_constraints()
        self.backup_reservation_constraints()

        (
            backup_capacity,
            reservation_holding,
            protected_completion,
            flow_tiebreaker,
        ) = self.objective_expressions()
        self.model.ModelSense = GRB.MINIMIZE
        self.model.setObjectiveN(
            backup_capacity,
            index=0,
            priority=4,
            weight=1.0,
            name="reserved_occupied_link_epochs",
        )
        self.model.setObjectiveN(
            reservation_holding,
            index=1,
            priority=3,
            weight=1.0,
            name="reservation_holding",
        )
        self.model.setObjectiveN(
            protected_completion,
            index=2,
            priority=2,
            weight=1.0,
            name="protected_completion",
        )
        self.model.setObjectiveN(
            flow_tiebreaker,
            index=3,
            priority=1,
            weight=1.0,
            name="flow_tiebreaker",
        )

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
        logging.debug(
            "Total time for preplanned deferred-protection setup %s",
            time.time() - setup_start,
        )
        if (
            self.user_input.instance.warmstart
            and self.user_input.instance.solution_method == SolutionMethod.ONE_SHOT
        ):
            self.model.update()
            self.model.read(self.user_input.instance.warmstart)

        self.model.optimize()
        if self.model.Status != GRB.OPTIMAL:
            logging.warning(
                "Preplanned deferred protection finished with non-optimal status %s",
                self.model.Status,
            )
        return self.model.Status

    def _reservation_release_profile(self) -> List[Dict[str, int]]:
        profile = []
        initial_count = 0
        for observation_epoch in self.observation_epochs:
            active_count = sum(
                1
                for key, var in self.backup_reservation.items()
                if key[0] == observation_epoch and var.X > 0.5
            )
            if observation_epoch == 0:
                initial_count = active_count
            profile.append(
                {
                    "observation_epoch": observation_epoch,
                    "active_reservations": active_count,
                    "released_reservations": initial_count - active_count,
                }
            )
        return profile

    def _chunk_completion_profile(self) -> List[Dict[str, int]]:
        return self._extract_chunk_working_completion_profile()

    def get_schedule(self):
        flows, schedule_json = super().get_schedule()
        if not schedule_json:
            return flows, schedule_json

        release_profile = self._reservation_release_profile()
        schedule_json["7e-Failure_Time_Epoch"] = None
        schedule_json["7f-Detection_Delay_Epochs"] = None
        schedule_json["7g-Recovery_Start_Epoch"] = None
        schedule_json["7h-Recovery_Inherits_Scenario_Valid_Working_Buffers"] = False
        schedule_json["7i-Post_Failure_Working_Flows_Continue"] = False
        schedule_json["7j-Failure_Model"] = "NOT_APPLICABLE"
        schedule_json["7k-Failure_Exposure_Granularity"] = (
            "atomic source-chunk completion"
        )
        schedule_json["7m-Deferred_Timing_Mode"] = "PREPLANNED"
        schedule_json["7n-Protection_Timing_Model"] = (
            "pre-failure planning with delayed backup reservation"
        )
        schedule_json["7o-Protection_Unit"] = "atomic source-chunk multicast commodity"
        schedule_json["7p-Protection_Guarantee"] = (
            "directed-link-disjoint preplanned contingency schedule"
        )
        schedule_json["12-Reservation_Release_Profile"] = release_profile
        chunk_completion_profile = self._chunk_completion_profile()
        schedule_json["12a-Chunk_Working_Completion_Profile"] = chunk_completion_profile
        schedule_json["12b-Initial_Backup_Reservation_Count"] = (
            release_profile[0]["active_reservations"] if release_profile else 0
        )
        schedule_json["12c-Final_Backup_Reservation_Count"] = (
            release_profile[-1]["active_reservations"] if release_profile else 0
        )
        schedule_json["12d-Reservation_Holding_Units"] = sum(
            row["active_reservations"] for row in release_profile
        )
        schedule_json["12e-Backup_Is_Preplanned_Not_Normally_Transmitted"] = True
        protection_flows = self._extract_phase_flows("flow_p_")
        accounting = build_future_reservation_accounting(
            protection_flows,
            chunk_completion_profile,
            self._extract_link_occupancy_epochs(),
            self.epoch_duration,
            self.user_input.topology.chunk_size,
        )
        add_common_resource_fields(schedule_json, accounting)
        beta_weighted = (
            self.user_input.instance.beta_weighted_holding_objective
        )
        schedule_json["12f-Primary_Holding_Objective"] = (
            "beta-weighted occupied link-epoch holding"
            if beta_weighted
            else "unweighted reserved flow-start holding"
        )
        schedule_json["12g-Primary_Holding_Objective_Value"] = (
            accounting["future_reservation_holding_units"]
            if beta_weighted
            else schedule_json["12d-Reservation_Holding_Units"]
        )
        schedule_json["12h-Primary_Holding_Objective_Physical_Equivalent"] = (
            accounting["future_reservation_holding_link_second_squared"]
            if beta_weighted
            else None
        )
        schedule_json["12i-Objective_Hierarchy"] = [
            "reserved occupied link-epochs",
            "reservation holding",
            "protected completion",
            "flow tiebreaker",
        ]
        schedule_json["12j-Actual_Working_Completion_Epoch"] = max(
            (
                row["working_completion_epoch"]
                for row in chunk_completion_profile
            ),
            default=0,
        )
        schedule_json["12k-Actual_Protection_Completion_Epoch"] = (
            self.find_demand_satisfied_k() + 1
        )
        schedule_json["12l-Configured_Service_Deadline_Epoch"] = (
            self.final_deadline + 1
        )
        detection_delay = self.user_input.instance.detection_delay_epochs
        latest_relevant_failure = max(
            0,
            schedule_json["12j-Actual_Working_Completion_Epoch"] - 1,
        )
        schedule_json["12m-Pairing_Detection_Delay_Epochs"] = detection_delay
        schedule_json["12n-Latest_Relevant_Failure_Epoch"] = (
            latest_relevant_failure
        )
        schedule_json["12o-Latest_Detection_Precedes_Activation"] = (
            latest_relevant_failure + detection_delay
            <= self.deferred_activation_epoch
        )
        return flows, schedule_json
