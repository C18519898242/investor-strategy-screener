"""第 1 集研究用探针（第十一轮）：确定财报单次可取的时间跨度上限，以及"按公告日"路径的可用性。

第十轮实测（probe_out10.txt）：
 - income(ts_code=..., 1 年区间) 成功；3 年区间 -> date_range_too_large
 - income 不带 ts_code -> invalid_params（两次，含单日区间）
 - income/balancesheet/fina_indicator 按 ann_date -> 连续 9 次 upstream_pool_exhausted，同会话带 ts_code 的查询正常

本轮要问：
 1) 跨度上限到底在哪：2 年？18 个月？
 2) 带 ts_code 时能否再叠加 ann_date（逐股按公告日探测新披露）
 3) dividend 按 ann_date 是否仍然可用（与财报形成对照）
 4) fina_indicator 单股 1 年区间是否与 income 同规格

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

INCOME_FIELDS = "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,n_income_attr_p,basic_eps"
FINA_FIELDS = "ts_code,ann_date,end_date,update_flag,roe_waa,roe_dt,debt_to_assets,roic,eps"
DIV_FIELDS = "ts_code,end_date,ann_date,div_proc,cash_div_tax,base_share"

results = []


def call(label, api, tries=3, pause=8, gap=30, **params):
    last = ""
    for attempt in range(1, tries + 1):
        try:
            frame = getattr(pro, api)(**params)
            time.sleep(pause)
            rows = 0 if frame is None else len(frame)
            print(f"[OK   ] {label:<48} -> {rows} 行")
            results.append((label, f"{rows} 行"))
            return frame
        except Exception as error:  # noqa: BLE001
            last = f"{type(error).__name__}: {str(error)[:95]}"
            if attempt < tries:
                print(f"[retry] {label:<48} -> {last[:66]}（等 {gap} 秒）")
                time.sleep(gap)
    print(f"[FAIL ] {label:<48} -> {last}")
    results.append((label, f"失败：{last}"))
    return None


print("=== 健康检查 ===")
for attempt in range(1, 6):
    try:
        pro.trade_cal(exchange="SSE", start_date="20261001", end_date="20261010", fields="cal_date,is_open")
        print(f"        第 {attempt} 次健康检查成功")
        break
    except Exception as error:  # noqa: BLE001
        print(f"        第 {attempt} 次健康检查失败：{str(error)[:70]}")
        time.sleep(45)

print("\n=== 1) 跨度上限：单只股票 ===")
call("income 600519 18 个月 20240101-20250630", "income", ts_code="600519.SH",
     start_date="20240101", end_date="20250630", fields=INCOME_FIELDS)
call("income 600519 2 年 20230101-20241231", "income", ts_code="600519.SH",
     start_date="20230101", end_date="20241231", fields=INCOME_FIELDS)
call("income 600519 30 个月 20230101-20250630", "income", ts_code="600519.SH",
     start_date="20230101", end_date="20250630", fields=INCOME_FIELDS)

print("\n=== 2) 带 ts_code 时叠加 ann_date（逐股按公告日探测）===")
call("income 600519 ann_date=20260430", "income", ts_code="600519.SH",
     ann_date="20260430", fields=INCOME_FIELDS)
call("income 600519 ann_date=20250430", "income", ts_code="600519.SH",
     ann_date="20250430", fields=INCOME_FIELDS)

print("\n=== 3) dividend 按 ann_date（与财报对照）===")
call("dividend ann_date=20260430", "dividend", ann_date="20260430", fields=DIV_FIELDS)

print("\n=== 4) fina_indicator 单股 1 年区间 ===")
fina_one = call("fina_indicator 600519 20250101-20251231", "fina_indicator", ts_code="600519.SH",
                start_date="20250101", end_date="20251231", fields=FINA_FIELDS)
if fina_one is not None and len(fina_one):
    print(fina_one.to_string(index=False, max_colwidth=20))
    counts = fina_one.groupby(["ts_code", "end_date"]).size()
    print(f"        唯一 (ts_code, end_date) {len(counts)} 组，出现多行的 {int((counts > 1).sum())} 组")
    print(f"        update_flag 取值: {fina_one['update_flag'].value_counts().to_dict()}")

print("\n=== 汇总 ===")
for label, outcome in results:
    print(f"  {label:<48} {outcome}")
print("\n探针结束。")
