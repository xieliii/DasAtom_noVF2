#!/bin/bash
set -euo pipefail

ROOT="$HOME/Dream/Code/DasAtom_noVF2"
CUR="$ROOT/DasAtom"
BASE="$ROOT/DasAtom_Origin"
BUNDLE="$ROOT/.tmp_local48_smoke"
CASES="$BUNDLE/cases"
CUR_OUT="$CUR/.tmp_local48_smoke_current"
BASE_OUT="$BASE/.tmp_local48_smoke_baseline"

mkdir -p "$CASES/3_regular_10" "$CUR_OUT/3_regular_10" "$BASE_OUT/3_regular_10"

SRC="$(find "$CUR/Data" -name '3_regular_10.qasm' -print | head -n 1)"
cp -f "$SRC" "$CASES/3_regular_10/3_regular_10.qasm"

python3 "$CUR/DasAtom.py" "smoke_cur_3_regular_10" "$CASES/3_regular_10" \
  --engine noVF2 \
  --results_folder "$CUR_OUT/3_regular_10" \
  --no_save_embeddings \
  > "$BUNDLE/current.log" 2>&1

python3 "$BASE/DasAtom.py" "smoke_base_3_regular_10" "$CASES/3_regular_10" \
  --results_folder "$BASE_OUT/3_regular_10" \
  --no_save_embeddings \
  > "$BUNDLE/baseline.log" 2>&1

echo "smoke_done"
