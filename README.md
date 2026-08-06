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

Run the paired Dedicated versus Preplanned Deferred experiment after activating
the project Conda environment:

```
python teccl/examples/compare_preplanned_protection.py --run
```

The runner generates a protectable Dedicated working schedule first, fixes the
same raw working flows in Preplanned Deferred, checks 24 fairness/correctness
invariants for `DCN4WAN` and `InterDC8`, and writes CSV/JSON/Markdown results under
`teccl/examples/results/preplanned_comparison/`.

Run the fixed-total-data chunk-granularity sensitivity with:

```
python teccl/examples/chunk_granularity_sensitivity.py --run
```

This compares 1, 2, and 4 chunks for 100 GB per source on `DCN4WAN` and
`InterDC8`. It uses beta-weighted occupied link-seconds, records solver
optimality and working-schedule provenance, and writes auditable results under
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
