"""Two-stage scenario-robust reserved-capacity protection."""

import json
import logging
import re
import time
from itertools import product
from pathlib import Path
from typing import Dict, List, Tuple

import gurobipy as gp
from gurobipy import GRB

from teccl.input_data import (
    DeferredTimingMode,
    FailureModel,
    SolutionMethod,
    UserInputParams,
)
from teccl.solvers.deferred_protection import DeferredProtectionFormulation
from teccl.solvers.protection_resource_accounting import (
    add_common_resource_fields,
    build_future_reservation_accounting,
)
from teccl.topologies.topology import Topology


class StrictDedicatedProtectionFormulation(DeferredProtectionFormulation):
    """Reserve common capacity first, then execute recovery per failure.

    ``flow_w`` is normal working traffic, ``flow_p`` is first-stage reserved
    capacity ``rho``, and ``flow_r`` is second-stage executed recovery. Backup
    reservation never creates data or buffer state. Every recovery start is
    bound to a reservation on the same directed link and epoch.

    One solve covers all configured directed-link failures at one configured
    failure epoch. Failure-time sweeps remain an outer experiment.
    """

    def __init__(self, user_input: UserInputParams, topology: Topology) -> None:
        super().__init__(user_input, topology)
        if not self.user_input.instance.enable_failure_scenarios:
            raise ValueError(
                "Scenario-robust protection requires enable_failure_scenarios=true"
            )
        self.solver_name = "ScenarioRobustProtection_MILP"
        self.real_failure_timing_enabled = True
        self.enable_dynamic_backup_release = False
        # A rooted working tree has one source-to-destination path. Demands whose
        # path crosses the failed edge must be sent again from the source over
        # reserved backup capacity. Do not count causally disconnected downstream
        # working sends as valid scenario arrivals.
        self.recovery_inherits_working_arrivals = False
        # Unlike the inherited heuristic prototype, strict DPP uses the
        # request's configured horizon as the common service deadline.
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
        # The same scenario-robust model supports the paired timing policies.
        # DPP reserves inside the working window; DDPP shifts the same kind of
        # contingency capacity into the second half of the service horizon.
        self.deferred_reservation = (
            self.user_input.instance.deferred_timing_mode
            == DeferredTimingMode.PREPLANNED
        )
        self.dpp_boundary_epoch = self._reservation_deadline_epoch()
        self.working_deadline = self.dpp_boundary_epoch
        self.reservation_start_epoch = (
            self.dpp_boundary_epoch + 1 if self.deferred_reservation else 0
        )
        self.reservation_deadline_epoch = (
            self.final_deadline
            if self.deferred_reservation
            else self.dpp_boundary_epoch
        )
        self.deferred_activation_epoch = self.reservation_start_epoch
        # Do not clamp detection to the service horizon. If notification occurs
        # after the deadline, every recovery start is gated off and an affected
        # scenario correctly becomes infeasible.
        self.recovery_start_epoch = max(
            self.reservation_start_epoch,
            self.failure_time_epoch + self.detection_delay_epochs,
        )
        self.enforce_failure_time_robust_reservation = bool(
            self.user_input.instance.enforce_failure_time_robust_reservation
        )
        self.chunk_complete_before: Dict[Tuple[int, int, int], gp.Var] = {}

    def initialize_variables(self) -> None:
        super().initialize_variables()
        if not self.enforce_failure_time_robust_reservation:
            return
        for s, c in product(self.nodes, self.chunks):
            if not any(self.demand[s][d][c] for d in self.nodes):
                continue
            for observation_epoch in self.epochs:
                self.chunk_complete_before[(s, c, observation_epoch)] = (
                    self.model.addVar(
                        0,
                        1,
                        vtype=GRB.BINARY,
                        name="strict_chunk_complete_before_%d_%d_%d"
                        % (s, c, observation_epoch),
                    )
                )

    def failure_time_robust_reservation_constraints(self) -> None:
        """Place each commodity's reservation after nominal completion.

        For every relevant failure ``tau < completion``, reservation starts are
        shifted by ``detection_delay - 1`` beyond nominal completion. Thus
        activation is no later than the first eligible reservation. The tau=0
        recovery certifies a superset of later affected demands on the same
        reserved tree and schedule.
        """

        if not self.enforce_failure_time_robust_reservation:
            return
        for (s, c, observation_epoch), complete_var in (
            self.chunk_complete_before.items()
        ):
            destinations = [
                d for d in self.nodes if self.demand[s][d][c]
            ]
            if observation_epoch == 0:
                self.model.addConstr(
                    complete_var == 0,
                    name="strict_chunk_incomplete_at_admission_%d_%d" % (s, c),
                )
            else:
                delivered = [
                    self.total_demand_sat_w[s][d][c][observation_epoch - 1]
                    for d in destinations
                ]
                for destination, delivered_var in zip(destinations, delivered):
                    self.model.addConstr(
                        complete_var <= delivered_var,
                        name="strict_chunk_complete_ub_%d_%d_%d_%d"
                        % (s, c, destination, observation_epoch),
                    )
                self.model.addConstr(
                    complete_var
                    >= gp.quicksum(delivered) - len(delivered) + 1,
                    name="strict_chunk_complete_lb_%d_%d_%d"
                    % (s, c, observation_epoch),
                )

        for s, i, j, c, reservation_epoch in product(
            self.nodes,
            self.nodes,
            self.nodes,
            self.chunks,
            self.epochs,
        ):
            if self.topology.capacity[i][j] <= 0:
                continue
            reservation_var = self.flow_p[s][i][j][c][reservation_epoch]
            if not self._is_var(reservation_var):
                continue
            eligible_completion_epoch = (
                reservation_epoch - self.detection_delay_epochs + 1
            )
            complete_var = self.chunk_complete_before.get(
                (s, c, eligible_completion_epoch)
            )
            if complete_var is None:
                self.model.addConstr(
                    reservation_var == 0,
                    name="reservation_before_robust_activation_%d_%d_%d_%d_%d"
                    % (s, i, j, c, reservation_epoch),
                )
            else:
                self.model.addConstr(
                    reservation_var <= complete_var,
                    name="reservation_after_chunk_completion_%d_%d_%d_%d_%d"
                    % (s, i, j, c, reservation_epoch),
                )

    def _reservation_deadline_epoch(self) -> int:
        schedule_path = self.user_input.instance.fixed_working_schedule
        if not schedule_path:
            return self.working_deadline
        schedule = json.loads(Path(schedule_path).read_text())
        if schedule.get("7p-DPP_Reservation_Deadline_Epoch") is not None:
            return min(
                self.final_deadline,
                max(
                    0,
                    int(schedule["7p-DPP_Reservation_Deadline_Epoch"]) - 1,
                ),
            )
        return self.working_deadline

    def reservation_timing_constraints(self) -> None:
        """Keep reservations inside the selected DPP or DDPP time window."""

        for s, i, j, c, k in product(
            self.nodes,
            self.nodes,
            self.nodes,
            self.chunks,
            self.epochs,
        ):
            if self.reservation_start_epoch <= k <= self.reservation_deadline_epoch:
                continue
            reservation_var = self.flow_p[s][i][j][c][k]
            if self._is_var(reservation_var):
                self.model.addConstr(
                    reservation_var == 0,
                    name="reservation_window_%d_%d_%d_%d_%d" % (s, i, j, c, k),
                )

    @staticmethod
    def _load_reservation_slots(schedule_path: str):
        schedule = json.loads(Path(schedule_path).read_text())
        pattern = re.compile(
            r"Chunk (?P<c>\d+) from (?P<s>\d+) reserves "
            r"(?P<i>\d+)->(?P<j>\d+) in epoch (?P<k>\d+)"
        )
        reservations = set()
        for row in schedule.get("11b-Reserved_Backup_Slots", []):
            match = pattern.fullmatch(str(row))
            if not match:
                raise ValueError(f"Could not parse fixed reservation: {row}")
            reservations.add(
                (
                    int(match.group("s")),
                    int(match.group("i")),
                    int(match.group("j")),
                    int(match.group("c")),
                    int(match.group("k")),
                )
            )
        return reservations

    def fixed_reservation_constraints(self) -> None:
        """Optionally fix rho or retain a seed plan while allowing additions."""

        fixed_path = self.user_input.instance.fixed_reservation_schedule
        minimum_path = self.user_input.instance.minimum_reservation_schedule
        if fixed_path and minimum_path:
            raise ValueError(
                "fixed_reservation_schedule and minimum_reservation_schedule "
                "are mutually exclusive"
            )
        if not fixed_path and not minimum_path:
            return
        fixed = self._load_reservation_slots(fixed_path) if fixed_path else None
        minimum = (
            self._load_reservation_slots(minimum_path) if minimum_path else set()
        )
        for s, i, j, c, k in product(
            self.nodes,
            self.nodes,
            self.nodes,
            self.chunks,
            self.epochs,
        ):
            reservation_var = self.flow_p[s][i][j][c][k]
            if not self._is_var(reservation_var):
                continue
            slot = (s, i, j, c, k)
            if fixed is not None:
                self.model.addConstr(
                    reservation_var == int(slot in fixed),
                    name="fixed_reservation_%d_%d_%d_%d_%d" % (s, i, j, c, k),
                )
            elif slot in minimum:
                self.model.addConstr(
                    reservation_var == 1,
                    name="retained_reservation_%d_%d_%d_%d_%d"
                    % (s, i, j, c, k),
                )

    def fixed_backup_tree_constraints(self) -> None:
        """Fix reserved tree edges while leaving reservation epochs free."""

        tree_path = self.user_input.instance.fixed_backup_tree_schedule
        if not tree_path:
            return
        if (
            self.user_input.instance.fixed_reservation_schedule
            or self.user_input.instance.minimum_reservation_schedule
        ):
            raise ValueError(
                "fixed_backup_tree_schedule cannot be combined with fixed or "
                "minimum reservation schedules"
            )
        tree_edges = {
            (s, i, j, c)
            for s, i, j, c, _ in self._load_reservation_slots(tree_path)
        }
        for s, i, j, c in product(
            self.nodes,
            self.nodes,
            self.nodes,
            self.chunks,
        ):
            if self.topology.capacity[i][j] <= 0:
                continue
            self.model.addConstr(
                self.link_used_p[s][i][j][c]
                == int((s, i, j, c) in tree_edges),
                name="fixed_backup_tree_%d_%d_%d_%d" % (s, i, j, c),
            )

    def fixed_working_tree_constraints(self) -> None:
        """Fix working-tree edges while leaving transmission epochs free."""

        tree_path = self.user_input.instance.fixed_working_tree_schedule
        if not tree_path:
            return
        if self.user_input.instance.fixed_working_schedule:
            raise ValueError(
                "fixed_working_tree_schedule cannot be combined with an "
                "exact fixed_working_schedule"
            )
        schedule = json.loads(Path(tree_path).read_text())
        pattern = re.compile(
            r"Chunk (?P<c>\d+) from (?P<s>\d+) traveled over "
            r"(?P<i>\d+)->(?P<j>\d+) in epoch (?P<k>\d+)"
            r"(?: via switches (?P<switches>[\d,\- >]+))?"
        )
        tree_edges = set()
        for row in schedule.get("10-Working_Flows", []):
            match = pattern.search(str(row))
            if not match:
                raise ValueError(f"Could not parse fixed working tree row: {row}")
            source = int(match.group("s"))
            chunk = int(match.group("c"))
            start_node = int(match.group("i"))
            end_node = int(match.group("j"))
            switches = match.group("switches") or ""
            for i, j in self._expand_fixed_working_link(
                start_node,
                end_node,
                switches,
            ):
                tree_edges.add((source, i, j, chunk))
        if not tree_edges:
            raise ValueError("fixed_working_tree_schedule has no working edges")
        for s, i, j, c in product(
            self.nodes,
            self.nodes,
            self.nodes,
            self.chunks,
        ):
            if self.topology.capacity[i][j] <= 0:
                continue
            self.model.addConstr(
                self.link_used_w[s][i][j][c]
                == int((s, i, j, c) in tree_edges),
                name="fixed_working_tree_%d_%d_%d_%d" % (s, i, j, c),
            )

    def reservation_binding_constraints(self) -> None:
        """Require every scenario recovery start to consume reserved capacity."""

        start = time.time()
        for scenario_idx, _ in enumerate(self.failure_scenarios):
            for s, i, j, c, k in product(
                self.nodes,
                self.nodes,
                self.nodes,
                self.chunks,
                self.epochs,
            ):
                recovery_var = self._flow_r(scenario_idx, s, i, j, c, k)
                if not self._is_var(recovery_var):
                    continue
                reservation_var = self.flow_p[s][i][j][c][k]
                if self._is_var(reservation_var):
                    self.model.addConstr(
                        recovery_var <= reservation_var,
                        name=(
                            "recovery_uses_reservation_%d_%d_%d_%d_%d_%d"
                            % (scenario_idx, s, i, j, c, k)
                        ),
                    )
                else:
                    self.model.addConstr(
                        recovery_var == 0,
                        name=(
                            "recovery_without_reservation_%d_%d_%d_%d_%d_%d"
                            % (scenario_idx, s, i, j, c, k)
                        ),
                    )
        logging.debug(
            "Finished adding recovery-reservation binding in %s",
            time.time() - start,
        )

    def multicast_tree_pair_constraints(self) -> None:
        """Restrict each source chunk to one working and one backup tree.

        DPP 1:1 is a unicast path-pair notion. For an AllGather commodity the
        corresponding fixed structure is a pair of rooted multicast trees.
        Binary node-use variables plus depth ordering rule out multiple
        parents, disconnected reserved components, and directed cycles.
        """

        if not self.user_input.instance.enforce_multicast_tree_pair:
            return
        start = time.time()
        big_m = max(1, self.num_nodes)
        for phase, link_used in (
            ("working", self.link_used_w),
            ("backup", self.link_used_p),
        ):
            for s, c in product(self.nodes, self.chunks):
                if not any(self.demand[s][d][c] for d in self.nodes):
                    continue
                node_used = {
                    node: self.model.addVar(
                        0,
                        1,
                        vtype=GRB.BINARY,
                        name=f"{phase}_tree_node_{s}_{c}_{node}",
                    )
                    for node in self.nodes
                }
                depth = {
                    node: self.model.addVar(
                        0,
                        max(0, self.num_nodes - 1),
                        vtype=GRB.CONTINUOUS,
                        name=f"{phase}_tree_depth_{s}_{c}_{node}",
                    )
                    for node in self.nodes
                }
                self.model.addConstr(
                    node_used[s] == 1,
                    name=f"{phase}_tree_source_used_{s}_{c}",
                )
                self.model.addConstr(
                    depth[s] == 0,
                    name=f"{phase}_tree_source_depth_{s}_{c}",
                )
                for destination in self.nodes:
                    if not self.demand[s][destination][c]:
                        continue
                    self.model.addConstr(
                        node_used[destination] == 1,
                        name=(
                            f"{phase}_tree_destination_used_"
                            f"{s}_{c}_{destination}"
                        ),
                    )
                for node in self.nodes:
                    incoming = [
                        link_used[s][i][node][c]
                        for i in self.nodes
                        if self.topology.capacity[i][node] > 0
                    ]
                    incoming_expr = gp.quicksum(incoming)
                    if node == s:
                        self.model.addConstr(
                            incoming_expr == 0,
                            name=f"{phase}_tree_no_source_parent_{s}_{c}",
                        )
                    else:
                        self.model.addConstr(
                            incoming_expr == node_used[node],
                            name=f"{phase}_tree_one_parent_{s}_{c}_{node}",
                        )
                    for j in self.nodes:
                        if self.topology.capacity[node][j] <= 0:
                            continue
                        edge = link_used[s][node][j][c]
                        self.model.addConstr(
                            edge <= node_used[node],
                            name=f"{phase}_tree_tail_used_{s}_{c}_{node}_{j}",
                        )
                        self.model.addConstr(
                            depth[j]
                            >= depth[node] + 1 - big_m * (1 - edge),
                            name=f"{phase}_tree_acyclic_{s}_{c}_{node}_{j}",
                        )
        logging.debug(
            "Finished adding multicast tree-pair constraints in %s",
            time.time() - start,
        )

    def inactive_commodity_constraints(self) -> None:
        """Eliminate flows for switch/source chunks with no collective demand."""

        inactive = {
            (s, c)
            for s, c in product(self.nodes, self.chunks)
            if not any(self.demand[s][d][c] for d in self.nodes)
        }
        for s, c in inactive:
            for i, j, k in product(self.nodes, self.nodes, self.epochs):
                working_var = self.flow_w[s][i][j][c][k]
                if self._is_var(working_var):
                    self.model.addConstr(
                        working_var == 0,
                        name="inactive_working_%d_%d_%d_%d_%d" % (s, i, j, c, k),
                    )
                reservation_var = self.flow_p[s][i][j][c][k]
                if self._is_var(reservation_var):
                    self.model.addConstr(
                        reservation_var == 0,
                        name="inactive_reservation_%d_%d_%d_%d_%d"
                        % (s, i, j, c, k),
                    )
        for (scenario_idx, s, i, j, c, k), recovery_var in self.flow_r.items():
            if (s, c) not in inactive or not self._is_var(recovery_var):
                continue
            self.model.addConstr(
                recovery_var == 0,
                name="inactive_recovery_%d_%d_%d_%d_%d_%d"
                % (scenario_idx, s, i, j, c, k),
            )

    def strict_recovery_deadline_constraints(self) -> None:
        """Require every affected demand to recover inside the policy window."""

        for scenario_idx, _ in enumerate(self.failure_scenarios):
            for s, d, c in product(self.nodes, self.nodes, self.chunks):
                if not self.demand[s][d][c]:
                    continue
                self.model.addConstr(
                    self._demand_r(
                        scenario_idx,
                        s,
                        d,
                        c,
                        self.reservation_deadline_epoch,
                    )
                    >= self.scenario_demand_fail[(scenario_idx, s, d, c)],
                    name=(
                        "strict_recovery_deadline_%d_%d_%d_%d"
                        % (scenario_idx, s, d, c)
                    ),
                )

    def set_lexicographic_objectives(self) -> None:
        """Install the theory-audit objective hierarchy without scalar mixing."""

        reserved_capacity = gp.LinExpr(0.0)
        reservation_holding = gp.LinExpr(0.0)
        flow_tiebreaker = gp.LinExpr(0.0)

        for s, i, j, c, k in product(
            self.nodes,
            self.nodes,
            self.nodes,
            self.chunks,
            self.epochs,
        ):
            if self.topology.capacity[i][j] <= 0:
                continue
            reservation_var = self.flow_p[s][i][j][c][k]
            if self._is_var(reservation_var):
                occupied_epochs = self.get_beta_num_back(i, j) + 1
                reserved_capacity.add(reservation_var, occupied_epochs)
                reservation_holding.add(
                    reservation_var,
                    occupied_epochs * (self.final_deadline - k + 1),
                )
                flow_tiebreaker.add(reservation_var)
            working_var = self.flow_w[s][i][j][c][k]
            if self._is_var(working_var):
                flow_tiebreaker.add(working_var)

        for recovery_var in self.flow_r.values():
            flow_tiebreaker.add(recovery_var)

        self.model.ModelSense = GRB.MINIMIZE
        self.model.setObjectiveN(
            self.working_completion_epoch,
            0,
            priority=5,
            name="working_completion",
        )
        self.model.setObjectiveN(
            reserved_capacity,
            1,
            priority=4,
            name="reserved_occupied_link_epochs",
        )
        self.model.setObjectiveN(
            reservation_holding,
            2,
            priority=3,
            name="reservation_holding",
        )
        self.model.setObjectiveN(
            self.protection_completion_epoch,
            3,
            priority=2,
            name="protected_completion",
        )
        self.model.setObjectiveN(
            flow_tiebreaker,
            4,
            priority=1,
            name="flow_tiebreaker",
        )

    def encode_problem(
        self,
        use_one_less_epoch: bool = False,
        previous_buffers=None,
    ) -> int:
        del use_one_less_epoch, previous_buffers
        setup_start = time.time()
        self.model = gp.Model(self.solver_name)
        self.initialize_variables()
        self.inactive_commodity_constraints()
        self.fixed_working_schedule_constraints()

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

        # Capacity reserves working + rho. The reservation variables have no
        # buffer or destination constraints because they are not data.
        self.capacity_constraints()
        self.link_usage_constraints()
        self.fixed_working_tree_constraints()
        self.multicast_tree_pair_constraints()
        self.failure_time_robust_reservation_constraints()
        self.reservation_timing_constraints()
        self.fixed_reservation_constraints()
        self.fixed_backup_tree_constraints()
        if self.user_input.instance.failure_model == FailureModel.EXACT:
            self.demand_path_constraints()

        self.recovery_destination_constraints()
        self.recovery_completion_time_constraints()
        self.recovery_node_constraints()
        self.recovery_timing_constraints()
        self.recovery_link_usage_constraints()
        self.reservation_binding_constraints()
        self.strict_recovery_deadline_constraints()
        # Redundant with flow_r <= rho plus working + rho capacity, but useful
        # as an explicit scenario-capacity assertion in the audit trail.
        self.recovery_capacity_constraints()
        self.failure_scenario_constraints()
        self.set_lexicographic_objectives()

        log_file = (
            f"Logs/{self.solver_name}_{self.user_input.topology.name}_"
            f"{self.num_nodes}-nodes_{self.num_chunks}-chunks_"
            f"{self.num_epochs}-epochs_{self.epoch_duration}-epochduration"
        )
        if self.user_input.gurobi.output_flag == 1 or self.user_input.instance.debug:
            if self.user_input.gurobi.log_file:
                log_file += self.user_input.gurobi.log_file
            self.model.setParam("LogFile", log_file + ".log")
            self.model.Params.LogToConsole = 0

        self.set_gurobi_params()
        logging.debug(
            "Total strict-dedicated setup time %s",
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
                "Scenario-robust protection finished with non-optimal status %s",
                self.model.Status,
            )
        return self.model.Status

    def get_schedule(self) -> Tuple[List[Tuple[int, int, int, int, int]], Dict]:
        if self.model.SolCount <= 0:
            return [], {}
        _, schedule_json = super().get_schedule()
        working_flows = self._extract_phase_flows("flow_w_")
        reservations = self._extract_phase_flows("flow_p_")
        recovery_flows = self._extract_recovery_flows()

        policy_name = "DDPP" if self.deferred_reservation else "DPP"
        schedule_json["7m-Protection_Semantics"] = (
            f"scenario-robust {policy_name} fixed-time subproblem"
        )
        schedule_json["7n-Normal_Operation_Backup_Data_Transmissions"] = 0
        schedule_json["7o-Recovery_Reservation_Binding"] = "flow_r <= flow_p"
        schedule_json["7p-DPP_Reservation_Deadline_Epoch"] = (
            self.dpp_boundary_epoch + 1
        )
        schedule_json["7q-Failure_Time_Known_To_Optimizer"] = True
        schedule_json["7r-Robust_Across_Unknown_Failure_Time"] = False
        schedule_json["7s-Strict_Protected_Completion_Deadline_Epoch"] = (
            self.reservation_deadline_epoch + 1
        )
        schedule_json["7t-Fixed_Reservation_Schedule"] = (
            self.user_input.instance.fixed_reservation_schedule
        )
        schedule_json["7u-Minimum_Reservation_Schedule"] = (
            self.user_input.instance.minimum_reservation_schedule
        )
        schedule_json["7u2-Fixed_Backup_Tree_Schedule"] = (
            self.user_input.instance.fixed_backup_tree_schedule
        )
        schedule_json["7u3-Fixed_Working_Tree_Schedule"] = (
            self.user_input.instance.fixed_working_tree_schedule
        )
        schedule_json["7v-Recovery_Link_Disjoint_From_Working_Per_Scenario"] = (
            self.user_input.instance.protection_link_disjoint
        )
        schedule_json["7w-Reservation_Start_Epoch"] = (
            self.reservation_start_epoch + 1
        )
        schedule_json["7x-Reservation_End_Epoch"] = (
            self.reservation_deadline_epoch + 1
        )
        schedule_json["7y-Prior_Link_Epoch_Occupancy_File"] = (
            self.user_input.instance.prior_link_epoch_occupancy_file
        )
        schedule_json["7z-Prior_Occupied_Link_Epoch_Count"] = sum(
            self.prior_link_epoch_occupancy.values()
        )
        schedule_json["7aa-Multicast_Tree_Pair_Enforced"] = (
            self.user_input.instance.enforce_multicast_tree_pair
        )
        schedule_json["7ab-Failure_Time_Robust_Reservation_Enforced"] = (
            self.enforce_failure_time_robust_reservation
        )
        schedule_json["7ac-Failure_Time_Robustness_Construction"] = (
            "each commodity backup reservation starts no earlier than nominal "
            "working completion plus detection delay minus one; tau=0 covers "
            "the maximal affected set"
            if self.enforce_failure_time_robust_reservation
            else ""
        )
        schedule_json["7r-Robust_Across_Unknown_Failure_Time"] = (
            self.enforce_failure_time_robust_reservation
        )
        schedule_json["11-Protection_Flows"] = []
        schedule_json["11b-Reserved_Backup_Slots"] = [
            f"Chunk {c} from {s} reserves {i}->{j} in epoch {k}"
            for s, i, j, c, k in reservations
        ]
        schedule_json["11c-Reserved_Backup_Slot_Count"] = len(reservations)
        schedule_json["11d-Executed_Recovery_Flow_Count"] = len(recovery_flows)

        working_profile = self._extract_chunk_working_completion_profile()
        schedule_json["9e-Actual_Working_Completion_Epoch"] = max(
            (row["working_completion_epoch"] for row in working_profile),
            default=0,
        )
        schedule_json["9f-Actual_Protected_Completion_Epoch"] = (
            self.find_demand_satisfied_k() + 1
        )

        accounting = build_future_reservation_accounting(
            reservations,
            working_profile,
            self._extract_link_occupancy_epochs(),
            self.epoch_duration,
            self.user_input.topology.chunk_size,
        )
        add_common_resource_fields(schedule_json, accounting)
        executed_recovery = [
            (s, i, j, c, k)
            for _, s, i, j, c, k in recovery_flows
        ]
        return working_flows + executed_recovery, schedule_json
