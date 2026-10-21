"""同一个报告期有多条上游记录时，只留一条的规则。

`balancesheet` 与 `fina_indicator` 都按"一个季度一行"入库（2026-10-21 决定）：上游对
同一期会返回修订版本，留着多行等于让每个策略先判断用哪一条。两张表共用这一份实现，
避免规则各写一遍后走样。
"""


def keep_latest(
    rows: list[tuple],
    *,
    columns: tuple[str, ...],
    key: tuple[str, ...],
    version: tuple[str, ...],
) -> list[tuple]:
    """同一 `key` 只留一行，取 `version` 最大的那条。

    三者完全并列还出现两行时（实测没有，但上游没保证），取列表里靠后的一条，
    与 `income` 的 `drop_duplicates(keep="last")` 保持一致。
    """
    position = {name: index for index, name in enumerate(columns)}
    key_positions = tuple(position[name] for name in key)
    version_positions = tuple(position[name] for name in version)
    best: dict[tuple, tuple[str, ...]] = {}
    kept: dict[tuple, tuple] = {}
    for row in rows:
        row_key = tuple(row[index] for index in key_positions)
        row_version = tuple(str(row[index] or "") for index in version_positions)
        if row_key not in best or row_version >= best[row_key]:
            best[row_key] = row_version
            kept[row_key] = row
    return list(kept.values())
