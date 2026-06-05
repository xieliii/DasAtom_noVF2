# GPT Project Handoff

This file is a handoff for another GPT/Codex instance. The goal is to let it quickly understand:

1. What this project does.
2. What the current `noVF2` pipeline is.
3. What code was changed from the original approach.
4. What has been tested and what the current stable conclusions are.
5. How to explain the project back to the user without re-discovering everything from scratch.

Use this file as the first read before opening source files.

## 1. Repository Role

Project root:

- `DasAtom.py`: top-level driver for one circuit / one benchmark folder.
- `DasAtom_fun.py`: partitioning, embedding orchestration, fidelity computation, strict validation helpers.
- `mcts_mapper.py`: MCTS initial mapping engine.
- `analytical_placer.py`: force-directed partition embedding engine.
- `Enola/route.py`: movement/routing implementation.
- `Data/benchmark_circuits/`: main benchmark folder used in recent comparisons.
- `DasAtom_Origin` is the reference/original implementation used as baseline. Do not treat it as the edited experimental path.

## 2. Current High-Level Method

The active `noVF2` method is:

`layer-only partition -> MCTS initial mapping -> force-directed partition embedding -> light strict legality fallback -> routing -> fidelity/runtime metrics`

The intended paper narrative is:

`A unified MCTS + force-directed compilation framework, with a small number of explainable structure-adaptive rules.`

Avoid describing the method as a pile of circuit-specific tricks.

## 3. What Was Intentionally Changed

### 3.1 Baseline path was repaired

Baseline should use VF2 again and must not accidentally go through the `noVF2` path.

Relevant entry points:

- `DasAtom.py:540`
- `DasAtom_fun.py:1407`

Meaning:

- `baseline` is the original-style VF2 embedding path.
- `dual` / `noVF2` are the experimental paths.

### 3.2 noVF2 partitioning was rewritten

Relevant function:

- `DasAtom_fun.py:173` `layer_only_partition(...)`

This is no longer VF2-based layer merging.
It now greedily merges DAG layers under light constraints:

- qubit capacity
- graph degree budget
- density / size guardrails

Important consequence:

- partitioning is a fast pre-cut stage
- exact physical executability is enforced later in embedding / strict validation

This function was also optimized incrementally to avoid repeated graph rebuilds.

### 3.3 Initial mapping is now MCTS-first

Relevant code:

- `DasAtom.py:391`
- `DasAtom.py:450-516`
- `mcts_mapper.py:110`
- `mcts_mapper.py:868`

Meaning:

- the first partition in `noVF2` uses MCTS as the primary initial mapping engine
- not seed-first anymore
- adaptive iteration budgets are applied based on first-partition structure

The MCTS budget logic is intentionally structure-aware, not file-name-aware.

### 3.4 MCTS was changed into a core-qubit search engine

Relevant functions:

- `mcts_mapper.py:257` `_select_search_qubits(...)`
- `mcts_mapper.py:419` `_rank_positions(...)`
- `mcts_mapper.py:551` `_greedy_complete_mapping(...)`
- `mcts_mapper.py:609` `_estimate_fidelity(...)`
- `mcts_mapper.py:752` `search(...)`

Current MCTS behavior:

- only searches a subset of frontier/core qubits
- completes the rest greedily
- ranks physical positions geometrically instead of random exploration
- uses a fidelity-like rollout objective
- uses warm-start shortcuts and early stopping

The current retained structure tags inside MCTS are:

- `chain_like`
- `hub_like`
- `dense_small`
- `dense_frontier_sparse`

These are graph-structure categories, not circuit-name categories.

### 3.5 force-directed embedding is the partition-level optimizer

Relevant functions:

- `DasAtom_fun.py:1432` `get_embeddings(...)`
- `analytical_placer.py:445` `force_directed_mapping(...)`

Current embedding flow:

- attempt to reuse previous mapping
- light legality repair if possible
- run force-directed placement if needed
- apply strict single-gate fallback when necessary

This is not supposed to be a large post-processing layer anymore. The intended design is:

`MCTS gives a good initial state, force-directed gives a good partition embedding, repair is only a thin legality layer.`

### 3.6 force-directed has two retained structure-aware seeds

Relevant functions:

- `analytical_placer.py:200` `_build_hub_seed_mapping(...)`
- `analytical_placer.py:347` `_build_dense_seed_mapping(...)`

These are kept because they are explainable as structure-adaptive initialization:

- hub/star-like partitions
- small dense partitions

This should be described as a small number of structure-adaptive rules, not benchmark-specific tuning.

### 3.7 strict correctness validation exists and matters

Relevant code:

- `DasAtom.py:177`
- `DasAtom_fun.py:739`
- `DasAtom_fun.py:1896`

