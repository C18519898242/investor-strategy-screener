"""复刻 Tushare 代理的实测行为，供各测试共用。

两条都是 2026-10-18 实测出来的，实现依赖它们，所以假客户端必须照做：

- **只返回 `fields` 里点名的列**。真实代理就是这样，不照做会让"两次调用按 `ts_code` 合并"
  的逻辑失真（假数据全列返回时合并会撞出 `_x`/`_y` 后缀）。
- **`stock_basic` 的 `fields` 超过 13 个报 `invalid_params`**。13 个（长度 113）成功、
  14 个（长度 111）失败 —— 卡的是个数不是字符串长度，`fields='*'` 也报错。
"""

import pandas as pd

from screener.sync.balancesheet import COLUMNS as BALANCESHEET_COLUMNS
from screener.sync.cashflow import COLUMNS as CASHFLOW_COLUMNS
from screener.sync.daily_basic import COLUMNS as DAILY_BASIC_COLUMNS
from screener.sync.dividend import COLUMNS as DIVIDEND_COLUMNS
from screener.sync.fina_indicator import COLUMNS as FINA_INDICATOR_COLUMNS
from screener.sync.income import COLUMNS as INCOME_COLUMNS
from screener.sync.stock_basic import COLUMNS as STOCK_BASIC_COLUMNS
from screener.sync.stock_st import COLUMNS as STOCK_ST_COLUMNS

STOCK_BASIC_FIELD_LIMIT = 13


def stock_basic_row(**overrides):
    """一行 stock_basic 的 15 列，默认全是空串。"""
    return {**dict.fromkeys(STOCK_BASIC_COLUMNS, ""), **overrides}


def daily_basic_row(**overrides):
    """一行 daily_basic 的 18 列，默认全是 0.0，ts_code/trade_date 留空串。"""
    return {
        **dict.fromkeys(DAILY_BASIC_COLUMNS, 0.0),
        "ts_code": "",
        "trade_date": "",
        **overrides,
    }


def income_row(**overrides):
    """一行 income 的 94 列，默认全是 None。"""
    return {**dict.fromkeys(INCOME_COLUMNS, None), **overrides}


def balancesheet_row(**overrides):
    """一行 balancesheet 的 157 列，默认全是 None。"""
    return {**dict.fromkeys(BALANCESHEET_COLUMNS, None), **overrides}


def cashflow_row(**overrides):
    """一行 cashflow 的 97 列，默认全是 None。"""
    return {**dict.fromkeys(CASHFLOW_COLUMNS, None), **overrides}


def fina_indicator_row(**overrides):
    """一行 fina_indicator 的 109 列，默认全是 None。"""
    return {**dict.fromkeys(FINA_INDICATOR_COLUMNS, None), **overrides}


def dividend_row(**overrides):
    """一行 dividend 的 16 列，默认全是 None。"""
    return {**dict.fromkeys(DIVIDEND_COLUMNS, None), **overrides}


def stock_st_row(**overrides):
    """一行 stock_st 的 5 列，默认全是空串。"""
    return {**dict.fromkeys(STOCK_ST_COLUMNS, ""), **overrides}


class FakeTushareClient:
    """按 `fields` 裁剪列的假客户端，不联网。"""

    def __init__(
        self,
        *,
        stock_basic_rows=(),
        stock_st_rows=(),
        trade_cal_rows=(),
        suspend_d_rows=(),
        daily_basic_rows=(),
        income_rows=(),
        balancesheet_rows=(),
        cashflow_rows=(),
        fina_indicator_rows=(),
    ):
        self.stock_basic_rows = list(stock_basic_rows)
        self.stock_st_rows = list(stock_st_rows)
        self.trade_cal_rows = list(trade_cal_rows)
        self.suspend_d_rows = list(suspend_d_rows)
        self.daily_basic_rows = list(daily_basic_rows)
        self.income_rows = list(income_rows)
        self.balancesheet_rows = list(balancesheet_rows)
        self.cashflow_rows = list(cashflow_rows)
        self.fina_indicator_rows = list(fina_indicator_rows)
        self.calls = []

    def stock_basic(self, **params):
        fields = self._record("stock_basic", params)
        if len(fields) > STOCK_BASIC_FIELD_LIMIT:
            raise Exception("[invalid_params] 业务参数缺失或格式不合法")
        return self._slice(self.stock_basic_rows, fields)

    def trade_cal(self, **params):
        return self._slice(self.trade_cal_rows, self._record("trade_cal", params))

    def stock_st(self, **params):
        """按交易日期取当天的 ST 名单。"""
        fields = self._record("stock_st", params)
        picked = [row for row in self.stock_st_rows if row["trade_date"] == params["trade_date"]]
        return self._slice(picked, fields)

    def suspend_d(self, **params):
        fields = self._record("suspend_d", params)
        picked = [
            row
            for row in self.suspend_d_rows
            if params["start_date"] <= row["trade_date"] <= params["end_date"]
        ]
        return self._slice(picked, fields)

    def daily_basic(self, **params):
        fields = self._record("daily_basic", params)
        picked = [row for row in self.daily_basic_rows if row["trade_date"] == params["trade_date"]]
        return self._slice(picked, fields)

    def income(self, **params):
        """按 ts_code + period + report_type 取，照真实代理：一次只给一个报告期的一种口径。"""
        fields = self._record("income", params)
        picked = [
            row
            for row in self.income_rows
            if row["ts_code"] == params["ts_code"]
            and row["end_date"] == params["period"]
            and row["report_type"] == params["report_type"]
        ]
        return self._slice(picked, fields)

    def balancesheet(self, **params):
        """按 ts_code + period + report_type 取，照真实代理：只返回合并报表，一期可能多行。"""
        fields = self._record("balancesheet", params)
        picked = [
            row
            for row in self.balancesheet_rows
            if row["ts_code"] == params["ts_code"]
            and row["end_date"] == params["period"]
            and row["report_type"] == params["report_type"]
        ]
        return self._slice(picked, fields)

    def cashflow(self, **params):
        """按 ts_code + period + report_type 取，照真实代理：一次只给一个报告期的一种口径。"""
        fields = self._record("cashflow", params)
        picked = [
            row
            for row in self.cashflow_rows
            if row["ts_code"] == params["ts_code"]
            and row["end_date"] == params["period"]
            and row["report_type"] == params["report_type"]
        ]
        return self._slice(picked, fields)

    def fina_indicator(self, **params):
        """按 ts_code + period 取，照真实代理：没有 report_type，一期可能多行（修订版本）。"""
        fields = self._record("fina_indicator", params)
        picked = [
            row
            for row in self.fina_indicator_rows
            if row["ts_code"] == params["ts_code"] and row["end_date"] == params["period"]
        ]
        return self._slice(picked, fields)

    def _record(self, api_name, params):
        self.calls.append((api_name, params))
        return params["fields"].split(",")

    @staticmethod
    def _slice(rows, fields):
        return pd.DataFrame([{name: row[name] for name in fields} for row in rows], columns=fields)
