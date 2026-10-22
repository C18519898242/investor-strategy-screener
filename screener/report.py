"""把筛选结果渲染成一页可直接阅读的 HTML。

输出刻意做成「一张表」而不是卡片流：同一策略的字段是固定的，逐行对齐才好横着比。
数值列右对齐并用等宽数字，三年度一类的长文本列允许折行。整页只依赖内联 CSS，
不联网、不引脚本，双击就能在浏览器里看。
"""

import datetime as dt
import re
from html import escape
from pathlib import Path

_NUMBER = re.compile(r"^[0-9][0-9.,\s%/]*$")

_COLUMNS = (("code", "代码"), ("name", "名称"))

_STYLE = """  :root {
    --ink: #16202c;
    --muted: #6b7684;
    --line: #e3e7ed;
    --page: #f4f6f9;
    --card: #ffffff;
    --stripe: #fafbfd;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 40px 24px 72px;
    background: var(--page);
    color: var(--ink);
    font: 15px/1.55 -apple-system, "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
  }
  .page { max-width: 1360px; margin: 0 auto; }
  .card {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 14px;
    box-shadow: 0 1px 2px rgba(16, 32, 48, 0.04);
  }
  header { display: flex; flex-wrap: wrap; gap: 8px 40px; align-items: baseline;
           justify-content: space-between; padding: 22px 26px; }
  h1 { margin: 0; font-size: 21px; font-weight: 650; letter-spacing: 0.2px; }
  .meta { margin: 0; display: flex; flex-wrap: wrap; gap: 6px 28px;
          color: var(--muted); font-size: 14px; }
  .count { margin: 22px 2px 10px; color: var(--muted); font-size: 14px; }
  .count strong { color: var(--ink); font-size: 17px; font-weight: 650; }
  .count .note { margin-left: 16px; font-size: 13px; }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  thead th {
    position: sticky;
    top: 0;
    z-index: 1;
    padding: 10px 12px;
    background: #f7f9fc;
    border-bottom: 1px solid var(--line);
    color: var(--muted);
    font-size: 12.5px;
    font-weight: 600;
    letter-spacing: 0.3px;
    line-height: 1.35;
    text-align: left;
    white-space: normal;
  }
  tbody td { padding: 10px 12px; border-bottom: 1px solid var(--line); white-space: nowrap; }
  tbody tr:nth-child(even) { background: var(--stripe); }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr:hover { background: #f2f7ff; }
  .code { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; color: var(--muted); }
  .name { font-weight: 600; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .wrap { white-space: normal; min-width: 15ch; }
  .empty { margin: 0; padding: 56px 24px; text-align: center; color: var(--muted); }
  footer { margin-top: 14px; color: var(--muted); font-size: 12.5px; text-align: right; }"""


def render_html(
    *,
    strategy_name: str,
    trade_date: str,
    synced_at: str | None,
    rows: list[dict[str, str]],
    display_fields: tuple[str, ...],
    note: str | None = None,
) -> str:
    updated = "无" if synced_at is None else synced_at
    fields = (*_COLUMNS, *((field, field) for field in display_fields))
    body = _table(rows, fields) if rows else '<p class="empty">本次无符合条件的股票</p>'
    lines = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(strategy_name)}</title>",
        "<style>",
        _STYLE,
        "</style>",
        "</head>",
        "<body>",
        '<main class="page">',
        '  <header class="card">',
        f"    <h1>{escape(strategy_name)}</h1>",
        f'    <p class="meta"><span>行情交易日：{escape(trade_date)}</span>'
        f"<span>本地数据上次更新：{escape(updated)}</span></p>",
        "  </header>",
        '  <p class="count">入选股票 '
        f"<strong>{len(rows)}</strong> 只"
        + (f'<span class="note">{escape(note)}</span>' if note else "")
        + "</p>",
        body,
        "  <footer>仅使用本地已保存数据</footer>",
        "</main>",
        "</body>",
        "</html>",
        "",
    ]
    return "\n".join(lines)


def _table(rows: list[dict[str, str]], fields: tuple[tuple[str, str], ...]) -> str:
    numeric = _numeric_fields(rows, fields)
    wrapped = _wide_fields(rows, fields)
    head = "".join(
        f"<th{_classes(field, numeric, wrapped)}>{escape(label)}</th>" for field, label in fields
    )
    body = "\n".join(
        "        <tr>"
        + "".join(
            f"<td{_classes(field, numeric, wrapped)}>{escape(_value(row, field))}</td>"
            for field, _ in fields
        )
        + "</tr>"
        for row in rows
    )
    return "\n".join(
        [
            '  <div class="card table-wrap">',
            "    <table>",
            "      <thead>",
            f"        <tr>{head}</tr>",
            "      </thead>",
            "      <tbody>",
            body,
            "      </tbody>",
            "    </table>",
            "  </div>",
        ]
    )


def _classes(field: str, numeric: set[str], wrapped: set[str]) -> str:
    classes = [
        name for name, active in (("num", field in numeric), ("wrap", field in wrapped)) if active
    ]
    classes.append(field if field in {"code", "name"} else "")
    rendered = " ".join(name for name in classes if name)
    return f' class="{rendered}"' if rendered else ""


def _value(row: dict[str, str], field: str) -> str:
    return "" if row.get(field) is None else str(row[field])


def _numeric_fields(rows, fields) -> set[str]:
    return {
        field for field, _ in fields[2:] if all(_NUMBER.match(_value(row, field)) for row in rows)
    }


def _wide_fields(rows, fields) -> set[str]:
    return {field for field, _ in fields[2:] if max(len(_value(row, field)) for row in rows) > 22}


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
