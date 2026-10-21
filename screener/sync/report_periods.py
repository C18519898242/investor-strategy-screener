"""按法定披露截止日算当前该补哪个报告期，财报类数据集共用。

按截止日算而不是按自然季度算：一季报的截止日是 4 月 30 日，按自然季度会在 1～3 月
去拉一个还没人披露的 0331。4 月 30 日同时是上年年报的截止日，所以从那天起两期一起补。
"""

from datetime import date


def target_periods(today: date) -> tuple[str, ...]:
    """最近一个已经过了法定披露截止日的报告期。"""
    if (today.month, today.day) >= (10, 31):
        return (f"{today.year}0930",)
    if (today.month, today.day) >= (8, 31):
        return (f"{today.year}0630",)
    if (today.month, today.day) >= (4, 30):
        return (f"{today.year}0331", f"{today.year - 1}1231")
    return (f"{today.year - 1}0930",)
