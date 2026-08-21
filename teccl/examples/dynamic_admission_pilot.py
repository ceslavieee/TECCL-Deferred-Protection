"""Run a small shared-trace DPP/DDPP dynamic-admission pilot."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import time
from typing import Dict, List, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teccl.dynamic_admission import CapacityTimeLedger, ScheduleTemplate  # noqa: E402
from teccl.examples.audit_strict_dedicated_schedule import audit  # noqa: E402


BASE_INPUT = ROOT / "teccl/examples/sample_inputs/mesh2_strict_dedicated_protection.json"
RESULTS = ROOT / "teccl/examples/results/dynamic_admission_pilot"
HORIZON = 12
PHYSICAL_TREE_PAIR_RESULTS = (
    ROOT / "teccl/examples/results/multicast_tree_pair_physical_25gb"
)
FULL_FAILURE_RESULTS = PHYSICAL_TREE_PAIR_RESULTS / "full_directed_failures"
DETECTION_CERTIFICATE = (
    PHYSICAL_TREE_PAIR_RESULTS / "detection_delay_boundary/certificate.json"
)
TOPOLOGY_CONFIGS = {
    "Mesh2": {
        "base_input": BASE_INPUT,
        "horizon": HORIZON,
    },
    "DCN4WAN": {
        "base_input": FULL_FAILURE_RESULTS / "inputs/dcn4wan_dpp.json",
        "horizon": 30,
        "chunk_size_gb": 25.0,
        "epoch_duration_seconds": 2.0,
        "detection_delay_epochs": 1,
        "failure_model": "EXACT",
        "expected_failure_scenarios": 32,
        "detection_certificate": DETECTION_CERTIFICATE,
        "fallback_gurobi_time_limit_hours": 0.01,
        "fallback_wall_clock_limit_seconds": 60.0,
        "residual_failure_time_validation": "theorem_backed_construction",
        "require_tree_pair": True,
        "cached_templates": {
            "early_dpp": FULL_FAILURE_RESULTS / "schedules/dcn4wan_dpp.json",
            "ddpp": FULL_FAILURE_RESULTS / "schedules/dcn4wan_ddpp.json",
        },
    },
    "InterDC8": {
        "base_input": FULL_FAILURE_RESULTS / "inputs/interdc8_dpp.json",
        "horizon": 40,
        "chunk_size_gb": 25.0,
        "epoch_duration_seconds": 2.0,
        "detection_delay_epochs": 1,
        "failure_model": "EXACT",
        "expected_failure_scenarios": 26,
        "detection_certificate": DETECTION_CERTIFICATE,
        "fallback_gurobi_time_limit_hours": 0.01,
        "fallback_wall_clock_limit_seconds": 60.0,
        "residual_failure_time_validation": "theorem_backed_construction",
        "require_tree_pair": True,
        "candidate_tree_pair_library": [
            ROOT
            / "teccl/examples/results/dynamic_admission_physical_locator/"
            "interdc8/rate_0p06/seed_20260810/ddpp/"
            "002_arrival_8_schedule.json",
            ROOT
            / "teccl/examples/results/"
            "dynamic_admission_candidate_tree_first_calibration/"
            "interdc8/rate_0p06/seed_20260811/ddpp/"
            "002_arrival_3_schedule.json",
            ROOT
            / "teccl/examples/results/"
            "dynamic_admission_candidate_tree_first_calibration/"
            "interdc8/rate_0p06/seed_20260811/ddpp/"
            "003_arrival_6_schedule.json",
            ROOT
            / "teccl/examples/results/dynamic_admission_long_trace_pilot/"
            "interdc8/interdc8/rate_0p06/seed_20260811/ddpp/"
            "004_arrival_13_schedule.json",
        ],
        "cached_templates": {
            "early_dpp": FULL_FAILURE_RESULTS / "schedules/interdc8_dpp.json",
            "ddpp": FULL_FAILURE_RESULTS / "schedules/interdc8_ddpp.json",
        },
    },
}
POLICIES = {
    "early_dpp": 2,
    "ddpp": 1,
}


def write_json(data: Dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def implementation_provenance() -> Dict[str, object]:
    files = [
        ROOT / "teccl/dynamic_admission.py",
        ROOT / "teccl/input_data.py",
        ROOT / "teccl/examples/dynamic_admission_pilot.py",
        ROOT / "teccl/examples/dynamic_admission_topology_sweep.py",
        ROOT / "teccl/examples/audit_strict_dedicated_schedule.py",
        ROOT / "teccl/solvers/base_formulation.py",
        ROOT / "teccl/solvers/deferred_protection.py",
        ROOT / "teccl/solvers/strict_dedicated_protection.py",
        ROOT / "teccl/topologies/dcn4wan.py",
        ROOT / "teccl/topologies/interdc8.py",
    ]
    hashes = {
        str(path.relative_to(ROOT)): sha256_file(path)
        for path in files
    }
    canonical = json.dumps(hashes, sort_keys=True, separators=(",", ":"))
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
    )
    return {
        "git_revision": revision,
        "worktree_dirty": dirty,
        "relevant_file_sha256": hashes,
        "implementation_bundle_sha256": hashlib.sha256(
            canonical.encode()
        ).hexdigest(),
    }


def certified_template_provenance(
    topology: str,
    policy: str,
    template_path: Path,
    certificate_path: Path,
    detection_delay_epochs: int,
    expected_failure_scenarios: int,
) -> Dict[str, object]:
    """Bind a cached template to the corrected fixed-plan certificate."""

    schedule = json.loads(template_path.read_text())
    certificate = json.loads(certificate_path.read_text())
    certificate_policy = "DPP" if policy == "early_dpp" else "DDPP"
    matches = [
        row
        for row in certificate.get("boundaries", [])
        if row.get("topology") == topology
        and row.get("policy") == certificate_policy
    ]
    if len(matches) != 1 or not matches[0].get("boundary_certified"):
        raise AssertionError(
            f"No certified detection boundary for {topology} {certificate_policy}"
        )
    boundary = matches[0]
    if Path(str(boundary["fixed_plan"])) != template_path.relative_to(ROOT):
        raise AssertionError("Detection certificate references another fixed plan")
    if detection_delay_epochs > int(boundary["maximum_detection_delay_epochs"]):
        raise AssertionError("Trace detection delay exceeds the template certificate")
    if schedule.get("7j-Failure_Model") != "EXACT":
        raise AssertionError("Cached template does not use EXACT failure exposure")
    if len(schedule.get("9a-Failure_Scenarios", [])) != expected_failure_scenarios:
        raise AssertionError("Cached template does not contain the full scenario set")
    if float(schedule.get("14q-Accounting_Chunk_Size_GB", -1)) != 25.0:
        raise AssertionError("Cached template is not the 25 GB physical baseline")
    if float(schedule.get("14p-Accounting_Epoch_Duration_Seconds", -1)) != 2.0:
        raise AssertionError("Cached template does not use two-second epochs")
    return {
        "template_file": str(template_path.relative_to(ROOT)),
        "template_sha256": sha256_file(template_path),
        "certificate_file": str(certificate_path.relative_to(ROOT)),
        "certificate_sha256": sha256_file(certificate_path),
        "certificate_policy": certificate_policy,
        "maximum_certified_detection_delay_epochs": int(
            boundary["maximum_detection_delay_epochs"]
        ),
        "configured_detection_delay_epochs": detection_delay_epochs,
        "failure_model": "EXACT",
        "failure_scenarios": expected_failure_scenarios,
        "fixed_plan_boundary_certified": True,
    }


def generate_trace(
    seed: int,
    request_count: int,
    arrival_rate: float,
    topology: str = "Mesh2",
    horizon: int = HORIZON,
    chunk_size_gb: float = None,
    epoch_duration_seconds: float = None,
    detection_delay_epochs: int = 1,
) -> Dict:
    rng = random.Random(seed)
    arrivals = [0]
    for _ in range(1, request_count):
        interarrival = max(1, math.ceil(rng.expovariate(arrival_rate)))
        arrivals.append(arrivals[-1] + interarrival)
    body = {
        "generator": "discretized exponential interarrival pilot",
        "seed": seed,
        "request_count": request_count,
        "arrival_rate_per_epoch": arrival_rate,
        "arrival_epochs": arrivals,
        "topology": topology,
        "collective": "AllGather",
        "chunks_per_source": 1,
        "deadline_epochs": horizon,
        "detection_delay_epochs": detection_delay_epochs,
    }
    if chunk_size_gb is not None:
        body["chunk_size_gb"] = chunk_size_gb
    if epoch_duration_seconds is not None:
        body["epoch_duration_seconds"] = epoch_duration_seconds
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    body["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return body


def run_solver(
    input_path: Path,
    wall_clock_limit_seconds: float = None,
) -> Dict[str, object]:
    command = [
        sys.executable,
        "-m",
        "teccl",
        "solve",
        "--input_args",
        str(input_path.relative_to(ROOT)),
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=wall_clock_limit_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "classification": "WALL_CLOCK_TIMEOUT",
            "return_code": None,
            "wall_clock_seconds": time.monotonic() - started,
            "output": str(exc.stdout or ""),
        }
    return {
        "classification": "COMPLETED" if result.returncode == 0 else "PROCESS_ERROR",
        "return_code": result.returncode,
        "wall_clock_seconds": time.monotonic() - started,
        "output": result.stdout,
    }


def certify_residual_schedule(
    candidate_path: Path,
    candidate_input: Dict,
    policy_dir: Path,
    stem: str,
    initial_quality: Dict,
    initial_wall_clock_seconds: float,
    require_tree_pair: bool,
    expected_failure_scenarios: int,
    wall_clock_limit_seconds: float,
) -> Dict[str, object]:
    """Fix a residual plan and certify every affected failure epoch."""

    candidate = json.loads(candidate_path.read_text())
    completion = int(candidate["9e-Actual_Working_Completion_Epoch"])
    seed_failure_epoch = int(
        candidate_input["InstanceParams"]["failure_time_epoch"]
    )
    if not 0 <= seed_failure_epoch < completion:
        raise AssertionError("Candidate seed failure epoch is outside working time")

    rows: List[Dict[str, object]] = [
        {
            "failure_epoch_zero_based": seed_failure_epoch,
            "classification": "FEASIBLE",
            "solver_status": initial_quality["status_name"],
            "solver_runtime_seconds": initial_quality["runtime"],
            "solver_wall_clock_seconds": initial_wall_clock_seconds,
            "schedule_file": str(candidate_path.relative_to(ROOT)),
            "reused_admission_solve": True,
        }
    ]
    fixed_working = set(candidate.get("10-Working_Flows", []))
    fixed_reservations = set(candidate.get("11b-Reserved_Backup_Slots", []))

    validation_epochs = [
        epoch for epoch in range(completion) if epoch != seed_failure_epoch
    ]
    for failure_epoch in validation_epochs:
        validation_stem = f"{stem}_tau_{failure_epoch}"
        input_path = policy_dir / "failure_time_certification" / (
            validation_stem + "_input.json"
        )
        schedule_path = policy_dir / "failure_time_certification" / (
            validation_stem + "_schedule.json"
        )
        result_path = policy_dir / "failure_time_certification" / (
            validation_stem + "_solver_result.json"
        )
        payload = json.loads(json.dumps(candidate_input))
        payload["InstanceParams"].update(
            {
                "failure_time_epoch": failure_epoch,
                "failure_model": 2,
                "max_failure_scenarios": -1,
                "fixed_working_schedule": str(candidate_path.relative_to(ROOT)),
                "fixed_working_tree_schedule": "",
                "fixed_reservation_schedule": str(candidate_path.relative_to(ROOT)),
                "minimum_reservation_schedule": "",
                "fixed_backup_tree_schedule": "",
                "schedule_output_file": str(schedule_path.relative_to(ROOT)),
                "solver_result_output_file": str(result_path.relative_to(ROOT)),
            }
        )
        write_json(payload, input_path)
        for stale in (schedule_path, result_path):
            if stale.exists():
                stale.unlink()

        solver_run = run_solver(input_path, wall_clock_limit_seconds)
        row: Dict[str, object] = {
            "failure_epoch_zero_based": failure_epoch,
            "solver_wall_clock_seconds": solver_run["wall_clock_seconds"],
            "input_file": str(input_path.relative_to(ROOT)),
            "reused_admission_solve": False,
        }
        if solver_run["classification"] != "COMPLETED" or not result_path.exists():
            row.update(
                {
                    "classification": "UNKNOWN",
                    "solver_status": solver_run["classification"],
                    "solver_runtime_seconds": None,
                    "schedule_file": "",
                }
            )
            rows.append(row)
            break

        result = json.loads(result_path.read_text())
        quality = result["solver_quality"]
        row["solver_status"] = quality["status_name"]
        row["solver_runtime_seconds"] = quality["runtime"]
        if not result["produced_schedule"] or not schedule_path.exists():
            row["classification"] = (
                "INFEASIBLE"
                if quality["status_name"] in {"INFEASIBLE", "INF_OR_UNBD"}
                else "UNKNOWN"
            )
            row["schedule_file"] = ""
            rows.append(row)
            break

        schedule = json.loads(schedule_path.read_text())
        affected = any(
            summary.get("affected_demands")
            for summary in schedule.get("9b-Failure_Scenario_Summary", {}).values()
        )
        checks = audit(
            schedule,
            require_nonempty=affected,
            require_tree_pair=require_tree_pair,
        )
        failures = [check["name"] for check in checks if not check["passed"]]
        if len(schedule.get("9a-Failure_Scenarios", [])) != expected_failure_scenarios:
            failures.append("incomplete failure-scenario set")
        if set(schedule.get("10-Working_Flows", [])) != fixed_working:
            failures.append("working schedule changed during certification")
        if set(schedule.get("11b-Reserved_Backup_Slots", [])) != fixed_reservations:
            failures.append("reservation schedule changed during certification")
        row.update(
            {
                "classification": "FEASIBLE" if not failures else "INVALID",
                "audit_failures": failures,
                "schedule_file": str(schedule_path.relative_to(ROOT)),
            }
        )
        rows.append(row)
        if failures:
            break

    certified = (
        len(rows) == completion
        and all(row["classification"] == "FEASIBLE" for row in rows)
    )
    rows.sort(key=lambda row: int(row["failure_epoch_zero_based"]))
    report = {
        "certificate": "residual fixed-plan exact failure-time coverage",
        "certified": certified,
        "failure_model": "EXACT",
        "detection_delay_epochs": int(
            candidate_input["InstanceParams"]["detection_delay_epochs"]
        ),
        "expected_failure_epochs": list(range(completion)),
        "fixed_working_and_reservation_plan": str(candidate_path.relative_to(ROOT)),
        "trials": rows,
    }
    report_path = (
        policy_dir / "failure_time_certification" / f"{stem}_certificate.json"
    )
    write_json(report, report_path)
    report["certificate_file"] = str(report_path.relative_to(ROOT))
    report["certificate_sha256"] = sha256_file(report_path)
    return report


def certify_residual_schedule_by_construction(
    candidate_path: Path,
    candidate_input: Dict,
    policy_dir: Path,
    stem: str,
    require_tree_pair: bool,
    expected_failure_scenarios: int,
) -> Dict[str, object]:
    """Certify all failure times using the enforced Lemma-6 construction."""

    schedule = json.loads(candidate_path.read_text())
    checks = audit(
        schedule,
        require_nonempty=True,
        require_tree_pair=require_tree_pair,
    )
    failed_checks = [str(check["name"]) for check in checks if not check["passed"]]
    completion = int(schedule.get("9e-Actual_Working_Completion_Epoch", 0))
    scenario_count = len(schedule.get("9a-Failure_Scenarios", []))
    construction_claimed = (
        schedule.get("7ab-Failure_Time_Robust_Reservation_Enforced") is True
        and schedule.get("7r-Robust_Across_Unknown_Failure_Time") is True
    )
    input_tau = int(candidate_input["InstanceParams"]["failure_time_epoch"])
    certified = (
        not failed_checks
        and completion > 0
        and scenario_count == expected_failure_scenarios
        and schedule.get("7j-Failure_Model") == "EXACT"
        and construction_claimed
        and input_tau == 0
    )
    report = {
        "certificate": "theorem-backed common failure-time construction",
        "certified": certified,
        "outer_failure_time_milps_executed": False,
        "proof_reference": (
            "THEORY_FOUNDATION_AUDIT.md Lemma 6: every commodity reservation "
            "starts no earlier than nominal working completion plus detection "
            "delay minus one; the tau=0 affected set is a superset of every "
            "later affected set on the unique working tree"
        ),
        "failure_model": schedule.get("7j-Failure_Model"),
        "failure_scenarios": scenario_count,
        "expected_failure_scenarios": expected_failure_scenarios,
        "detection_delay_epochs": int(
            schedule.get("7f-Detection_Delay_Epochs", -1)
        ),
        "working_completion_epoch": completion,
        "covered_failure_epochs": list(range(completion)),
        "construction_claimed": construction_claimed,
        "seed_failure_epoch_zero_based": input_tau,
        "fixed_plan": str(candidate_path.relative_to(ROOT)),
        "fixed_plan_sha256": sha256_file(candidate_path),
        "independent_audit_passed": not failed_checks,
        "independent_audit_failures": failed_checks,
        "independent_audit_checks": [
            {"name": str(check["name"]), "passed": bool(check["passed"])}
            for check in checks
        ],
        "scope_boundary": (
            "One directed-link failure at a time under the stated event "
            "convention; excludes node, correlated, bidirectional-fiber, and "
            "multiple simultaneous failures."
        ),
    }
    report_path = policy_dir / "construction_certification" / (
        f"{stem}_certificate.json"
    )
    write_json(report, report_path)
    report["certificate_file"] = str(report_path.relative_to(ROOT))
    report["certificate_sha256"] = sha256_file(report_path)
    return report


def resolve_residual_plan_across_failure_times(
    initial_candidate_path: Path,
    seed_input: Dict,
    policy_dir: Path,
    stem: str,
    initial_quality: Dict,
    initial_wall_clock_seconds: float,
    require_tree_pair: bool,
    expected_failure_scenarios: int,
    wall_clock_limit_seconds: float,
) -> Dict[str, object]:
    """Try deterministic failure-epoch seeds until one fixed plan certifies."""

    current_path = initial_candidate_path
    current_input = json.loads(json.dumps(seed_input))
    current_quality = initial_quality
    current_wall_clock = initial_wall_clock_seconds
    attempted_epochs = set()
    attempts: List[Dict[str, object]] = []

    while True:
        seed_epoch = int(current_input["InstanceParams"]["failure_time_epoch"])
        attempted_epochs.add(seed_epoch)
        certificate = certify_residual_schedule(
            current_path,
            current_input,
            policy_dir,
            f"{stem}_seed_tau_{seed_epoch}",
            current_quality,
            current_wall_clock,
            require_tree_pair,
            expected_failure_scenarios,
            wall_clock_limit_seconds,
        )
        attempts.append(
            {
                "seed_failure_epoch_zero_based": seed_epoch,
                "candidate_schedule_file": str(current_path.relative_to(ROOT)),
                "certificate_file": certificate["certificate_file"],
                "certificate_sha256": certificate["certificate_sha256"],
                "certified": certificate["certified"],
            }
        )
        if certificate["certified"]:
            return {
                "classification": "CERTIFIED",
                "candidate_schedule_file": str(current_path.relative_to(ROOT)),
                "certificate": certificate,
                "attempts": attempts,
            }

        failed_trials = [
            row
            for row in certificate["trials"]
            if row["classification"] != "FEASIBLE"
        ]
        if any(
            row["classification"] in {"UNKNOWN", "INVALID"}
            for row in failed_trials
        ):
            return {
                "classification": "UNKNOWN",
                "reason": "fixed-plan certification was inconclusive or invalid",
                "certificate": certificate,
                "attempts": attempts,
            }

        completion = len(certificate["expected_failure_epochs"])
        preferred = [
            int(row["failure_epoch_zero_based"])
            for row in failed_trials
            if row["classification"] == "INFEASIBLE"
            and int(row["failure_epoch_zero_based"]) not in attempted_epochs
        ]
        remaining = [
            epoch for epoch in range(completion) if epoch not in attempted_epochs
        ]
        if not preferred and not remaining:
            return {
                "classification": "UNKNOWN",
                "reason": (
                    "all deterministic failure-epoch candidate seeds were "
                    "exhausted without finding one common fixed plan"
                ),
                "certificate": certificate,
                "attempts": attempts,
            }
        next_epoch = (preferred or remaining)[0]
        retry_stem = f"{stem}_candidate_tau_{next_epoch}"
        input_path = policy_dir / "robust_candidate_search" / (
            retry_stem + "_input.json"
        )
        schedule_path = policy_dir / "robust_candidate_search" / (
            retry_stem + "_schedule.json"
        )
        result_path = policy_dir / "robust_candidate_search" / (
            retry_stem + "_solver_result.json"
        )
        retry_input = json.loads(json.dumps(seed_input))
        retry_input["InstanceParams"].update(
            {
                "failure_time_epoch": next_epoch,
                "fixed_working_schedule": "",
                "fixed_reservation_schedule": "",
                "minimum_reservation_schedule": "",
                "fixed_backup_tree_schedule": "",
                "schedule_output_file": str(schedule_path.relative_to(ROOT)),
                "solver_result_output_file": str(result_path.relative_to(ROOT)),
            }
        )
        write_json(retry_input, input_path)
        for stale in (schedule_path, result_path):
            if stale.exists():
                stale.unlink()
        solver_run = run_solver(input_path, wall_clock_limit_seconds)
        if solver_run["classification"] != "COMPLETED" or not result_path.exists():
            return {
                "classification": "UNKNOWN",
                "reason": solver_run["classification"],
                "certificate": certificate,
                "attempts": attempts,
            }
        result = json.loads(result_path.read_text())
        quality = result["solver_quality"]
        if not result["produced_schedule"] or not schedule_path.exists():
            if quality["status_name"] in {"INFEASIBLE", "INF_OR_UNBD"}:
                return {
                    "classification": "BLOCKED",
                    "reason": (
                        "an exact single-failure-time subproblem required by "
                        "any common robust plan is infeasible"
                    ),
                    "blocking_witness_failure_epoch_zero_based": next_epoch,
                    "blocking_witness_solver_status": quality["status_name"],
                    "certificate": certificate,
                    "attempts": attempts,
                }
            return {
                "classification": "UNKNOWN",
                "reason": quality["status_name"],
                "certificate": certificate,
                "attempts": attempts,
            }

        schedule = json.loads(schedule_path.read_text())
        checks = audit(
            schedule,
            require_nonempty=True,
            require_tree_pair=require_tree_pair,
        )
        if not all(check["passed"] for check in checks):
            return {
                "classification": "UNKNOWN",
                "reason": "retry candidate failed the independent audit",
                "certificate": certificate,
                "attempts": attempts,
            }
        if len(schedule.get("9a-Failure_Scenarios", [])) != expected_failure_scenarios:
            return {
                "classification": "UNKNOWN",
                "reason": "retry candidate has an incomplete scenario set",
                "certificate": certificate,
                "attempts": attempts,
            }
        current_path = schedule_path
        current_input = retry_input
        current_quality = quality
        current_wall_clock = float(solver_run["wall_clock_seconds"])


def solve_fixed_tree_pair_candidate(
    base: Dict,
    residual_occupancy_path: Path,
    template_path: Path,
    candidate_dir: Path,
    stem: str,
    timing_mode: int,
    time_limit_hours: float,
    wall_clock_limit_seconds: float,
    expected_failure_scenarios: int,
) -> Dict[str, object]:
    """Re-time one certified tree pair before unrestricted joint search."""

    candidate_input_path = candidate_dir / f"{stem}_input.json"
    schedule_path = candidate_dir / f"{stem}_schedule.json"
    result_path = candidate_dir / f"{stem}_solver_result.json"
    payload = json.loads(json.dumps(base))
    if time_limit_hours is not None:
        payload["GurobiParams"]["time_limit"] = time_limit_hours
    payload["InstanceParams"].update(
        {
            "deferred_timing_mode": timing_mode,
            "fixed_working_schedule": "",
            "fixed_working_tree_schedule": str(template_path.relative_to(ROOT)),
            "fixed_reservation_schedule": "",
            "minimum_reservation_schedule": "",
            "fixed_backup_tree_schedule": str(template_path.relative_to(ROOT)),
            "enforce_failure_time_robust_reservation": True,
            "prior_link_epoch_occupancy_file": str(
                residual_occupancy_path.relative_to(ROOT)
            ),
            "schedule_output_file": str(schedule_path.relative_to(ROOT)),
            "solver_result_output_file": str(result_path.relative_to(ROOT)),
        }
    )
    write_json(payload, candidate_input_path)
    for stale in (schedule_path, result_path):
        if stale.exists():
            stale.unlink()

    solver_run = run_solver(candidate_input_path, wall_clock_limit_seconds)
    attempt: Dict[str, object] = {
        "method": "certified_fixed_tree_pair_timing_candidate",
        "classification": "UNKNOWN",
        "global_infeasibility_claimed": False,
        "input_file": str(candidate_input_path.relative_to(ROOT)),
        "tree_pair_template": str(template_path.relative_to(ROOT)),
        "process_classification": solver_run["classification"],
        "solver_wall_clock_seconds": solver_run["wall_clock_seconds"],
    }
    if solver_run["classification"] != "COMPLETED" or not result_path.exists():
        return attempt
    solver_result = json.loads(result_path.read_text())
    quality = solver_result["solver_quality"]
    attempt["solver_quality"] = quality
    if not solver_result["produced_schedule"]:
        if quality["status_name"] == "INFEASIBLE":
            attempt["classification"] = "TREE_PAIR_INFEASIBLE"
        return attempt

    schedule = json.loads(schedule_path.read_text())
    checks = audit(schedule, require_nonempty=True, require_tree_pair=True)
    attempt["independent_audit_checks"] = checks
    if not all(check["passed"] for check in checks):
        attempt["classification"] = "INVALID"
        return attempt
    if len(schedule.get("9a-Failure_Scenarios", [])) != expected_failure_scenarios:
        attempt["classification"] = "INVALID"
        attempt["reason"] = "incomplete failure-scenario set"
        return attempt
    certificate = certify_residual_schedule_by_construction(
        schedule_path,
        payload,
        candidate_dir,
        stem,
        True,
        expected_failure_scenarios,
    )
    attempt["construction_certificate"] = certificate
    if not certificate["certified"]:
        attempt["classification"] = "INVALID"
        return attempt
    attempt.update(
        {
            "classification": "CERTIFIED_FEASIBLE",
            "schedule_file": str(schedule_path.relative_to(ROOT)),
            "failure_time_certificate_file": certificate["certificate_file"],
            "failure_time_certificate_sha256": certificate[
                "certificate_sha256"
            ],
        }
    )
    return attempt


def run_policy(
    policy: str,
    timing_mode: int,
    trace: Dict,
    results_dir: Path = RESULTS,
    base_input: Path = BASE_INPUT,
    horizon: int = HORIZON,
    time_limit_hours: float = None,
    cached_template_path: Path = None,
    require_tree_pair: bool = False,
    topology_config: Dict = None,
    wall_clock_limit_seconds: float = None,
    no_rel_heur_work: float = 0.0,
    candidate_tree_pair_first: bool = False,
) -> Dict:
    policy_dir = results_dir / policy
    policy_dir.mkdir(parents=True, exist_ok=True)
    ledger = CapacityTimeLedger()
    request_rows: List[Dict] = []
    base = json.loads(base_input.read_text())
    base["GurobiParams"]["output_flag"] = 0
    if time_limit_hours is not None:
        base["GurobiParams"]["time_limit"] = time_limit_hours
    base["GurobiParams"]["no_rel_heur_work"] = float(no_rel_heur_work)
    cached_template = None
    template_provenance = None
    candidate_library_paths: List[Path] = []
    if cached_template_path is not None:
        cached_payload = json.loads(cached_template_path.read_text())
        cached_checks = audit(
            cached_payload,
            require_nonempty=True,
            require_tree_pair=require_tree_pair,
        )
        if not all(check["passed"] for check in cached_checks):
            raise AssertionError(f"Cached template audit failed: {cached_template_path}")
        cached_template = ScheduleTemplate.from_path(
            cached_template_path,
            f"{policy}-cached-certified-template",
        )
        if topology_config is None:
            raise AssertionError("Certified cached templates require topology metadata")
        template_provenance = certified_template_provenance(
            str(trace["topology"]),
            policy,
            cached_template_path,
            Path(topology_config["detection_certificate"]),
            int(trace["detection_delay_epochs"]),
            int(topology_config["expected_failure_scenarios"]),
        )
        template_provenance["base_input_file"] = str(base_input.relative_to(ROOT))
        template_provenance["base_input_sha256"] = sha256_file(base_input)
    if candidate_tree_pair_first:
        if topology_config is None:
            raise AssertionError("Fixed-tree candidate search lacks topology metadata")
        for raw_path in topology_config.get("candidate_tree_pair_library", []):
            candidate_path = Path(raw_path)
            if not candidate_path.is_absolute():
                candidate_path = ROOT / candidate_path
            candidate_checks = audit(
                json.loads(candidate_path.read_text()),
                require_nonempty=True,
                require_tree_pair=True,
            )
            if not all(check["passed"] for check in candidate_checks):
                raise AssertionError(
                    f"Candidate tree-pair library audit failed: {candidate_path}"
                )
            candidate_library_paths.append(candidate_path)

    for request_index, arrival in enumerate(trace["arrival_epochs"]):
        request_id = f"request-{request_index}"
        stem = f"{request_index:03d}_arrival_{arrival}"
        occupancy_path = policy_dir / f"{stem}_occupancy.json"
        input_path = policy_dir / f"{stem}_input.json"
        schedule_path = policy_dir / f"{stem}_schedule.json"
        result_path = policy_dir / f"{stem}_solver_result.json"

        residual = ledger.residual_occupancy_payload(arrival, horizon)
        occupied_before = ledger.occupied_cell_count
        if cached_template is not None:
            cached_decision = ledger.try_admit(
                request_id,
                arrival,
                cached_template,
            )
            if cached_decision.accepted:
                request_rows.append(
                    {
                        "request_id": request_id,
                        "arrival_epoch": arrival,
                        "trace_sha256": trace["sha256"],
                        "solver_status": "CACHED_CERTIFIED_TEMPLATE",
                        "solver_solution_count": 1,
                        "solver_runtime_seconds": 0.0,
                        "solver_mip_gap": None,
                        "prior_occupied_local_cells": len(residual["epochs"]),
                        "outcome": "accepted",
                        "independent_audit_passed": True,
                        "failure_time_certified": True,
                        "failure_time_certificate_file": template_provenance[
                            "certificate_file"
                        ],
                        "failure_time_certificate_sha256": template_provenance[
                            "certificate_sha256"
                        ],
                        "released_protection_transfers": (
                            cached_decision.released_protection_transfers
                        ),
                        "schedule_file": str(cached_template_path.relative_to(ROOT)),
                        "admission_path": "cached_template",
                    }
                )
                continue
            if ledger.occupied_cell_count != occupied_before:
                raise AssertionError("Rejected cached template mutated the ledger")

        write_json(residual, occupancy_path)
        tree_pair_candidate_attempts: List[Dict[str, object]] = []
        candidate_admitted = False
        if candidate_tree_pair_first and cached_template_path is not None:
            candidate_paths = [cached_template_path]
            candidate_paths.extend(
                path
                for path in candidate_library_paths
                if path != cached_template_path
            )
            for candidate_index, candidate_path in enumerate(candidate_paths):
                tree_pair_candidate = solve_fixed_tree_pair_candidate(
                    base,
                    occupancy_path,
                    candidate_path,
                    policy_dir / "tree_pair_candidate" / str(candidate_index),
                    stem,
                    timing_mode,
                    time_limit_hours,
                    wall_clock_limit_seconds,
                    int(topology_config["expected_failure_scenarios"]),
                )
                tree_pair_candidate_attempts.append(tree_pair_candidate)
                if tree_pair_candidate["classification"] != "CERTIFIED_FEASIBLE":
                    continue
                admitted_schedule_path = ROOT / Path(
                    str(tree_pair_candidate["schedule_file"])
                )
                template = ScheduleTemplate.from_path(
                    admitted_schedule_path,
                    f"{policy}-{request_id}-fixed-tree-candidate",
                )
                decision = ledger.try_admit(request_id, arrival, template)
                if not decision.accepted:
                    raise AssertionError(
                        "Certified fixed-tree candidate conflicts with ledger"
                    )
                quality = tree_pair_candidate["solver_quality"]
                request_rows.append(
                    {
                        "request_id": request_id,
                        "arrival_epoch": arrival,
                        "trace_sha256": trace["sha256"],
                        "solver_status": quality["status_name"],
                        "solver_solution_count": quality["solution_count"],
                        "solver_runtime_seconds": quality["runtime"],
                        "solver_wall_clock_seconds": tree_pair_candidate[
                            "solver_wall_clock_seconds"
                        ],
                        "solver_mip_gap": quality["mip_gap"],
                        "prior_occupied_local_cells": len(residual["epochs"]),
                        "outcome": "accepted",
                        "independent_audit_passed": True,
                        "failure_time_certified": True,
                        "failure_time_certificate_file": tree_pair_candidate[
                            "failure_time_certificate_file"
                        ],
                        "failure_time_certificate_sha256": tree_pair_candidate[
                            "failure_time_certificate_sha256"
                        ],
                        "released_protection_transfers": (
                            decision.released_protection_transfers
                        ),
                        "schedule_file": str(
                            admitted_schedule_path.relative_to(ROOT)
                        ),
                        "admission_path": "certified_tree_pair_timing_candidate",
                        "tree_pair_candidate_attempts": (
                            tree_pair_candidate_attempts
                        ),
                    }
                )
                candidate_admitted = True
                break
        if candidate_admitted:
            continue

        payload = json.loads(json.dumps(base))
        payload["InstanceParams"].update(
            {
                "deferred_timing_mode": timing_mode,
                "fixed_working_schedule": "",
                "fixed_working_tree_schedule": "",
                "fixed_reservation_schedule": "",
                "minimum_reservation_schedule": "",
                "fixed_backup_tree_schedule": "",
                "enforce_failure_time_robust_reservation": True,
                "prior_link_epoch_occupancy_file": str(
                    occupancy_path.relative_to(ROOT)
                ),
            }
        )
        payload["InstanceParams"]["schedule_output_file"] = str(
            schedule_path.relative_to(ROOT)
        )
        payload["InstanceParams"]["solver_result_output_file"] = str(
            result_path.relative_to(ROOT)
        )
        write_json(payload, input_path)
        for stale in (schedule_path, result_path):
            if stale.exists():
                stale.unlink()

        solver_run = run_solver(input_path, wall_clock_limit_seconds)
        if solver_run["classification"] != "COMPLETED" or not result_path.exists():
            request_rows.append(
                {
                    "request_id": request_id,
                    "arrival_epoch": arrival,
                    "trace_sha256": trace["sha256"],
                    "solver_status": solver_run["classification"],
                    "solver_solution_count": 0,
                    "solver_runtime_seconds": None,
                    "solver_wall_clock_seconds": solver_run["wall_clock_seconds"],
                    "solver_mip_gap": None,
                    "prior_occupied_local_cells": len(residual["epochs"]),
                    "outcome": "unknown",
                    "process_return_code": solver_run["return_code"],
                    "process_output_tail": str(solver_run["output"])[-2000:],
                    "admission_path": "residual_capacity_solver",
                }
            )
            if ledger.occupied_cell_count != occupied_before:
                raise AssertionError("Inconclusive request mutated the ledger")
            continue
        solver_result = json.loads(result_path.read_text())
        quality = solver_result["solver_quality"]
        row = {
            "request_id": request_id,
            "arrival_epoch": arrival,
            "trace_sha256": trace["sha256"],
            "solver_status": quality["status_name"],
            "solver_solution_count": quality["solution_count"],
            "solver_runtime_seconds": quality["runtime"],
            "solver_wall_clock_seconds": solver_run["wall_clock_seconds"],
            "solver_mip_gap": quality["mip_gap"],
            "prior_occupied_local_cells": len(residual["epochs"]),
            "tree_pair_candidate_attempts": tree_pair_candidate_attempts,
        }

        if solver_result["produced_schedule"]:
            schedule_payload = json.loads(schedule_path.read_text())
            checks = audit(
                schedule_payload,
                require_nonempty=True,
                require_tree_pair=require_tree_pair,
            )
            if not all(check["passed"] for check in checks):
                raise AssertionError(f"Protection audit failed for {policy} {request_id}")
            if topology_config is not None and len(
                schedule_payload.get("9a-Failure_Scenarios", [])
            ) != int(topology_config["expected_failure_scenarios"]):
                raise AssertionError("Residual schedule has an incomplete scenario set")

            failure_time_certificate = None
            admitted_schedule_path = schedule_path
            if require_tree_pair:
                if topology_config is None:
                    raise AssertionError(
                        "Strict main-topology fallback lacks certification metadata"
                    )
                validation_mode = topology_config.get(
                    "residual_failure_time_validation"
                )
                if validation_mode == "theorem_backed_construction":
                    certificate = certify_residual_schedule_by_construction(
                        schedule_path,
                        payload,
                        policy_dir,
                        stem,
                        require_tree_pair,
                        int(topology_config["expected_failure_scenarios"]),
                    )
                    resolution = {
                        "classification": (
                            "CERTIFIED" if certificate["certified"] else "UNKNOWN"
                        ),
                        "candidate_schedule_file": str(
                            schedule_path.relative_to(ROOT)
                        ),
                        "certificate": certificate,
                        "attempts": [
                            {
                                "method": "theorem_backed_construction",
                                "seed_failure_epoch_zero_based": 0,
                                "candidate_schedule_file": str(
                                    schedule_path.relative_to(ROOT)
                                ),
                                "certificate_file": certificate[
                                    "certificate_file"
                                ],
                                "certificate_sha256": certificate[
                                    "certificate_sha256"
                                ],
                                "certified": certificate["certified"],
                            }
                        ],
                        "reason": (
                            None
                            if certificate["certified"]
                            else "theorem-backed construction audit failed"
                        ),
                    }
                elif validation_mode == "outer_milp":
                    resolution = resolve_residual_plan_across_failure_times(
                        schedule_path,
                        payload,
                        policy_dir,
                        stem,
                        quality,
                        float(solver_run["wall_clock_seconds"]),
                        require_tree_pair,
                        int(topology_config["expected_failure_scenarios"]),
                        wall_clock_limit_seconds,
                    )
                else:
                    raise AssertionError(
                        "Strict main-topology fallback has no recognized "
                        "failure-time validation mode"
                    )
                row["robust_candidate_attempts"] = resolution["attempts"]
                if resolution["classification"] != "CERTIFIED":
                    failure_time_certificate = resolution["certificate"]
                    row.update(
                        {
                            "outcome": (
                                "blocked"
                                if resolution["classification"] == "BLOCKED"
                                else "unknown"
                            ),
                            "independent_audit_passed": True,
                            "failure_time_certified": False,
                            "failure_time_certificate_file": (
                                failure_time_certificate["certificate_file"]
                            ),
                            "failure_time_certificate_sha256": (
                                failure_time_certificate["certificate_sha256"]
                            ),
                            "schedule_file": str(schedule_path.relative_to(ROOT)),
                            "admission_path": "residual_capacity_solver",
                            "robust_resolution_reason": resolution.get("reason"),
                            "blocking_witness_failure_epoch_zero_based": (
                                resolution.get(
                                    "blocking_witness_failure_epoch_zero_based"
                                )
                            ),
                        }
                    )
                    if ledger.occupied_cell_count != occupied_before:
                        raise AssertionError(
                            "Uncertified residual request mutated the ledger"
                        )
                    request_rows.append(row)
                    continue
                failure_time_certificate = resolution["certificate"]
                admitted_schedule_path = ROOT / Path(
                    str(resolution["candidate_schedule_file"])
                )

            template = ScheduleTemplate.from_path(
                admitted_schedule_path,
                f"{policy}-{request_id}",
            )
            decision = ledger.try_admit(request_id, arrival, template)
            if not decision.accepted:
                raise AssertionError(
                    f"Solver schedule conflicts with ledger for {policy} {request_id}"
                )
            row.update(
                {
                    "outcome": "accepted",
                    "independent_audit_passed": True,
                    "failure_time_certified": (
                        failure_time_certificate is not None
                        and failure_time_certificate["certified"]
                    ),
                    "failure_time_certificate_file": (
                        failure_time_certificate["certificate_file"]
                        if failure_time_certificate is not None
                        else None
                    ),
                    "failure_time_certificate_sha256": (
                        failure_time_certificate["certificate_sha256"]
                        if failure_time_certificate is not None
                        else None
                    ),
                    "released_protection_transfers": (
                        decision.released_protection_transfers
                    ),
                    "schedule_file": str(admitted_schedule_path.relative_to(ROOT)),
                    "admission_path": "residual_capacity_solver",
                }
            )
        elif quality["status_name"] == "INFEASIBLE":
            row["outcome"] = "blocked"
            row["admission_path"] = "residual_capacity_solver"
        else:
            row["outcome"] = "unknown"
            row["admission_path"] = "residual_capacity_solver"

        if row["outcome"] != "accepted" and ledger.occupied_cell_count != occupied_before:
            raise AssertionError("Rejected request mutated the capacity ledger")
        request_rows.append(row)

    ledger.assert_safe()
    accepted = sum(row["outcome"] == "accepted" for row in request_rows)
    blocked = sum(row["outcome"] == "blocked" for row in request_rows)
    unknown = sum(row["outcome"] == "unknown" for row in request_rows)
    if accepted + blocked + unknown != len(request_rows):
        raise AssertionError("Admission accounting identity failed")
    conclusive = accepted + blocked
    return {
        "policy": policy,
        "trace_sha256": trace["sha256"],
        "requests": len(request_rows),
        "accepted": accepted,
        "blocked": blocked,
        "unknown": unknown,
        "blocking_probability_conclusive": blocked / conclusive if conclusive else None,
        "unknown_rate": unknown / len(request_rows),
        "final_ledger_occupied_cells": ledger.occupied_cell_count,
        "ledger_safe": True,
        "baseline_structure": (
            "fixed rooted multicast working/backup tree pair"
            if require_tree_pair
            else "scenario-robust reserved-capacity structure"
        ),
        "admission_algorithm": (
            "certified-template, frozen certified tree-pair timing candidates, "
            "then unrestricted residual-capacity solver"
            if candidate_tree_pair_first
            else (
                "certified-template-first with residual-capacity solver fallback"
                if cached_template is not None
                else "residual-capacity solver per arrival"
            )
        ),
        "fallback_optimization": (
            "fixed certified tree-pair timing candidates followed, when "
            "needed, by joint strict working-tree and backup-tree optimization "
            "against request-local residual capacity; DPP/DDPP differ only in "
            "the reservation timing mode"
            if candidate_tree_pair_first
            else (
                "joint strict working-tree and backup-tree optimization against "
                "request-local residual capacity; DPP/DDPP differ only in the "
                "reservation timing mode"
            )
        ),
        "fallback_wall_clock_limit_seconds": wall_clock_limit_seconds,
        "fallback_no_rel_heur_work": float(no_rel_heur_work),
        "candidate_tree_pair_first": bool(candidate_tree_pair_first),
        "candidate_tree_pair_library": (
            [
                {
                    "file": str(path.relative_to(ROOT)),
                    "sha256": sha256_file(path),
                }
                for path in candidate_library_paths
            ]
            if topology_config is not None
            else []
        ),
        "residual_failure_time_validation": (
            topology_config.get("residual_failure_time_validation")
            if topology_config is not None
            else None
        ),
        "source_provenance": template_provenance,
        "request_results": request_rows,
    }


def main(argv: Sequence[str] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260807)
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument("--arrival-rate", type=float, default=0.8)
    parser.add_argument("--topology", choices=sorted(TOPOLOGY_CONFIGS), default="Mesh2")
    parser.add_argument("--time-limit-hours", type=float)
    parser.add_argument("--wall-clock-limit-seconds", type=float)
    parser.add_argument("--no-rel-heur-work", type=float, default=0.0)
    parser.add_argument("--candidate-tree-pair-first", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if (
        args.requests <= 0
        or args.arrival_rate <= 0
        or args.no_rel_heur_work < 0
    ):
        raise SystemExit(
            "requests and arrival-rate must be positive; "
            "no-rel-heur-work must be nonnegative"
        )

    config = TOPOLOGY_CONFIGS[args.topology]
    effective_time_limit_hours = (
        args.time_limit_hours
        if args.time_limit_hours is not None
        else config.get("fallback_gurobi_time_limit_hours")
    )
    effective_wall_clock_limit_seconds = (
        args.wall_clock_limit_seconds
        if args.wall_clock_limit_seconds is not None
        else config.get("fallback_wall_clock_limit_seconds")
    )
    results_dir = args.output_dir or (
        RESULTS if args.topology == "Mesh2" else RESULTS / args.topology.lower()
    )
    if not results_dir.is_absolute():
        results_dir = ROOT / results_dir
    trace = generate_trace(
        args.seed,
        args.requests,
        args.arrival_rate,
        args.topology,
        config["horizon"],
        config.get("chunk_size_gb"),
        config.get("epoch_duration_seconds"),
        config.get("detection_delay_epochs", 1),
    )
    write_json(trace, results_dir / "shared_trace.json")
    reports = [
        run_policy(
            policy,
            timing_mode,
            trace,
            results_dir,
            config["base_input"],
            config["horizon"],
            effective_time_limit_hours,
            config.get("cached_templates", {}).get(policy),
            config.get("require_tree_pair", False),
            config,
            effective_wall_clock_limit_seconds,
            args.no_rel_heur_work,
            args.candidate_tree_pair_first,
        )
        for policy, timing_mode in POLICIES.items()
    ]
    if len({report["trace_sha256"] for report in reports}) != 1:
        raise AssertionError("Policies did not consume the same trace")
    report = {
        "authoritative_performance_result": False,
        "purpose": f"Stage-C {args.topology} shared-trace pipeline pilot",
        "fallback_time_limit_hours": effective_time_limit_hours,
        "fallback_wall_clock_limit_seconds": effective_wall_clock_limit_seconds,
        "fallback_no_rel_heur_work": args.no_rel_heur_work,
        "candidate_tree_pair_first": args.candidate_tree_pair_first,
        "trace": trace,
        "implementation_provenance": implementation_provenance(),
        "policies": reports,
    }
    write_json(report, results_dir / "report.json")
    (results_dir / "README.md").write_text(
        "# Dynamic admission shared-trace pilot\n\n"
        f"This Stage-C {args.topology} pilot validates identical-trace execution, "
        "residual-capacity re-optimization, explicit accepted/blocked/unknown "
        "classification, independent protection audits, and transactional "
        "ledger safety for early DPP and DDPP. It is not an authoritative "
        "performance result; final claims require a load sweep, independent "
        "seeds, confidence intervals, and the declared thesis topologies.\n"
    )
    for policy_report in reports:
        print(
            f"{policy_report['policy']}: accepted={policy_report['accepted']} "
            f"blocked={policy_report['blocked']} unknown={policy_report['unknown']}"
        )


if __name__ == "__main__":
    main()
