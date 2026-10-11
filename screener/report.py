import datetime as dt
from html import escape
from pathlib import Path


def render_html(
    *,
    strategy_name: str,
    trade_date: str,
    synced_at: str | None,
    rows: list[dict[str, str]],
    display_fields: tuple[str, ...],
) -> str:
    updated = "无" if synced_at is None else synced_at
    if rows:
        body = "".join(_stock_entry(row, display_fields) for row in rows)
    else:
        body = "<p>本次无符合条件的股票</p>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{escape(strategy_name)}</title>
<style>
body {{ font-size: 20px; }}
</style>
</head>
<body>
<h1>{escape(strategy_name)}</h1>
<p>行情交易日：{escape(trade_date)}</p>
<p>本地数据上次更新：{escape(updated)}</p>
<p>入选股票：{len(rows)} 只</p>
{body}
</body>
</html>
"""


def _stock_entry(row: dict[str, str], display_fields: tuple[str, ...]) -> str:
    fields = "".join(
        f"<div>{escape(field)}：{escape(row[field])}</div>" for field in display_fields
    )
    return (
        "<article>"
        f"<div>股票代码：{escape(row['code'])}</div>"
        f"<div>股票名称：{escape(row['name'])}</div>"
        f"{fields}</article>"
    )


def write_result(
    root: Path,
    *,
    output_dir: str,
    generated_at: dt.datetime,
    html: str,
) -> Path:
    directory = root / "outputs" / output_dir
    directory.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.strftime("%Y%m%d-%H%M%S")
    path = directory / f"screen-{stamp}.html"
    sequence = 2
    while path.exists():
        path = directory / f"screen-{stamp}-{sequence}.html"
        sequence += 1
    path.write_text(html, encoding="utf-8")
    return path
