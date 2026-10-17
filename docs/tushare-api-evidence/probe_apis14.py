"""第 1 集研究用探针（第十四轮）：验证 B 方案 —— 用 disclosure_date 把财报日常增量压到分钟级。

思路：
  1 次调用 disclosure_date(actual_date=某日) 拿到当天实际披露财报的股票名单，
  再对名单里的股票逐股拉 income / balancesheet / fina_indicator（各 1 次）。
  若每天披露几十到几百只，则日常增量 = 1 + 3×N 次调用，分钟级完成。

本轮要问：
 1) 代理端是否支持 disclosure_date
 2) disclosure_date(actual_date=...) 能否给出当天披露的股票名单，规模多大
 3) 端到端：用该名单里的股票按 ann_date 拉三表是否成功
 4) 当季（20260930）披露计划的规模，用于估算披露季峰值

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

DISC_FIELDS = "ts_code,ann_date,end_date,pre_date,actual_date,modify_date"
INCOME_FIELDS = "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,n_income_attr_p,basic_eps"
BALANCE_FIELDS = "ts_code,ann_date,end_date,report_type,update_flag,total_cur_assets,total_hldr_eqy_exc_min_int"
FINA_FIELDS = "ts_code,ann_date,end_date,update_flag,roe_waa,roe_dt,debt_to_assets,eps"

results = []


def call(label, api, tries=3, pause=5, gap=30, **params):
    last = ""
    for attempt in range(1, tries + 1):
        try:
            frame = getattr(pro, api)(**params)
            time.sleep(pause)
            rows = 0 if frame is None else len(frame)
            print(f"[OK   ] {label:<50} -> {rows} 行")
            results.append((label, f"{rows} 行"))
            return frame
        except Exception as error:  # noqa: BLE001
            last = f"{type(error).__name__}: {str(error)[:95]}"
            if attempt < tries:
                print(f"[retry] {label:<50} -> {last[:60]}（等 {gap} 秒）")
                time.sleep(gap)
    print(f"[FAIL ] {label:<50} -> {last}")
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

print("\n=== 1) disclosure_date 是否可用（当期披露计划）===")
plan = call("disclosure_date end_date=20260930", "disclosure_date", end_date="20260930", fields=DISC_FIELDS)
if plan is not None and len(plan):
    print(f"        当期披露计划行数: {len(plan)}，涉及股票 {plan['ts_code'].nunique()} 只")
    print(f"        actual_date 已填写的: {plan['actual_date'].notna().sum()} 行")
    filled = plan[plan["actual_date"].notna()]
    if len(filled):
        print(f"        实际披露日分布（前 10）: {filled['actual_date'].value_counts().head(10).to_dict()}")

print("\n=== 2) 按实际披露日取当天名单（增量入口）===")
day_list = None
for day in ("20261016", "20261015", "20261014", "20261013"):
    frame = call(f"disclosure_date actual_date={day}", "disclosure_date", actual_date=day, fields=DISC_FIELDS)
    if frame is not None and len(frame):
        day_list = frame
        print(f"        使用 {day} 作为样本日：当天披露 {len(frame)} 只")
        break
    print(f"        {day} 无数据，换前一天")

print("\n=== 3) 端到端：对名单里的股票按公告日拉三表 ===")
if day_list is not None and len(day_list):
    sample_codes = day_list["ts_code"].head(3).tolist()
    print(f"        样本股票: {sample_codes}")
    for code in sample_codes:
        call(f"income {code}", "income", ts_code=code, start_date="20260101", end_date="20261231",
             fields=INCOME_FIELDS)
        call(f"balancesheet {code}", "balancesheet", ts_code=code, start_date="20260101",
             end_date="20261231", fields=BALANCE_FIELDS)
        call(f"fina_indicator {code}", "fina_indicator", ts_code=code, start_date="20260101",
             end_date="20261231", fields=FINA_FIELDS)

print("\n=== 4) 增量成本估算 ===")
if day_list is not None and len(day_list):
    n = len(day_list)
    print(f"        当天披露 {n} 只 -> 日常增量调用数 = 1 + 3×{n} = {1 + 3 * n} 次")
    print(f"        按 200 次/分钟约 {round((1 + 3 * n) / 200, 1)} 分钟")
if plan is not None and len(plan):
    peak = plan["actual_date"].value_counts()
    if len(peak):
        print(f"        当期峰值单日披露 {int(peak.max())} 只 -> 峰值增量约 {1 + 3 * int(peak.max())} 次调用")
    print(f"        全量回填仍是 5577×3×11 次（与是否用本接口无关）")

print("\n=== 汇总 ===")
for label, outcome in results:
    print(f"  {label:<50} {outcome}")
print("\n探针结束。")
