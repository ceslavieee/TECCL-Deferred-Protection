# TE-CCL

TE-CCL is a tool to generate collective communication schedules for large topologies using a Traffic Engineering-based solver.

TE-CCL takes in a topology and collective (e.g. AllGather) and outputs a schedule (in JSON) detailing data transfer steps for each node that satisfies the demands specified by the collective. In a Traffic Engineering-based approach, TE-CCL encodes the collective communication process into *capacity constraints*, *flow conservation constraints*, and *destination constraints*, and solves a mixed-integer linear program (MILP), with options to convert to an linear program (LP) form or A* form for better scalibility.

> **Rethinking Machine Learning Collective Communication as a Multi-Commodity Flow Problem** <br/>
> Xuting Liu, Behnaz Arzani, Siva Kesava Reddy Kakarla, Liangyu Zhao, Vincent Liu, Miguel Castro, Srikanth Kandula, and Luke Marshall <br/>
> **SIGCOMM 2024** [https://doi.org/10.1145/3651890.3672249]

## Citing TE-CCL
```
@inproceedings{10.1145/3651890.3672249,
author = {Liu, Xuting and Arzani, Behnaz and Kakarla, Siva Kesava Reddy and Zhao, Liangyu and Liu, Vincent and Castro, Miguel and Kandula, Srikanth and Marshall, Luke},
title = {Rethinking Machine Learning Collective Communication as a Multi-Commodity Flow Problem},
year = {2024},
isbn = {9798400706141},
publisher = {Association for Computing Machinery},
address = {New York, NY, USA},
url = {https://doi.org/10.1145/3651890.3672249},
doi = {10.1145/3651890.3672249},
booktitle = {Proceedings of the ACM SIGCOMM 2024 Conference},
pages = {16–37},
numpages = {22},
keywords = {GPU, collective communication, traffic engineering},
location = {Sydney, NSW, Australia},
series = {ACM SIGCOMM '24}
}
```

## Installation
### Prerequisites
- Install [Anaconda](https://www.anaconda.com/) and activate an anaconda environment.
- Obtain and install a [Gurobi license](https://www.gurobi.com/downloads/). After getting the license, follow the steps to install and activate Gurobi.
```
conda config --add channels http://conda.anaconda.org/gurobi
conda install -c conda-forge gurobi -y
<command to install Gurobi license. e.g. grbgetkey xxxxxx>
```
### Install TE-CCL
In the anaconda environment with Gurobi installed, run
```
pip install .
```

## Usage
TE-CCL takes in a JSON user input file that specifies the topology, Gurobi settings, and model instance parameters. Please refer to [Input Data](#input-data) for details.
```
teccl solve --input_args <input.json>
```

### Example
To generate a schedule for AllGather in the NDv2 topology, run
```
teccl solve --input_args teccl/examples/sample_inputs/ndv2_input.json
```
This will generate the schedule file `teccl/examples/schedules/ndv2_schedule.json`

### Cross-Data-Center Protection Extension
This version extends TE-CCL with protection-aware AllGather scheduling for
cross-data-center collective communication. It adds:

- `InterDC8`, an 8-node WAN-style topology for inter-data-center experiments.
- `DCN4WAN`, the 4-DCN and 5-WAN-transit-node topology used in the formal
  paired protection experiment.
- `Ladder6`, a small illustrative topology for debugging and explaining
  protection behavior; it is not used in the formal paired results.
- Preplanned deferred protection, where a complete contingency schedule is
  computed before failure, placed after the working window, and progressively
  released as atomic source chunks complete.
- Post-failure deferred recovery, where a realized failure time and detection
  delay determine scenario-specific recovery flows.
- Dedicated and shared protection baselines for comparison.

`InterDC8` and `DCN4WAN` are synthetic physical-unit topologies. Their link
tables use Gbit/s and milliseconds and are converted to TE-CCL chunks/s and
seconds in `teccl/topologies/research_wan.py`. See `TOPOLOGY_DESIGN.md` for the
source-of-truth parameters and claim boundary.

Protection timing semantics:

- `deferred_timing_mode = PREPLANNED` selects pre-failure planning with delayed
  backup reservation. Its protection unit is an atomic `(source, chunk)`
  multicast commodity, and it reports an exact per-epoch reservation release
  profile for that unit.
- `deferred_timing_mode = POST_FAILURE` selects the existing realized
  failure-time recovery model.
- Dedicated and shared protection are static pre-planned baselines. They do not
  use failure-time information; any demand whose working path is exposed to a
  failed link requires backup.
- Post-failure deferred protection evaluates a realized failure-time scenario.
  A single MILP solve uses one configured `failure_time_epoch`, and
  `teccl/examples/failure_time_sensitivity.py` sweeps possible failure epochs
  when the claim depends on unknown failure timing.
- The affected counts therefore have different meanings: dedicated/shared report
  static path-exposed demands, while deferred reports demands still at risk after
  the realized failure time.

Minimal examples:

```
teccl solve --input_args teccl/examples/sample_inputs/interdc8_baseline.json
teccl solve --input_args teccl/examples/sample_inputs/interdc8_deferred_protection.json
teccl solve --input_args teccl/examples/sample_inputs/interdc8_dedicated_protection.json
teccl solve --input_args teccl/examples/sample_inputs/interdc8_shared_protection.json
teccl solve --input_args teccl/examples/sample_inputs/dcn4wan_deferred_protection.json
teccl solve --input_args teccl/examples/sample_inputs/dcn4wan_preplanned_deferred_protection.json
```

The pre-reset timing-pair runner is retained only for historical diagnostics:

```
python teccl/examples/run_strict_dpp_ddpp_pair.py --topology all
```

Its outputs under `teccl/examples/results/strict_dpp_ddpp_pair/` predate the
physical-unit and strict-tree corrections and are not current certificates.
The corrected 25 GB fixed plans and their full directed-link/failure-time
certificates are under
`teccl/examples/results/multicast_tree_pair_physical_25gb/`.

Certify the maximum detection delay tolerated by the corrected exported fixed
plans, after activating the project Conda environment, with:

```
python teccl/examples/physical_tree_pair_detection_delay_boundary.py
```

The runner derives a candidate from the necessary root-edge reservation on each
affected destination's unique backup-tree path. It verifies every affected
failure epoch at that endpoint, treats only an explicit solver `INFEASIBLE`
result as the adjacent failing boundary, and writes its auditable report under
`teccl/examples/results/multicast_tree_pair_physical_25gb/detection_delay_boundary/`.

Compare source-chunk exposure against deterministic demand-level failure replay
with:

```
python teccl/examples/compare_failure_exposure_models.py
```

This report predates strict tree pairs and is retained as a historical
diagnostic under `teccl/examples/results/failure_exposure_comparison/`. Current
strict-tree certificates use `FailureModel.EXACT`, because every destination
has a unique working-tree path.

The older Dedicated versus standalone Preplanned Deferred runner is retained
for reproducibility:

```
python teccl/examples/compare_preplanned_protection.py --run
```

That legacy runner generates a protectable Dedicated working schedule first, fixes the
same raw working flows in Preplanned Deferred, checks 24 fairness/correctness
invariants for `DCN4WAN` and `InterDC8`, and writes CSV/JSON/Markdown results under
`teccl/examples/results/preplanned_comparison/`. Its protection services are
not semantically equivalent, so its percentages are not thesis evidence.

Run the fixed-total-data chunk-granularity sensitivity with:

```
python teccl/examples/chunk_granularity_sensitivity.py --run
```

The existing runner compares 1, 2, and 4 chunks for 100 GB per source on
`DCN4WAN` and `InterDC8`. Those generated schedules predate the physical-unit
topology reset and are historical only. Before the next run, the runner will be
reset to the documented 25 GB fixed-total-data baseline. It uses beta-weighted
occupied link-seconds, records solver optimality and working-schedule
provenance, and writes auditable results under
`teccl/examples/results/chunk_granularity/`.

Compare the legacy flow-start holding objective with the beta-weighted physical
holding objective using:

```
python teccl/examples/compare_preplanned_holding_objectives.py --run
```

This A/B test fixes the same working schedule and changes only the
highest-priority Preplanned Deferred objective. Results are written under
`teccl/examples/results/preplanned_objective_ablation/`.

Replay the paired schedules under every relevant single directed-link failure
time and activate protection only for affected `(source, chunk)` units:

```
python teccl/examples/evaluate_preplanned_failure_execution.py
```

Results are written under
`teccl/examples/results/preplanned_failure_execution/`.

Generate the corresponding thesis-ready PNG, PDF, and SVG comparison figure
with:

```
python teccl/examples/plot_preplanned_failure_execution.py
```

Figures are written under
`teccl/examples/results/figures/preplanned_failure_execution/`.

The command above is retained only for historical reproducibility. Its figures
must not be used in the thesis. Generate the current certified figures with:

```
python teccl/examples/plot_certified_protection_results.py
```

The certified PNG/PDF/SVG outputs and their claim manifest are written under
`teccl/examples/results/figures/certified/`. They certify the single-request
timing subproblem only; dynamic-admission blocking and utilization experiments
are still required before selecting the thesis result figures.

Run the deterministic Stage-A admission-ledger check with:

```
python teccl/examples/dynamic_admission_smoke.py
```

This check validates transactional capacity accounting and safe future-backup
release using fixed schedule templates. Its report under
`teccl/examples/results/dynamic_admission_smoke/` is deliberately marked as a
non-authoritative performance result. The full dynamic model is specified in
[`DYNAMIC_ADMISSION_DESIGN.md`](DYNAMIC_ADMISSION_DESIGN.md).

Run the Stage-B residual-capacity solver integration check with:

```
python teccl/examples/dynamic_residual_capacity_smoke.py
```

This admits two protected Mesh2 requests in consecutive epochs. The second
request is solved against the first request's active link-epoch commitments and
must pass both the independent protection audit and the final ledger-capacity
check. Results under `teccl/examples/results/dynamic_residual_capacity_smoke/`
validate solver integration only, not a DPP/DDPP performance advantage.

Run the non-authoritative Mesh2 shared-trace and load-shape pilots with:

```
python teccl/examples/dynamic_admission_pilot.py
python teccl/examples/dynamic_admission_pilot_sweep.py
python teccl/examples/summarize_dynamic_admission_load_sweep.py
```

The aggregate report under
`teccl/examples/results/dynamic_admission_load_sweep/` validates the paired
experiment pipeline and a plausible load-dependent admission mechanism. It is
not thesis performance evidence: the topology and sample are intentionally
small, and the comparison baseline remains an early scenario-robust DPP
adaptation rather than literal path-pair DPP 1:1.

Run a corrected main-topology probe with, for example:

```
python teccl/examples/dynamic_admission_pilot.py \
  --topology DCN4WAN --requests 4 --arrival-rate 0.2 \
  --time-limit-hours 0.005
```

The current DCN4WAN/InterDC8 probe summary is generated with:

```
python teccl/examples/summarize_dynamic_admission_topology_probes.py
```

The report under `teccl/examples/results/dynamic_admission_topology_pilot/`
uses only corrected runs with identical paired traces, conclusive outcomes,
audited schedules, and safe ledgers. Four requests on one seed are sufficient
to locate candidate loads, not to support thesis performance claims.

Run or resume paired main-topology load/seed locator sweeps with:

```
python teccl/examples/dynamic_admission_topology_sweep.py \
  --topology InterDC8 --rates 0.01 0.05 0.1 \
  --seeds 20260807 20260808
```

Each aggregate row reports both the raw arrival rate and
`offered_arrivals_per_deadline = arrival_rate * deadline_epochs`. The current
strict-tree reports are written under
`teccl/examples/results/dynamic_admission_tree_pair_sweep/`. They are
deliberately non-authoritative: they locate useful load regions and exercise
the corrected admission path, but do not replace longer traces, more seeds,
confidence intervals, or the complete predeclared load range.

The pre-topology-reset locator points showed a repeated InterDC8 direction (early DPP
6/8, 5/8, 4/8 versus DDPP 8/8 across increasing normalized loads) and
DCN4WAN ties (8/8 at both points). Unknown solver outcomes are kept separate
and resolved only by an explicitly reported longer fallback; they are never
counted as blocking. These values are historical pilot evidence after the
2026-08-09 physical-unit topology reset and must be regenerated before thesis
use.

Audit the stricter fixed multicast-tree DPP/DDPP timing pairs with:

```
python teccl/examples/audit_multicast_tree_pair.py
python teccl/examples/audit_multicast_tree_pair.py \
  --dpp teccl/examples/results/multicast_tree_pair/schedules/interdc8_dpp_tree_pair.json \
  --ddpp teccl/examples/results/multicast_tree_pair/schedules/interdc8_ddpp_tree_pair_fixed.json \
  --output teccl/examples/results/multicast_tree_pair/interdc8_report.json
```

This certificate requires identical serialized working flows, identical backup
tree edges, adjacent DPP/DDPP reservation windows, equal reserved occupied
link-epochs, and independent schedule audits. It certifies fairness and
feasibility, not blocking performance or global optimality of a time-limited
solve.

The formal preplanned model and its claim boundaries are documented in
[`PREPLANNED_DEFERRED_PROTECTION.md`](PREPLANNED_DEFERRED_PROTECTION.md).

### Detailed Examples
For detailed examples, please refer to instructions in the [examples](teccl/examples/) directory.

### Hardware and Resource Requirements
Simple topologies (like the example above) can be easily solved using a laptop within seconds. Larger topologies (like 4-chassis ones provided in our `examples` directory) need 256GB RAM and 1+ hours. 


## Input Data
A user input JSON file is consists of three parts: `TopologyParams`, `GurobiParams`, and `InstanceParams`. Detailed explainations of each argument is in [input_data.py](teccl/input_data.py).

### TopologyParams
This sepcifies the topology considered. Each topology is defined as a seperate Python file in teccl/topologies.

### GurobiParams
Parameters for the Gurobi solver.

### InstanceParams
Parameters for the model instance, including the collective, the choice of objective function and size of epochs.

## Contributing

This project welcomes contributions and suggestions.  Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit https://cla.opensource.microsoft.com.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft 
trademarks or logos is subject to and must follow 
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
