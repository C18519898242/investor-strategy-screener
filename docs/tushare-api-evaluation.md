# Tushare 接口实测评估：第 1 集格雷厄姆策略

> **读者：后续接手本项目的 AI 助手。** 本文是一次**真实调用 Tushare 接口**的实测记录，目的是让下一个 AI 不必重新摸索：哪些接口要用、参数怎么写、字段叫什么、单位是什么、哪些坑必须绕。
>
> 实测日期：2026-10-17（使用的交易日为 20261016）。范围仅限 [`strategies.md`](../strategies.md)「第 1 集：本杰明·格雷厄姆投资思想」的「我们的实现口径」所需数据。
> **本文只是研究结论，没有据此改动任何代码或策略文档。**

## 0. 证据等级约定（重要）

本文每条结论都标注来源，请勿混用：

| 标记 | 含义 |
| --- | --- |
| **实测** | 本次真的调用了接口并看到返回，原始输出见 [`tushare-api-evidence/`](tushare-api-evidence/) |
| **官方** | 来自 tushare.pro 官方文档页，附 doc_id 链接 |
| **推断** | 由前两者推理得出，没有直接依据，接入时必须自己验证 |

两条必须知道的前提：

1. 实测走的是**用户本机的 Tushare 代理**（`TUSHARE_API_URL=https://tl.kaixin8.top/tushare/sdk`），不是 `http://api.tushare.pro` 官方直连。代理层加了自己的限制（见第 6 节），官方直连未必有这些限制，也未必有相同的可用性。
2. 实测用的是**用户本人的账号**。`*_vip` 这类接口"不存在"的结论，只对"本账号 + 本代理"成立。

## 1. 结论摘要

1. 第 1 集需要 **8 个必需接口**：`trade_cal`、`stock_basic`、`suspend_d`、`daily_basic`、`income`、`balancesheet`、`fina_indicator`、`dividend`。**`stock_st` 已决定不接入**（2026-10-18）——所有策略都排除 ST/\*ST，ST 直接从 `stock_basic.name` 前缀识别即可，理由与实测依据见第 4.1 节。另有 2 个按需接口（`namechange`、`index_member_all`），以及 1 个**同步增量专用**接口 `disclosure_date`（第 4.5 节，把日常财报更新从 1.4 小时压到分钟级）。
2. **`daily` 不需要**。第 1 集只要收盘价和总市值，`daily_basic.close` 与 `daily_basic.total_mv` 已覆盖，且 `daily` 明显更慢。
3. **财务三表没有全市场批量接口**：`income_vip`/`balancesheet_vip`/`fina_indicator_vip` 在本账号返回 `unknown_api`。财报只能**按单只股票**循环取，这是整个同步设计里最贵的部分（第 7 节给出修正后的成本）。
4. **`income` 的单季报表（`report_type='2'`）可用**，实测四个单季之和与年报累计**完全相等**（差 0 元）。因此 [`strategies.md`](../strategies.md) 中「最近 12 个月归母净利润：公式已定，字段待核验」这一行，可以按"字段已核验"处理。
5. **行业排除只要 1 次调用**：`stock_basic.industry` 直接含「银行」42 只、「保险」5 只，不必逐股查行业。注意申万口径下保险在 **L2「保险Ⅱ」**，用 L1「非银金融」会误杀证券（实测）。
6. **代理层有 7 条硬约束**（第 6 节）：必须显式传 `fields`、财报查询必须带 `ts_code`、单次区间上限 1 年、限频、偶发不可用、慢查询超时、部分参数被静默忽略。实现时必须按这些约束写重试与分段逻辑。
7. **同一报告期会返回多个版本**（实测）：`income`/`balancesheet`/`fina_indicator` 都存在同报告期多行，且**两类冲突都有**（同公告日仅 `update_flag` 不同、公告日不同而 `update_flag` 相同）。`update_flag` 的语义**未定论**，不能按"0=最新"取用（第 4.3 节给出两个反例）。三表**都没有"更新时间"字段**。
8. **两张"已规划"表已实测通过**（第 4.6 节）：`cashflow` 与 `income` 完全同构（逐股取、1 年上限、同报告期多版本）；`adj_factor` 可按交易日取全市场（**5577 行/日**），逐股可一次取长区间，历史自 **2015-03** 起 → 11 年起点够用。

**各接口作用速查（只说接口本身是什么）：**

| 接口 | 作用 |
| --- | --- |
| `trade_cal` | 交易日历：每天是否开市、上一交易日是哪天 |
| `stock_basic` | 股票列表：名称、地区、行业、板块、交易所、上市状态、上市/退市日期等静态信息 |
| `suspend_d` | 每日停牌、复牌记录 |
| `daily_basic` | 每日行情与估值指标：收盘价、总市值、流通市值、PE/PB/PS、股息率、换手率、股本等 |
| `income` | 利润表：营业收入、净利润、归母净利润、EPS 等，含单季与累计两种口径 |
| `balancesheet` | 资产负债表：资产、负债、股东权益各科目 |
| `fina_indicator` | 财务指标：ROE、毛利率、资产负债率、流动比率等现成比率 |
| `dividend` | 分红送配方案：每股分红、送转比例、登记日/除权日、方案进度 |

另有三张非必需接口：`namechange`（股票曾用名变更记录）、`index_member_all`（申万行业成分股）、`disclosure_date`（财报预计与实际披露日期），细节见第 4.5、4.6 节。

**不接入 `stock_st`（2026-10-18）**：所有策略都排除 ST/\*ST，不需要单独维护 ST 名单；ST 直接从 `stock_basic.name` 前缀识别（实测同日与 `stock_st` 的 201 只完全一致）。注意 `stock_basic` 是当前快照，历史筛选日的 ST 状态需靠 `namechange` 还原。

## 2. 环境与复现方法

- Python 3.13 + `tushare` SDK 1.4.29（由 `uv.lock` 锁定），在项目目录用 `uv run --locked` 执行。
- token 与代理地址来自 `.env`，经 [`screener/config.py`](../screener/config.py) 的 `load_settings()` 读入；也可用 [`screener/sync.py`](../screener/sync.py) 的 `create_tushare_client()` 构造带代理的客户端。
- **本文与证据文件中都不含 token**，只有代理返回的 `request_id`（无害）。
- 复现命令（脚本会自行从 `.env` 读 token，不会打印）：

```bash
uv run --locked python -u docs/tushare-api-evidence/probe_apis.py
```

- 本机沙箱注意：需要设 `UV_CACHE_DIR` 指向项目内目录，否则 `uv` 写缓存被拒。
- 实测期间共约 300 次只读调用（第 1～15 轮），触发过数次限频（`rate_limited`）与上游不可用（`upstream_pool_exhausted`）。

