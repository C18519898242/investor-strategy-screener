"""第 1 集研究用探针（第二十轮）：定案 27 天分片的安全性、重复键、0 行响应。

第十九轮留下的缺口（代码评审 2026-10-18 指出）：
 - 已测过的"不截断"样本**全是自然月**（14/30/31 天，最大 7673 行），
   "截断"样本全是 ≥75 天的季度。**从未发出过一个 27 天区间**。
 - 于是"阈值是 31 天"与"阈值是自然月边界"两种机制与现有证据完全等价，
   而生产分片绝大多数会跨自然月（水位是任意交易日）。

本轮要问：
 1) 27 天区间（含跨月与月内）到底会不会被截断到 5000 行？
 2) 若触顶，阈值落在哪一段？（按需二分）
 3) 上游有没有重复的 (ts_code, trade_date, suspend_type)？suspend_timing 是否参与区分？
 4) 0 行响应回来时，DataFrame 的列还在不在（`frame[list(COLUMNS)]` 会不会 KeyError）？

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
KEY = ["ts_code", "trade_date", "suspend_type"]

calls = 0


def call(label, tries=3, pause=3, gap=30, **params):
    """返回 DataFrame（失败返回 None）；同时打印行数与耗时。"""
    global calls
    last = ""
    for attempt in range(1, tries + 1):
        started = time.monotonic()
        calls += 1
        try:
            frame = pro.suspend_d(**params)
            elapsed = time.monotonic() - started
            time.sleep(pause)
            rows = 0 if frame is None else len(frame)
            flag = "  <== 恰好 5000，触顶" if rows == LIMIT else ""
            print(f"[OK   ] {label:<34} -> {rows:>6} 行  {elapsed:>5.1f}s{flag}", flush=True)
            return frame
        except Exception as error:  # noqa: BLE001
            last = f"{type(error).__name__}: {str(error)[:80]}"
            if attempt < tries:
                time.sleep(gap)
    print(f"[FAIL ] {label:<34} -> {last}", flush=True)
    return None


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

# ---------------------------------------------------------------- 1) 27 天区间
print("\n=== 1) 27 天区间：跨月与月内各测几个 ===", flush=True)
print("    对照：2015-11 整月 30 天 7673 行、2016-03 整月 31 天 6656 行（第十九轮实测）", flush=True)

WINDOWS = [
    ("月内 20151101-20151127", "20151101", "20151127", 6900),
    ("跨月 20151110-20151206", "20151110", "20151206", 6000),
    ("月内 20160301-20160327", "20160301", "20160327", 5800),
    ("跨月 20160315-20160410", "20160315", "20160410", 5800),
    ("跨月 20160920-20161016", "20160920", "20161016", 4700),
]
truncated = []
for label, start, end, expected in WINDOWS:
    frame = call(label, start_date=start, end_date=end, fields=FIELDS)
    if frame is None:
        continue
    rows = len(frame)
    if rows == LIMIT:
        truncated.append((label, start, end))
    else:
        print(f"        期望约 {expected} 行 -> 实际 {rows} 行，未触顶", flush=True)

# 若触顶，二分定位阈值（按天数找最大安全段长）
if truncated:
    print("\n=== 2) 27 天触顶，二分定位安全段长（从 20151101 起算）===", flush=True)
    base_year, base_month, base_day = 2015, 11, 1
    import datetime as dt

    base = dt.date(base_year, base_month, base_day)

    def rows_for(days):
        end = (base + dt.timedelta(days=days - 1)).strftime("%Y%m%d")
        frame = call(f"    {days:>2} 天 20151101-{end}", start_date="20151101", end_date=end, fields=FIELDS)
        return None if frame is None else len(frame)

    for days in (20, 24, 28, 31, 35, 45):
        got = rows_for(days)
        verdict = "触顶" if got == LIMIT else ("未触顶" if got is not None else "失败")
        print(f"        {days} 天 -> {got} 行（{verdict}）", flush=True)
else:
    print("\n    五个 27 天窗口都没触顶。", flush=True)

# ---------------------------------------------------------------- 3) 重复键
print("\n=== 3) 重复键：高密度月 + 基线日 ===", flush=True)
DUP_RANGES = [
    ("20151101-20151130", "20151101", "20151130"),
    ("20160301-20160331", "20160301", "20160331"),
    ("20160401-20160430", "20160401", "20160430"),
]
for label, start, end in DUP_RANGES:
    frame = call(f"重复键检查 {label}", start_date=start, end_date=end, fields=FIELDS)
    if frame is None:
        continue
    dup_key = frame.duplicated(subset=KEY).sum()
    dup_all = frame.duplicated(subset=[*KEY, "suspend_timing"]).sum()
    print(f"        重复 (ts_code,trade_date,suspend_type)：{dup_key} 行", flush=True)
    print(f"        重复（再含 suspend_timing）：{dup_all} 行", flush=True)
    if dup_key:
        shown = frame[frame.duplicated(subset=KEY, keep=False)].sort_values(KEY)
        print(f"        样例（最多 5 行）：\n{shown.head(5).to_string(index=False)}", flush=True)

frame = call("重复键检查 20261016", trade_date="20261016", fields=FIELDS)
if frame is not None:
    print(f"        20261016 重复 (ts_code,trade_date,suspend_type)：{frame.duplicated(subset=KEY).sum()} 行", flush=True)

# ---------------------------------------------------------------- 4) 0 行响应
print("\n=== 4) 0 行响应：列还在不在 ===", flush=True)
for trade_date in ("20261017", "20261018"):
    frame = pro.suspend_d(trade_date=trade_date, fields=FIELDS)
    calls += 1
    time.sleep(3)
    rows = 0 if frame is None else len(frame)
    columns = [] if frame is None else list(frame.columns)
    print(f"    trade_date={trade_date} -> {rows} 行；columns={columns}", flush=True)
    missing = [name for name in FIELDS.split(",") if name not in columns]
    print(f"        缺失的列：{missing if missing else '无'}", flush=True)
    if missing:
        print("        ^ 空响应列不全会让 frame[list(COLUMNS)] 抛 KeyError", flush=True)

print(f"\n=== 汇总 ===\n  调用次数：{calls}\n探针结束。", flush=True)
