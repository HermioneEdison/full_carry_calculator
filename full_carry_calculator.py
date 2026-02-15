# app.py
# PVC 全覆盖 / 全Carry 计算器（Streamlit）
# 需求实现：
# 1) v03 表示 3 月交割合约；最后交易日 = 交割月第 10 个交易日
# 2) 实物交割在随后 3 个交易日内完成
# 3) 根据 today 自动判断“最近交割月”，并向后推 12 个连续合约（跨年：…v12, v01, v02）
# 4) 其它计算方式不改动（仍按：资金=价格*占用比例*r*天数/365，仓储=元/吨/天*天数，+固定费用，+新老货修正）

import math
from dataclasses import dataclass
from datetime import date
import pandas as pd
import streamlit as st
import plotly.express as px

st.set_page_config(page_title="PVC 全覆盖计算器", layout="wide")
st.title("PVC 全覆盖 / 全Carry 计算器（自动推合约序列）")
st.caption("按：交割月第10个交易日为最后交易日 + 3个交易日完成交割；自动识别最近交割月并推12个合约。")

# --------------------------
# 交易日/合约序列工具
# --------------------------
def nth_trading_day_of_month(y: int, m: int, n: int) -> pd.Timestamp:
    """用工作日(周一~周五)近似交易日：返回当月第 n 个交易日（n从1开始）。"""
    first = pd.Timestamp(y, m, 1)
    # 若月初不是交易日，滚到下一个交易日
    first_td = first if first.weekday() < 5 else first + pd.offsets.BDay(1)
    # 第n个交易日 = 第1个交易日 + (n-1)个BDay
    return first_td + pd.offsets.BDay(n - 1)

def add_trading_days(ts: pd.Timestamp, k: int) -> pd.Timestamp:
    """加 k 个交易日（工作日近似）。"""
    return ts + pd.offsets.BDay(k)

@dataclass
class ContractSpec:
    label: str          # v03
    year: int           # 2026
    month: int          # 3
    last_trade: pd.Timestamp   # 交割月第10个交易日
    delivery_done: pd.Timestamp  # last_trade + 3 个交易日

def build_front_12_contracts(today: date) -> list[ContractSpec]:
    """
    根据 today 自动找最近交割月（该月交割完成日 > today），并返回12个连续合约。
    注意：这里把“持有天数”定义到 delivery_done（交割完成日），更贴近你描述的实物流转。
    """
    t = pd.Timestamp(today)

    # 先找 front：从本月开始往后扫，找第一个 delivery_done > today 的交割月
    front_y, front_m = None, None
    for i in range(0, 24):  # 扫两年足够
        y = t.year + (t.month - 1 + i) // 12
        m = (t.month - 1 + i) % 12 + 1
        last_trade = nth_trading_day_of_month(y, m, 10)
        delivery_done = add_trading_days(last_trade, 3)
        if delivery_done > t:
            front_y, front_m = y, m
            break

    if front_y is None:
        raise RuntimeError("无法定位最近交割合约（检查日期/逻辑）。")

    # 从 front 月开始推 12 个连续月份
    out = []
    for j in range(12):
        y = front_y + (front_m - 1 + j) // 12
        m = (front_m - 1 + j) % 12 + 1
        last_trade = nth_trading_day_of_month(y, m, 10)
        delivery_done = add_trading_days(last_trade, 3)
        label = f"v{m:02d}"
        out.append(ContractSpec(label=label, year=y, month=m, last_trade=last_trade, delivery_done=delivery_done))
    return out

def days_to_delivery_done(today: date, delivery_done: pd.Timestamp) -> int:
    t = pd.Timestamp(today)
    d = int((delivery_done.normalize() - t.normalize()).days)
    return max(d, 0)

# --------------------------
# 输入区
# --------------------------
colA, colB, colC, colD = st.columns([1.05, 1, 1, 1])

with colA:
    today = st.date_input("今天日期", value=date.today())
    spot = st.number_input("现货价格 S（元/吨）", min_value=0.0, value=4600.0, step=10.0)

with colB:
    r = st.number_input("资金年化利率 r（%）", min_value=0.0, value=4.0, step=0.1) / 100.0
    funding_mode = st.selectbox("期货资金占用口径", ["全额(100%)", "保证金比例", "自定义比例"])
    margin_ratio = st.number_input("保证金比例（例如 0.0625）", min_value=0.0, value=0.062465753, step=0.001, format="%.6f")
    custom_ratio = st.number_input("自定义占用比例", min_value=0.0, value=1.0, step=0.05)

with colC:
    storage_receipt = st.number_input("仓储（仓单） 元/吨/天", min_value=0.0, value=1.00, step=0.05)
    storage_spot = st.number_input("仓储（现货） 元/吨/天", min_value=0.0, value=0.70, step=0.05)
    storage_mode = st.selectbox("仓储口径", ["用现货仓储", "用仓单仓储"])

with colD:
    fee_delivery = st.number_input("交割手续费（元/吨）", min_value=0.0, value=2.0, step=1.0)
    fee_inout = st.number_input("出入库（元/吨）", min_value=0.0, value=100.0, step=5.0)
    fee_qc = st.number_input("质检费用（元/吨）", min_value=0.0, value=0.0, step=1.0)
    premium_wh = st.number_input("仓库升贴水（元/吨）", value=0.0, step=5.0)

def funding_ratio() -> float:
    if funding_mode == "全额(100%)":
        return 1.0
    if funding_mode == "保证金比例":
        return float(margin_ratio)
    return float(custom_ratio)

