# 原始层首批接口（股票列表与交易日历）设计

> 日期：2026-10-18。本文只记录设计，不含可运行代码。
>
> 上游依据：[技术选型与架构](2026-10-11-tech-selection-architecture-design.md)（「代码组织」「数据流」「数据层设计」）、[数据建模提案](../../data-model-proposal.md)（D1～D10）、[Tushare 接口实测评估](../../tushare-api-evaluation.md)（字段清单与代理约束）。
>
> 本文不改变 `product.md`、`strategies.md` 与上游 spec 的既有决定；与它们冲突时以上游为准。本文需要改动上游 spec 的三处，逐条列在第 8 节。

## 1. 范围与完成标准

**做什么**：把 `trade_cal`（交易日历）和 `stock_basic`（股票列表）接进原始层——菜单「更新本地数据」真的联网调用 Tushare，把接口返回的字段全部写进 SQLite。

**完成标准**：

1. `python main.py` → 更新本地数据，两张表都有数据，字段与行数与接口返回一致。
2. 紧接着再运行一次，结果不变：不重复、不报错（幂等）。
3. 筛选流程一行不改；「上一交易日」仍走 `cli.py` 现有的留空分支，留到下一轮。
4. 全部测试用假客户端，不联网。

本轮**只做这两个接口**，其余 6 个必需接口不做。

## 2. 代码组织：`sync` 改成包

`screener/sync.py` 拆成一个包，一个接口一个模块；编排、映射、重试各自独立成文件。

```text
investor-strategy-screener/
├── main.py                     # 启动入口（不改）
├── screener/
│   ├── __init__.py
│   ├── cli.py                  # 菜单
│   ├── sync/                   # 唯一可以联网的部分
│   │   ├── __init__.py         # 只做再导出，不含逻辑
│   │   ├── client.py           # 读配置 + 构造客户端
│   │   ├── pipeline.py         # sync_all：批次编排
│   │   ├── registry.py         # 数据名 → 抓取函数
│   │   ├── retry.py            # 退避重试
│   │   ├── stock_basic.py      # 股票列表
│   │   └── trade_cal.py        # 交易日历
│   ├── store.py                # SQLite：建表与写入
│   ├── report.py               # HTML
│   └── strategies/
├── tests/
└── .gitignore
```

| 模块 | 职责 | 不允许做的事 |
| --- | --- | --- |
| `sync/__init__.py` | 再导出 `SyncError`、`sync_all`、`create_tushare_client`、`fetch_datasets` | 不写逻辑，不导入 `tushare` |
| `sync/client.py` | 读 `TUSHARE_TOKEN` / `TUSHARE_API_URL`，构造指向代理的客户端 | 不抓数据，不认识表结构 |
| `sync/pipeline.py` | `sync_all`：开批次、调用抓取、记账、发布 | 不认识具体接口 |
| `sync/registry.py` | 「数据名 → 抓取函数」的映射 | 不认识 SQL |
| `sync/retry.py` | 代理错误的重试与退避 | 不认识具体接口 |
| `sync/stock_basic.py` | 调 `stock_basic` 并写入 | 不做策略判断 |
| `sync/trade_cal.py` | 调 `trade_cal` 并写入 | 不做策略判断 |

约束：

- **只有 `screener/sync/client.py` 可以 `import tushare` 或设置代理地址**（原来是"只有 `sync.py`"）。
- `screener/strategies/` 与 `screener/report.py` 的源码仍不得出现 `tushare`。
- `sync/__init__.py` 的再导出让 `from screener.sync import SyncError, sync_all, create_tushare_client` 这些既有写法继续有效，现有测试的 import 不需要改。`cli.py` 要改：它现在导入的 `fetch_not_implemented` 会删除（第 7 节）。

## 3. 数据名与登记

D6 规定策略文件只登记**数据名**，不出现接口名。本轮两个数据名：

| 数据名 | 对应接口 |
| --- | --- |
| `股票列表` | `stock_basic` |
| `交易日历` | `trade_cal` |

映射写在 `sync/registry.py`。第 1 集的 `required_datasets` 从 `()` 改成 `("股票列表", "交易日历")`——不登记的话，菜单算出的"更新范围"是空集，一个接口都不会被调用。

## 4. 两张表

