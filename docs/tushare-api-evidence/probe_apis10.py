"""第 1 集研究用探针（第十轮）：重问第九轮未答的问题。

第九轮（probe_out9.txt）全程被代理端 rate_limited / upstream_pool_exhausted 打回，未取得数据。
本轮先做健康检查，健康后才逐个提问，并加大重试间隔。

要回答的三个问题：
 1) 财报三表能否按 ann_date 取 —— 决定日常增量能否按公告日走（D8）
 2) 同一报告期是否出现多行 —— 决定是否必须保留多版本（D3）
 3) 区间查询的参数与长度限制 —— 决定一次更新要几次调用

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

INCOME_FIELDS = (
    "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,"
    "n_income_attr_p,total_revenue,revenue,basic_eps"
)
BALANCE_FIELDS = (
    "ts_code,ann_date,end_date,report_type,update_flag,"
    "total_cur_assets,total_cur_liab,total_ncl,total_hldr_eqy_exc_min_int,goodwill"
)
FINA_FIELDS = "ts_code,ann_date,end_date,update_flag,roe_waa,roe,roe_dt,debt_to_assets,roic,eps"

results = []


def call(label, api, tries=3, pause=8, gap=30, **params):
    """返回 (frame, 错误文本)。frame 为 None 表示失败。"""
    last = ""
    for attempt in range(1, tries + 1):
        try:
            frame = getattr(pro, api)(**params)
            time.sleep(pause)
            rows = 0 if frame is None else len(frame)
            print(f"[OK   ] {label:<46} -> {rows} 行")
            results.append((label, f"{rows} 行"))
            return frame
        except Exception as error:  # noqa: BLE001
            last = f"{type(error).__name__}: {str(error)[:100]}"
            if attempt < tries:
                print(f"[retry] {label:<46} -> {last[:70]}（等 {gap} 秒）")
                time.sleep(gap)
    print(f"[FAIL ] {label:<46} -> {last}")
    results.append((label, f"失败：{last}"))
    return None


print("=== 健康检查：代理端是否恢复 ===")
healthy = False
for attempt in range(1, 7):
    try:
        cal = pro.trade_cal(exchange="SSE", start_date="20261001", end_date="20261020", fields="cal_date,is_open")
        print(f"        第 {attempt} 次健康检查成功，返回 {len(cal)} 行")
        healthy = True
        break
    except Exception as error:  # noqa: BLE001
        print(f"        第 {attempt} 次健康检查失败：{str(error)[:70]}")
        time.sleep(45)

if not healthy:
    print("\n代理端仍不可用，本轮结束（不继续消耗调用）。")
    sys.exit(0)

print("\n=== 1) 财报三表能否按 ann_date 取（单日全市场）===")
income_day = call("income ann_date=20260430", "income", ann_date="20260430", fields=INCOME_FIELDS)
if income_day is None:
    income_day = call("income ann_date=20260428", "income", ann_date="20260428", fields=INCOME_FIELDS)
balance_day = call("balancesheet ann_date=20260430", "balancesheet", ann_date="20260430", fields=BALANCE_FIELDS)
fina_day = call("fina_indicator ann_date=20260430", "fina_indicator", ann_date="20260430", fields=FINA_FIELDS)

print("\n=== 2) 单只股票区间查询（已知可用，用作对照与多版本检查）===")
one = call(
    "income 600519.SH 20250101-20251231",
    "income",
    ts_code="600519.SH",
    start_date="20250101",
    end_date="20251231",
    fields=INCOME_FIELDS,
)

print("\n=== 3) 区间查询的参数要求与长度上限 ===")
call("income 区间无 ts_code 20260401-20260430", "income", start_date="20260401", end_date="20260430", fields=INCOME_FIELDS)
call("income 区间无 ts_code 20260430-20260430", "income", start_date="20260430", end_date="20260430", fields=INCOME_FIELDS)
call(
    "income 600519.SH 20240101-20241231（1 年）",
    "income",
    ts_code="600519.SH",
    start_date="20240101",
    end_date="20241231",
    fields=INCOME_FIELDS,
)
call(
    "income 600519.SH 20230101-20251231（3 年）",
    "income",
    ts_code="600519.SH",
    start_date="20230101",
    end_date="20251231",
    fields=INCOME_FIELDS,
)

print("\n=== 4) 同一报告期是否出现多行（D3 关键验证）===")


def duplicates(frame, keys, label):
    if frame is None or not len(frame):
        print(f"        {label}: 无数据")
        return
    counts = frame.groupby(keys).size()
    multi = counts[counts > 1]
    print(f"        {label}: 唯一键 {len(counts)} 组，出现多行的 {len(multi)} 组")
    if len(multi):
        sample_key = multi.index[0]
        idx = frame.set_index(keys).index
        mask = [key == sample_key for key in idx]
        print(f"        样例（{int(multi.max())} 行为最多）:")
        print(frame[mask].to_string(index=False, max_colwidth=22))


duplicates(income_day, ["ts_code", "end_date", "report_type"], "income 单公告日")
duplicates(balance_day, ["ts_code", "end_date", "report_type"], "balancesheet 单公告日")
duplicates(fina_day, ["ts_code", "end_date"], "fina_indicator 单公告日")
duplicates(one, ["ts_code", "end_date", "report_type"], "600519 区间")

if one is not None and len(one):
    print(f"        update_flag 取值: {one['update_flag'].value_counts().to_dict()}")
    print(f"        report_type 取值: {one['report_type'].value_counts().to_dict()}")
if income_day is not None and len(income_day):
    print(f"        单公告日 end_date 分布: {income_day['end_date'].value_counts().to_dict()}")
    print(f"        单公告日 update_flag 分布: {income_day['update_flag'].value_counts().to_dict()}")
    print(f"        单公告日涉及股票数: {income_day['ts_code'].nunique()}")

print("\n=== 汇总 ===")
for label, outcome in results:
    print(f"  {label:<46} {outcome}")
print("\n探针结束。")
