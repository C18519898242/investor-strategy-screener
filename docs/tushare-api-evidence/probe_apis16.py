"""第 1 集研究用探针（第十六轮）：补测 D9 的两张"已规划"表 —— cashflow 与 adj_factor。

要问的问题：
 1) 代理端是否支持 cashflow / adj_factor
 2) cashflow 是否也受"单次区间上限 1 年"约束（决定首次回填按年分段）
 3) cashflow 是否支持按 ann_date 取全市场（若支持，增量更便宜）；不带 ts_code 是否 invalid_params
 4) adj_factor 能否按 trade_date 取全市场（按日拉 11 年约 2700 次）
 5) adj_factor 是否支持长区间（若支持，逐股 1 次取 11 年）
 6) adj_factor 的历史能回溯到哪一年（11 年起点 = 2015 年）
 7) cashflow 的资本支出字段名（c_pay_acq_const_fiolta）与 free_cashflow 是否存在

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

CF_FIELDS = "ts_code,ann_date,end_date,report_type,update_flag,n_cashflow_act,c_pay_acq_const_fiolta"
CF_FIELDS_FREE = "ts_code,end_date,n_cashflow_act,c_pay_acq_const_fiolta,free_cashflow"
AF_FIELDS = "ts_code,trade_date,adj_factor"

results = []


def call(label, api, tries=3, pause=5, gap=30, **params):
    last = ""
    for attempt in range(1, tries + 1):
        try:
            frame = getattr(pro, api)(**params)
            time.sleep(pause)
            rows = 0 if frame is None else len(frame)
            print(f"[OK   ] {label:<52} -> {rows} 行")
            results.append((label, f"{rows} 行"))
            return frame
        except Exception as error:  # noqa: BLE001
            last = f"{type(error).__name__}: {str(error)[:90]}"
            if attempt < tries:
                print(f"[retry] {label:<52} -> {last[:58]}（等 {gap} 秒）")
                time.sleep(gap)
    print(f"[FAIL ] {label:<52} -> {last}")
    results.append((label, f"失败：{last}"))
    return None


print("=== 健康检查 ===")
for attempt in range(1, 6):
    try:
        pro.trade_cal(exchange="SSE", start_date="20261001", end_date="20261010", fields="cal_date,is_open")
        print("        健康检查成功")
        break
    except Exception as error:  # noqa: BLE001
        print(f"        健康检查失败：{str(error)[:60]}")
        time.sleep(45)

print("\n=== 1) cashflow 基本可用性与区间上限 ===")
cf_one = call("cashflow 600519 1 年 20250101-20251231", "cashflow", ts_code="600519.SH",
              start_date="20250101", end_date="20251231", fields=CF_FIELDS)
if cf_one is not None and len(cf_one):
    print(cf_one.head(5).to_string(index=False, max_colwidth=18))
call("cashflow 600519 2 年 20240101-20251231", "cashflow", ts_code="600519.SH",
     start_date="20240101", end_date="20251231", fields=CF_FIELDS)
call("cashflow 600519 11 年 20160101-20261231", "cashflow", ts_code="600519.SH",
     start_date="20160101", end_date="20261231", fields=CF_FIELDS)

print("\n=== 2) cashflow 是否支持按公告日取全市场 / 不带 ts_code ===")
call("cashflow ann_date=20260430（全市场）", "cashflow", ann_date="20260430", fields=CF_FIELDS)
call("cashflow 区间不带 ts_code", "cashflow", start_date="20261001", end_date="20261016", fields=CF_FIELDS)

print("\n=== 3) cashflow 的资本支出 / 自由现金流字段是否存在 ===")
call("cashflow 600519 period=20241231 含 free_cashflow", "cashflow", ts_code="600519.SH",
     period="20241231", fields=CF_FIELDS_FREE)

print("\n=== 4) adj_factor 按交易日取全市场 ===")
af_day = call("adj_factor trade_date=20261016（全市场）", "adj_factor", trade_date="20261016", fields=AF_FIELDS)
if af_day is not None and len(af_day):
    print(af_day.head(3).to_string(index=False))

print("\n=== 5) adj_factor 区间能力与历史深度 ===")
call("adj_factor 600519 11 年 20160101-20261231", "adj_factor", ts_code="600519.SH",
     start_date="20160101", end_date="20261231", fields=AF_FIELDS)
call("adj_factor 600519 trade_date=20261016", "adj_factor", ts_code="600519.SH",
     trade_date="20261016", fields=AF_FIELDS)
call("adj_factor 全市场 trade_date=20160104（历史深度）", "adj_factor", trade_date="20160104", fields=AF_FIELDS)
call("adj_factor 全市场 trade_date=20150105（更早）", "adj_factor", trade_date="20150105", fields=AF_FIELDS)

print("\n=== 汇总 ===")
for label, outcome in results:
    print(f"  {label:<52} {outcome}")
print("\n探针结束。")
