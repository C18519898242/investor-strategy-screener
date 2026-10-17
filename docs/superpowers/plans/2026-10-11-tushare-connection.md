# Tushare 连接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpower-subagent-driven-development (recommended) or superpower-executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `screener/sync.py` 中固定 Tushare 的连接方式，使后续数据接口都通过同一个已配置客户端调用。

**Architecture:** 程序只从环境变量读取 Key，再把 SDK 的请求地址改到指定的代理服务。骨架计划中的 `fetch` 继续负责具体数据集；本计划不决定表结构，也不拉取业务数据。

**Tech Stack:** 已安装的 `tushare` Python SDK、`TUSHARE_TOKEN` 环境变量。

**Spec:** `docs/superpowers/specs/2026-10-11-tech-selection-architecture-design.md`

## Global Constraints

- 代理地址固定为 `https://tl.kaixin8.top/tushare/sdk`。
- Key 只从 `TUSHARE_TOKEN` 读取，不写入代码、SQLite、HTML、日志或 Git。
- 构造顺序固定为：`pro = ts.pro_api(token)`，随后 `pro._DataApi__http_url = "https://tl.kaixin8.top/tushare/sdk"`。
- 只有 `screener/sync.py` 可以导入 `tushare` 或设置该地址。
- 本计划不调用具体数据接口，不决定保存哪些字段。

## Review Focus

- 环境变量缺失：抛出已有的 `SyncError`，不发出网络请求。
- 返回的客户端必须已经指向代理地址，不能保留 SDK 默认地址。
- 源码和测试不包含真实 Key。

---

### Task 1: 客户端构造

**Files:**
- Modify: `screener/sync.py`
- Test: `tests/test_tushare_connection.py`

**Interfaces:**
- Consumes: `read_token(env: Mapping[str, str]) -> str`，由骨架计划定义。
- Produces: `create_tushare_client(env: Mapping[str, str]) -> object`。返回对象的 `_DataApi__http_url` 等于 `https://tl.kaixin8.top/tushare/sdk`。缺少环境变量时抛出 `SyncError("缺少环境变量 TUSHARE_TOKEN")`。

- [ ] **Step 1: Write the failing test**

使用假的 `tushare` 模块，避免测试联网。断言传入的 Token 原样交给 `pro_api`，返回对象的地址等于上述代理地址，并且源码文件不含测试中使用的假 Token。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_tushare_connection -v`

Expected: FAIL because `create_tushare_client` does not exist.

- [ ] **Step 3: Implement `create_tushare_client(env: Mapping[str, str]) -> object`**

按 Global Constraints 的两行顺序构造客户端。函数不打印 Token，也不在异常文本中拼接 Token。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_tushare_connection -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add screener/sync.py tests/test_tushare_connection.py
git commit -m "Configure the Tushare client from the environment"
```
