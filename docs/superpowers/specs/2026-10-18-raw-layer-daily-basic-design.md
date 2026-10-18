# 原始层：每日指标（`daily_basic`）设计

> 日期：2026-10-18。本文只记录设计，不含可运行代码。
>
> 上游依据：[技术选型与架构](2026-10-11-tech-selection-architecture-design.md)（「数据流」「数据层设计」「日志」）、[数据建模提案](../../data-model-proposal.md)（D1～D10）、[Tushare 接口实测评估](../../tushare-api-evaluation.md)、[原始层：停复牌设计](2026-10-18-raw-layer-suspend-d-design.md)（本轮的体例与约定来源）。
>
> 本轮实测证据：[probe_apis22.py](../../tushare-api-evidence/probe_apis22.py) 与 [probe_out22.txt](../../tushare-api-evidence/probe_out22.txt)、[probe_apis23.py](../../tushare-api-evidence/probe_apis23.py) 与 [probe_out23.txt](../../tushare-api-evidence/probe_out23.txt)。
>
> 本文需要同步改动上游文档的地方逐条列在第 8 节。

## 1. 范围与完成标准

**做什么**：把 `daily_basic`（每日指标）接进原始层——菜单「更新本地数据」真的联网调用 Tushare，把接口返回的字段全部写进 SQLite；首次运行做 11 年回填，之后每次更新只补缺失的交易日。

**完成标准**：

1. 首次运行后 `daily_basic` 表有交易日历覆盖的全部交易日（当前约 11 年）；紧接着再运行一次，结果不变：不重复、不报错。
2. 日常更新只拉缺失的交易日，调用数等于缺失天数（通常 1 次）。
3. 筛选流程一行不改。
4. 全部测试用假客户端，不联网。

本轮**只做 `daily_basic`**；其余 4 个必需接口（`income`、`balancesheet`、`fina_indicator`、`dividend`）不做。

## 2. 数据名与登记

D6 规定策略文件只登记数据名，不出现接口名。本轮新增一个：

| 数据名 | 对应接口 |
| --- | --- |
| `每日指标` | `daily_basic` |

映射加进 `sync/registry.py` 的 `DATASET_FETCHERS`，水位查询加进 `WATER_QUERIES`（`SELECT MAX(trade_date) FROM daily_basic`）。

数据名取接口自己的名字「每日指标」，不用 `data-model-proposal.md` 里的长称「每日行情与估值指标」——它要出现在更新菜单的一行里。菜单由 `registered_datasets()` 从登记表取出，所以进了登记表就自动出现，不需要动任何策略。

## 3. 一张表

列名照抄接口字段、字段全部入库（D1）；日期一律 TEXT `YYYYMMDD`（D10）；原始层不存派生指标（D2）。

| 表 | 列 | 主键 | 附加索引 |
| --- | --- | --- | --- |
| `daily_basic` | `ts_code`、`trade_date`、`close`、`turnover_rate`、`turnover_rate_f`、`volume_ratio`、`pe`、`pe_ttm`、`pb`、`ps`、`ps_ttm`、`dv_ratio`、`dv_ttm`、`total_share`、`float_share`、`free_share`、`total_mv`、`circ_mv` | `(ts_code, trade_date)` | `trade_date` |

- **18 列**（实测 2026-10-18，证据见 `probe_out22.txt`）。`data-model-proposal.md` 与评估文档里写的「15 列」是旧数，按实测的 18 列建表。
- `ts_code`、`trade_date` 是 TEXT；其余 16 列是数值。
- 单位不换算：`total_mv`、`circ_mv` 是**万元**，`total_share`、`float_share`、`free_share` 是**万股**。换算留给计算层，各章按自己的阈值统一。
- `pe`、`pe_ttm`、`pb`、`ps`、`ps_ttm`、`dv_ratio`、`dv_ttm` 照存，但按 D1 的既有裁定，**不得被当作「我们的 PE / PB / 股息率」**——所有派生指标在计算层现算。
- 附加索引 `trade_date`：取数时要按它查水位、按它删行。

**写入方式：只删水位那一天，其余靠 INSERT 追加**（不是快照替换）。

```sql
DELETE FROM daily_basic WHERE trade_date = ?;   -- ? = 水位那一天
-- 然后 executemany：INSERT INTO daily_basic (18 列) VALUES (18 个 ?)
```

**为什么不是「先删空、再批量插入」**：`stock_basic`/`trade_cal` 是快照，删空安全；`daily_basic` 是**逐日累积**表，一次只拉一天，删空会把库里其余年份全清掉。

