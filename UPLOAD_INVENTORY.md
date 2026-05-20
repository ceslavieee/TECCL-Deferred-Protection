# Upload Inventory

This document compares the current workspace against the original TE-CCL
repository state and classifies files for a clean GitHub upload.

## Summary

The project is an extension of the original TE-CCL codebase, not a rewrite.

- Original TE-CCL still provides the CLI, input schema, scheduler, base MILP
  framework, default AllGather/AlltoAll formulations, topology abstraction, and
  original example data.
- The new work adds protection-aware AllGather formulations, two small/new
  topologies, protection experiment inputs, and optional result-analysis scripts.
- Only three original Python files were modified directly.

## Modified Original Files

These files existed in the original TE-CCL repository and were changed to connect
the new functionality into the existing framework.

| File | Change size | Purpose |
| --- | ---: | --- |
| `teccl/cli/solve.py` | +4 / -1 | Parse `protection_mode` and `failure_model` enum values from input JSON. |
| `teccl/input_data.py` | +21 / -1 | Add `ProtectionMode`, `FailureModel`, and protection-related input parameters. |
| `teccl/scheduler.py` | +15 / -2 | Register `Ladder6`, `InterDC8`, and protection solver choices. |

Total direct modifications to original files: **+40 / -4 lines**.

## New Core Code

These are the main files that should be considered part of the implementation.

| File | Approx. lines | Keep? | Reason |
| --- | ---: | --- | --- |
| `teccl/solvers/deferred_protection.py` | 625 | Yes | Main deferred-protection MILP formulation. |
| `teccl/solvers/dedicated_protection.py` | 175 | Yes | Dedicated-protection baseline. |
| `teccl/solvers/shared_protection.py` | 167 | Yes | Shared-protection baseline. |
| `teccl/solvers/heuristics.py` | 113 | Yes | Shortest-path based deadline/completion heuristic used by protection solver. |
| `teccl/topologies/interdc8.py` | 71 | Yes | 8-node WAN/inter-data-center topology for protection experiments. |
| `teccl/topologies/ladder6.py` | 41 | Yes | Small illustrative topology for debugging and explaining protection behavior. |

Approximate new core implementation size: **1192 lines**.

## New Example Inputs

These JSON files are useful because they show how to run the new functionality.
They are small and should usually be uploaded.

| File | Keep? | Reason |
| --- | --- | --- |
| `teccl/examples/sample_inputs/interdc8_baseline.json` | Yes | Baseline InterDC8 AllGather comparison. |
| `teccl/examples/sample_inputs/interdc8_deferred_protection.json` | Yes | Main deferred-protection InterDC8 example. |
| `teccl/examples/sample_inputs/interdc8_dedicated_protection.json` | Yes | Dedicated-protection comparison. |
| `teccl/examples/sample_inputs/interdc8_shared_protection.json` | Yes | Shared-protection comparison. |
| `teccl/examples/sample_inputs/interdc8_deferred_protection_3fail.json` | Optional | Variant with selected failure scenarios. |
| `teccl/examples/sample_inputs/interdc8_deferred_protection_5fail.json` | Optional | Variant with selected failure scenarios. |
| `teccl/examples/sample_inputs/interdc8_deferred_protection_allfail.json` | Optional | Larger all-failure-scenario variant. |
| `teccl/examples/sample_inputs/ladder6_baseline.json` | Yes | Small baseline demonstration. |
| `teccl/examples/sample_inputs/ladder6_deferred_protection.json` | Yes | Small deferred-protection demonstration. |
| `teccl/examples/sample_inputs/mesh3_deferred_protection.json` | Optional | Extra experiment variant. |
| `teccl/examples/sample_inputs/mesh4_deferred_protection.json` | Optional | Extra experiment variant. |
| `teccl/examples/sample_inputs/ndv2_deferred_protection.json` | Optional | Extra experiment variant on original NDv2 topology. |

Recommended minimal upload set:

- InterDC8 baseline/deferred/dedicated/shared inputs.
- Ladder6 baseline/deferred inputs.
- Keep the extra variants only if they are discussed in the report or slides.

## New Analysis Scripts

These are helpful but not required for the solver to run.

| File | Keep? | Reason |
| --- | --- | --- |
| `teccl/examples/summarize_protection_results.py` | Optional yes | Converts generated schedules into comparison summaries. Useful if uploading results. |
| `teccl/examples/plot_protection_results.py` | Optional | Generates plots from summary CSV. Not needed for the core solver. |

