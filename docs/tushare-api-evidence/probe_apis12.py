"""第 1 集研究用探针（第十二轮）：判定 update_flag 的语义，并确认多版本的主键形状。

背景：官方文档只说 update_flag 是"更新标识"，未给取值含义；官方样例（600000.SH）本身就有
同一报告期的重复行。第十一轮实测 fina_indicator 600519 的 20250331 出现 update_flag=1 与 0 两行。

判定方法（不需要外部对账）：
 若同一报告期存在两行且 ann_date 不同，则"较晚公告日"那行应为修正后的最新版本。
 统计"较晚公告日"那行带的是 update_flag=1 还是 0，即可判定语义。

顺带确认：
 - 同一 (ts_code, end_date) 是否在两行数值不同时仍能用 update_flag 区分（决定主键是否必须含它）
 - income / balancesheet 是否也出现同报告期多行

只读，不写任何文件。
"""

import sys
import time
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "screener").is_dir())
sys.path.insert(0, str(ROOT))

import tushare as ts

from screener.config import load_settings

cfg = load_settings()
pro = ts.pro_api(cfg.tushare_token, timeout=120)
pro._DataApi__http_url = cfg.tushare_api_url

FINA_FIELDS = "ts_code,ann_date,end_date,update_flag,roe_waa,roe_dt,debt_to_assets,eps"
INCOME_FIELDS = "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,n_income_attr_p,basic_eps"
BALANCE_FIELDS = "ts_code,ann_date,end_date,report_type,update_flag,total_cur_assets,total_hldr_eqy_exc_min_int"

SAMPLE_SIZE = 25
START, END = "20250101", "20251231"

frames = {"fina_indicator": [], "income": [], "balancesheet": []}


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


print("=== 取样本股票（等距抽样，避免集中在同一板块）===")
basic = call("stock_basic", list_status="L", fields="ts_code,name")
codes = []
if basic is not None and len(basic):
    step = max(1, len(basic) // SAMPLE_SIZE)
    codes = basic["ts_code"].tolist()[::step][:SAMPLE_SIZE]
print(f"        样本 {len(codes)} 只: {codes[:6]} ...")

print(f"\n=== 逐股取 {START}-{END} 的财务数据 ===")
for index, code in enumerate(codes, start=1):
    for api, fields in (
        ("fina_indicator", FINA_FIELDS),
        ("income", INCOME_FIELDS),
        ("balancesheet", BALANCE_FIELDS),
    ):
        frame = call(api, ts_code=code, start_date=START, end_date=END, fields=fields)
        if frame is not None and len(frame):
            frames[api].append(frame)
    if index % 5 == 0:
        print(f"        已完成 {index}/{len(codes)} 只")


def analyse(api, keys):
    print(f"\n=== {api}：同一报告期的多版本 ===")
    parts = frames[api]
    if not parts:
        print("        无数据")
        return
    import pandas as pd

    frame = pd.concat(parts, ignore_index=True)
    print(f"        总行数 {len(frame)}，涉及股票 {frame['ts_code'].nunique()} 只")
    counts = frame.groupby(keys).size()
    multi_keys = counts[counts > 1].index
    print(f"        唯一键 {len(counts)} 组，出现多行的 {len(multi_keys)} 组")
    if not len(multi_keys):
        return
    subset = frame.set_index(keys).loc[multi_keys].reset_index()
    later_flag_counts = {"1": 0, "0": 0}
    same_ann_date = 0
    differing_values = 0
    value_cols = [c for c in subset.columns if c not in [*keys, "ann_date", "f_ann_date", "update_flag"]]
    for key, group in subset.groupby(keys):
        if group["ann_date"].nunique() > 1:
            latest = group.sort_values("ann_date").iloc[-1]
            later_flag_counts[str(latest["update_flag"])] = later_flag_counts.get(str(latest["update_flag"]), 0) + 1
        else:
            same_ann_date += 1
        if len(group[value_cols].drop_duplicates()) > 1:
            differing_values += 1
    print(f"        多行组中：ann_date 不同 {len(multi_keys) - same_ann_date} 组，ann_date 相同 {same_ann_date} 组")
    print(f"        **较晚公告日那行的 update_flag 分布: {later_flag_counts}**")
    print(f"        两行数值不同的组: {differing_values}")
    print(f"        update_flag 总分布: {frame['update_flag'].value_counts().to_dict()}")
    print("        样例（最多 4 组）:")
    for key in list(multi_keys)[:4]:
        group = subset.set_index(keys).loc[[key]].reset_index()
        print(group.to_string(index=False, max_colwidth=18))


analyse("fina_indicator", ["ts_code", "end_date"])
analyse("income", ["ts_code", "end_date", "report_type"])
analyse("balancesheet", ["ts_code", "end_date", "report_type"])

print("\n=== 判定说明 ===")
print("        若'较晚公告日'那行绝大多数为 update_flag=1，则 1=最新、0=被取代的旧版本。")
print("\n探针结束。")