## 3. 数据需求 → 接口映射

### 3.1 跨策略通用规则

| 规则 | 数据需求 | 接口与字段 |
| --- | --- | --- |
| 通用规则 1：日期 | 运行日的上一交易日；所选日或之前最近交易日 | `trade_cal`（`is_open`、`pretrade_date`）。**实测** `pretrade_date` 有值可用（20261016→20261015）；窗口第一行为空串，不要依赖首行 |
| 通用规则 2：期间末取价 | 期间内最后一个"未停牌且有收盘行情"的交易日 | `daily_basic`（有记录即有行情）+ `suspend_d`（停牌判定）。**实测**停牌股在 `daily` 和 `daily_basic` 中都没有记录，所以"期间内最后一个交易日"天然不会取到停牌日 |
| 通用规则 3：股票池 | 板块、行业、ST、退市整理期、停牌 | `stock_basic`（`market`/`exchange`/`industry`/`list_status`/`delist_date`，ST 看 `name` 前缀）+ `suspend_d` + `namechange`（退市整理期，备用） |

通用规则 3 的实测实现要点：

- **板块**：`stock_basic.market` 取值实测为 `主板`/`创业板`/`科创板`/`北交所`（官方输出说明里没列"北交所"，但实测确实返回，**实测**）。上市股票 5577 只中：主板 3197、创业板 1411、科创板 618、北交所 351。排除北交所即 `market != '北交所'`（或 `exchange != 'BSE'`）。
- **B 股**：`list_status='L'` 的 5577 只里**没有** 900xxx（沪 B）或 200xxx（深 B）（**实测**）。但 `list_status='D'`（退市 339 只）等其它状态是否含 B 股**未核验**。
- **ST/*ST**：**不接入 `stock_st`**（见 4.1 节），ST 直接从 `stock_basic.name` 前缀识别 —— 实测 `stock_st` 返回的 201 只（20261016）与同日 `stock_basic.name` 含 "ST" 的 201 只**完全一致**；且 `stock_st.type` 全为 `ST`、`type_name` 为 `风险警示板`，本来就**不能**区分 ST 与 \*ST，`name` 前缀才是唯一可用判据（**实测**）。注意 `stock_basic` 是当前快照，历史筛选日需靠 `namechange` 还原。
- **退市整理期**：`namechange` 实测能取到 `change_reason='退市整理期'`（000004.SZ，`name='国华退'`，`start_date=20260623`）（**实测**）；退市股 `stock_basic.name` 也带 "退"（如 `PT金田A(退)`、`国华退`）。
- **停牌**：`suspend_d(suspend_type='S', trade_date=...)` 实测当日 10 只。`daily` 官方明确"停牌期间不提供数据"（**官方**），与实测一致。
- **行业排除银行、保险**：见 4.2 节，`stock_basic.industry` 一次调用即可，**不需要**申万行业接口。

### 3.2 八项筛选条件

| 条件 | 指标 | 接口与字段 | 说明 |
| --- | --- | --- | --- |
| E01-1 | 格雷厄姆数 = PE_TTM × PB | `daily_basic.total_mv` + `income.n_income_attr_p`（单季）+ `balancesheet.total_hldr_eqy_exc_min_int` | PE 分母取最近 12 个月 4 个单季归母净利润之和；PB 分母取最近已披露归母股东权益，须 > 0 |
| E01-2 | 近 3 年平均市盈率 | 12 个季度末的 `daily_basic.total_mv` + 覆盖 15 个季度的单季 `income` | 每季样本 = 该季末最后一个未停牌交易日的总市值 ÷ 含该季的 4 个单季之和 |
| E01-3 | 近 3 年年报 ROE | `fina_indicator.roe_waa` | 按年报 `period`（如 `20241231`）取；**不要**用 `roe_dt` 替代 |
| E01-4 | 近 3 年年报归母净利润 | `income.n_income_attr_p`，`report_type='1'`（累计合并） | 与 E01-3 相同的 3 个会计年度 |
| E01-5 | 近 3 年每年股息率 | `dividend`（`end_date`/`ann_date`/`div_proc`/`cash_div_tax`/`base_share`）+ 该年度末 `daily_basic.total_mv` | 分子 = 同归属年度全部现金分红方案合计；**不要**用 `daily_basic.dv_ratio`/`dv_ttm` |
| E01-6 | 流动比率 | `balancesheet.total_cur_assets` / `total_cur_liab` | 同一报告期、同一版本 |
| E01-7 | 营运资本 / 长期负债 | 同上 + `balancesheet.total_ncl` | 长期负债取"非流动负债合计" |
| E01-8 | 总市值 > 50 亿元 | `daily_basic.total_mv`（所选交易日） | 单位万元，阈值 5,000,000 万元 |

## 4. 接口清单与实测细节

### 4.1 行情与基础数据

#### `trade_cal`（[doc_id=26](https://tushare.pro/document/2?doc_id=26)）

- 参数：`exchange='SSE'`、`start_date`、`end_date`、`is_open`（可选）。
- 字段：`exchange, cal_date, is_open, pretrade_date`（**实测**与官方一致）。
- **实测**：`trade_cal(exchange='SSE', start_date='20260915', end_date='20261017')` → 33 行；开市日 20261014/15/16；`pretrade_date` 正常填充。
- 注意：官方文档未写单次行数上限与频次（**官方**）。

#### `stock_basic`（[doc_id=25](https://tushare.pro/document/2?doc_id=25)）

- 参数：`ts_code`、`name`、`market`、`list_status`（`L`/`D`/`P`/`G`/`UN`）、`exchange`、`is_hs`、`fields`。
- 字段（**实测**）：`ts_code, symbol, name, area, industry, fullname, enname, cnspell, market, exchange, curr_type, list_status, list_date, delist_date, is_hs`。
- **实测**：`list_status='L'` → 5577 行；`list_status='D'` → 339 行；`list_status='P'` → 0 行；`exchange='SSE'` → 2467 行。
- **必须显式传 `fields`**：不传就报 `invalid_params`（见 6.1）。
- **`fields` 最多 13 个**（**实测 2026-10-18**）：13 个（字符串长度 113）成功、14 个（长度 111）报 `invalid_params`——卡的是**个数**不是字符串长度，`fields='*'` 也报错。要取全部 15 列必须拆成两次调用再按 `ts_code` 合并。该上限是 `stock_basic` 特有的：同一次实测里 `daily_basic` 取 14 个字段正常返回 5567 行。
- **`exchange=''` 在 `stock_basic` 上无害**（**实测 2026-10-18**）：`stock_basic(exchange='', list_status='L', fields=<≤13 个>)` → 5577 行，与不传 `exchange` 一致。第 6 节把 `exchange=''` 列为 `invalid_params` 触发条件，在 `stock_basic` 上不成立（对其它接口是否成立**未实测**）。
- 官方限量"每次最多 6000 行"，实测 5577 行未触顶，但股票数增长后会触顶（**官方+实测**）。

#### `stock_st`（[doc_id=397](https://tushare.pro/document/2?doc_id=397)）—— **已决定不接入**（2026-10-18）

- **原因**：本项目所有策略都排除 ST/\*ST，不需要单独维护 ST 名单；ST 直接从 `stock_basic.name` 前缀识别。
- **实测依据（保留）**：`stock_st(trade_date='20261016')` → 201 行；字段 `ts_code, name, trade_date, type, type_name`；`type` 全为 `ST`、`type_name` 为 `风险警示板`，**不能用 `type` 区分 ST 与 \*ST**。交叉验证：同日 `stock_basic.name` 含 "ST" 的也是 201 只，**两者一致** → 因此 `stock_basic` 已足够，不必多接一个接口、多建一张表。
- **注意**：`stock_basic` 是当前快照，历史筛选日的 ST 状态需靠 `namechange` 还原。

#### `suspend_d`（[doc_id=214](https://tushare.pro/document/2?doc_id=214)）

- 参数：`ts_code`、`trade_date`、`start_date`、`end_date`、`suspend_type`（`S` 停牌 / `R` 复牌）。
- 字段（**实测**）：`ts_code, trade_date, suspend_timing, suspend_type`。`trade_date` 是停复牌日期，**覆盖从停牌到复牌期间的连续日期**，不只是停牌当天（**官方**）。
- **实测（2026-10-18）**：`suspend_d(trade_date='20261016')` → 11 行（`S` 10 行 + `R` 1 行），**一次调用同时返回停牌与复牌记录**，不必按 `suspend_type` 分两次取。`suspend_timing` 的官方口径是「**日内停牌才有值，否则为空**」，不是"恒为 null"。
- **分段实测（2026-10-18）**：区间超过约 1 个月会被**静默截断到恰好 5000 行**（不报错，只是少数据）；单个自然月从不截断（2015-11 为 7673 行，2018-01 为 5017 行）；11 年区间直接报 `date_range_too_large`。官方**没有 `limit`/`offset` 分页参数**，按日期分段是唯一出路 —— 本项目取固定 **27 天**一段（2015Q4–2017Q4 九个季度共 143829 行 ≈ 1.6 万行/季度；2016 年约 6.5 万行，2024 年仅 3740 行）。
- 官方：数据从 2000-01-03 起，单次 5000 行（**官方**）。

#### `daily_basic`（[doc_id=32](https://tushare.pro/document/2?doc_id=32)）

- 参数：`trade_date`（按日全市场）或 `ts_code`（按股）+ `start_date`/`end_date`；**需要 `fields`**。
- 字段（**实测**，默认返回全集）：`ts_code, trade_date, close, turnover_rate, turnover_rate_f, volume_ratio, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm, total_share, float_share, free_share, total_mv, circ_mv`。
- **单位（实测确认）**：`total_mv` 是**万元**，且 `total_mv == close × total_share`（000001.SZ 20261016：`11.5 × 1940591.8198 = 22316805.93`，与接口值**完全相等**）。`total_share` 是**万股**。
- **实测覆盖**：单日 5567 行（20261016）、5370 行（20241231）、4119 行（20201231）、2545 行（20151231）→ 历史总市值可回溯，2015 年即 2545 只。
- **停牌股无记录**：20261016 的 10 只停牌股在 `daily_basic` 中 0 只出现（**实测**）。这与通用规则 2 的口径天然吻合。
- 官方：至少 2000 积分，单次最大 6000 行（**官方**）。实测 5567 行已接近上限，**若要一次取全市场，需留意 6000 上限**。

#### `daily`（[doc_id=27](https://tushare.pro/document/2?doc_id=27)）——第 1 集不需要

- **实测**：`daily(trade_date='20261016')` → 5567 行，耗时 **21.4 秒**（`fields` 精简后仍慢）；不传 `fields` 时 30 秒超时。
- 官方明确"未复权行情，停牌期间不提供数据"（**官方**）。
- 结论：第 1 集用 `daily_basic.close` 即可，不必接入 `daily`（**推断**）。

### 4.2 行业（第 1 集用 `stock_basic` 即可，申万接口备用）

#### `stock_basic.industry` —— **推荐路径**

- **实测**：110 个取值；`industry == '银行'` 42 只、`'保险'` 5 只（中国人寿、中国太保、新华保险、中国人保…）、`'证券'` 50 只、`'多元金融'` 23 只、`'房地产'` 0 只（房地产归在 `房地产开发` 等取值下）。
- 结论：**一次 `stock_basic` 调用**即可排除银行、保险，符合 [`strategies.md`](../strategies.md)「行业只排除银行、保险；证券、多元金融和房地产不因行业排除」。
- 注意：这是 tushare 自有分类（110 类），**不是申万分类**（**实测**）。策略文档要求"行业分类字段仍须在数据源接入时核验"，此处即为核验结果。

#### `index_member_all`（[doc_id=335](https://tushare.pro/document/2?doc_id=335)）—— 备用

- 参数：`ts_code`、`l1_code`、`l2_code`、`l3_code`、`is_new`、`limit`、`offset`、`fields`。
- 字段（**实测**）：`l1_code, l1_name, l2_code, l2_name, l3_code, l3_name, ts_code, name, in_date, out_date, is_new`。
- **实测按股票取可用**：`index_member_all(ts_code='600519.SH')` → 1 行（L1 食品饮料 / L2 白酒Ⅱ / L3 白酒Ⅲ，`in_date=20010731`，`is_new='Y'`）。
- **行业层级实测**：工商银行 L1=银行；中国平安 L1=非银金融 / **L2=保险Ⅱ**；中信证券 L1=非银金融 / **L2=证券Ⅱ**；中国太保、新华保险、\*ST天茂均为 L2=保险Ⅱ；万科A L1=房地产。
  → **排除保险必须用 L2='保险Ⅱ'，用 L1='非银金融' 会连证券、多元金融一起误杀**（**实测**）。
- **`l1_code` 过滤在本代理上失效**：`index_member_all(l1_code='801780.SI', fields=...)` 返回 3000 行，但内容是**全表切片**（样例是计算机、房地产、交通运输），并非银行（**实测**）。
- **`limit`/`offset` 分页可用**：`limit=2000&offset=0` → 2000 行；`offset=2000` → 1000 行，两页首行不同（**实测**）。全表约 3000 行，**少于 5577 只股票，覆盖不全**（**实测**），因此第 1 集不依赖它。
- 无参数仅传 `fields` → 连接被重置（响应过大，**实测**）。
- 官方另有 `index_member`，其文档页 404，**不要使用**（**官方**）。

#### `namechange`（[doc_id=100](https://tushare.pro/document/2?doc_id=100)）—— 备用

- 字段（**实测**）：`ts_code, name, start_date, end_date, ann_date, change_reason`。
- **实测**：`namechange(ts_code='000004.SZ')` → 17 行；样例 `name='国华退'`、`change_reason='退市整理期'`、`start_date='20260623'`。
- 用途：还原历史某日股票名称（判断历史 ST）或识别退市整理期。积分/限量官方未写（**官方**）。

### 4.3 财务三表

#### `income`（[doc_id=33](https://tushare.pro/document/2?doc_id=33)）

- 参数：`ts_code`（必填）、`period`、`report_type`、`start_date`/`end_date`（**公告日**语义）、`ann_date`、`f_ann_date`、`comp_type`、`fields`。
- 关键字段（**实测**）：`ts_code, ann_date, f_ann_date, end_date, report_type, comp_type, end_type, total_revenue, revenue, ..., n_income, n_income_attr_p, minority_gain, ebit, ebitda, update_flag`（默认返回全集，含 `update_flag`）。
- **单季可用（关键实测）**：`report_type='2'`（单季合并）实测可取。贵州茅台 2024 年四个单季归母净利润：24,065,262,374 / 17,630,348,609 / 19,131,941,135 / 25,400,594,303 元，**合计 86,228,146,422 元 = 年报累计 86,228,146,422 元，差 0 元**。
  → 因此**不需要**用累计值做差构造单季值（虽然做差也是合法备选）。
- `report_type` 官方 12 种取值：1=合并报表（默认）、2=单季合并、3=调整单季合并表、4=调整合并报表、5=调整前合并报表、6=母公司报表、7=母公司单季表、8=母公司调整单季表、9=母公司调整表、10=母公司调整前报表、11=母公司调整前合并报表、12=母公司调整前报表（**官方**）。指定 `report_type` 后实测每次只返回 1 行（**实测**）。
- `update_flag` 与多版本（**实测**，证据 `probe_out12.txt`、`probe_out13.txt`）：**同一报告期确实会返回多行**——12 只样本 100 个报告期中 `income` 有 10 组、`fina_indicator` 11 组、`balancesheet` 7 组出现多行（25 只样本时为 23 / 28 / 10 组）。多行组的 `update_flag` 组合几乎都是 `(0, 1)`，但也有 `(0, 0)`（两行同为 `0`，公告日不同）。
- **`update_flag` 语义未定论，不能按"0=最新"取用**（**实测**）：
  - 单行报告期的 flag 分布三表相反：`fina_indicator` `1`:35 / `0`:2、`income` `1`:35 / `0`:5、`balancesheet` `0`:39 / `1`:4。
  - 反例一（按 0 取会丢数据）：`fina_indicator` 603366.SH 20250630 同公告日两行，`update_flag=0` 那行 `roe_waa`/`roe_dt`/`eps` 全为空值，`update_flag=1` 那行有值。
  - 反例二（flag=0 反而更晚）：`income` 301479.SZ 20241231 两行，`update_flag=1` 的 `ann_date=20250221`、`update_flag=0` 的 `ann_date=20250428`，且归母净利润不同（165,216,968.40 / 165,227,269.14）。
  - 官方 FAQ 称 `1`=修正后、`0`=初始（**官方**），与本机实测不一致；接口页本身只写"更新标识"（**官方**）。
- **三表都没有"更新时间"字段**（**官方+实测**）：只有 `ann_date`（公告日）、`income.f_ann_date`（实际公告日）、`update_flag`，没有时间戳。因此"哪条最新"无时间戳可依靠，接入时应**全版本入库**，把"取哪一行"留作计算层口径。
- **区间上限（代理层）**：`income(ts_code='600519.SH', start_date='20240101', end_date='20261231')` → `date_range_too_large`；改为 1 年（20240101~20241231）→ 4 行，半年 → 2 行，一季 → 1 行（**实测**）。补充实测：**18 个月、2 年、30 个月全部失败**，1 年是安全上限（**实测**，`probe_out11.txt`）。**按 1 年分段是安全做法**。
- **不带 `ts_code` 的区间查询直接失败**（**实测**）：`income(start_date=..., end_date=...)` 不带 `ts_code`（试过 30 天区间与单日区间）→ `invalid_params`；代理要求财报查询必须带 `ts_code`（**实测**，`probe_out10.txt`）。
- **按 `ann_date` 取全市场财报不可用**（**实测**）：`income`/`balancesheet`/`fina_indicator` 按 `ann_date` 查询连续 9 次返回 `upstream_pool_exhausted`，而同会话内带 `ts_code` 的查询正常（**实测**，`probe_out10.txt`）。带 `ts_code` 时叠加 `ann_date` 可用（`income(ts_code='600519.SH', ann_date='20250430')` → 1 行）。→ **财报增量不能走"按公告日全市场"，必须用第 4.5 节的 `disclosure_date` 名单。**
- 官方：`ts_code` 必填，**只能按单只股票取历史**，全市场需 `*_vip`（**官方**）；金额字段官方**未标单位**（**官方**）。实测数值量级为元（如 `n_income_attr_p=86228146421.62` 对应 862.28 亿元）（**实测**）。

#### `balancesheet`（[doc_id=36](https://tushare.pro/document/2?doc_id=36)）

- 参数：`ts_code`（必填）、`period`、`report_type`、`start_date`/`end_date`、`ann_date`、`comp_type`、`fields`。
- 关键字段（**实测**，默认返回全集）：`total_cur_assets`（流动资产合计）、`total_cur_liab`（流动负债合计）、`total_ncl`（非流动负债合计）、`total_hldr_eqy_exc_min_int`（不含少数股东权益的股东权益合计）、`total_hldr_eqy_inc_min_int`、`total_assets`、`total_liab`、`treasury_share`、`oth_eqt_tools`（其他权益工具）、`report_type`、`update_flag`。
- **实测**：`balancesheet(ts_code='600519.SH', period='20241231')` → 1 行，四个所需科目齐全；`total_hldr_eqy_exc_min_int=233,105,984,399.47`。
- **实测**：按区间 `start_date='20240101', end_date='20261231'` → 13 行（约 11 个报告期，可能含调整版本）→ 接入时应按 `period` 精确取，不要依赖区间语义（**推断**）。
- **多版本实测**（`probe_out12.txt`、`probe_out13.txt`）：12 只样本 100 个报告期中 **7 组**出现同报告期多行。其中存在**同报告期、公告日不同、`update_flag` 相同**的冲突（如 301479.SZ 20241231 两行 `ann_date` 分别 20250221 / 20250428 且 `update_flag` 均为 `0`；第十二轮另见两行 `update_flag` 均为 `1`）→ **主键必须含 `ann_date`**，否则这类记录会互相覆盖。
- **实测**：1 年区间可用（`fina_indicator` 600519 20250101~20251231 → 5 行，其中 4 个报告期有 1 组重复）→ 与 `income` 同样受"单次 1 年"上限约束。
- `oth_eqt_tools` 的存在解释了为什么不能用 `daily_basic.pb`：官方 `pb` 分母是"净资产 − 其他权益工具"，与第 1 集"归母股东权益"定义不同（**官方+实测**）。
- 官方：金额字段未标单位（**官方**）；实测为元（**实测**）。

#### `fina_indicator`（[doc_id=79](https://tushare.pro/document/2?doc_id=79)）

- 参数：`ts_code`（必填）、`period`、`ann_date`、`start_date`/`end_date`（注意：这里是**报告期**语义，与 `income`/`balancesheet` 的公告日语义不同）（**官方**）。
- 关键字段（**实测**）：`roe=38.4283`、`roe_waa=36.02`、`roe_dt=38.434`、`current_ratio`、`working_capital`、`networking_capital`、`debt_to_assets=19.0448`、`end_date`、`ann_date`、`update_flag` 等。**该接口没有 `report_type`/`comp_type`/`f_ann_date`**（**实测+官方**）。
- **实测**：`roe_waa=36.02` 与贵州茅台 2024 年报未扣非加权 ROE 36.02 一致（与 [`sources/data-source-evaluation.md`](../sources/data-source-evaluation.md) 既有核对结论一致）。
- 官方限制：单次最多返回 **100 条**；按区间实测可取 18 行（20230101~20261231）（**官方+实测**）。
- **多版本实测**（`probe_out13.txt`）：12 只样本 100 个报告期中 **11 组**出现同报告期多行，且全部是**同公告日、仅 `update_flag` 不同**（如 603366.SH 20250630：`update_flag=1` 行有 `roe_waa=0.99`，`update_flag=0` 行 `roe_waa` 为空值）→ **主键必须含 `update_flag`**，否则这类记录会互相覆盖；同时**不能按"`update_flag=0` 是最新"取值**，否则会取到空值行。
- 注意：该接口自带 `current_ratio`、`working_capital` 等现成比率，但第 1 集**必须**用 `balancesheet` 科目自算，以执行策略文档规定的零分母规则（**推断**，依据 [`strategies.md`](../strategies.md) 的实现口径）。

### 4.4 分红

#### `dividend`（[doc_id=103](https://tushare.pro/document/2?doc_id=103)）

- 参数：`ts_code`、`ann_date`、`record_date`、`ex_date`、`imp_ann_date`（官方标全部可选但"至少一个不能为空"），外加 `fields`。**官方没有 `start_date`/`end_date` 参数**（`end_date` 只是输出字段）（**官方**）。
- 默认字段（**实测**）：`ts_code, end_date, ann_date, div_proc, stk_div, stk_bo_rate, stk_co_rate, cash_div, cash_div_tax, record_date, ex_date, pay_date, div_listdate, imp_ann_date`。
  → **默认字段里没有 `base_share` 和 `base_date`，必须显式传 `fields` 才能取到**（**实测**）。
- **按公告日全市场取可用（推荐路径）**：`dividend(ann_date='20250403', fields='ts_code,end_date,ann_date,div_proc,cash_div_tax,base_share')` → 141 行，`base_share` 正常返回（**实测**）。
- **单位自洽（实测）**：`cash_div_tax` 是**每股税前**分红（元/股），`base_share` 是**万股** → 乘积为**万元**。样例：603167.SH `0.4 × 46914.4503 = 18765.78` 万元；600963.SH `0.03014 × 175756.7854 = 5297.31` 万元。
- **`div_proc` 实测取值**：`预案`、`股东大会通过`、`实施`（贵州茅台 89 行中分别为 20 / 37 / 32；某公告日全市场 141 行中也同时出现 `股东大会通过` 与 `实施`）。官方**没有给出 `div_proc` 取值枚举表**（**官方**），"取消"等取值**未观测到**（**推断**）。
  → 同一笔分红会随进度出现多行，第 1 集"同一方案的进度记录不能重复计算"的去重要求**必须实现**（**实测支撑**）。
- **按股票 + `base_share` 反复失败**：`dividend(ts_code='600519.SH', fields='...base_share...')` 连续 4 次都返回 `upstream_pool_exhausted`，而同一时间按 `ann_date` 取全市场成功（**实测**）→ 建议**统一走按公告日取全市场**的路径，并按公告日分段增量更新。
- 官方：起始时间 2000-01-01（但同页样例含 1995 年数据，文档自相矛盾），单次 2000 行，每日 20~21 点更新，至少 2000 积分（**官方**）。

### 4.5 财报披露日期（`disclosure_date`）—— 同步增量的入口

#### `disclosure_date`（[doc_id=162](https://tushare.pro/document/2?doc_id=162)）

- 参数：`ts_code`、`end_date`（报告期）、`pre_date`（预计披露日）、`actual_date`（**实际披露日**）、`ann_date`，外加 `fields`。输出：`ts_code, ann_date, end_date, pre_date, actual_date, modify_date`（**官方**）。
- **为什么需要它**：财报三表既不能按 `ann_date` 取全市场（实测 `upstream_pool_exhausted`），也不能不带 `ts_code` 查询（实测 `invalid_params`），逐股全量重拉又要 1.4 小时。`disclosure_date` 提供"某天有哪些股票披露了财报"的名单，把增量调用量从"全市场"降到"当天披露的那几十到上千只"（**实测**）。
- **实测（`probe_out14.txt`）**：
  - `disclosure_date(end_date='20260930', fields=...)` → **5582 行**，覆盖全市场当期披露计划；其中 `actual_date` 已填写的 50 行（2026 三季报披露季刚开始）。
  - `disclosure_date(actual_date='20261016')` → **15 行**，即当天实际披露 15 只。
  - 端到端：对名单里 3 只股票（300688.SZ / 300610.SZ / 300018.SZ）按 2026 年区间拉 `income`/`balancesheet`/`fina_indicator`，**9 次调用全部成功**。
- **实测（`probe_out15.txt`）披露季单日峰值**（N 为实测披露只数，调用数与耗时按"1 + 4×N"推算，含第 4.6 节的 `cashflow`）：

| 报告期 | 单日最多披露 | 增量调用（1 + 4×N） | 耗时（按 200 次/分钟） |
| --- | --- | --- | --- |
| 2026 一季报 | **1236 只**（20260429） | 4945 次 | **约 25 分钟** |
| 2026 中报 | 765 只（20260829） | 3061 次 | 约 15.5 分钟 |
| 2025 年报 | 700 只（20260429） | 2801 次 | 约 14 分钟 |
| 2026 三季报（进行中） | 18 只（20261017） | 73 次 | 0.4 分钟 |

- **用法**：每次更新按"最近 7 天"的 `actual_date` 逐日取名单（覆盖迟报与更正），再对名单里的股票逐股拉四张财务表；`end_date`（报告期）查询可用于季度校验（**推断**）。
- **注意**：`disclosure_date` 记录的是"披露计划/实际披露日"，**财报更正公告不一定出现在名单里**，因此仍需保留回补窗口（见数据模型提案 D8）（**推断**）。

### 4.6 已规划待接入的两张表：`cashflow` 与 `adj_factor`

#### `cashflow`（[doc_id=44](https://tushare.pro/document/2?doc_id=44)）

- 服务的条件：自由现金流净现比（第 7 集）、资本支出比率（E04-5、E09-5）。
- **实测可用（`probe_out16.txt`）**：`cashflow(ts_code='600519.SH', start_date='20250101', end_date='20251231')` → 6 行；`n_cashflow_act`（经营活动现金流净额）与 `c_pay_acq_const_fiolta`（购建固定资产等支付的现金 = 资本支出）都有值；`free_cashflow` 字段名也被接受。
- **约束与 `income` 完全一致**：单次区间上限 1 年（2 年、11 年 → `date_range_too_large`）；不带 `ts_code` → `invalid_params`；按 `ann_date` 取全市场 → `upstream_pool_exhausted`。
- **同报告期多版本同样存在**：600519 的 20250930、20250331 各两行（`update_flag` 分别为 0/1）→ 数据模型提案 D3 的主键规则直接适用。

#### `adj_factor`（[doc_id=28](https://tushare.pro/document/2?doc_id=28)）

- 服务的条件：股价近 5 年分位（E02-2、E07-2）。`daily_basic.close` 是**未复权**价，不能用来算分位。
- **实测可用（`probe_out16.txt`、`probe_out17.txt`）**：
  - 按交易日取全市场：`adj_factor(trade_date='20261016')` → **5577 行**（含停牌股；同日 `daily_basic` 5567 行，差 10 只即当日停牌数）。
  - 逐股可一次取长区间：`adj_factor(ts_code='600519.SH', start_date='20050101', end_date='20261231')` → 372 行（**不受财报的 1 年上限约束**）。
  - 历史起点约 **2015-03**（600519 最早 20150317）；全市场覆盖自 2015-06 起（20150630 → 2815 行、20151008 → 2837 行、20151231 → 2884 行）；20150105 只有 1 行（仅个别股票有数据）。
- **两种拉法都可行**：按交易日取全市场（11 年约 2700 次 ≈ 13 分钟，天然增量 1 次/天）或逐股取长区间（5577 次）。**建议按交易日取**，与 `daily_basic` 共用同一套"逐日补差集"逻辑。

## 5. 实测确认的关键口径汇总

| 结论 | 证据 |
| --- | --- |
| `total_mv` 单位是万元，且恒等于 `close × total_share` | 000001.SZ 20261016 两值完全相等（22316805.93） |
| `income` 单季（`report_type='2'`）可用，且四单季之和 = 年报累计 | 600519.SH 2024：86,228,146,422 = 86,228,146,422，差 0 元 |
| 停牌股在 `daily` 与 `daily_basic` 中均无记录 | 20261016 十只停牌股，两个接口各 0 只命中 |
| 排除保险必须用申万 L2「保险Ⅱ」 | 中国平安/中国太保/新华保险 L2=保险Ⅱ；中信证券 L2=证券Ⅱ |
| `stock_basic.industry` 可直接识别银行、保险 | 银行 42 只、保险 5 只、证券 50 只、多元金融 23 只 |
| **`stock_basic.name` 可直接识别 ST**（与 `stock_st` 交叉验证一致，故不接入 `stock_st`） | 同日均 201 只 |
| 分红 `cash_div_tax × base_share` 单位为万元 | 0.4 × 46914.4503 = 18765.78 万元 |
| **同一报告期会返回多个版本**（三表都是） | 12 只样本 100 个报告期中：`fina_indicator` 11 组、`income` 10 组、`balancesheet` 7 组出现多行 |
| **多版本有两类冲突，主键须同时含 `ann_date` 与 `update_flag`** | 同公告日仅 flag 不同（`fina_indicator` 11 组、`income` 10 组）；公告日不同而 flag 相同（`balancesheet` 两行 flag 均为 `0`，另一例均为 `1`） |
| **`update_flag` 语义未定论，不能按"0=最新"取** | `fina_indicator` 603366.SH 20250630 的 `update_flag=0` 行 `roe_waa` 为空值；`income` 301479.SZ 20241231 的 `update_flag=0` 行公告日更晚（20250428 vs 20250221） |
| **三张财务表都没有"更新时间"字段** | 官方字段表与实测返回均无时间戳，只有 `ann_date`/`f_ann_date`/`update_flag` |
| **财报单次区间上限是 1 年** | 1 年区间成功；18 个月、2 年、30 个月均 `date_range_too_large` |
| **财报查询必须带 `ts_code`** | 不带 `ts_code` 的 `income` 区间查询（30 天与单日）均 `invalid_params` |
| **`disclosure_date(actual_date=某日)` 可给出当天披露名单** | 20261016 → 15 行；端到端对 3 只股票拉三表 9 次调用全部成功 |
| **披露季单日峰值 1236 只** | 2026 一季报 20260429 单日 1236 只 → 增量 1 + 4×1236 = 4945 次调用 ≈ 25 分钟 |
| **`cashflow` 与 `income` 完全同构** | 1 年区间 6 行；2 年/11 年 → `date_range_too_large`；不带 `ts_code` → `invalid_params`；按 `ann_date` 全市场 → `upstream_pool_exhausted`；20250930、20250331 各两行 |
| **`adj_factor` 按交易日取全市场 5577 行** | 20261016 → 5577 行（含停牌股），同日 `daily_basic` 5567 行（差 10 只即停牌数） |
| **`adj_factor` 逐股可一次取长区间，历史自 2015-03 起** | 600519 20050101-20261231 → 372 行，最早 20150317；全市场 20151008 → 2837 行 |
| `dividend` 默认字段不含 `base_share` | 默认 14 个字段中无 `base_share`/`base_date` |

## 6. 数据源与代理层约束（实现时必须处理）

本代理（`https://tl.kaixin8.top/tushare/sdk`）暴露的错误码与实测触发条件：

| 错误码 | 含义 | 实测触发 | 实现要求 |
| --- | --- | --- | --- |
| `invalid_params` | 业务参数缺失或格式不合法 | **不传 `fields`** 的 `stock_basic`/`index_classify`；**`stock_basic` 的 `fields` 超过 13 个**（见 4.1）；`exchange=''`（`stock_basic` 实测无害，见 4.1）；`index_member_all(l1_code=..., is_new='N')`；**财报区间查询不带 `ts_code`** | **所有调用显式传 `fields`**；**`stock_basic` 的 `fields` 拆到 ≤13 个**；不要传空字符串参数；**财报必须逐股取** |
| `unknown_api` | 接口不存在或未开放 | `income_vip`、`balancesheet_vip`、`fina_indicator_vip` | **不能依赖全市场财报批量接口**，必须逐股循环 |
| `date_range_too_large` | 历史区间超过单次安全范围 | `income` 按公告日跨 2.75 年；**财报跨 18 个月 / 2 年 / 30 个月** | 财报按**年**分段（实测 1 年安全，1 年 = 上限） |
| `rate_limited` | 请求额度达到限制，建议等 30 秒 | 连续调用 `stock_basic` 多值 `list_status` | 需要**退避重试**（30 秒级） |
| `upstream_pool_exhausted` | 上游数据当前不可用 | `dividend(ts_code=..., fields 含 base_share)` 连续 4 次；**`income`/`balancesheet`/`fina_indicator` 按 `ann_date` 取全市场连续 9 次** | 需要**重试 + 换查询路径**（分红改按 `ann_date`；财报改走 `disclosure_date` 名单） |
| 连接被重置 / `Read timed out` | 响应过大或上游慢 | `daily` 不传 `fields`（30 秒超时）、`index_member_all` 无参数、`daily_basic` 全市场偶发超时 | 默认 30 秒超时**不够**，建议 ≥120 秒 + 重试 |

其它实测行为：

- `index_classify(level='L1', src='SW2021')` 不带 `fields` → `invalid_params`；带 `fields` → **31 行**（SW2021 一级行业数），说明该接口可用，只是必须传 `fields`（**实测**）。
- `index_member_all` 的 `l1_code` 参数被**静默忽略**（返回全表切片而非该行业成分），且 `limit`/`offset` 可用（**实测**）。静默忽略参数比报错更危险，接入时要校验返回内容。
- `daily_basic` 单日 5567 行，接近官方 6000 行上限（**实测+官方**）。

## 7. 调用成本估算与建议

以全市场约 5577 只上市股票、第 1 集需要 15 个连续季度 + 3 个年报为基准（**推断**，基于实测的接口能力）：

**便宜的（每次运行约 20 次调用）**

| 接口 | 次数 | 说明 |
| --- | --- | --- |
| `stock_basic` | 2 | `L` 与 `D` 各一次（ST 判定所需的 `name` 也在其中） |
| `suspend_d` | ~13 | 筛选日 + 12 个季度末 |
| `daily_basic` | ~16 | 筛选日 + 12 个季末 + 3 个年度末 |
| `trade_cal` | 1~2 | 推导上一交易日与季末取价日 |

**贵的：财务三表逐股循环（2026-10-17 修正）**

原估的"6~7 次/股 ≈ 3.5 万次调用 ≈ 3 小时"**是错的**——财报单次区间上限实测为 **1 年**（18 个月即失败），逐股回填必须按年分段：

| 场景 | 调用次数 | 耗时（按 200 次/分钟） |
| --- | --- | --- |
| 首次全量回填 **11 年 × 4 张财务表**（`income`/`balancesheet`/`fina_indicator`/`cashflow`，5577 只 × 11 段） | 约 24.5 万次 | **约 20 小时** |
| 首次全量回填日频（`daily_basic`、`adj_factor`，按交易日） | 每张表约 2700 次 | 每张表约 13 分钟 |
| 仅 3 张财务表（不含 `cashflow`，第 1～2 集够用） | 约 18.4 万次 | 约 15 小时 |

→ 全量回填是"跑一夜到一天"的量级，**必须能中断续跑**（数据模型提案 D8 的差集机制）。数据模型提案 D7 已决定首次回填含全部已规划表（4 张财务表 + `adj_factor`）。

**日常增量：走 `disclosure_date` 名单（第 4.5 节）**

1 次调用取当天实际披露名单 → 对名单里的股票逐股拉四张财务表（1 + 4×N，含第 4.6 节的 `cashflow`）。平时 N 为十几到几十只（**<1.5 分钟**），披露季峰值 N=1236 只（**约 25 分钟**）。

**已放弃的备选：两阶段筛选**

原建议"先用约 20 次调用把股票池压小，再只对活下来的股票拉财报"，可把逐股调用量降一个数量级，但已决定**不做**（数据模型提案 D7：原始层全量镜像，不放列也不放行）。原因：原始层是跨策略共享的，两阶段只服务第 1 集，会让库里出现"只有第 1 集需要的股票"的数据空洞。

`dividend` 建议按 `ann_date` 逐日取全市场（3 年约 750 个公告日 ≈ 750 次调用、约 10 万行），比逐股取更稳（逐股 + `base_share` 实测失败）。

## 8. 待验证 / 未定项

1. **`update_flag` 的语义**：已实测"同一报告期会返回多行、两类冲突都存在"，但**哪个值代表最新仍未定论**（三表单行报告期的 flag 分布相反，且存在"按 0 取到空值行"与"flag=0 反而公告日更晚"两个反例）。当前处理：全版本入库，计算层口径待定。
2. **`f_ann_date` 能否作为"最新版本"的判据未验证**（只有 `income` 实测有该字段；`balancesheet` 未请求过）。
3. 分红去重的具体规则（同一 `end_date` 下 `预案`→`股东大会通过`→`实施` 如何合并、"取消"是否存在）**未验证**。
4. `list_status='D'`/`'P'` 等列表是否含 B 股**未核验**。
5. `index_member_all` 全表仅约 3000 行、少于 5577 只股票的原因**未知**；如将来需要申万口径，需重新核验覆盖度。
6. 财务字段的官方单位未标注（实测量级为元），接入时应做一次样本对账再固化。
7. 官方直连（`api.tushare.pro`）是否也有"`fields` 必填""财报区间上限 1 年""按 `ann_date` 取全市场不可用"等限制**未知**；本文所有约束结论都基于本机代理。
8. 单季 `income` 是否对**所有**公司（含金融类、亏损类）都存在 `report_type='2'` 记录**未验证**，只在贵州茅台 2024 年验证过。
9. **财报更正公告是否出现在 `disclosure_date` 名单里未验证**；若不出现在名单里，则更正只能靠回补窗口（重拉最近 7 天 + 季度校验）捕获。
10. `disclosure_date` 的 `end_date`（报告期）查询是否可用于"季度校验"（对比库内已有的报告期清单）**未验证**。

## 9. 与现有文档的关系

本文结论可以支持对下列文档做**后续**修订（除已标注"已同步"的一条外，本次未改）：

- ✅ **已同步（2026-10-17）**：[`docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md`](superpowers/specs/2026-10-11-tech-selection-architecture-design.md) 的「数据流」已补上"字段全部入库、不做行级取舍、历史起点 11 年、日常只补差集"，并新增「数据层设计」一节（分层模型、一接口一表、各表主键、多版本规则、同步差集与增量路径、首次回填成本、数据库写法）；「明确留到实现时决定」中与 11 年起点冲突的表述已改。

- [`strategies.md`](../strategies.md) 第 280 行「最近 12 个月归母净利润 …… **公式已定，字段待核验**」：单季字段已实测可用，可升级为"字段已核验"。
- [`strategies.md`](../strategies.md) 第 272 行附近表格中「当前及历史 `PE_TTM`」「当前 `PB`」的"需要计算"结论**保持不变**，本文未推翻任何既有口径。
- [`strategies.md`](../strategies.md) 第 286 行「流动比率、营运资本/长期负债」：所需三个 `balancesheet` 科目已实测齐全。
- [`strategies.md`](../strategies.md) 第 21 行通用规则 3「股票状态和行业分类字段仍须在数据源接入时核验」：本文第 3.1、4.2 节即为核验结果（`market` 可识别北交所、`industry` 可排除银行/保险、`stock_basic.name` 可判 ST、`namechange` 可识别退市整理期）。
- [`sources/data-source-evaluation.md`](../sources/data-source-evaluation.md) 第 41 行「实际账号能否调用、历史年份是否齐全、停牌日与退市股票的覆盖、分页及频率限制，均待接入测试」：本文已给出实测答案（全部可调用；历史总市值回溯到 2015 年；停牌股无记录；分页与限频行为见第 6 节）。
- [`docs/data-model-proposal.md`](data-model-proposal.md)：该提案的 D3（多版本与主键）、D7（全量下载的代价与时间起点）、D8（同步进度与差集、财报增量路径）均已按本文第 4.3、4.5、7 节的实测结论更新。本文是该提案的**证据来源**，两者结论必须保持一致。
- [`docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md`](superpowers/specs/2026-10-11-tech-selection-architecture-design.md) 第 68 行「Tushare Token 只从本机环境变量读取」：当前实现是 [`.env`](../.env.example) 文件加环境变量、由 [`screener/config.py`](../screener/config.py) 统一读取，措辞可更新。该文档中**没有**"代理地址固定为某 URL"的表述（本次核对确认），与本评估无关。

## 10. 证据文件清单

原始脚本与输出均入库于 [`tushare-api-evidence/`](tushare-api-evidence/)：

| 文件 | 覆盖内容 |
| --- | --- |
| `probe_apis.py` / `probe_out.txt` | 第一轮：交易日历、股票列表、行情、每日指标（含 `total_mv` 单位核对）、停复牌、曾用名、行业、财务三表、分红 |
| `probe_apis2.py` / `probe_out2.txt` | 第二轮：`stock_basic` 失败复现、`daily` 全市场、`index_classify` 参数组合、`stock_st`、单季四季度求和验证 |
| `probe_apis3.py` / `probe_out3.txt` | 第三轮：停牌股是否仍在行情接口、退市名单、ST 字段 |
| `probe_apis4.py` / `probe_out4.txt` | 第四轮：`fields` 必需性矩阵、`div_proc` 分布、`date_range_too_large` |
| `probe_apis5.py` / `probe_out5.txt` | 第五轮：银行/保险/证券的行业层级、历史行情覆盖、`pretrade_date`、`income` 分段 |
| `probe_apis6.py` / `probe_out6.txt` | 第六轮：`*_vip` 不存在、分红按公告日取 `base_share`、B 股与北交所分布 |
| `probe_apis7.py` / `probe_out7.txt` | 第七轮：`l1_code` 过滤失效、保险 L2 逐股对照 |
| `probe_apis8.py` / `probe_out8.txt` | 第八轮：`stock_basic.industry` 银行/保险数量、`limit`/`offset` 分页 |
| `probe_apis9.py` / `probe_out9.txt` | 第九轮：`ann_date` 增量探测（整轮失败：限频后连续 `upstream_pool_exhausted`；仅测到"`income` 区间不带 `ts_code` → `invalid_params`"） |
| `probe_apis10.py` / `probe_out10.txt` | 第十轮：健康检查后重测——`ann_date` 全市场仍全部失败；`income` 单股 1 年区间成功（4 行、0 重复组）；区间不带 `ts_code` 失败；3 年区间 `date_range_too_large` |
| `probe_apis11.py` / `probe_out11.txt` | 第十一轮：区间上限定位（18 个月/2 年/30 个月全部失败）；带 `ts_code` 叠加 `ann_date` 可用；`dividend` 按 `ann_date` 可用；`fina_indicator` 单股 1 年区间 5 行（**首次发现同报告期多行**） |
| `probe_apis12.py` / `probe_out12.txt` | 第十二轮：25 只股票 × 3 表的多版本普查（`fina_indicator` 28 组、`income` 23 组、`balancesheet` 10 组多行） |
| `probe_apis13.py` / `probe_out13.txt` + `probe13_*.csv` | 第十三轮：`update_flag` 语义判定（单行报告期 flag 分布三表相反；两个反例：空值行与更晚公告日行）；原始行存成 CSV |
| `probe_apis14.py` / `probe_out14.txt` | 第十四轮：`disclosure_date` 可用性（当期 5582 行、`actual_date` 取当天名单 15 行、端到端逐股拉三表成功） |
| `probe_apis15.py` / `probe_out15.txt` | 第十五轮：披露季单日峰值（2026 一季报 20260429 单日 1236 只 → 增量约 18.5 分钟） |
| `probe_apis16.py` / `probe_out16.txt` | 第十六轮：`cashflow` 与 `adj_factor` 补测（`cashflow` 与 `income` 同构、同报告期多版本；`adj_factor` 按日全市场 5577 行、逐股可长区间） |
| `probe_apis17.py` / `probe_out17.txt` | 第十七轮：`adj_factor` 历史起点（600519 最早 20150317；全市场自 2015-06 起完整；20150105 仅 1 行） |

脚本从 `.env` 读取 token（经 `load_settings()`），**不打印 token**；输出文件里只有接口返回的字段、行数、样例行与代理 `request_id`。

这些脚本按"实测证据原样保留"处理，已被 [`pyproject.toml`](../pyproject.toml) 的 `extend-exclude` 排除在 Ruff 检查与格式化之外（因此它们不符合项目的 lint/格式规范是**故意**的，不要"顺手修好"）。要重新验证时直接运行即可，脚本会自行定位仓库根目录并读取 `.env`。