**为什么裸 INSERT 就够、不需要 upsert**：待补的交易日全部满足「库里没有任何该日行」（第 4 节第 4 步的差集），唯一例外是水位那天，而它先被删掉了。写了一半的那一天必然含至少一行，于是它自己就是 `MAX(trade_date)`，下次被删掉重拉。**推断**：重跑补写不会撞主键——与 `suspend_d` 同构，而 `suspend_d` 已实测重跑不撞。

**与 `suspend_d` 的一处不同：不需要按 `trade_date` 排序**。`suspend_d` 一次调用返回一个区间的多天数据，必须按日期升序写才能保证「已写前缀按日期连续」；`daily_basic` 一次只拉一天，返回的行全部同一天，天内顺序无意义。保证前缀连续的是**调用顺序**（第 4 节：按日期升序逐日拉），不是写入顺序。

## 4. 取数规则

**客户端**：沿用 `ts.pro_api(token, timeout=120)`。

**参数**：**只传 `trade_date`**，加显式 `fields`（18 个字段，项目统一要求显式传）。不传 `ts_code`、不传 `start_date`/`end_date`。

**为什么只能逐日**（实测 2026-10-18，证据见 `probe_out22.txt`）：

- 一天全市场就是 **5567 行**，已经贴着官方 6000 行的单次上限——多天一段会被静默截断，且截断无声。
- 不给 `ts_code` 而只给 `start_date`/`end_date` 直接报 `[invalid_params]`，多天窗口这条路根本不给走。
- 换成逐股路线也不行：单只股票取 11 年可以（600519.SH → 2672 行），但两只股票同取 11 年就报 `[date_range_too_large]`（要求分段），且逐股实测更慢（第 4 节末尾）。
- 官方文档**没有分页参数**，所以「一个交易日一次调用」是唯一出路。

**一天返回的就是当日全部有行情的股票**（实测 2026-10-18，证据见 `probe_out22.txt`）：基线日 20261016 返回 5567 行，当日上市 5577 只，差 10 只，**这 10 只全部在当日停牌名单里**；`daily_basic` 有、上市名单没有的 0 只；`(ts_code, trade_date)` 重复 0 行。同日 `suspend_d` 有 11 只停牌、只缺 10 只——有一只日内停牌的股票仍有 `daily_basic` 行，符合官方「日内停牌才有值」的口径。

**取数流程**：

1. 读水位 `W = SELECT MAX(trade_date) FROM daily_basic`。
2. 读**止**：`E = SELECT MAX(cal_date) FROM trade_cal WHERE cal_date <= 今天`（「今天」取自可注入的时钟，测试里注入固定值）。日历为空时 `E` 为空，本次一个交易日都不拉、写 0 行，**不报错也不提示**（业主裁定 2026-10-18：日历到哪天就同步到哪天，周末与节假日都不特殊处理；上限取「不晚于今天」只是为了不去撞日历里可能存在的未来日期）。
3. `W` 不是 `NULL` 时执行 `DELETE FROM daily_basic WHERE trade_date = W`——水位那一天可能是上次拉到一半的，删掉它等于把它重新放回待补集合。
4. 待补交易日列表 = 交易日历里 `is_open = 1`、`cal_date <= E`、**且 `daily_basic` 里没有任何该日行**的交易日，按 `cal_date` 升序：

   ```sql
   SELECT cal_date FROM trade_cal
   WHERE is_open = 1 AND cal_date <= ?
     AND cal_date NOT IN (SELECT trade_date FROM daily_basic)
   ORDER BY cal_date
   ```

5. 按日期升序逐个交易日调用接口，**每个交易日请求之前**打一条进度（第 5 节），返回的行批量 INSERT。
6. 待补列表跑完，`sync_all` 照常记账、发布。

**为什么按日期升序拉**：任何中断（崩溃、Ctrl+C、被捕获的失败）都只留下「已写前缀」（第 5 节）。按日期升序拉，这个前缀必定是**按日期连续的**：水位之前的日期完整，只有水位那天可能缺行。重跑删水位那天、再按日历补齐缺失日就够了。乱序拉的话，前缀可能留下日期空洞（补了 10-01、10-03 却没补 10-02），而水位是最大日期，那个洞会一直留着。

