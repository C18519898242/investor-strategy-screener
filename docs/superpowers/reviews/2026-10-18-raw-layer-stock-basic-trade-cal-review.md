# 代码评审记录：原始层 `stock_basic` + `trade_cal`

> 本文只记录**评审结论与逐条裁决**，不代表已经动手修。每条的状态见各表的「状态」列。

## 0. 这次评审是谁做的

- **范围**：`71e9787..9526d0d`（10 个提交：设计文档与计划入库 → 原始层两个接口 → `invalid_params` 修复）
- **评审者**：DSH subagent `54b82e32`（标签 `Review raw layer changes`），**全新上下文**，看不到会话历史
- **它拿到的东西**：设计文档与计划的路径、要审的 diff 范围、以及一份「业主已拍板、不要当问题提」的清单
- **约束**：只读（不许 `git add`/`commit`/`checkout`）、不许再派 subagent、所有判断自己做
- **两轮**：先审到 `0c7d665`；修掉 `invalid_params` 后改审 `9526d0d`
- **结论**：**无 Critical**；2 个 Important；7 个 Minor；15 条 Declined to judge（评审明确交回给执行者逐条裁决）

### 评审自己跑的验证（不是照抄执行者的结论）

| 项目 | 结果 |
| --- | --- |
| `unittest discover` / `pytest` | 48 tests OK / 48 passed |
| `ruff check` / `ruff format --check` | 全通过（32 个文件） |
| 真实接口端到端（约 35 次调用，只写 `test-temp/reviewer-live-e2e/`） | `stock_basic` 5577 行 / 15 列；`trade_cal` 4019 行；两个数据集均 published；**重跑结果一致**；SQLite 文件里**没有 token 字节** |
| 独立复现 13 个字段上限 | 15 个字段 → `invalid_params`（4 次，含间隔 32 秒）；`COLUMNS[:13]` → 5577 行；另一组 13 个字段 → 5577 行；两组 14 个字段（111 与 117 字符）→ 均失败 |
| 顺带排除 | `exchange=""` 在 ≤13 字段时无害（5577 行）；11 年区间 `trade_cal` 一次调用可行 |

## 1. 裁决：接受（本次实现自己弄出来的问题）

| # | 位置 | 问题 | 修法 | 状态 |
| --- | --- | --- | --- | --- |
| A1 | `docs/data-model-proposal.md` D7（约 158/162/167 行） | D7「原始层全量镜像：不做行级取舍」仍写着 `stock_basic` 要连 `L`/`D`/`P` 一起拉、取数阶段不做排除。代码只拉 `L`，spec 第 72 行已改，**这个上游文档没改**。而设计文档开头写着「与上游冲突时以上游为准」，所以下一个实现者读到 D7 会把「只拉 L」当成 bug | 照 D6 已有的写法加一行 `**改动记录（2026-10-18）**`（模板见 `data-model-proposal.md:156`） | **已改**（2026-10-18，加在 D7「状态」之后） |
| A2 | `screener/sync/stock_basic.py`（`FIELD_LIMIT = 13` 之后） | 那段说明文字是**裸表达式**，不是 docstring —— 挂不到任何东西，模块也没有真正的模块 docstring | 改成 `#` 注释，或并进 `fetch_stock_basic` 的 docstring | **已改**（2026-10-18，改成 `#` 注释并移到常量上方） |
| A3 | `docs/tushare-api-evaluation.md:296` | 写了「`exchange=''`（`stock_basic` 实测无害，见 4.1）」，但 §4.1（108–115 行）根本没提 `exchange=''` | 把实测补进 §4.1，或删掉「见 4.1」 | **已改**（2026-10-18，实测补进 §4.1） |

## 2. 裁决：需要业主拍板

### B1 空响应的报错信息不可读（评审 Important #2）

- **现象**：代理返回非 2xx（5xx / 429）时，SDK 走 `if res:` 的 else 分支返回**没有列的空 DataFrame**（`tushare/pro/client.py`）。于是 `screener/sync/stock_basic.py` 的 `merge` 抛 `KeyError: 'ts_code'`，选列抛 pandas 的 `KeyError: "None of [Index([...])] are in the [columns]"`，`screener/cli.py:88` 把它原样打成「更新失败：…」。评审直接验证了 pandas 的这个行为。
- **评审的说明**：它**不是**要加行数防护（那是业主否掉的），只是把这个 `KeyError` 翻成 `SyncError("股票列表 返回缺少字段：…")`，约 3 行 + 1 个测试。
- **为什么要拍板**：业主此前的规则是「不要把这种小概率异常考虑进去，MVP 个人项目简单高效就好」。这条算不算被那条规则覆盖，是业主的判断。
- **补充事实**：业主否掉防护时，这条风险被记成「静默截断」；实际表现是**硬中止 + 一句用户看不懂的话**。代理 5xx / 429 是这条路径的真实触发场景（评估报告第 6 节记录过 `rate_limited`、`upstream_pool_exhausted`）。
- **状态**：**不做**（2026-10-18 业主决定）。已把决定写进架构文档「数据流」一节，紧跟失败语义那条之后：**「上游返回空响应或短响应时，同样不加任何防护，也不翻译异常——这一条已经定案，不再重新讨论」**（`docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md`）。

## 3. 裁决：倾向不做

