# Code Usage Map

This file summarizes which parts of the repository are used by the main TE-CCL
solver path and which parts are examples, generated outputs, or draft material.

## Main runtime path

The installed command is defined in `setup.py`:

```text
teccl = teccl.__main__:main
```

When a user runs `teccl solve --input_args <input.json>`, the code path is:

1. `teccl/__main__.py`
   - Creates the top-level CLI parser.
   - Registers the `solve` subcommand from `teccl.cli`.
2. `teccl/cli/solve.py`
   - Reads the JSON input file.
   - Converts enum-like input values into `ObjectiveType`, `SolutionMethod`,
     `Collective`, `EpochType`, `ProtectionMode`, and `FailureModel`.
   - Creates `TECCLSolver` and calls `solve()`.
3. `teccl/input_data.py`
   - Defines all user input data classes and enums.
   - Required by the CLI, scheduler, solvers, and topologies.
4. `teccl/scheduler.py`
   - Chooses the topology implementation.
   - Chooses the solver formulation.
   - Runs the solve process and writes the schedule JSON.

## Solver code in use

These files are used by `teccl/scheduler.py` depending on input parameters:

- `teccl/solvers/base_formulation.py`
  - Common base class for solver formulations.
- `teccl/solvers/allgather.py`
  - Default AllGather MILP formulation.
- `teccl/solvers/allgather_astar.py`
  - AllGather A* formulation when `objective_type` is `ASTAR`.
- `teccl/solvers/alltoall.py`
  - AlltoAll formulation when `collective` is `ALLTOALL`.
- `teccl/solvers/deferred_protection.py`
  - AllGather deferred-protection formulation when `protection_mode` is
    `DEFERRED`.
- `teccl/solvers/dedicated_protection.py`
  - AllGather dedicated-protection formulation when `protection_mode` is
    `DEDICATED`.
- `teccl/solvers/shared_protection.py`
  - AllGather shared-protection formulation when `protection_mode` is `SHARED`.
- `teccl/solvers/heuristics.py`
  - Helper used by `deferred_protection.py` to estimate protection deadlines.

## Topology code in use

These topology files are selected by `TopologyParams.name` in the input JSON:

- `teccl/topologies/topology.py`
  - Base topology class.
- `teccl/topologies/dgx1.py`
  - Used when `name` is `DGX1`.
- `teccl/topologies/dgx2.py`
  - Used when `name` is `DGX2`.
- `teccl/topologies/ndv2.py`
  - Used when `name` is `NDv2`.
- `teccl/topologies/amd.py`
  - Used when `name` is `AMD`.
- `teccl/topologies/mesh.py`
  - Used when `name` is `Mesh`.
- `teccl/topologies/ladder6.py`
  - Used when `name` is `Ladder6`.
- `teccl/topologies/interdc8.py`
  - Used when `name` is `InterDC8`.

## Example and analysis code

These are useful for experiments, demos, and result analysis, but they are not
part of the normal `teccl solve` runtime path:

- `teccl/examples/json_gen.py`
  - Generates many experiment input JSON files.
- `teccl/examples/run_experiments.sh`
  - Runs batches of generated experiments.
- `teccl/examples/generate_tables.py`
  - Builds paper-style result tables from experiment outputs.
- `teccl/examples/generate_figures.py`
  - Builds paper-style figures from tables.
- `teccl/examples/summarize_protection_results.py`
  - Summarizes protection-mode schedule JSON files into CSV/JSON/Markdown.
- `teccl/examples/plot_protection_results.py`
  - Plots protection-summary CSV data.
- `teccl/examples/MSCCL_examples/`
  - MSCCL-related example artifacts and scripts.

## Data and generated artifacts

These files are useful as inputs, outputs, or references, but they are not Python
runtime code:

- `teccl/examples/sample_inputs/`
  - Example input JSON files for `teccl solve`.
- `teccl/examples/schedules/`
  - Generated or checked-in schedule JSON outputs.
- `teccl/examples/results/`
  - Generated protection-analysis summaries and figures.
- `teccl/examples/experiments/`
  - Large experiment inputs/outputs used for paper reproduction.
- `teccl/examples/experiments_output/`
  - Generated tables and other processed experiment outputs.
- Root-level `*.md` draft/report/note files
  - Design notes, meeting notes, presentation notes, and writeups.
  - Useful for documentation/history, but not imported by the program.

## Likely removable only after confirmation

Do not delete these automatically without confirming intent:

- `teccl/examples/__pycache__/`
  - Python bytecode cache; safe to regenerate.
- `.DS_Store`
  - macOS metadata; safe to remove from source control if present.
- Generated result files under `teccl/examples/results/`, `schedules/`, and
  `experiments_output/`
  - Safe to regenerate only if the corresponding experiment commands are still
    available and affordable to run.

## Current feature-specific useful files

The protection/topology changes currently depend on this set:

- `teccl/input_data.py`
- `teccl/cli/solve.py`
- `teccl/scheduler.py`
- `teccl/solvers/deferred_protection.py`
- `teccl/solvers/dedicated_protection.py`
- `teccl/solvers/shared_protection.py`
- `teccl/solvers/heuristics.py`
- `teccl/topologies/ladder6.py`
- `teccl/topologies/interdc8.py`
- Protection-related input JSON files in `teccl/examples/sample_inputs/`
- Protection-related generated schedules in `teccl/examples/schedules/`
- Protection summary/plot scripts in `teccl/examples/`