**补的是真差集，不是「水位之后」**：待补集合是「日历里的交易日」减去「库里已有行的日期」，所以中间的空洞（某天返回 0 行却算成功、那天一行都没写）会在下次更新时自动补上；水位那天被第 3 步删掉后也回到待补集合里。首次回填的起点因此不是写死的「11 年前」，而是**交易日历的最早交易日**——当前日历从 2015-10-18 起，与 D7 的 11 年起点一致。日历里 `is_open = 0` 的非交易日不会被当成缺失。

`trade_date` 存 TEXT 不影响排序与比较：`YYYYMMDD` 定宽零填充，字典序就是日期序，`MAX(trade_date)` 同理。

**首次回填成本**：日历覆盖的 11 年约 **2672 个交易日**（实测：600519.SH 在 20151019～20261016 区间返回 2672 行、2672 个不同 `trade_date`），也就是约 **2700 次调用**。单次耗时实测 4 次为 27.6 / 17.3 / 18.7 / 38.3 秒，**中位数约 23 秒**，于是首次约 **13～29 小时**。`tushare-api-evaluation.md` 里「每张表约 13 分钟」是按 0.3 秒/次估的，错了约两个数量级，第 8 节要改。

- 对照：`stock_basic` 同样是 5577 行只要 3.1 秒（同一次实测）。慢的不是网络或代理，是 `daily_basic` 上游自己的计算。
- 逐股路线实测更慢：单只股票 11 年 16.4 秒 × 5577 只 ≈ **25.5 小时**，且多股票同取会被拒。不采用。
- 行数量级（推断）：2672 天 × 每天数千行 ≈ **1000 万～1500 万行**，库体积约 1～2 GB。

**不做并发**（业主裁定 2026-10-18）：担心并发写库丢数据；当前写库是 autocommit + 单连接，多进程并发写同一张表会踩写入顺序的坑。顺序跑的附带好处是随时可以中断，已写完的交易日留在库里。

**回补窗口 1 天**：只删水位那一天。D8 已改成「回补窗口由各数据集各自决定」，本数据集定为 1 天，加上按日历补齐缺失日。

**重试**：沿用既有规则（3 次尝试；`rate_limited` 等 30 秒；`upstream_pool_exhausted`、连接重置、读超时退避 5/15 秒；3 次都失败抛 `SyncError`，整批失败；等待函数可注入，测试里不许真等）。`daily_basic` 实测触发过一次 `upstream_pool_exhausted`（提示等 30 秒后重试）。

## 5. 失败语义

**不引入事务，不做失败回滚，不加异常护栏**：连接是 autocommit（`journal_mode=WAL` + `synchronous=NORMAL`），每一条写入立即落盘；中间出错就下次更新重跑一遍。理由与代价见[停复牌设计](2026-10-18-raw-layer-suspend-d-design.md)第 5 节，本轮不重复。

- **任何中断的落盘状态都一样**：进程崩溃、Ctrl+C、被捕获的失败，都只留下「已写前缀」。因为按日期升序逐日拉（第 4 节），这个前缀必定按日期连续，水位就是它的最大日期。
- 于是重跑只需「删水位那天、再按日历补齐缺失日」——**断点自愈不需要额外机制**，11 年回填被打断不会白跑。
- 不因为接口偶发失败做额外处理，也不加「返回 0 行」「行数恰好 6000（疑似截断）」之类的检查。**截断是明确接受的静默风险**：单日 5567 行已贴着 6000 上限，将来某天超过就会被静默少写且不报错。

日志沿用既有约定：`开始拉取 {数据名}` / `{数据名} 完成：{n} 行` 走 INFO、控制台可见；接口请求与返回、写入行数走 DEBUG、只进文件；重试告警走 WARNING、控制台也可见；`更新失败：…` 走 ERROR、堆栈只进文件。另外**每个交易日请求之前**先打一条 `每日指标 第 {n}/{N} 个交易日：{日期}` 走 INFO、控制台可见（`N` = 本次要拉的交易日数，第 4 节第 3 步就能算出来；按业主口径放在请求之前：只要某天落了盘，控制台必定已经报过它）。单次调用要十几到几十秒，这条进度是唯一能看出没卡死的东西。日志系统本身的约定见[架构设计](2026-10-11-tech-selection-architecture-design.md)的「日志」节。

## 6. 代码组织

一个接口一个模块，沿用既有约定：

```text
screener/sync/daily_basic.py    # DATASET = "每日指标"、COLUMNS、fetch_daily_basic(connection, client) -> int
```

