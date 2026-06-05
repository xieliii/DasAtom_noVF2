import argparse
import math
import os

import pandas as pd


def _to_numeric(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")


def _fmt_pct(x):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "N/A"
    return f"{x:+.2f}%"


def build_cn_compare_table(df_base: pd.DataFrame, df_new: pd.DataFrame) -> pd.DataFrame:
    key = "QASM File"
    cols = ["Num Qubits", "Elapsed Time (s)", "Fidelity", "Total Move Distance"]
    m = df_base[[key] + cols].merge(
        df_new[[key, "Elapsed Time (s)", "Fidelity", "Total Move Distance"]],
        on=key,
        suffixes=("_base", "_new"),
    )

    m = m[m[key].astype(str).str.endswith(".qasm")].copy()

    m["time_imp_pct"] = (
        (m["Elapsed Time (s)_base"] - m["Elapsed Time (s)_new"])
        / m["Elapsed Time (s)_base"].replace(0, float("nan"))
        * 100
    )
    m["dist_imp_pct"] = (
        (m["Total Move Distance_base"] - m["Total Move Distance_new"])
        / m["Total Move Distance_base"].replace(0, float("nan"))
        * 100
    )
    m["fid_imp_pct"] = (
        (m["Fidelity_new"] - m["Fidelity_base"])
        / m["Fidelity_base"].replace(0, float("nan"))
        * 100
    )

    out = pd.DataFrame(
        {
            "电路文件": m[key],
            "量子比特数": m["Num Qubits"],
            "基线移动距离": m["Total Move Distance_base"],
            "新版移动距离": m["Total Move Distance_new"],
            "移动距离提升率": m["dist_imp_pct"].map(_fmt_pct),
            "基线保真度": m["Fidelity_base"],
            "新版保真度": m["Fidelity_new"],
            "保真度提升率": m["fid_imp_pct"].map(_fmt_pct),
            "基线耗时(秒)": m["Elapsed Time (s)_base"],
            "新版耗时(秒)": m["Elapsed Time (s)_new"],
            "时间提升率": m["time_imp_pct"].map(_fmt_pct),
        }
    )
    return out


def main():
    parser = argparse.ArgumentParser(description="导出中文对比表（含百分号）")
    parser.add_argument("--baseline", required=True, help="baseline summary.xlsx 路径")
    parser.add_argument("--target", required=True, help="新版 summary.xlsx 路径")
    parser.add_argument(
        "--out_prefix",
        default="compare_cn",
        help="输出文件前缀（会生成 .csv 和 .xlsx）",
    )
    args = parser.parse_args()

    df_base = pd.read_excel(args.baseline)
    df_new = pd.read_excel(args.target)
    _to_numeric(df_base, ["Num Qubits", "Elapsed Time (s)", "Fidelity", "Total Move Distance"])
    _to_numeric(df_new, ["Num Qubits", "Elapsed Time (s)", "Fidelity", "Total Move Distance"])

    out_df = build_cn_compare_table(df_base, df_new)
    out_csv = f"{args.out_prefix}.csv"
    out_xlsx = f"{args.out_prefix}.xlsx"
    out_df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    out_df.to_excel(out_xlsx, index=False)

    print(f"已生成: {os.path.abspath(out_csv)}")
    print(f"已生成: {os.path.abspath(out_xlsx)}")


if __name__ == "__main__":
    main()
