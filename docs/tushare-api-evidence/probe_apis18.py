"""第 1 集研究用探针（第十八轮）：suspend_d 的取数形态与回填成本。

业主决策所需的确切数字，本轮要问：
 1) 区间形态（只给 start_date/end_date，不给 trade_date/ts_code）能不能用？
 2) 区间形态一次能返回多少行，会不会在官方 5000 行上限处被静默截断？
 3) 11 年回填要多少次调用、总共多少行（= 入库多少行）？
 4) 一次调用是否同时返回 S 与 R？suspend_type 有哪些取值？
 5) 单次调用耗时多少？
 6) 2015 年停牌潮会不会让某一年超过 5000 行上限？

已知基线（评估报告 4.1 节实测）：trade_date=20261016 不带 suspend_type → 11 行，
带 suspend_type='S' → 10 行。那 1 行非 S 的取值本轮一并查明。

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

FIELDS = "ts_code,trade_date,suspend_timing,suspend_type"
LIMIT = 5000

results = []


def call(label, tries=3, pause=5, gap=30, **params):
    last = ""
    for attempt in range(1, tries + 1):
        started = time.monotonic()
        try:
            frame = pro.suspend_d(**params)
            elapsed = time.monotonic() - started
            time.sleep(pause)
            rows = 0 if frame is None else len(frame)
            flag = "   <== 恰好等于官方上限，疑似截断" if rows == LIMIT else ""
            print(f"[OK   ] {label:<48} -> {rows:>6} 行  {elapsed:>5.1f}s{flag}")
            results.append((label, rows, elapsed))
            return frame
        except Exception as error:  # noqa: BLE001
            last = f"{type(error).__name__}: {str(error)[:85]}"
            if attempt < tries:
                time.sleep(gap)
    print(f"[FAIL ] {label:<48} -> {last}")
    results.append((label, None, None))
    return None


print("=== 健康检查 ===")
for attempt in range(1, 6):
    try:
        pro.trade_cal(
            exchange="SSE", start_date="20261001", end_date="20261010", fields="cal_date,is_open"
        )
        print("        健康检查成功")
        break
    except Exception as error:  # noqa: BLE001
        print(f"        健康检查失败：{str(error)[:60]}")
        time.sleep(45)

print("\n=== 1) 基线：按日形态（对照，评估报告实测 11 行 / 10 行）===")
day = call("trade_date=20261016（不带 suspend_type）", trade_date="20261016", fields=FIELDS)
if day is not None and len(day):
    print(f"        suspend_type 取值：{day['suspend_type'].value_counts().to_dict()}")
call("trade_date=20261016, suspend_type=S", trade_date="20261016", suspend_type="S", fields=FIELDS)
call("trade_date=20261016, suspend_type=R", trade_date="20261016", suspend_type="R", fields=FIELDS)

print("\n=== 2) 区间形态：11 年一次调用（能否一次拿全 + 是否截断）===")
whole = call("start=20151018, end=20261016（11 年）", start_date="20151018", end_date="20261016", fields=FIELDS)
if whole is not None and len(whole):
    print(f"        suspend_type 取值：{whole['suspend_type'].value_counts().to_dict()}")
    print(f"        最早 {whole['trade_date'].min()}，最晚 {whole['trade_date'].max()}")
    print(f"        覆盖交易日数：{whole['trade_date'].nunique()}")

print("\n=== 3) 区间形态：逐年（给出确切的行数分布与总行数）===")
YEARS = [
    "2015", "2016", "2017", "2018", "2019", "2020",
    "2021", "2022", "2023", "2024", "2025", "2026",
]
yearly = {}
for year in YEARS:
    start = "20151018" if year == "2015" else f"{year}0101"
    end = "20261016" if year == "2026" else f"{year}1231"
    frame = call(f"start={start}, end={end}", start_date=start, end_date=end, fields=FIELDS)
    yearly[year] = 0 if frame is None else len(frame)

print("\n=== 4) suspend_type 拆分对照（2025 年：S + R 是否等于全部）===")
call("2025 仅 S", start_date="20250101", end_date="20251231", suspend_type="S", fields=FIELDS)
call("2025 仅 R", start_date="20250101", end_date="20251231", suspend_type="R", fields=FIELDS)

print("\n=== 汇总 ===")
ok = [row for row in results if row[1] is not None]
failed = [row for row in results if row[1] is None]
print(f"  成功 {len(ok)} 次，失败 {len(failed)} 次")
print(f"  成功调用返回的总行数：{sum(row[1] for row in ok)}")
print(f"  成功调用耗时合计：{sum(row[2] for row in ok):.1f} 秒")
if failed:
    print("  失败的调用：")
    for label, _, _ in failed:
        print(f"    {label}")
print("\n  逐年行数（2015 自 20151018 起，2026 至 20261016 止）：")
for year in YEARS:
    print(f"    {year}: {yearly[year]:>6} 行")
print(f"  逐年合计：{sum(yearly.values())} 行")
print("\n探针结束。")
