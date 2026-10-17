"""第 1 集研究用探针（第十七轮）：adj_factor 的历史起点与覆盖度。

第十六轮结果：
 - adj_factor 按 trade_date=20261016 → 5577 行（全市场完整）
 - adj_factor 逐股 11 年区间（20160101-20261231）→ 371 行（**不受 1 年上限约束**）
 - adj_factor 按 trade_date=20160104 → 2884 行，但 trade_date=20150105 → **只有 1 行**（异常，需确认起点）

本轮要问：
 1) adj_factor 逐股能回溯到哪一年（2016 起点够用吗？11 年起点是 2015-10）
 2) 2015 年内各交易日的覆盖度（1 行是数据缺口还是只有个别股票）
 3) 与 daily_basic 的覆盖起点是否一致

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

AF_FIELDS = "ts_code,trade_date,adj_factor"
DB_FIELDS = "ts_code,trade_date,close,total_mv"

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
            last = f"{type(error).__name__}: {str(error)[:85]}"
            if attempt < tries:
                time.sleep(gap)
    print(f"[FAIL ] {label:<50} -> {last}")
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

print("\n=== 1) adj_factor 逐股历史起点 ===")
long_frame = call("adj_factor 600519 20050101-20261231", "adj_factor", ts_code="600519.SH",
                  start_date="20050101", end_date="20261231", fields=AF_FIELDS)
if long_frame is not None and len(long_frame):
    print(f"        最早交易日: {long_frame['trade_date'].min()}，最晚: {long_frame['trade_date'].max()}")

print("\n=== 2) 2015-2016 各交易日的全市场覆盖度 ===")
for day in ("20150105", "20150630", "20151008", "20151231", "20160104", "20160630"):
    frame = call(f"adj_factor trade_date={day}", "adj_factor", trade_date=day, fields=AF_FIELDS)
    if frame is not None and len(frame) == 1:
        print(f"        {day} 只有 1 行，样例: {frame.to_dict('records')}")

print("\n=== 3) 对照：daily_basic 同期覆盖度 ===")
for day in ("20151008", "20160104"):
    call(f"daily_basic trade_date={day}", "daily_basic", trade_date=day, fields=DB_FIELDS)

print("\n=== 汇总 ===")
for label, outcome in results:
    print(f"  {label:<50} {outcome}")
print("\n探针结束。")