## Generated Outputs

These files are generated artifacts. They are useful for evidence and figures, but
they are not required to run the code.

| Path/File | Upload? | Reason |
| --- | --- | --- |
| `teccl/examples/schedules/interdc8_*_schedule.json` | Optional | Example solver outputs. Upload only selected representative schedules. |
| `teccl/examples/schedules/ladder6_*_schedule.json` | Optional | Small demonstration outputs. Nice to keep if explaining Ladder6. |
| `teccl/examples/schedules/mesh3_baseline_schedule.json` | Probably no | Extra generated output not central to current story. |
| `teccl/examples/results/interdc8_protection_summary.csv` | Optional | Useful compact result table. |
| `teccl/examples/results/interdc8_protection_summary.json` | Probably no | Same information as CSV/Markdown, less readable. |
| `teccl/examples/results/interdc8_protection_summary.md` | Optional | Human-readable result summary. |
| `teccl/examples/results/figures/*.png` | Optional | Upload if the GitHub repo should include generated figures. |

Recommended clean choice:

- Upload either the result summary/figures, or only the scripts needed to
  regenerate them.
- Avoid uploading every generated schedule unless the teacher specifically wants
  reproducibility artifacts.

## Original TE-CCL Files Still Used

These original files are still essential and should stay in the repository:

- `setup.py`
- `teccl/__main__.py`
- `teccl/cli/solve.py`
- `teccl/input_data.py`
- `teccl/scheduler.py`
- `teccl/solvers/base_formulation.py`
- `teccl/solvers/allgather.py`
- `teccl/solvers/allgather_astar.py`
- `teccl/solvers/alltoall.py`
- `teccl/topologies/topology.py`
- Existing original topology files: `amd.py`, `dgx1.py`, `dgx2.py`, `mesh.py`,
  `ndv2.py`

## Original TE-CCL Data

The original repository already contains many JSON files under:

```text
teccl/examples/experiments/output_provided/
```

These are original TE-CCL paper reproduction outputs. They are large and numerous,
but they are not new changes from this work.

If uploading a fork of the full TE-CCL repository, they may remain because they
already belong to the original project. If creating a smaller project submission,
they can be omitted to reduce size.

## Not Necessary For A Clean Submission

These are not needed for the core code upload:

- `teccl/examples/results/` if the submission only needs runnable code.
- Most generated schedule JSON files in `teccl/examples/schedules/`.
- Extra protection input variants not referenced by the report.
- `teccl/examples/plot_protection_results.py` if no plots are uploaded or required.
- `CODE_USAGE.md` and this file, unless you want documentation explaining the
  cleanup and upload decisions.

## Recommended Upload Set

For a clean GitHub submission, include:

1. Modified original integration files:
   - `teccl/cli/solve.py`
   - `teccl/input_data.py`
   - `teccl/scheduler.py`
2. New core implementation files:
   - `teccl/solvers/deferred_protection.py`
   - `teccl/solvers/dedicated_protection.py`
   - `teccl/solvers/shared_protection.py`
   - `teccl/solvers/heuristics.py`
   - `teccl/topologies/interdc8.py`
   - `teccl/topologies/ladder6.py`
3. Minimal example inputs:
   - `teccl/examples/sample_inputs/interdc8_baseline.json`
   - `teccl/examples/sample_inputs/interdc8_deferred_protection.json`
   - `teccl/examples/sample_inputs/interdc8_dedicated_protection.json`
   - `teccl/examples/sample_inputs/interdc8_shared_protection.json`
   - `teccl/examples/sample_inputs/ladder6_baseline.json`
   - `teccl/examples/sample_inputs/ladder6_deferred_protection.json`
4. Optional documentation:
   - `CODE_USAGE.md`
   - `UPLOAD_INVENTORY.md`

For a fuller experiment submission, also include:

- `teccl/examples/summarize_protection_results.py`
- selected `teccl/examples/schedules/*protection*_schedule.json`
- selected `teccl/examples/results/*`

## Suggested Description

Use this wording when explaining the relationship to the original TE-CCL code:

> This work extends the original TE-CCL codebase. The original repository provides
> the command-line interface, input schema, scheduler, topology abstraction, and
> base MILP formulations. My contribution adds protection-aware AllGather
> formulations for deferred, dedicated, and shared protection, along with new
> topology examples and experiment configurations. Only a small number of original
> files were modified to register the new modes and route inputs to the new
> solvers.
