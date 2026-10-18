"""第 1 集研究用探针（第十九轮）：suspend_d 按季度分段的真实回填成本。

第十八轮结果（已推翻按年分段的可行性）：
 - 区间形态可用，但 11 年一次调用报 `date_range_too_large`（区间有安全上限，文档未写）。
 - 按年分段时 2015-2023 连续 9 年都返回**恰好 5000 行**（官方上限），即被截断。
 - 2024 年 3740 行、2025 年 4045 行、2026 年（至 20261016）2739 行 —— 这三年低于上限，可信。
 - 一次调用同时返回 S 与 R：20261016 → {'S': 10, 'R': 1}；2025 全年 S=3473 + R=572 = 4045。

本轮要问（业主决策所需的最终数字）：
 1) 按季度分段能否全部避开 5000 行截断？
 2) 11 年回填总共多少行（= 入库多少行）？
 3) 需要多少次调用？
 4) 单次调用耗时与总耗时？
 5) 有没有哪个季度仍然截断（尤其 2015 年停牌潮）？

遇到返回恰好 5000 行的季度，自动下钻到该季度的 3 个月，用月合计替代。
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
START = "20151018"
END = "20261016"

results = []


def call(label, tries=3, pause=3, gap=30, **params):
    last = ""
    for attempt in range(1, tries + 1):
        started = time.monotonic()
        try:
            frame = pro.suspend_d(**params)
            elapsed = time.monotonic() - started
            time.sleep(pause)
            rows = 0 if frame is None else len(frame)
            print(f"[OK   ] {label:<32} -> {rows:>6} 行  {elapsed:>5.1f}s", flush=True)
            results.append((label, rows, elapsed))
            return rows
        except Exception as error:  # noqa: BLE001
            last = f"{type(error).__name__}: {str(error)[:80]}"
            if attempt < tries:
                time.sleep(gap)
    print(f"[FAIL ] {label:<32} -> {last}", flush=True)
    results.append((label, None, None))
    return None


def months_of(quarter_start, quarter_end):
    """把季度拆成 3 个月区间（各自截到 11 年窗口内）。"""
    year = int(quarter_start[:4])
    first_month = int(quarter_start[4:6])
    chunks = []
    for offset in range(3):
        month = first_month + offset
        start = f"{year}{month:02d}01"
        last_day = (31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]
        end = f"{year}{month:02d}{last_day:02d}"
        chunks.append((max(start, START), min(end, END)))
    return chunks


print("=== 健康检查 ===", flush=True)
for attempt in range(1, 6):
    try:
        pro.trade_cal(
            exchange="SSE", start_date="20261001", end_date="20261010", fields="cal_date,is_open"
        )
        print("        健康检查成功", flush=True)
        break
    except Exception as error:  # noqa: BLE001
        print(f"        健康检查失败：{str(error)[:60]}", flush=True)
        time.sleep(45)

print(f"\n=== 按季度分段：{START} ～ {END} ===", flush=True)
quarters = []
for year in range(2015, 2027):
    for quarter in range(1, 5):
        first_month = (quarter - 1) * 3 + 1
        start = f"{year}{first_month:02d}01"
        last_month = first_month + 2
        last_day = (31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[last_month - 1]
        end = f"{year}{last_month:02d}{last_day:02d}"
        start, end = max(start, START), min(end, END)
        if start <= end:
            quarters.append((start, end))

truncated_quarters = []
total_rows = 0
for start, end in quarters:
    rows = call(f"{start}-{end}", start_date=start, end_date=end, fields=FIELDS)
    if rows is None:
        continue
    if rows == LIMIT:
        truncated_quarters.append((start, end))
        print("        ^ 触到 5000 行上限，下钻到月", flush=True)
        monthly = []
        for month_start, month_end in months_of(start, end):
            month_rows = call(f"  月 {month_start}-{month_end}", start_date=month_start, end_date=month_end, fields=FIELDS)
            if month_rows == LIMIT:
                print("        ^ 该月仍触顶！", flush=True)
            monthly.append(0 if month_rows is None else month_rows)
        print(f"        月合计：{sum(monthly)} 行（季度调用只看到 {rows} 行）", flush=True)
        total_rows += sum(monthly)
    else:
        total_rows += rows

print("\n=== 汇总 ===", flush=True)
ok = [row for row in results if row[1] is not None]
failed = [row for row in results if row[1] is None]
print(f"  成功 {len(ok)} 次，失败 {len(failed)} 次")
print(f"  11 年回填总行数（触顶季度已下钻到月）：{total_rows} 行")
print(f"  总耗时：{sum(row[2] for row in ok):.1f} 秒")
print(f"  季度区间数：{len(quarters)}")
if truncated_quarters:
    print(f"  触顶的季度（{len(truncated_quarters)} 个）：")
    for start, end in truncated_quarters:
        print(f"    {start}-{end}")
else:
    print("  没有任何季度触到 5000 行上限。")
if failed:
    print("  失败的调用：")
    for label, _, _ in failed:
        print(f"    {label}")
print("\n探针结束。", flush=True)
