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

1. 第 1 集需要 **9 个必需接口**：`trade_cal`、`stock_basic`、`stock_st`、`suspend_d`、`daily_basic`、`income`、`balancesheet`、`fina_indicator`、`dividend`。另有 2 个按需接口（`namechange`、`index_member_all`）。
2. **`daily` 不需要**。第 1 集只要收盘价和总市值，`daily_basic.close` 与 `daily_basic.total_mv` 已覆盖，且 `daily` 明显更慢。
3. **财务三表没有全市场批量接口**：`income_vip`/`balancesheet_vip`/`fina_indicator_vip` 在本账号返回 `unknown_api`。财报只能**按单只股票**循环取，这是整个同步设计里最贵的部分，建议做成两阶段筛选（第 7 节）。
4. **`income` 的单季报表（`report_type='2'`）可用**，实测四个单季之和与年报累计**完全相等**（差 0 元）。因此 [`strategies.md`](../strategies.md) 中「最近 12 个月归母净利润：公式已定，字段待核验」这一行，可以按"字段已核验"处理。
5. **行业排除只要 1 次调用**：`stock_basic.industry` 直接含「银行」42 只、「保险」5 只，不必逐股查行业。注意申万口径下保险在 **L2「保险Ⅱ」**，用 L1「非银金融」会误杀证券（实测）。
6. **代理层有 6 条硬约束**（第 6 节）：必须显式传 `fields`、单次区间上限、限频、偶发不可用、慢查询超时、部分参数被静默忽略。实现时必须按这些约束写重试与分段逻辑。

## 2. 环境与复现方法

- Python 3.13 + `tushare` SDK 1.4.29（由 `uv.lock` 锁定），在项目目录用 `uv run --locked` 执行。
- token 与代理地址来自 `.env`，经 [`screener/config.py`](../screener/config.py) 的 `load_settings()` 读入；也可用 [`screener/sync.py`](../screener/sync.py) 的 `create_tushare_client()` 构造带代理的客户端。
- **本文与证据文件中都不含 token**，只有代理返回的 `request_id`（无害）。
- 复现命令（脚本会自行从 `.env` 读 token，不会打印）：

```bash
uv run --locked python -u docs/tushare-api-evidence/probe_apis.py
```

- 本机沙箱注意：需要设 `UV_CACHE_DIR` 指向项目内目录，否则 `uv` 写缓存被拒。
- 实测期间共约 80 次只读调用，触发过 1 次限频（`rate_limited`）。

## 3. 数据需求 → 接口映射

### 3.1 跨策略通用规则

| 规则 | 数据需求 | 接口与字段 |
| --- | --- | --- |
| 通用规则 1：日期 | 运行日的上一交易日；所选日或之前最近交易日 | `trade_cal`（`is_open`、`pretrade_date`）。**实测** `pretrade_date` 有值可用（20261016→20261015）；窗口第一行为空串，不要依赖首行 |
| 通用规则 2：期间末取价 | 期间内最后一个"未停牌且有收盘行情"的交易日 | `daily_basic`（有记录即有行情）+ `suspend_d`（停牌判定）。**实测**停牌股在 `daily` 和 `daily_basic` 中都没有记录，所以"期间内最后一个交易日"天然不会取到停牌日 |
| 通用规则 3：股票池 | 板块、行业、ST、退市整理期、停牌 | `stock_basic`（`market`/`exchange`/`industry`/`list_status`/`delist_date`）+ `stock_st`（按日 ST 名单）+ `suspend_d` + `namechange`（退市整理期，备用） |

通用规则 3 的实测实现要点：

