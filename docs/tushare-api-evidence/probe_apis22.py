"""第 1 集研究用探针（第二十二轮）：`daily_basic` 按 `trade_date` 取全市场。

要回答的问题（用户提出）：
  1. **只给 `trade_date`**（不给 `ts_code`、不给 `start_date`/`end_date`）能不能拿到一整天的数据？
  2. **是不是返回全部一天的股票** —— 和当日上市名单、当日停牌名单对得上吗？

顺带量清楚三件对设计有决定性的事：
  3. 单日调用耗时 —— 用来估 2700 次回填到底要多久。
  4. 默认返回多少列。
  5. 多天窗口会不会被静默截断到 6000 行 —— 这一条直接决定「一个交易日一次调用」是不是必须的。

基线日 20261016 取自评估文档：同日 `daily_basic` 实测 5567 行、`adj_factor` 5577 行（差 10 只
即当日停牌数）。本轮复用这一天，数字可以直接对比。

只读，不写任何文件。
"""

import sys
import time
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "screener").is_dir())
sys.path.insert(0, str(ROOT))

import tushare as ts

from screener.config import load_settings

DAY = "20261016"
WINDOW_START = "20261012"
WINDOW_END = "20261016"


def call(label, function, **kwargs):
    """调一次接口，打印行数/列数/耗时；失败重试 3 次。返回 (frame, 耗时)。"""
    for attempt in range(1, 4):
        started = time.monotonic()
        try:
            frame = function(**kwargs)
        except Exception as error:  # noqa: BLE001
            print(f"    {label} 第 {attempt} 次失败：{str(error)[:80]}", flush=True)
            time.sleep(20)
            continue
        elapsed = time.monotonic() - started
        rows = 0 if frame is None else len(frame)
        columns = [] if frame is None else list(frame.columns)
        print(f"    {label} -> {rows} 行，{len(columns)} 列，耗时 {elapsed:.1f} 秒", flush=True)
        print(f"        columns = {columns}", flush=True)
        return frame, elapsed
    return None, None


cfg = load_settings()
pro = ts.pro_api(cfg.tushare_token, timeout=120)
pro._DataApi__http_url = cfg.tushare_api_url

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

print(f"\n=== 1. 只给 trade_date={DAY}（不给 ts_code、不给 start/end）===", flush=True)
day_frame, day_seconds = call("daily_basic(trade_date=…)", pro.daily_basic, trade_date=DAY)
time.sleep(4)

print("\n=== 2. 当日上市名单与停牌名单 ===", flush=True)
listing, _ = call(
    "stock_basic(list_status=L)", pro.stock_basic, exchange="", list_status="L",
    fields="ts_code,name",
)
time.sleep(4)
suspended, _ = call(
    "suspend_d(trade_date=…)", pro.suspend_d, trade_date=DAY, fields="ts_code,trade_date,suspend_type"
)
time.sleep(4)

if day_frame is not None and listing is not None:
    got = set(day_frame["ts_code"])
    listed = set(listing["ts_code"])
    suspended_codes = set() if suspended is None else set(suspended["ts_code"])
    missing = sorted(listed - got)
    print("\n=== 3. 覆盖率：是不是全部一天的股票 ===", flush=True)
    print(f"    daily_basic 返回      {len(got)} 只", flush=True)
    print(f"    当日上市（list_status=L）{len(listed)} 只", flush=True)
    print(f"    当日停牌（suspend_d）  {len(suspended_codes)} 只", flush=True)
    print(f"    上市有、daily_basic 没有：{len(missing)} 只", flush=True)
    print(f"        其中在停牌名单里：{len(set(missing) & suspended_codes)} 只", flush=True)
    print(f"        不在停牌名单里（前 20）：{missing[:20]}", flush=True)
    print(f"    daily_basic 有、上市名单没有：{len(got - listed)} 只", flush=True)
    print(
        f"    (ts_code, trade_date) 重复行："
        f"{day_frame.duplicated(subset=['ts_code', 'trade_date']).sum()}",
        flush=True,
    )
    if missing and not (set(missing) - suspended_codes):
        print("    ^ 缺的恰好全是停牌股：返回的就是当日全部有行情的股票", flush=True)

print(f"\n=== 4. 多天窗口 {WINDOW_START}~{WINDOW_END}（默认字段）===", flush=True)
time.sleep(4)
window_frame, _ = call(
    "daily_basic(start_date/end_date)", pro.daily_basic,
    start_date=WINDOW_START, end_date=WINDOW_END,
)
if window_frame is not None:
    days = sorted(set(window_frame["trade_date"]))
    print(f"    覆盖交易日 {len(days)} 天：{days}", flush=True)
    for day in days:
        print(f"        {day}: {(window_frame['trade_date'] == day).sum()} 行", flush=True)
    print(
        f"    (ts_code, trade_date) 重复行："
        f"{window_frame.duplicated(subset=['ts_code', 'trade_date']).sum()}",
        flush=True,
    )
    if len(window_frame) == 6000:
        print("    ^ 正好 6000 行：多天窗口被静默截断，必须一个交易日一次调用", flush=True)
    else:
        print(f"    ^ {len(window_frame)} 行，未见截断", flush=True)

print("\n=== 5. 耗时汇总 ===", flush=True)
if day_seconds is not None:
    print(f"    单日一次调用 {day_seconds:.1f} 秒；2700 个交易日 ≈ {2700 * day_seconds / 3600:.1f} 小时", flush=True)

print("\n探针结束。", flush=True)