列名照抄接口字段、字段全部入库（D1）；日期一律 TEXT `YYYYMMDD`（D10）；原始层不存派生指标（D2）。

| 表 | 列 | 主键 | 附加索引 |
| --- | --- | --- | --- |
| `stock_basic` | `ts_code`、`symbol`、`name`、`area`、`industry`、`fullname`、`enname`、`cnspell`、`market`、`exchange`、`curr_type`、`list_status`、`list_date`、`delist_date`、`is_hs` | `ts_code` | `list_status` |
| `trade_cal` | `exchange`、`cal_date`、`is_open`、`pretrade_date` | `(exchange, cal_date)` | — |

- 类型：代码、名称、日期、状态列都是 TEXT；`is_open` 是 INTEGER（0/1）。
- `pretrade_date` 在区间第一行是**空串**（实测，评估报告 3.1）。按 D4，存储层如实存接口返回值——**空串就存空串**，不转 NULL、不补默认值，缺失语义留给计算层。
- `trade_cal` 的主键是**本轮新定的**，上游 spec 的主键表里没有这一行（第 8 节第 3 条）。

**写入方式：先删空、再批量插入**（全量替换，不用 upsert）。

```sql
DELETE FROM <表>;
-- 然后 executemany：INSERT INTO <表> (<全部列>) VALUES (<全部占位符>)
```

两张表都是快照，每次更新都拿回完整的一份，所以直接替换：

- 不用维护"所有非主键列"的 `ON CONFLICT ... DO UPDATE SET` 清单，也就没有 D10 警告的那个坑（`INSERT OR REPLACE` 是"删除 + 插入"，未给出的列会被清成 NULL）。
- 自动清掉已经离开数据集的旧行。`stock_basic` 只拉 `L` 时这点尤其必要：某只股票退市后不再出现在 `L` 里，upsert 会把它那一行永远留在表里、`list_status` 还写着 `L`。
- 批量插入用 `executemany`，5577 行毫秒级。
- 中间出错就下次更新重跑一遍，不做额外处理（第 6 节）。

## 5. 取数规则

**客户端**：`ts.pro_api(token, timeout=120)`。默认 30 秒不够（评估报告第 6 节：`daily` 不传 `fields` 就 30 秒超时）。

**`stock_basic`**：只拉 `list_status='L'`（在市），**一次调用**，实测 5577 行。`D`（退市）、`P`（暂停上市）不拉——需求不关心这两类。

- **显式传 `fields`**，列出上表全部 15 列。不传 `fields` 会被代理判 `invalid_params`（评估报告第 6 节）。
- 实测（2026-10-18）：传 `list_status` 时返回的每一行都是该状态，响应内 `ts_code` 不重复；不传 `list_status` 只回 `L`（官方默认值就是 `L`）。所以主键取 `ts_code` 安全。
- 副作用，接受：表里只有当前在市的股票，`delist_date` 全为空，历史筛选日的股票池无法从这张表重建。

**`trade_cal`**：`exchange='SSE'`，区间 = **运行日往前 11 年**（D7 的起点）到运行日，一次调用拿完。

- 显式传 `fields='exchange,cal_date,is_open,pretrade_date'`。
- **只拉 SSE**：实测只验过 SSE；深市日历是否与沪市完全一致**未实测**（推断一致）。将来需要深市再加一次调用即可。

**重试**：最多 3 次尝试（首次 + 2 次重试）。

| 错误 | 处理 |
| --- | --- |
| `rate_limited` | 等 30 秒后重试（评估报告第 6 节建议 30 秒级退避） |
| `upstream_pool_exhausted` | 退避 5 秒、15 秒后重试。这两张表没有"换查询路径"的余地（评估报告里的换路径是给分红和财报的） |
| 连接重置 / 读超时 | 同 `upstream_pool_exhausted` |

3 次都失败就抛 `SyncError`，整批失败。等待函数必须可注入，测试里不许真等 30 秒。

## 6. 失败语义

**不引入事务，不做失败回滚，不加异常护栏**：中间出错就下次更新重跑一遍。

- 更新是"删空 + 批量插入"。中途失败时表里的内容可能不完整，下次运行从头再来一遍即可。
- 不因为接口偶发失败做额外处理，也不加"返回 0 行""返回行数接近上限"之类的检查。
- 因此失败之后到重跑之前，表里可能是残缺的，而 `published_datasets()` 仍报告上一次成功批次的数据集齐备，筛选**可能读到这份残缺数据**。这是明确接受的代价。