- `fetch_daily_basic` 内部完成第 4 节的全部步骤，返回写入的行数。
- **不新增写入原语**：复用 `suspend_d` 那轮加的 `insert_rows`（只插入）与 `delete_rows`（按某一列的值删行），组合是「删水位那一天 + 逐日批量插入」。
- `registry.py` 的 `DATASET_FETCHERS` 与 `WATER_QUERIES` 各加一项。
- 表在 `open_database` 里建（含两列主键与 `trade_date` 索引）。

## 7. 接线改动

- `screener/sync/registry.py`：`DATASET_FETCHERS` 和 `WATER_QUERIES` 各增加一行 `"每日指标"`。
- `screener/store.py`：`open_database` 里建 `daily_basic` 表。
- `cli.py`、`pipeline.py`、`main.py` 不改：菜单从登记表取，水位从 `WATER_QUERIES` 取。
- 策略的 `required_datasets` 不改：更新菜单只看 registry，策略声明只用于筛股前的就绪检查。

## 8. 需要同步修改的既有文档

1. **`docs/tushare-api-evaluation.md` 的 `daily_basic` 小节**：补实测——只给 `trade_date` 即返回当日全市场（5567 行、18 列，缺的 10 只全是停牌股、重复 0 行）；不给 `ts_code` 时 `start_date`/`end_date` 报 `invalid_params`；多股票长区间报 `date_range_too_large`；单日调用 17～38 秒；并把「每张表约 13 分钟」改成 13～29 小时、列数 15 改成 18。
2. **`docs/data-model-proposal.md` D8 的增量表**：`daily_basic` 一行已有「用交易日历定出缺失的交易日，逐日补」，补上「首次约 2700 次、13～29 小时」。
3. **`docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md` 的「同步」表**：同上补一行耗时。
4. **`product.md`**：更新菜单示意（多出「每日指标」），并说明首次更新约 13～29 小时、可以随时中断、下次接着补。
5. **`docs/data-model-proposal.md` D3 主键表**：`daily_basic` 一行已存在（`(ts_code, trade_date)` + `trade_date` 索引），**无需改动**——本轮只是落实它。

## 9. 测试与验证

全程 TDD，假客户端，不联网。

| 层 | 要验证的行为 |
| --- | --- |
| `store` | `daily_basic` 表能建起来；18 列齐全；主键 `(ts_code, trade_date)`（同一天同一只股票重复插入会报错）；`trade_date` 索引存在；「删某一天 + 批量插入」只删那一天、其余行不动；跑两次结果一致（不重复、不报错） |
| `sync/daily_basic` | 表为空时从 11 年前起；有水位时先删水位那一天；交易日列表取自 `trade_cal` 的 `is_open = 1`，只补缺失的交易日；日历为空时 0 次调用、0 行、不报错；日历只到某天时止于该天；每次调用只传 `trade_date` 与显式 `fields`；**进度行在请求之前**（照 `suspend_d` 那条测试的写法，记录事件时间线）；中断后已写入的交易日留在库里；返回计数正确 |
| `sync/registry` | 「每日指标」能分发到 `fetch_daily_basic`；水位查询返回 `MAX(trade_date)` |
| `cli` | 更新流程端到端可用，菜单里出现「每日指标」，输出不含 token |

**未测的数字**：首次回填的总行数与库体积。按每天数千行外推是 1000 万～1500 万行、1～2 GB，但**不影响设计**——逐日粒度由上游的单次调用上限决定，与总行数无关——只在评估耗时与磁盘时需要，本轮不追。

## 10. 明确不做

- 不做并发（业主裁定：担心并发写丢数据）。
- 不做逐股回填路线（实测 25.5 小时，比逐日慢，且多股票同取被上游拒）。
- 不接其余 4 个必需接口（`income`、`balancesheet`、`fina_indicator`、`dividend`）。
- 不做 `pe`/`pe_ttm`/`pb`/`ps`/`dv_ratio`/`dv_ttm` 的语义处理（只按 D1 原样存）。
- 不建水位表（水位从数据表自身推，D8 已定）。
- 不加截断检测护栏（第 5 节，业主既有裁定）。
- 不引入事务、不做失败回滚。
- 不实现负结果缓存（D8 已定）。
- 不裁剪字段（D1）。
- 不为「日历只更新到半年前」加提示或告警（业主裁定 2026-10-18：同步到交易日历最后一天，无需提示）。
- 不按 `trade_date` 排序写入（一天一次调用，天内顺序无意义，见第 3 节）。
