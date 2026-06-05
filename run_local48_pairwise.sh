#!/bin/bash
set -euo pipefail

ROOT="$HOME/Dream/Code/DasAtom_noVF2"
CUR="$ROOT/DasAtom"
BASE="$ROOT/DasAtom_Origin"
LIST="$ROOT/local48_benchmark_list.txt"
BUNDLE="$ROOT/.tmp_local48_m4"
CASES="$BUNDLE/cases"
CUR_OUT="$CUR/.tmp_local48_current"
BASE_OUT="$BASE/.tmp_local48_baseline"
CUR_LOG="$BUNDLE/logs_current"
BASE_LOG="$BUNDLE/logs_baseline"
DRIVER_LOG="$BUNDLE/local48_driver.log"
TIMEOUT_SEC=$((3 * 60 * 60))

mkdir -p "$CASES" "$CUR_OUT" "$BASE_OUT" "$CUR_LOG" "$BASE_LOG"

find_qasm() {
  local name="$1"
  find "$CUR/Data" -name "$name" -print | head -n 1
}

prepare_cases() {
  while IFS= read -r qasm_name; do
    [[ -z "$qasm_name" ]] && continue
    local src
    src="$(find_qasm "$qasm_name")"
    if [[ -z "$src" ]]; then
      echo "[MISSING] $qasm_name" | tee -a "$DRIVER_LOG"
      exit 1
    fi
    local stem="${qasm_name%.qasm}"
    local case_dir="$CASES/$stem"
    mkdir -p "$case_dir"
    cp -f "$src" "$case_dir/$qasm_name"
  done < "$LIST"
}

run_current() {
  local stem="$1"
  local case_dir="$CASES/$stem"
  local out_dir="$CUR_OUT/$stem"
  local result_file="$out_dir/Rb2Re4/${stem}.qasm_rb2.xlsx"
  if [[ -f "$result_file" ]]; then
    echo "[CUR SKIP] $stem" | tee -a "$DRIVER_LOG"
    return
  fi
  mkdir -p "$out_dir"
  echo "[CUR RUN] $stem" | tee -a "$DRIVER_LOG"
  timeout "$TIMEOUT_SEC" \
    python3 "$CUR/DasAtom.py" "local48_cur_$stem" "$case_dir" \
      --engine noVF2 \
      --results_folder "$out_dir" \
      --no_save_embeddings \
      > "$CUR_LOG/$stem.log" 2>&1 || true
}

run_baseline() {
  local stem="$1"
  local case_dir="$CASES/$stem"
  local out_dir="$BASE_OUT/$stem"
  local result_file="$out_dir/Rb2Re4/${stem}.qasm_rb2.xlsx"
  if [[ -f "$result_file" ]]; then
    echo "[BASE SKIP] $stem" | tee -a "$DRIVER_LOG"
    return
  fi
  mkdir -p "$out_dir"
  echo "[BASE RUN] $stem" | tee -a "$DRIVER_LOG"
  timeout "$TIMEOUT_SEC" \
    python3 "$BASE/DasAtom.py" "local48_base_$stem" "$case_dir" \
      --results_folder "$out_dir" \
      --no_save_embeddings \
      > "$BASE_LOG/$stem.log" 2>&1 || true
}

: > "$DRIVER_LOG"
prepare_cases

idx=0
while IFS= read -r qasm_name; do
  [[ -z "$qasm_name" ]] && continue
  stem="${qasm_name%.qasm}"
  if (( idx % 2 == 0 )); then
    run_current "$stem"
    run_baseline "$stem"
  else
    run_baseline "$stem"
    run_current "$stem"
  fi
  idx=$((idx + 1))
done < "$LIST"