这条与上游 spec 第 57 行「已经写入的部分新数据不能与旧数据混合后被筛选读到」冲突，因此第 57 行要改（第 8 节第 2 条）。

## 7. 接线改动

**`fetch` 的签名**从 `fetch(datasets, token)` 改成 `fetch(connection, datasets, client)`：

- `sync_all` 负责读配置、构造客户端，两者都在 `begin_sync` **之前**——保持"配置缺失时根本不产生批次"这个既有行为（现有测试 `test_missing_token_does_not_open_batch` 依赖它）。
- 抓取函数只依赖 `(connection, client)`，测试注入假客户端即可，不必伪造 `sys.modules["tushare"]`。
- `fetch_datasets(connection, datasets, client) -> Mapping[str, int]` 按数据名分发，遇到未登记的数据名抛 `SyncError`。`sync_all` 现有的"结果缺少所需数据"检查保持不变。

**`cli.py`**：默认 `fetch` 从 `fetch_not_implemented` 换成 `fetch_datasets`；占位函数 `fetch_not_implemented` 删除（除 `cli.py` 的默认值外没有别处引用）。`main.py` 不改。

## 8. 需要同步修改的既有文档

1. **spec「代码组织」**（第 18～51 行）：目录树里 `sync.py` 换成 `sync/` 包；模块职责表里 `sync` 一行改为"唯一可以联网的部分，落在 `sync/client.py`"。
2. **spec 第 57 行**：删掉"已经写入的部分新数据不能与旧数据混合后被筛选读到"，改为明确接受残缺状态、靠重跑覆盖（理由见第 6 节）。
3. **spec「数据层设计」主键表**：补 `trade_cal` 一行 `(exchange, cal_date)`，无附加索引。
4. **`tests/test_tushare_connection.py` 第 50 行**：它读 `Path("screener/sync.py")` 断言源码不含 token；包化后该文件不存在，改成遍历 `screener/sync/` 下的所有 `.py`。
5. **`tests/test_strategies.py` 第 17 行**：它断言 `spec.required_datasets == ()`，第 3 节改了登记内容，这条断言要跟着改。
6. **`docs/superpowers/plans/2026-10-11-tushare-connection.md`**：其中"只有 `screener/sync.py` 可以导入 `tushare`"是已完成计划的记录，不改，仅在此说明新的落点是 `sync/client.py`。

spec 的章节编号一律不动，`data-model-proposal.md` 等按章节号引用它的地方不受影响。

## 9. 测试与验证

全程 TDD，假客户端，不联网。

| 层 | 要验证的行为 |
| --- | --- |
| `store` | 两张表能建起来；写入是"删空 + 批量插入"，跑两次结果一致（不重复、不报错）；第二次写入会清掉上一次有、这一次没有的行；`trade_cal` 的主键含 `exchange` |
| `sync/stock_basic` | 只调用一次且 `list_status='L'`；显式传了 `fields`；行数写进库；返回计数正确 |
| `sync/trade_cal` | 参数是 `exchange='SSE'`、区间为运行日往前 11 年、显式 `fields`；行数写进库 |
| `sync/registry` | 未登记的数据名抛 `SyncError` |
| `sync/retry` | 前两次抛 `rate_limited`、第三次成功时，尝试次数与等待时长符合上表（注入假等待函数） |
| `sync/pipeline` | 注入的假 `fetch` 收到 `(connection, datasets, client)`；失败后 `published_datasets` 不变（改造现有测试） |
| `cli` | 更新流程端到端可用，输出不含 token |

## 10. 明确不做

- 不接其余 6 个必需接口（`suspend_d`、`daily_basic`、`income`、`balancesheet`、`fina_indicator`、`dividend`）。
- 不实现「上一交易日」查询，`cli.py` 的留空分支维持现状。
- 不建水位、不算差集：这两张表都按快照处理，每次全量重拉、直接替换。水位将来从数据表自身推，**不建水位表**。
- 不引入事务，不做失败回滚，不加异常护栏（第 6 节）。
- 不实现负结果缓存（D8 已决定）。
- 不拉 `D`/`P` 上市状态，不拉 `G`/`UN`，不拉 `SZSE` 日历。
- 不裁剪字段（D1）。