Environment flag:

- `DASATOM_STRICT_VALIDATE=1`

What it checks:

- gate coverage / no missing scheduled 2Q gates
- embedding count consistency
- Rb legality
- strict single-gate fallback behavior

This exists because the user explicitly prioritizes correctness before performance.

## 4. Important Physical Model Notes

Physical parameters:

- `DasAtom_fun.py:651`

Current defaults include:

- `T_cz = 0.2`
- `T_trans = 20`
- `Move_speed = 0.55`
- `F_cz = 0.995`
- `F_trans = 1`

Important implication:

- transfer currently has time cost
- transfer currently has no direct fidelity penalty because `F_trans = 1`

Therefore:

- fidelity differences currently come mainly from idle time / total time / movement organization
- not from an explicit transfer-infidelity factor

## 5. Current Stable Optimization Conclusions

The stable path is around the current `optN` benchmark result:

- summary file:
  - `.tmp_bench_current_optN/Rb2Re4/benchcur_summary.xlsx`

This path retained these specific MCTS changes:

- `dense_frontier_sparse` search target:
  - `mcts_mapper.py:295`
  - `8 if <= 20 qubits else 9`
- `dense_frontier_sparse` early-stop patience shrink:
  - `mcts_mapper.py:771-772`
  - `patience = max(5, int(patience * 0.5))`

This version outperformed the previous stable version (`optM`) by a small but real margin on the benchmark set, without changing fidelity or movement metrics.

## 6. What Was Tried and Reverted

The following directions were tested and then reverted because they were not stable wins:

- globally shrinking force-directed active sets
- reducing force-directed future layers
- aggressive force-directed implementation-level changes
- tighter MCTS patience such as `* 0.4`
- shrinking `dense_frontier_sparse` search target too far
- weighted center-penalty reward changes
- horizon shortening in `_estimate_fidelity`
- node-level ranked-position caching in MCTS

Why this matters:

- the project is already near a local engineering optimum for the current implementation shape
- many "obvious" micro-optimizations did not survive full-benchmark testing

## 7. Transfer Behavior Observation

The user asked whether the new method has more transfers.

Observed from existing comparable single-circuit logs:

- on a 38-circuit overlap sample, current method had fewer transfers on 22 circuits
- equal transfers on 11 circuits
- more transfers on 5 circuits
- average transfers were lower in the current method than in baseline

Interpretation:

- more partitions does not automatically imply more transfers
- the current pipeline often creates smoother transitions between neighboring partitions

## 8. Files Generated for User-Facing Comparison

Recent comparison files created in this repo root:

- `baseline_vs_optN_compare.xlsx`
- `baseline_vs_optN_compare_v2.xlsx`
- `baseline_vs_optN_compare_v3.xlsx`
- `baseline_vs_optN_with_transfer.xlsx`

The latest transfer-aware file is:

- `baseline_vs_optN_with_transfer.xlsx`

It includes:

- movement distance comparison
- fidelity comparison
- time comparison
- speedup
- transfer counts

## 9. How Another GPT Should Read This Project

Recommended reading order:

1. Read this file first.
2. Open `DasAtom.py` and understand the top-level flow:
   - `process_qasm_file`
   - `_retrieve_or_generate_embeddings`
3. Open `DasAtom_fun.py`:
   - `layer_only_partition`
   - `get_embeddings_vf2`
   - `get_embeddings`
   - strict validation helpers
4. Open `mcts_mapper.py`:
   - `MCTSEngine.__init__`
   - `_select_search_qubits`
   - `_rank_positions`
   - `_greedy_complete_mapping`
   - `_estimate_fidelity`
   - `search`
5. Open `analytical_placer.py`:
   - hub seed
   - dense seed
   - `force_directed_mapping`

## 10. How Another GPT Should Explain It Back To The User

When explaining, do not start with a vague summary.
Use this order:

1. top-level execution flow for one circuit
2. where partitioning happens
3. where MCTS happens
4. where force-directed happens
5. what strict validation does
6. what metrics are computed
7. which parts are baseline vs current method

Avoid saying:

- "many special tricks for many circuits"

Prefer saying:

- "a unified MCTS + force-directed framework with a small number of structure-adaptive rules"

## 11. Practical Prompt To Give Another GPT

You can ask another GPT to do this:

`Please read /Users/shirley/Dream/Code/DasAtom_noVF2/DasAtom/GPT_PROJECT_HANDOFF.md first, then read the referenced source files in the suggested order, and explain this project to me from the perspective of execution flow and code structure. Focus on what was changed in the noVF2 path, how MCTS works, how force-directed embedding works, and how correctness is guaranteed.`