| # | 位置 | 评审建议 | 判断 |
| --- | --- | --- | --- |
| C1 | `screener/sync/stock_basic.py:48` | `merge(..., on="ts_code", how="left")` 加 `validate="one_to_one"` | 不做。评审自己标 Minor；重复 `ts_code` 本来就会先撞主键 `IntegrityError`，加一个关键字只是换个报错，不改行为 |
| C2 | `screener/store.py:56` | `stock_basic.ts_code TEXT PRIMARY KEY` 补 `NOT NULL`（SQLite 允许非 INTEGER 主键为 NULL），与 `trade_cal` 的 `NOT NULL` 主键不对称 | 不做。DDL 是计划规定的；不对称是既成事实，改它要动建表语句和已有库 |
| C3 | `stock_basic.py:49-55` 与 `trade_cal.py:27-33` | 两处重复同一段「选列 → NaN 转 None → `replace_rows`」，抽公共函数 | 不做。评审自己也说「两个调用点可以接受，等第三个接口落地再抽」 |
| C4 | `docs/data-model-proposal.md:26` | 仍指向「spec 第 67、69、70 行」说菜单并发 / 数据库不提交 Git / 上一交易日；那些内容不在那儿（「上一交易日」现在是 spec 第 136 行） | 倾向修但没做。**这条在本次改动之前就已失准**，不是本次引入的；修法是换成章节名引用，免得下次改 spec 再漂 |

## 4. 裁决：倾向做

把 2026-10-18 的实测探针存进 `docs/tushare-api-evidence/`（评审 Minor #7）。

- **理由**：该目录的既有惯例是 `probe_apisN.py` + `probe_outN.txt` 成对（最新一对是 2026-10-17），评估报告 §0 的证据约定也要求这样。13 个字段的上限目前只活在代码注释和两份文档里，**没有可复现的探针**。
- **状态**：**倾向做，未做**

## 5. 评审的 15 条 Declined to judge，逐条裁决

评审把这 15 条列为「交由执行者裁决，不静默丢弃」。逐条：

| # | 行为 | 裁决 |
| --- | --- | --- |
| 1 | 其余 6 个必需接口未实现 | 本轮明确不做，保留 |
| 2 | 无事务 / 无回滚 / 筛选能看到半成品 | 业主已拍板，保留 |
| 3 | 短响应静默截断、批次仍发布 | 业主已拍板的风险。**新增事实**：拆成 13+3 两次调用后这个风险的形状变宽了 —— 第二次调用返回短帧时只把 `delist_date`/`is_hs` 置空，其余 13 列看起来完好，更难发现。同一类风险，记录在案 |
| 4 | 只存 `L`、`delist_date` 恒空、历史股票池不可重建 | 业主已拍板，保留 |
| 5 | 不拉 `D`/`P` | 业主已拍板，保留 |
| 6 | 闰年 2 月 29 日 `date.replace` 抛 `ValueError` | 业主明确不加防护，保留 |
| 7 | `SyncError` 放在 `screener/sync/errors.py` 而非 `pipeline.py` | 循环导入的修正，保留 |
| 8 | `trade_cal` 只拉 SSE | 业主已拍板，保留 |
| 9 | 无水位 / 差集同步，两张表都是全量快照 | 业主已拍板（设计 §10、spec 第 72 行），保留 |
| 10 | `sync_all` 不再单独调 `read_tushare_config` | 行为等价（校验仍在 `begin_sync` 之前），且已有测试钉住。合理的偏离，保留 |
| 11 | `replace_rows` 返回 `len(materialized)` 而非 `cursor.rowcount` | 合理（空 `executemany` 的 rowcount 是 -1），保留 |
| 12 | `stock_basic` 两次调用而非 spec 写的「一次调用」 | 代理上限逼出来的，已写进设计文档与评估报告，保留 |
| 13 | 给 `stock_basic` 传了 `exchange=""` | 实测无害，评估报告已更正，保留 |
| 14 | `tests/tushare_fakes.py` 导入生产的 `COLUMNS` 并自行声明 13 这个上限 | 可接受的测试耦合；独立常量反而是更好的判据，保留 |
| 15 | 既有脚手架遗留（`tests/test_sync.py` 连接未关 / ResourceWarning、README 的「尚无可运行的选股程序」、无 schema 版本表、`data-model-proposal.md:26` 失准、计划文件里未勾的 `- [ ]`） | 本次范围外；`data-model-proposal.md:26` 见 C4，其余保留 |

## 6. 评审认可的强项（供以后参考）

- `screener/sync/` 拆分干净：只有 `client.py` 碰 `tushare` 与代理 URL，`__init__.py` 只做再导出，`registry.py` 独占中文名映射，无循环导入
- 重试设计可测：`sleep` 可注入，`rate_limited` → 30 秒，上游 / 超时 → 5 秒再 15 秒，未知错误立即抛（且有测试断言它不是 `SyncError`）
- 测试打的是真实代码路径而不是它的 mock：`test_update_uses_the_real_fetcher_by_default` 走的是 CLI 的**默认** `fetch`；`tests/tushare_fakes.py` 复刻了两条实测代理行为，13+3 合并一旦回归就能在测试里失败
- `replace_rows` 返回 `len(materialized)` 是实打实的改进（空 `executemany` 的 rowcount 是 -1）
- token 只从 `TUSHARE_TOKEN` 来，且有测试断言它不出现在 `screener/sync/**` 源码、SQLite 字节和终端输出里

## 7. 未处理的既有问题（不在本次范围）

- `tests/test_sync.py` 一直没关连接 → 套件输出有 `ResourceWarning: unclosed database`，`test-temp/` 下会残留测试目录
- `README.md` 仍写着「尚无可运行的选股程序」
- 没有 schema 版本表
- 计划文件 `docs/superpowers/plans/2026-10-18-raw-layer-stock-basic-trade-cal.md` 里的 `- [ ]` 复选框没有勾
