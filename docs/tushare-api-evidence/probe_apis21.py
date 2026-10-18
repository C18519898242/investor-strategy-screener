"""第 1 集研究用探针（第二十一轮）：补第二十轮被 `rate_limited` 打断的那一项。

第二十轮已经定案的两件事（证据见 probe_out20.txt）：
 - **27 天分片安全**：5 个 27 天区间（含跨月）全部未触顶 —— 7347 / 6525 / 5410 / 5445 / 3405 行。
   "阈值是自然月边界"的猜想被推翻，`CHUNK_DAYS = 27` 不用改。
 - **没有重复键**：2015-11（7673 行）、2016-03（6656 行）、2016-04（6639 行）与基线日 20261016，
   `(ts_code, trade_date, suspend_type)` 重复数全为 0；再含 `suspend_timing` 也是 0。

本轮只剩一项：**0 行响应回来时，DataFrame 的列还在不在** ——
`screener/sync/suspend_d.py` 里那句 `frame[list(COLUMNS)]`，若空响应不带列就会 KeyError。
先等 35 秒让上一轮的额度限制过去。

只读，不写任何文件。
"""

import sys
import time
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "screener").is_dir())
sys.path.insert(0, str(ROOT))

import tushare as ts

from screener.config import load_settings

FIELDS = "ts_code,trade_date,suspend_timing,suspend_type"
COLUMNS = FIELDS.split(",")

print("=== 等 35 秒，让上一轮的 rate_limited 过去 ===", flush=True)
time.sleep(35)

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

print("\n=== 0 行响应：列还在不在（2026-10-17/18 是周六周日）===", flush=True)
for trade_date in ("20261017", "20261018"):
    for attempt in range(1, 4):
        try:
            frame = pro.suspend_d(trade_date=trade_date, fields=FIELDS)
            rows = 0 if frame is None else len(frame)
            columns = [] if frame is None else list(frame.columns)
            print(f"    trade_date={trade_date} -> {rows} 行", flush=True)
            print(f"        columns = {columns}", flush=True)
            missing = [name for name in COLUMNS if name not in columns]
            if rows == 0:
                if missing:
                    print(f"        ^ 空响应缺列 {missing}：frame[list(COLUMNS)] 会抛 KeyError", flush=True)
                else:
                    print("        ^ 空响应列齐全：frame[list(COLUMNS)] 安全", flush=True)
            else:
                print("        （该日不是 0 行，换一个非交易日再试）", flush=True)
            break
        except Exception as error:  # noqa: BLE001
            print(f"        第 {attempt} 次失败：{str(error)[:70]}", flush=True)
            time.sleep(35)
    time.sleep(4)

print("\n探针结束。", flush=True)
