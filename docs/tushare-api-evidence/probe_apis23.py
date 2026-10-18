"""第 1 集研究用探针（第二十三轮）：钉死 `daily_basic` 单次调用的真实耗时，并试另一条取数路线。

第二十二轮的发现（证据见 probe_out22.txt）：
  - 只给 `trade_date` 就能拿到一整天：5567 行、18 列；与当日上市名单（5577 只）差 10 只，
    缺的 10 只**全部**在当日停牌名单里，重复行 0 —— 返回的就是当日全部有行情的股票。
  - 不给 `ts_code`、只给 `start_date`/`end_date` 报 `[invalid_params]`：多天窗口这条路走不通。
  - 单日一次调用 **23.4 秒** → 2700 个交易日 ≈ 17.5 小时（比 suspend_d 的 3.3 秒慢 7 倍）。

本轮要钉死两件事：
  1. 23.4 秒是不是冷启动假象 —— 同一天连打、再换两个不同交易日，看是否稳定。
  2. 另一条路线：给 `ts_code` + `start_date`/`end_date`（单只股票长区间）能不能用、多少行、多久；
     再试一次两只股票同取，看单次调用有没有行数上限。

只读，不写任何文件。
"""

import sys
import time
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "screener").is_dir())
sys.path.insert(0, str(ROOT))

import tushare as ts

from screener.config import load_settings

DAYS = ("20261016", "20261016", "20261015", "20261014")
STOCK = "600519.SH"
PAIR = "600519.SH,000001.SZ"
LONG_START = "20151019"
LONG_END = "20261016"


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
        print(f"    {label} -> {rows} 行，耗时 {elapsed:.1f} 秒", flush=True)
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

print("\n=== 1. 单日耗时是否稳定（20261016 连打两次，再换两天）===", flush=True)
day_seconds = []
for trade_date in DAYS:
    frame, elapsed = call(f"daily_basic(trade_date={trade_date})", pro.daily_basic, trade_date=trade_date)
    if elapsed is not None:
        day_seconds.append(elapsed)
    time.sleep(4)

print("\n=== 2. 逐股路线：ts_code + start_date/end_date ===", flush=True)
time.sleep(4)
single, single_seconds = call(
    f"daily_basic(ts_code={STOCK}, {LONG_START}~{LONG_END})",
    pro.daily_basic,
    ts_code=STOCK,
    start_date=LONG_START,
    end_date=LONG_END,
)
time.sleep(4)
pair, _ = call(
    f"daily_basic(ts_code={PAIR}, {LONG_START}~{LONG_END})",
    pro.daily_basic,
    ts_code=PAIR,
    start_date=LONG_START,
    end_date=LONG_END,
)

print("\n=== 3. 结论数字 ===", flush=True)
if day_seconds:
    stable = sorted(day_seconds)[len(day_seconds) // 2]
    print(f"    单日调用耗时（{len(day_seconds)} 次）：{['%.1f' % s for s in day_seconds]} 秒", flush=True)
    print(f"    中位数 {stable:.1f} 秒 → 2700 个交易日 ≈ {2700 * stable / 3600:.1f} 小时", flush=True)
if single is not None:
    print(f"    单只股票 11 年：{len(single)} 行，耗时 {single_seconds:.1f} 秒", flush=True)
    print(f"        交易日数 {single['trade_date'].nunique()}，覆盖 {single['trade_date'].min()}~{single['trade_date'].max()}", flush=True)
    print(f"        → 5577 只逐股 ≈ {5577 * single_seconds / 3600:.1f} 小时", flush=True)
if pair is not None:
    print(f"    两只股票 11 年：{len(pair)} 行 —— 若接近 6000 则是单次调用行数上限在截断", flush=True)
    print(f"        其中 {STOCK} {(pair['ts_code'] == STOCK).sum()} 行，{PAIR.split(',')[1]} {(pair['ts_code'] == PAIR.split(',')[1]).sum()} 行", flush=True)

print("\n探针结束。", flush=True)
