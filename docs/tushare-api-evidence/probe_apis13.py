"""第 1 集研究用探针（第十三轮）：判定 update_flag 语义 —— 用"单行报告期"反推"当前版本"的标记。

推理依据：
  某报告期只有一行时，那一行必然就是当前版本。因此统计"单行报告期"的 update_flag 分布，
  多数派即为"当前版本"的标记值。

第十二轮观察到的疑点：
  income / fina_indicator 的单行报告期以 update_flag=1 为主，而 balancesheet 的 flag 总分布以 0 为主，
  三表不一致，需要逐表精确统计（第十二轮是用减法推算的，可能不准）。

同时把原始行存成 CSV 放在同目录，便于后续复核而不必重复调用接口。

只读接口，只写 CSV 证据文件。
"""

import sys
import time
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "screener").is_dir())
sys.path.insert(0, str(ROOT))

import pandas as pd
import tushare as ts

from screener.config import load_settings

cfg = load_settings()
pro = ts.pro_api(cfg.tushare_token, timeout=120)
pro._DataApi__http_url = cfg.tushare_api_url

OUT_DIR = Path(__file__).resolve().parent

SPECS = {
    "fina_indicator": (
        "ts_code,ann_date,end_date,update_flag,roe_waa,roe_dt,debt_to_assets,eps",
        ["ts_code", "end_date"],
    ),
    "income": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,n_income_attr_p,basic_eps",
        ["ts_code", "end_date", "report_type"],
    ),
    "balancesheet": (
        "ts_code,ann_date,end_date,report_type,update_flag,total_cur_assets,total_hldr_eqy_exc_min_int",
        ["ts_code", "end_date", "report_type"],
    ),
}

SAMPLE_SIZE = 12
START, END = "20250101", "20251231"


def call(api, tries=3, pause=3, gap=25, **params):
    last = ""
    for attempt in range(1, tries + 1):
        try:
            frame = getattr(pro, api)(**params)
            time.sleep(pause)
            return frame
        except Exception as error:  # noqa: BLE001
            last = f"{type(error).__name__}: {str(error)[:80]}"
            if attempt < tries:
                time.sleep(gap)
    print(f"[FAIL ] {api} {params.get('ts_code')} -> {last}")
    return None


basic = call("stock_basic", list_status="L", fields="ts_code,name")
codes = []
if basic is not None and len(basic):
    step = max(1, len(basic) // SAMPLE_SIZE)
    codes = basic["ts_code"].tolist()[::step][:SAMPLE_SIZE]
print(f"样本 {len(codes)} 只: {codes}")

collected = {api: [] for api in SPECS}
for index, code in enumerate(codes, start=1):
    for api, (fields, _) in SPECS.items():
        frame = call(api, ts_code=code, start_date=START, end_date=END, fields=fields)
        if frame is not None and len(frame):
            collected[api].append(frame)
    if index % 4 == 0:
        print(f"  已完成 {index}/{len(codes)} 只")

summary = []
for api, (_, keys) in SPECS.items():
    parts = collected[api]
    if not parts:
        print(f"\n=== {api}: 无数据 ===")
        continue
    frame = pd.concat(parts, ignore_index=True)
    frame.to_csv(OUT_DIR / f"probe13_{api}.csv", index=False, encoding="utf-8-sig")

    sizes = frame.groupby(keys).size()
    single = sizes[sizes == 1].index
    multi = sizes[sizes > 1].index
    single_rows = frame.set_index(keys).loc[single].reset_index()
    multi_rows = frame.set_index(keys).loc[multi].reset_index()

    print(f"\n=== {api} ===")
    print(f"  总行数 {len(frame)}，唯一键 {len(sizes)} 组（单行 {len(single)} 组，多行 {len(multi)} 组）")
    print(f"  **单行报告期的 update_flag 分布（多数派 = 当前版本的标记）: "
          f"{single_rows['update_flag'].value_counts().to_dict()}**")

    composition = {}
    value_diff = []
    value_cols = [c for c in frame.columns if c not in [*keys, "ann_date", "f_ann_date", "update_flag"]]
    for key, group in multi_rows.groupby(keys):
        flags = tuple(sorted(str(f) for f in group["update_flag"]))
        composition[flags] = composition.get(flags, 0) + 1
        if len(group[value_cols].drop_duplicates()) > 1:
            value_diff.append(group)
    print(f"  多行组的 flag 组合分布: {composition}")
    print(f"  多行组中两行数值不同的: {len(value_diff)} 组")
    for group in value_diff:
        print("  --- 数值不同的样本（判断哪行是修正后）---")
        print(group.to_string(index=False, max_colwidth=20))

    summary.append((api, single_rows["update_flag"].value_counts().to_dict(), composition))

print("\n=== 汇总 ===")
for api, single_dist, composition in summary:
    print(f"  {api:<16} 单行期 flag 分布 {single_dist}  多行组组合 {composition}")
print("\n探针结束。CSV 证据: probe13_fina_indicator.csv / probe13_income.csv / probe13_balancesheet.csv")
