"""第 1 集研究用探针（第十五轮）：披露季单日峰值规模，用于估算增量最坏耗时。

第十四轮确认：disclosure_date(actual_date=某日) 可用，20261016 当天披露 15 只，
端到端逐股拉三表成功，增量约 46 次调用（0.2 分钟）。

本轮问：披露高峰期单日最多有多少只股票披露？据此算出增量最坏耗时。
（10 月中旬是三季报季开端，单日仅 15 只；年报+一季报的 4 月底通常是峰值。）

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

FIELDS = "ts_code,end_date,pre_date,actual_date"


def call(label, tries=3, pause=5, gap=30, **params):
    last = ""
    for attempt in range(1, tries + 1):
        try:
            frame = pro.disclosure_date(**params)
            time.sleep(pause)
            print(f"[OK   ] {label:<42} -> {0 if frame is None else len(frame)} 行")
            return frame
        except Exception as error:  # noqa: BLE001
            last = f"{type(error).__name__}: {str(error)[:90]}"
            if attempt < tries:
                print(f"[retry] {label:<42} -> {last[:60]}（等 {gap} 秒）")
                time.sleep(gap)
    print(f"[FAIL ] {label:<42} -> {last}")
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

print("\n=== 各披露季的单日峰值 ===")
peaks = []
for period, name in (
    ("20251231", "2025 年报"),
    ("20260331", "2026 一季报"),
    ("20260630", "2026 中报"),
    ("20260930", "2026 三季报"),
):
    frame = call(f"disclosure_date end_date={period}（{name}）", end_date=period, fields=FIELDS)
    if frame is None or not len(frame):
        continue
    filled = frame[frame["actual_date"].notna()]
    if not len(filled):
        print(f"        {name}: 暂无实际披露日")
        continue
    counts = filled["actual_date"].value_counts()
    top = counts.head(5).to_dict()
    print(f"        {name}: 已披露 {len(filled)} 只，单日最多 {int(counts.max())} 只（{counts.idxmax()}）")
    print(f"          单日前 5: {top}")
    peaks.append((name, int(counts.max())))

print("\n=== 增量最坏耗时估算（1 + 3×N 次调用，按 200 次/分钟）===")
for name, peak in peaks:
    calls = 1 + 3 * peak
    print(f"        {name} 峰值 {peak} 只 -> {calls} 次调用 ≈ {round(calls / 200, 1)} 分钟")
if peaks:
    worst = max(p for _, p in peaks)
    print(f"        全期最坏单日 {worst} 只 -> {1 + 3 * worst} 次调用 ≈ {round((1 + 3 * worst) / 200, 1)} 分钟")
print("\n探针结束。")