- **板块**：`stock_basic.market` 取值实测为 `主板`/`创业板`/`科创板`/`北交所`（官方输出说明里没列"北交所"，但实测确实返回，**实测**）。上市股票 5577 只中：主板 3197、创业板 1411、科创板 618、北交所 351。排除北交所即 `market != '北交所'`（或 `exchange != 'BSE'`）。
- **B 股**：`list_status='L'` 的 5577 只里**没有** 900xxx（沪 B）或 200xxx（深 B）（**实测**）。但 `list_status='D'`（退市 339 只）等其它状态是否含 B 股**未核验**。
- **ST/*ST**：`stock_st(trade_date=...)` 实测返回 201 行（20261016），字段 `ts_code, name, trade_date, type, type_name`；`type` 实测**全部**是 `ST`、`type_name` 是 `风险警示板`，**不能用 `type` 区分 ST 与 \*ST，要看 `name` 前缀**。交叉验证：同日 `stock_basic.name` 含 "ST" 的也是 201 只，两者一致（**实测**）。
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
- 官方限量"每次最多 6000 行"，实测 5577 行未触顶，但股票数增长后会触顶（**官方+实测**）。

#### `stock_st`（[doc_id=397](https://tushare.pro/document/2?doc_id=397)）

- 参数：`trade_date`。
- 字段（**实测**）：`ts_code, name, trade_date, type, type_name`。
- **实测**：`stock_st(trade_date='20261016')` → 201 行；`type` 全为 `ST`，`type_name` 为 `风险警示板`；样例 `*ST美丽`、`*ST康佳A`。
- 用途：判断**筛选日**是否 ST/*ST。区分 ST 与 \*ST 只能看 `name` 前缀（**实测**）。
- 官方：3000 积分起，单次最大 1000 行，数据从 20000101 开始（**官方**）。

#### `suspend_d`（[doc_id=214](https://tushare.pro/document/2?doc_id=214)）

- 参数：`ts_code`、`trade_date`、`start_date`、`end_date`、`suspend_type`（`S` 停牌 / `R` 复牌）。
- 字段（**实测**）：`ts_code, trade_date, suspend_timing, suspend_type`。
- **实测**：`suspend_d(trade_date='20261016', suspend_type='S')` → 10 行；`suspend_timing` 为 `null`（非日内停牌）。
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
- `update_flag`：官方 FAQ 说明 `1`=修正后数据、`0`=初始数据，用于识别财务数据修正导致的**重复记录**，即**同一报告期会返回多条**（**官方**）。接口页本身只写"更新标识"（**官方**）。实测默认字段集里包含 `update_flag`（**实测**），但"取最新修订版本"的具体排序规则**未验证**（**推断**）。
- **区间上限（代理层）**：`income(ts_code='600519.SH', start_date='20240101', end_date='20261231')` → `date_range_too_large`；改为 1 年（20240101~20241231）→ 4 行，半年 → 2 行，一季 → 1 行（**实测**）。**按 1 年分段是安全做法**。
- 官方：`ts_code` 必填，**只能按单只股票取历史**，全市场需 `*_vip`（**官方**）；金额字段官方**未标单位**（**官方**）。实测数值量级为元（如 `n_income_attr_p=86228146421.62` 对应 862.28 亿元）（**实测**）。

#### `balancesheet`（[doc_id=36](https://tushare.pro/document/2?doc_id=36)）

- 参数：`ts_code`（必填）、`period`、`report_type`、`start_date`/`end_date`、`ann_date`、`comp_type`、`fields`。
- 关键字段（**实测**，默认返回全集）：`total_cur_assets`（流动资产合计）、`total_cur_liab`（流动负债合计）、`total_ncl`（非流动负债合计）、`total_hldr_eqy_exc_min_int`（不含少数股东权益的股东权益合计）、`total_hldr_eqy_inc_min_int`、`total_assets`、`total_liab`、`treasury_share`、`oth_eqt_tools`（其他权益工具）、`report_type`、`update_flag`。
- **实测**：`balancesheet(ts_code='600519.SH', period='20241231')` → 1 行，四个所需科目齐全；`total_hldr_eqy_exc_min_int=233,105,984,399.47`。
- **实测**：按区间 `start_date='20240101', end_date='20261231'` → 13 行（约 11 个报告期，可能含调整版本）→ 接入时应按 `period` 精确取，不要依赖区间语义（**推断**）。
- `oth_eqt_tools` 的存在解释了为什么不能用 `daily_basic.pb`：官方 `pb` 分母是"净资产 − 其他权益工具"，与第 1 集"归母股东权益"定义不同（**官方+实测**）。
- 官方：金额字段未标单位（**官方**）；实测为元（**实测**）。

#### `fina_indicator`（[doc_id=79](https://tushare.pro/document/2?doc_id=79)）

- 参数：`ts_code`（必填）、`period`、`ann_date`、`start_date`/`end_date`（注意：这里是**报告期**语义，与 `income`/`balancesheet` 的公告日语义不同）（**官方**）。
- 关键字段（**实测**）：`roe=38.4283`、`roe_waa=36.02`、`roe_dt=38.434`、`current_ratio`、`working_capital`、`networking_capital`、`debt_to_assets=19.0448`、`end_date`、`ann_date`、`update_flag` 等。**该接口没有 `report_type`/`comp_type`/`f_ann_date`**（**实测+官方**）。
- **实测**：`roe_waa=36.02` 与贵州茅台 2024 年报未扣非加权 ROE 36.02 一致（与 [`sources/data-source-evaluation.md`](../sources/data-source-evaluation.md) 既有核对结论一致）。
- 官方限制：单次最多返回 **100 条**；按区间实测可取 18 行（20230101~20261231）（**官方+实测**）。
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

## 5. 实测确认的关键口径汇总

| 结论 | 证据 |
| --- | --- |
| `total_mv` 单位是万元，且恒等于 `close × total_share` | 000001.SZ 20261016 两值完全相等（22316805.93） |
| `income` 单季（`report_type='2'`）可用，且四单季之和 = 年报累计 | 600519.SH 2024：86,228,146,422 = 86,228,146,422，差 0 元 |
| 停牌股在 `daily` 与 `daily_basic` 中均无记录 | 20261016 十只停牌股，两个接口各 0 只命中 |
| 排除保险必须用申万 L2「保险Ⅱ」 | 中国平安/中国太保/新华保险 L2=保险Ⅱ；中信证券 L2=证券Ⅱ |
| `stock_basic.industry` 可直接识别银行、保险 | 银行 42 只、保险 5 只、证券 50 只、多元金融 23 只 |
| `stock_st` 与 `stock_basic.name` 含 ST 的数量一致 | 同日均 201 只 |
| 分红 `cash_div_tax × base_share` 单位为万元 | 0.4 × 46914.4503 = 18765.78 万元 |
| `dividend` 默认字段不含 `base_share` | 默认 14 个字段中无 `base_share`/`base_date` |

## 6. 数据源与代理层约束（实现时必须处理）

本代理（`https://tl.kaixin8.top/tushare/sdk`）暴露的错误码与实测触发条件：

| 错误码 | 含义 | 实测触发 | 实现要求 |
| --- | --- | --- | --- |
| `invalid_params` | 业务参数缺失或格式不合法 | **不传 `fields`** 的 `stock_basic`/`index_classify`；`exchange=''`；`index_member_all(l1_code=..., is_new='N')` | **所有调用显式传 `fields`**；不要传空字符串参数 |
| `unknown_api` | 接口不存在或未开放 | `income_vip`、`balancesheet_vip`、`fina_indicator_vip` | **不能依赖全市场财报批量接口**，必须逐股循环 |
| `date_range_too_large` | 历史区间超过单次安全范围 | `income` 按公告日跨 2.75 年 | 财报按**年**分段（实测 1 年安全） |
| `rate_limited` | 请求额度达到限制，建议等 30 秒 | 连续调用 `stock_basic` 多值 `list_status` | 需要**退避重试**（30 秒级） |
| `upstream_pool_exhausted` | 上游数据当前不可用 | `dividend(ts_code=..., fields 含 base_share)` 连续 4 次 | 需要**重试 + 换查询路径**（改按 `ann_date`） |
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
| `stock_basic` | 2 | `L` 与 `D` 各一次 |
| `stock_st` | 1 | 筛选日 |
| `suspend_d` | ~13 | 筛选日 + 12 个季度末 |
| `daily_basic` | ~16 | 筛选日 + 12 个季末 + 3 个年度末 |
| `trade_cal` | 1~2 | 推导上一交易日与季末取价日 |

**贵的：财务三表逐股循环**

`income`（按年分段约 4~5 次/股）+ `balancesheet`（1 次/股）+ `fina_indicator`（1 次/股）≈ 6~7 次/股 × 5577 只 ≈ **3.5 万次调用**。按 2000 积分档 200 次/分钟估算约 **3 小时**，且必然触发限频退避。

**建议：两阶段筛选**

1. 第一阶段只花约 20 次调用，用 `daily_basic`（E01-8 总市值 > 50 亿）、`stock_basic`（板块、行业排除银行/保险）、`stock_st`（ST）、`suspend_d`（停牌）把股票池从 5577 只压到很小的规模。
2. 第二阶段只对活下来的股票拉 `income`/`balancesheet`/`fina_indicator`，再算 E01-1～E01-7。

这样逐股调用量可降一个数量级。注意这不改变筛选结果口径：被第一阶段排除的股票本来就不满足 E01-8 或股票池规则。

`dividend` 建议按 `ann_date` 逐日取全市场（3 年约 750 个公告日 ≈ 750 次调用、约 10 万行），比逐股取更稳（逐股 + `base_share` 实测失败）。

## 8. 待验证 / 未定项

1. `update_flag` 修订版本的排序规则（`ann_date` / `f_ann_date` / `update_flag` 三者如何决定"最新版本"）**未验证**。
2. 分红去重的具体规则（同一 `end_date` 下 `预案`→`股东大会通过`→`实施` 如何合并、"取消"是否存在）**未验证**。
3. `list_status='D'`/`'P'` 等列表是否含 B 股**未核验**。
4. `index_member_all` 全表仅约 3000 行、少于 5577 只股票的原因**未知**；如将来需要申万口径，需重新核验覆盖度。
5. 财务字段的官方单位未标注（实测量级为元），接入时应做一次样本对账再固化。
6. 官方直连（`api.tushare.pro`）是否也有"`fields` 必填""区间上限"等限制**未知**；本文所有约束结论都基于本机代理。
7. 单季 `income` 是否对**所有**公司（含金融类、亏损类）都存在 `report_type='2'` 记录**未验证**，只在贵州茅台 2024 年验证过。

## 9. 与现有文档的关系

本文结论可以支持对下列文档做**后续**修订（本次未改）：

- [`strategies.md`](../strategies.md) 第 280 行「最近 12 个月归母净利润 …… **公式已定，字段待核验**」：单季字段已实测可用，可升级为"字段已核验"。
- [`strategies.md`](../strategies.md) 第 272 行附近表格中「当前及历史 `PE_TTM`」「当前 `PB`」的"需要计算"结论**保持不变**，本文未推翻任何既有口径。
- [`strategies.md`](../strategies.md) 第 286 行「流动比率、营运资本/长期负债」：所需三个 `balancesheet` 科目已实测齐全。
- [`strategies.md`](../strategies.md) 第 21 行通用规则 3「股票状态和行业分类字段仍须在数据源接入时核验」：本文第 3.1、4.2 节即为核验结果（`market` 可识别北交所、`industry` 可排除银行/保险、`stock_st` 可判 ST、`namechange` 可识别退市整理期）。
- [`sources/data-source-evaluation.md`](../sources/data-source-evaluation.md) 第 41 行「实际账号能否调用、历史年份是否齐全、停牌日与退市股票的覆盖、分页及频率限制，均待接入测试」：本文已给出实测答案（全部可调用；历史总市值回溯到 2015 年；停牌股无记录；分页与限频行为见第 6 节）。
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

脚本从 `.env` 读取 token（经 `load_settings()`），**不打印 token**；输出文件里只有接口返回的字段、行数、样例行与代理 `request_id`。

这些脚本按"实测证据原样保留"处理，已被 [`pyproject.toml`](../pyproject.toml) 的 `extend-exclude` 排除在 Ruff 检查与格式化之外（因此它们不符合项目的 lint/格式规范是**故意**的，不要"顺手修好"）。要重新验证时直接运行即可，脚本会自行定位仓库根目录并读取 `.env`。
