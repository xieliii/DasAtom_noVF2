import argparse
import os
import subprocess
import sys
import time


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SINGLE_RUNNER = os.path.join(SCRIPT_DIR, "run_initial_mapping_swap_experiment.py")


def read_csv_if_exists(path):
    if not os.path.exists(path):
        return None
    import pandas as pd

    return pd.read_csv(path)


def append_with_metadata(frames, df, **meta):
    if df is None or df.empty:
        return
    for key, value in meta.items():
        df[key] = value
    frames.append(df)


def main():
    parser = argparse.ArgumentParser(description="Batch runner for initial-mapping swap experiments.")
    parser.add_argument("--circuit_folder", default=os.path.join(SCRIPT_DIR, "Data", "benchmark_circuits"))
    parser.add_argument("--partitioning", choices=["fast", "vf2", "layer"], default="fast")
    parser.add_argument("--files", nargs="+", required=True)
    parser.add_argument("--timeout_s", type=int, default=1800)
    parser.add_argument(
        "--output_root",
        default=os.path.join(SCRIPT_DIR, "res", "init_mapping_swap_batch"),
    )
    args = parser.parse_args()

    os.makedirs(args.output_root, exist_ok=True)
    details_frames = []
    summary_frames = []
    pair_frames = []
    failures = []

    for qasm_file in args.files:
        slug = qasm_file.replace(".qasm", "")
        subdir = os.path.join(args.output_root, slug)
        os.makedirs(subdir, exist_ok=True)
        log_path = os.path.join(subdir, "stdout.log")
        err_path = os.path.join(subdir, "stderr.log")

        cmd = [
            sys.executable,
            SINGLE_RUNNER,
            "--circuit_folder",
            args.circuit_folder,
            "--partitioning",
            args.partitioning,
            "--files",
            qasm_file,
            "--output_dir",
            subdir,
        ]

        start = time.time()
        print(f"[BATCH] {qasm_file} start")
        try:
            result = subprocess.run(
                cmd,
                cwd=SCRIPT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=args.timeout_s,
            )
            elapsed = time.time() - start
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(result.stdout)
            with open(err_path, "w", encoding="utf-8") as f:
                f.write(result.stderr)

            if result.returncode != 0:
                failures.append(
                    {
                        "qasm_file": qasm_file,
                        "partitioning": args.partitioning,
                        "failure_type": "runner_nonzero_exit",
                        "error": f"returncode={result.returncode}",
                        "elapsed_s": elapsed,
                    }
                )
                print(f"[BATCH] {qasm_file} failed: returncode={result.returncode}")
                continue

            append_with_metadata(
                details_frames,
                read_csv_if_exists(os.path.join(subdir, "details.csv")),
                batch_qasm=qasm_file,
                batch_partitioning=args.partitioning,
            )
            append_with_metadata(
                summary_frames,
                read_csv_if_exists(os.path.join(subdir, "summary.csv")),
                batch_qasm=qasm_file,
                batch_partitioning=args.partitioning,
            )
            append_with_metadata(
                pair_frames,
                read_csv_if_exists(os.path.join(subdir, "pair_summary.csv")),
                batch_qasm=qasm_file,
                batch_partitioning=args.partitioning,
            )

            sub_fail = read_csv_if_exists(os.path.join(subdir, "failures.csv"))
            if sub_fail is not None and not sub_fail.empty:
                for _, row in sub_fail.iterrows():
                    failures.append(
                        {
                            "qasm_file": qasm_file,
                            "partitioning": args.partitioning,
                            "failure_type": "circuit_level_failure",
                            "error": row.to_dict().get("error", "unknown"),
                            "elapsed_s": elapsed,
                        }
                    )
            print(f"[BATCH] {qasm_file} done in {elapsed:.1f}s")

        except subprocess.TimeoutExpired as exc:
            elapsed = time.time() - start
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(stdout)
            with open(err_path, "w", encoding="utf-8") as f:
                f.write(stderr)
            failures.append(
                {
                    "qasm_file": qasm_file,
                    "partitioning": args.partitioning,
                    "failure_type": "timeout",
                    "error": f"timeout after {args.timeout_s}s",
                    "elapsed_s": elapsed,
                }
            )
            print(f"[BATCH] {qasm_file} timeout after {elapsed:.1f}s")

    import pandas as pd

    if details_frames:
        pd.concat(details_frames, ignore_index=True).to_csv(
            os.path.join(args.output_root, "details.csv"), index=False
        )
    if summary_frames:
        pd.concat(summary_frames, ignore_index=True).to_csv(
            os.path.join(args.output_root, "summary_by_circuit.csv"), index=False
        )
    if pair_frames:
        pair_df = pd.concat(pair_frames, ignore_index=True)
        pair_df.to_csv(os.path.join(args.output_root, "pair_summary_by_circuit.csv"), index=False)

        if "downstream_engine" in pair_df.columns:
            overall_rows = []
            for engine, sub in pair_df.groupby("downstream_engine"):
                total_circuits = sub["circuits"].sum()
                if total_circuits <= 0:
                    continue
                overall_rows.append(
                    {
                        "downstream_engine": engine,
                        "circuits": int(total_circuits),
                        "delta_end_to_end_time_s_mean": (
                            (sub["delta_end_to_end_time_s_mean"] * sub["circuits"]).sum() / total_circuits
                        ),
                        "delta_embedding_time_s_mean": (
                            (sub["delta_embedding_time_s_mean"] * sub["circuits"]).sum() / total_circuits
                        ),
                        "delta_total_move_distance_mean": (
                            (sub["delta_total_move_distance_mean"] * sub["circuits"]).sum() / total_circuits
                        ),
                        "delta_num_transfers_mean": (
                            (sub["delta_num_transfers_mean"] * sub["circuits"]).sum() / total_circuits
                        ),
                        "delta_fidelity_mean": (
                            (sub["delta_fidelity_mean"] * sub["circuits"]).sum() / total_circuits
                        ),
                        "alt_better_fidelity_count": int(sub["alt_better_fidelity_count"].sum()),
                        "alt_lower_move_distance_count": int(sub["alt_lower_move_distance_count"].sum()),
                        "alt_faster_end_to_end_count": int(sub["alt_faster_end_to_end_count"].sum()),
                    }
                )
            overall = pd.DataFrame(overall_rows)
            overall.to_csv(os.path.join(args.output_root, "pair_summary_overall_sum.csv"), index=False)

    if failures:
        pd.DataFrame(failures).to_csv(os.path.join(args.output_root, "failures.csv"), index=False)

    print(f"\nBatch output root: {args.output_root}")


if __name__ == "__main__":
    main()