fund_ratio = funding_ratio()
stor = float(storage_spot) if storage_mode == "用现货仓储" else float(storage_receipt)
fixed_fee = float(fee_delivery + fee_inout + fee_qc + premium_wh)

st.divider()

# --------------------------
# 自动生成合约序列（front + 12个月）
# --------------------------
specs = build_front_12_contracts(today)

front_info = specs[0]
st.subheader("自动识别的合约序列")
st.write(
    f"最近交割合约：**{front_info.label}**（{front_info.year}-{front_info.month:02d}）｜"
    f"最后交易日(第10交易日)：**{front_info.last_trade.date()}**｜"
    f"交割完成日(+3交易日)：**{front_info.delivery_done.date()}**"
)

# --------------------------
# 合约输入表：价格/新老货价差（可编辑）
# --------------------------
st.subheader("期限结构输入（价格 / 新老货价差）")
# 给一个“默认价格”列：先空着更安全，你也可以自己填
base_df = pd.DataFrame({
    "contract": [s.label for s in specs],
    "delivery_year": [s.year for s in specs],
    "delivery_month": [s.month for s in specs],
    "last_trade_day": [s.last_trade.date().isoformat() for s in specs],
    "delivery_done_day": [s.delivery_done.date().isoformat() for s in specs],
    "days_to_delivery_done": [days_to_delivery_done(today, s.delivery_done) for s in specs],
    "futures_price": [0.0 for _ in specs],   # 你在这里填盘面价格
    "new_old_adj": [0.0 for _ in specs],     # 你在这里填新老货价差修正
})

edited = st.data_editor(
    base_df,
    use_container_width=True,
    num_rows="fixed",
    column_config={
        "futures_price": st.column_config.NumberColumn("futures_price", format="%.2f"),
        "new_old_adj": st.column_config.NumberColumn("new_old_adj", format="%.2f"),
    }
)

st.divider()

# --------------------------
# 核心计算（保持你原逻辑：资金+仓储按天线性累加 + 固定费用 + 新老货修正）
# --------------------------
calc = edited.copy()
calc["days_to_delivery_done"] = pd.to_numeric(calc["days_to_delivery_done"], errors="coerce").fillna(0).astype(int)
calc["futures_price"] = pd.to_numeric(calc["futures_price"], errors="coerce").fillna(0.0)
calc["new_old_adj"] = pd.to_numeric(calc["new_old_adj"], errors="coerce").fillna(0.0)

# 资金成本（按天线性）
calc["funding_cost"] = calc["futures_price"] * fund_ratio * r * calc["days_to_delivery_done"] / 365.0
# 仓储成本（元/吨/天 * 天数）
calc["storage_cost"] = stor * calc["days_to_delivery_done"]
# 资金+仓储
calc["carry_cost"] = calc["funding_cost"] + calc["storage_cost"]

# full carry（修正前/后）
calc["full_carry_before_adj"] = calc["carry_cost"] + fixed_fee
calc["full_carry_after_adj"] = calc["full_carry_before_adj"] + calc["new_old_adj"]

# 全覆盖理论期货价（按：F_theo = spot + full_carry）
calc["F_theo_before_adj"] = spot + calc["full_carry_before_adj"]
calc["F_theo_after_adj"] = spot + calc["full_carry_after_adj"]

# 基差（现货-期货）
calc["basis_mkt"] = spot - calc["futures_price"]
calc["basis_theo_after_adj"] = spot - calc["F_theo_after_adj"]

st.subheader("结果表")
show_cols = [
    "contract","delivery_year","delivery_month","days_to_delivery_done",
    "futures_price","funding_cost","storage_cost","carry_cost",
    "full_carry_before_adj","new_old_adj","full_carry_after_adj",
    "F_theo_after_adj","basis_mkt"
]
st.dataframe(calc[show_cols].round(4), use_container_width=True)

st.divider()

# --------------------------
# 可视化：期限结构 + 全覆盖线
# --------------------------
st.subheader("可视化")

c1, c2 = st.columns(2)

with c1:
    fig_ts = px.line(
        calc,
        x="contract",
        y="futures_price",
        markers=True,
        title="PVC 期限结构（盘面期货价格）"
    )
    st.plotly_chart(fig_ts, use_container_width=True)

with c2:
    plot_df = calc[["contract","futures_price","F_theo_after_adj"]].melt(
        id_vars="contract",
        value_vars=["futures_price","F_theo_after_adj"],
        var_name="series",
        value_name="price"
    )
    fig_fc = px.line(
        plot_df,
        x="contract",
        y="price",
        color="series",
        markers=True,
        title="盘面 vs 全覆盖理论价（修正后）"
    )
    st.plotly_chart(fig_fc, use_container_width=True)

st.divider()

st.subheader("全Carry 成本拆解（修正前/后）")
bar_df = calc[["contract","carry_cost","full_carry_before_adj","full_carry_after_adj"]].melt(
    id_vars="contract",
    var_name="item",
    value_name="value"
)
fig_bar = px.bar(bar_df, x="contract", y="value", color="item", barmode="group")
st.plotly_chart(fig_bar, use_container_width=True)

st.download_button(
    "下载结果CSV",
    calc.to_csv(index=False).encode("utf-8-sig"),
    file_name="pvc_fullcarry_result.csv",
    mime="text/csv"
)

st.caption(
    "注：这里把“天数”定义到交割完成日（最后交易日的第10交易日 + 3交易日）。"
    "交易日用工作日(周一~周五)近似，未扣除法定假期；若你需要精确到交易所节假日，"
    "我们可以加一张“节假日表/交易日历”来修正。"
)