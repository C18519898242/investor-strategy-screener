import logging
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class SyncBatch:
    def __init__(self, connection: sqlite3.Connection, batch_id: int):
        self._connection = connection
        self._batch_id = batch_id

    def record(self, dataset: str, row_count: int) -> None:
        self._connection.execute(
            "INSERT INTO sync_dataset (batch_id, dataset, row_count) VALUES (?, ?, ?)",
            (self._batch_id, dataset, row_count),
        )

    def publish(self, finished_at: str) -> None:
        self._connection.execute(
            "UPDATE sync_batch SET state = 'published', finished_at = ? WHERE batch_id = ?",
            (finished_at, self._batch_id),
        )

    def abort(self) -> None:
        self._connection.execute(
            "UPDATE sync_batch SET state = 'aborted' WHERE batch_id = ?",
            (self._batch_id,),
        )


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # autocommit：不引入事务。中断只留下「已写前缀」，重跑一遍覆盖掉。
    connection = sqlite3.connect(path, isolation_level=None)
    # autocommit 是每行一次提交，默认 synchronous=FULL 会让每次提交都 fsync：
    # 写 8314 行要 66.8 秒，加这两行是 0.27 秒（2026-10-18 实测）。
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_batch (
            batch_id INTEGER PRIMARY KEY,
            state TEXT NOT NULL,
            finished_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_dataset (
            batch_id INTEGER NOT NULL,
            dataset TEXT NOT NULL,
            row_count INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_basic (
            ts_code TEXT PRIMARY KEY,
            symbol TEXT,
            name TEXT,
            area TEXT,
            industry TEXT,
            fullname TEXT,
            enname TEXT,
            cnspell TEXT,
            market TEXT,
            exchange TEXT,
            curr_type TEXT,
            list_status TEXT,
            list_date TEXT,
            delist_date TEXT,
            is_hs TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS stock_basic_list_status ON stock_basic (list_status)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_st (
            ts_code TEXT NOT NULL,
            name TEXT,
            trade_date TEXT NOT NULL,
            type TEXT,
            type_name TEXT,
            PRIMARY KEY (ts_code, trade_date)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS stock_st_trade_date ON stock_st (trade_date)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_cal (
            exchange TEXT NOT NULL,
            cal_date TEXT NOT NULL,
            is_open INTEGER,
            pretrade_date TEXT,
            PRIMARY KEY (exchange, cal_date)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS suspend_d (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            suspend_timing TEXT,
            suspend_type TEXT NOT NULL,
            PRIMARY KEY (ts_code, trade_date, suspend_type)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS suspend_d_trade_date ON suspend_d (trade_date)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_basic (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            close REAL,
            turnover_rate REAL,
            turnover_rate_f REAL,
            volume_ratio REAL,
            pe REAL,
            pe_ttm REAL,
            pb REAL,
            ps REAL,
            ps_ttm REAL,
            dv_ratio REAL,
            dv_ttm REAL,
            total_share REAL,
            float_share REAL,
            free_share REAL,
            total_mv REAL,
            circ_mv REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS daily_basic_trade_date ON daily_basic (trade_date)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS income (
            ts_code TEXT,
            ann_date TEXT,
            f_ann_date TEXT,
            end_date TEXT,
            report_type TEXT,
            comp_type TEXT,
            end_type TEXT,
            basic_eps REAL,
            diluted_eps REAL,
            total_revenue REAL,
            revenue REAL,
            int_income REAL,
            prem_earned REAL,
            comm_income REAL,
            n_commis_income REAL,
            n_oth_income REAL,
            n_oth_b_income REAL,
            prem_income REAL,
            out_prem REAL,
            une_prem_reser REAL,
            reins_income REAL,
            n_sec_tb_income REAL,
            n_sec_uw_income REAL,
            n_asset_mg_income REAL,
            oth_b_income REAL,
            fv_value_chg_gain REAL,
            invest_income REAL,
            ass_invest_income REAL,
            forex_gain REAL,
            total_cogs REAL,
            oper_cost REAL,
            int_exp REAL,
            comm_exp REAL,
            biz_tax_surchg REAL,
            sell_exp REAL,
            admin_exp REAL,
            fin_exp REAL,
            assets_impair_loss REAL,
            prem_refund REAL,
            compens_payout REAL,
            reser_insur_liab REAL,
            div_payt REAL,
            reins_exp REAL,
            oper_exp REAL,
            compens_payout_refu REAL,
            insur_reser_refu REAL,
            reins_cost_refund REAL,
            other_bus_cost REAL,
            operate_profit REAL,
            non_oper_income REAL,
            non_oper_exp REAL,
            nca_disploss REAL,
            total_profit REAL,
            income_tax REAL,
            n_income REAL,
            n_income_attr_p REAL,
            minority_gain REAL,
            oth_compr_income REAL,
            t_compr_income REAL,
            compr_inc_attr_p REAL,
            compr_inc_attr_m_s REAL,
            ebit REAL,
            ebitda REAL,
            insurance_exp REAL,
            undist_profit REAL,
            distable_profit REAL,
            rd_exp REAL,
            fin_exp_int_exp REAL,
            fin_exp_int_inc REAL,
            transfer_surplus_rese REAL,
            transfer_housing_imprest REAL,
            transfer_oth REAL,
            adj_lossgain REAL,
            withdra_legal_surplus REAL,
            withdra_legal_pubfund REAL,
            withdra_biz_devfund REAL,
            withdra_rese_fund REAL,
            withdra_oth_ersu REAL,
            workers_welfare REAL,
            distr_profit_shrhder REAL,
            prfshare_payable_dvd REAL,
            comshare_payable_dvd REAL,
            capit_comstock_div REAL,
            net_after_nr_lp_correct REAL,
            credit_impa_loss REAL,
            net_expo_hedging_benefits REAL,
            oth_impair_loss_assets REAL,
            total_opcost REAL,
            amodcost_fin_assets REAL,
            oth_income REAL,
            asset_disp_income REAL,
            continued_net_profit REAL,
            end_net_profit REAL,
            update_flag TEXT,
            PRIMARY KEY (ts_code, end_date, report_type, ann_date, update_flag)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_income_end_date ON income (end_date)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS balancesheet (
            ts_code TEXT,
            ann_date TEXT,
            f_ann_date TEXT,
            end_date TEXT,
            report_type TEXT,
            comp_type TEXT,
            end_type TEXT,
            total_share REAL,
            cap_rese REAL,
            undistr_porfit REAL,
            surplus_rese REAL,
            special_rese REAL,
            money_cap REAL,
            trad_asset REAL,
            notes_receiv REAL,
            accounts_receiv REAL,
            oth_receiv REAL,
            prepayment REAL,
            div_receiv REAL,
            int_receiv REAL,
            inventories REAL,
            amor_exp REAL,
            nca_within_1y REAL,
            sett_rsrv REAL,
            loanto_oth_bank_fi REAL,
            premium_receiv REAL,
            reinsur_receiv REAL,
            reinsur_res_receiv REAL,
            pur_resale_fa REAL,
            oth_cur_assets REAL,
            total_cur_assets REAL,
            fa_avail_for_sale REAL,
            htm_invest REAL,
            lt_eqt_invest REAL,
            invest_real_estate REAL,
            time_deposits REAL,
            oth_assets REAL,
            lt_rec REAL,
            fix_assets REAL,
            cip REAL,
            const_materials REAL,
            fixed_assets_disp REAL,
            produc_bio_assets REAL,
            oil_and_gas_assets REAL,
            intan_assets REAL,
            r_and_d REAL,
            goodwill REAL,
            lt_amor_exp REAL,
            defer_tax_assets REAL,
            decr_in_disbur REAL,
            oth_nca REAL,
            total_nca REAL,
            cash_reser_cb REAL,
            depos_in_oth_bfi REAL,
            prec_metals REAL,
            deriv_assets REAL,
            rr_reins_une_prem REAL,
            rr_reins_outstd_cla REAL,
            rr_reins_lins_liab REAL,
            rr_reins_lthins_liab REAL,
            refund_depos REAL,
            ph_pledge_loans REAL,
            refund_cap_depos REAL,
            indep_acct_assets REAL,
            client_depos REAL,
            client_prov REAL,
            transac_seat_fee REAL,
            invest_as_receiv REAL,
            total_assets REAL,
            lt_borr REAL,
            st_borr REAL,
            cb_borr REAL,
            depos_ib_deposits REAL,
            loan_oth_bank REAL,
            trading_fl REAL,
            notes_payable REAL,
            acct_payable REAL,
            adv_receipts REAL,
            sold_for_repur_fa REAL,
            comm_payable REAL,
            payroll_payable REAL,
            taxes_payable REAL,
            int_payable REAL,
            div_payable REAL,
            oth_payable REAL,
            acc_exp REAL,
            deferred_inc REAL,
            st_bonds_payable REAL,
            payable_to_reinsurer REAL,
            rsrv_insur_cont REAL,
            acting_trading_sec REAL,
            acting_uw_sec REAL,
            non_cur_liab_due_1y REAL,
            oth_cur_liab REAL,
            total_cur_liab REAL,
            bond_payable REAL,
            lt_payable REAL,
            specific_payables REAL,
            estimated_liab REAL,
            defer_tax_liab REAL,
            defer_inc_non_cur_liab REAL,
            oth_ncl REAL,
            depos_oth_bfi REAL,
            deriv_liab REAL,
            depos REAL,
            agency_bus_liab REAL,
            oth_liab REAL,
            prem_receiv_adva REAL,
            depos_received REAL,
            ph_invest REAL,
            reser_une_prem REAL,
            reser_outstd_claims REAL,
            reser_lins_liab REAL,
            reser_lthins_liab REAL,
            indept_acc_liab REAL,
            pledge_borr REAL,
            indem_payable REAL,
            policy_div_payable REAL,
            total_liab REAL,
            treasury_share REAL,
            ordin_risk_reser REAL,
            forex_differ REAL,
            invest_loss_unconf REAL,
            minority_int REAL,
            total_hldr_eqy_exc_min_int REAL,
            total_hldr_eqy_inc_min_int REAL,
            total_liab_hldr_eqy REAL,
            lt_payroll_payable REAL,
            oth_comp_income REAL,
            oth_eqt_tools REAL,
            oth_eqt_tools_p_shr REAL,
            lending_funds REAL,
            acc_receivable REAL,
            st_fin_payable REAL,
            payables REAL,
            hfs_assets REAL,
            hfs_sales REAL,
            cost_fin_assets REAL,
            fair_value_fin_assets REAL,
            cip_total REAL,
            oth_pay_total REAL,
            long_pay_total REAL,
            debt_invest REAL,
            oth_debt_invest REAL,
            oth_eq_invest REAL,
            oth_illiq_fin_assets REAL,
            oth_eq_ppbond REAL,
            receiv_financing REAL,
            use_right_assets REAL,
            lease_liab REAL,
            contract_assets REAL,
            contract_liab REAL,
            accounts_receiv_bill REAL,
            accounts_pay REAL,
            oth_rcv_total REAL,
            fix_assets_total REAL,
            update_flag TEXT,
            PRIMARY KEY (ts_code, end_date, report_type)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_balancesheet_end_date ON balancesheet (end_date)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS fina_indicator (
            ts_code TEXT NOT NULL,
            ann_date TEXT,
            end_date TEXT NOT NULL,
            eps REAL,
            dt_eps REAL,
            total_revenue_ps REAL,
            revenue_ps REAL,
            capital_rese_ps REAL,
            surplus_rese_ps REAL,
            undist_profit_ps REAL,
            extra_item REAL,
            profit_dedt REAL,
            gross_margin REAL,
            current_ratio REAL,
            quick_ratio REAL,
            cash_ratio REAL,
            ar_turn REAL,
            ca_turn REAL,
            fa_turn REAL,
            assets_turn REAL,
            op_income REAL,
            ebit REAL,
            ebitda REAL,
            fcff REAL,
            fcfe REAL,
            current_exint REAL,
            noncurrent_exint REAL,
            interestdebt REAL,
            netdebt REAL,
            tangible_asset REAL,
            working_capital REAL,
            networking_capital REAL,
            invest_capital REAL,
            retained_earnings REAL,
            diluted2_eps REAL,
            bps REAL,
            ocfps REAL,
            retainedps REAL,
            cfps REAL,
            ebit_ps REAL,
            fcff_ps REAL,
            fcfe_ps REAL,
            netprofit_margin REAL,
            grossprofit_margin REAL,
            cogs_of_sales REAL,
            expense_of_sales REAL,
            profit_to_gr REAL,
            saleexp_to_gr REAL,
            adminexp_of_gr REAL,
            finaexp_of_gr REAL,
            impai_ttm REAL,
            gc_of_gr REAL,
            op_of_gr REAL,
            ebit_of_gr REAL,
            roe REAL,
            roe_waa REAL,
            roe_dt REAL,
            roa REAL,
            npta REAL,
            roic REAL,
            roe_yearly REAL,
            roa2_yearly REAL,
            debt_to_assets REAL,
            assets_to_eqt REAL,
            dp_assets_to_eqt REAL,
            ca_to_assets REAL,
            nca_to_assets REAL,
            tbassets_to_totalassets REAL,
            int_to_talcap REAL,
            eqt_to_talcapital REAL,
            currentdebt_to_debt REAL,
            longdeb_to_debt REAL,
            ocf_to_shortdebt REAL,
            debt_to_eqt REAL,
            eqt_to_debt REAL,
            eqt_to_interestdebt REAL,
            tangibleasset_to_debt REAL,
            tangasset_to_intdebt REAL,
            tangibleasset_to_netdebt REAL,
            ocf_to_debt REAL,
            turn_days REAL,
            roa_yearly REAL,
            roa_dp REAL,
            fixed_assets REAL,
            profit_to_op REAL,
            q_saleexp_to_gr REAL,
            q_gc_to_gr REAL,
            q_roe REAL,
            q_dt_roe REAL,
            q_npta REAL,
            q_ocf_to_sales REAL,
            basic_eps_yoy REAL,
            dt_eps_yoy REAL,
            cfps_yoy REAL,
            op_yoy REAL,
            ebt_yoy REAL,
            netprofit_yoy REAL,
            dt_netprofit_yoy REAL,
            ocf_yoy REAL,
            roe_yoy REAL,
            bps_yoy REAL,
            assets_yoy REAL,
            eqt_yoy REAL,
            tr_yoy REAL,
            or_yoy REAL,
            q_sales_yoy REAL,
            q_op_qoq REAL,
            equity_yoy REAL,
            update_flag TEXT,
            PRIMARY KEY (ts_code, end_date)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_fina_indicator_end_date ON fina_indicator (end_date)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS dividend (
            ts_code TEXT NOT NULL,
            end_date TEXT NOT NULL,
            ann_date TEXT,
            div_proc TEXT,
            stk_div REAL,
            stk_bo_rate REAL,
            stk_co_rate REAL,
            cash_div REAL,
            cash_div_tax REAL,
            record_date TEXT,
            ex_date TEXT,
            pay_date TEXT,
            div_listdate TEXT,
            imp_ann_date TEXT,
            base_date TEXT,
            base_share REAL,
            PRIMARY KEY (ts_code, end_date)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_dividend_end_date ON dividend (end_date)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_dividend_ex_date ON dividend (ts_code, ex_date)"
    )
    return connection


def insert_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    columns: tuple[str, ...],
    rows: Iterable[Sequence[object]],
) -> int:
    """只插入，不删任何东西；连接是 autocommit，写完即落盘。"""
    materialized = list(rows)
    placeholders = ", ".join("?" * len(columns))
    connection.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        materialized,
    )
    return len(materialized)


def delete_rows(connection: sqlite3.Connection, *, table: str, column: str, value: object) -> int:
    """删掉 column = value 的行；连接是 autocommit，删完即落盘。"""
    cursor = connection.execute(f"DELETE FROM {table} WHERE {column} = ?", (value,))
    logger.debug("%s 删除 %s = %s 的行，共 %d 行", table, column, value, cursor.rowcount)
    return cursor.rowcount


def replace_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    columns: tuple[str, ...],
    rows: Iterable[Sequence[object]],
) -> int:
    """清空整张表再批量插入；连接是 autocommit，写完即落盘。"""
    materialized = list(rows)
    connection.execute(f"DELETE FROM {table}")
    count = insert_rows(connection, table=table, columns=columns, rows=materialized)
    logger.debug("%s 清空旧数据，写入 %d 行", table, count)
    return count


def _latest_published_id(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        """
        SELECT batch_id FROM sync_batch
        WHERE state = 'published'
        ORDER BY batch_id DESC
        LIMIT 1
        """
    ).fetchone()
    return None if row is None else row[0]


def last_successful_sync_at(connection: sqlite3.Connection) -> str | None:
    batch_id = _latest_published_id(connection)
    if batch_id is None:
        return None
    row = connection.execute(
        "SELECT finished_at FROM sync_batch WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    return row[0]


def published_datasets(connection: sqlite3.Connection) -> frozenset[str]:
    """所有成功批次里出现过的数据集。

    每次更新可以只拉一个数据集，所以「就绪」是跨批次的并集：某个数据集只要在任意
    一次成功更新里出现过就算就绪。只看最新批次会让增量更新后的筛股误报「本地数据
    不完整」。
    """
    rows = connection.execute(
        """
        SELECT DISTINCT sync_dataset.dataset
        FROM sync_dataset
        JOIN sync_batch ON sync_batch.batch_id = sync_dataset.batch_id
        WHERE sync_batch.state = 'published'
        """
    ).fetchall()
    return frozenset(row[0] for row in rows)


def begin_sync(connection: sqlite3.Connection) -> SyncBatch:
    cursor = connection.execute("INSERT INTO sync_batch (state) VALUES ('open')")
    return SyncBatch(connection, cursor.lastrowid)


def publish_backfill(
    connection: sqlite3.Connection,
    *,
    dataset: str,
    row_count: int,
    finished_at: str | None = None,
) -> str:
    """回填脚本写完后补记一次成功批次。

    菜单的「就绪」判定只看成功批次里出现过的数据集，而回填脚本是直接写表、不留批次，
    这样建出来的库会让筛选永远报「本地数据不完整」。所以脚本收尾时登记一次。
    """
    stamp = finished_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batch = begin_sync(connection)
    batch.record(dataset, row_count)
    batch.publish(stamp)
    logger.debug("回填登记：%s %d 行（%s）", dataset, row_count, stamp)
    return stamp
