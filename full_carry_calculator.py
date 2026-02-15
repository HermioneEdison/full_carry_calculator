# app.py
# PVC 全覆盖 / 全Carry 计算器（Streamlit）——中文版 + 自动推合约 + 正套空间图

from dataclasses import dataclass
from datetime import date
import pandas as pd
import streamlit as st
import plotly.express as px
from plotly.subplots import make_subplots
import plotly.graph_objects as go

st.set_page_config(page_title="PVC 全覆盖计算器", layout="wide")
st.title("PVC 全覆盖 / 全Carry 计算器（自动推合约序列）")
st.caption("规则：交割月第10个交易日为最后交易日 + 3个交易日完成交割；自动识别最近交割合约并推12个合约。")

# --------------------------
# 交易日/合约序列工具
# --------------------------
def nth_trading_day_of_month(y: int, m: int, n: int) -> pd.Timestamp:
    """用工作日(周一~周五)近似交易日：返回当月第 n 个交易日（n从1开始）。"""
    first = pd.Timestamp(y, m, 1)
    first_td = first if first.weekday() < 5 else first + pd.offsets.BDay(1)
    return first_td + pd.offsets.BDay(n - 1)

def add_trading_days(ts: pd.Timestamp, k: int) -> pd.Timestamp:
    """加 k 个交易日（工作日近似）。"""
    return ts + pd.offsets.BDay(k)

@dataclass
class ContractSpec:
    合约: str
    交割年: int
    交割月: int
    最后交易日: pd.Timestamp
    交割完成日: pd.Timestamp

def build_front_12_contracts(today: date) -> list[ContractSpec]:
    """
    根据 today 自动找最近交割月（该月交割完成日 > today），并返回12个连续合约（跨年）。
    天数定义到交割完成日（最后交易日 + 3个交易日）。
    """
    t = pd.Timestamp(today)

    front_y, front_m = None, None
    for i in range(0, 24):
        y = t.year + (t.month - 1 + i) // 12
        m = (t.month - 1 + i) % 12 + 1
        last_trade = nth_trading_day_of_month(y, m, 10)
        delivery_done = add_trading_days(last_trade, 3)
        if delivery_done > t:
            front_y, front_m = y, m
            break

    if front_y is None:
        raise RuntimeError("无法定位最近交割合约（检查日期/逻辑）。")

    out: list[ContractSpec] = []
    for j in range(12):
        y = front_y + (front_m - 1 + j) // 12
        m = (front_m - 1 + j) % 12 + 1
        last_trade = nth_trading_day_of_month(y, m, 10)
        delivery_done = add_trading_days(last_trade, 3)
        out.append(
            ContractSpec(
                合约=f"v{m:02d}",
                交割年=y,
                交割月=m,
                最后交易日=last_trade,
                交割完成日=delivery_done,
            )
        )
    return out

def days_to_delivery_done(today: date, delivery_done: pd.Timestamp) -> int:
    t = pd.Timestamp(today)
    d = int((delivery_done.normalize() - t.normalize()).days)
    return max(d, 0)

# --------------------------
# 输入区（中文版）
# --------------------------
colA, colB, colC, colD = st.columns([1.05, 1, 1, 1])

with colA:
    今天 = st.date_input("今天日期", value=date.today())
    现货价 = st.number_input("现货价格 S（元/吨）", min_value=0.0, value=4600.0, step=10.0)

with colB:
    年化利率 = st.number_input("资金年化利率 r（%）", min_value=0.0, value=4.0, step=0.1) / 100.0
    资金占用口径 = st.selectbox("期货资金占用口径", ["全额(100%)", "保证金比例"])
    保证金比例 = st.number_input("保证金比例（例如 0.0625）", min_value=0.0, value=0.062465753, step=0.001, format="%.6f")

with colC:
    仓储_仓单 = st.number_input("仓储（仓单） 元/吨/天", min_value=0.0, value=1.00, step=0.05)
    仓储_现货 = st.number_input("仓储（现货） 元/吨/天", min_value=0.0, value=0.70, step=0.05)
    仓储口径 = st.selectbox("仓储口径", ["用现货仓储", "用仓单仓储"])

with colD:
    交割手续费 = st.number_input("交割手续费（元/吨）", min_value=0.0, value=2.0, step=1.0)
    出入库费 = st.number_input("出入库（元/吨）", min_value=0.0, value=100.0, step=5.0)
    质检费 = st.number_input("质检费用（元/吨）", min_value=0.0, value=0.0, step=1.0)
    其它成本 = st.number_input("其它成本（元/吨）", min_value=0.0, value=0.0, step=5.0)

# 地域升贴水：可选预设 + 可手改
地域预设 = {
    "华东（默认）": 0.0,
    "华北(山东)": 0.0,
    "华北(天津)": 0.0,
    "自定义": 0.0,
}
地域 = st.selectbox("地域（用于升贴水修正）", list(地域预设.keys()), index=0)
地域升贴水默认 = float(地域预设.get(地域, 0.0))
地域升贴水 = st.number_input("地域升贴水（元/吨，可手动覆盖）", value=地域升贴水默认, step=5.0)

def 资金占用比例() -> float:
    if 资金占用口径 == "全额(100%)":
        return 1.0
    return float(保证金比例)

占用比例 = 资金占用比例()
仓储单价 = float(仓储_现货) if 仓储口径 == "用现货仓储" else float(仓储_仓单)

# 固定费用（每吨）
固定费用合计 = float(交割手续费 + 出入库费 + 质检费 + 其它成本 + 地域升贴水)

st.divider()

# --------------------------
# 自动生成合约序列
# --------------------------
specs = build_front_12_contracts(今天)
front = specs[0]
st.subheader("自动识别的合约序列")
st.write(
    f"最近交割合约：**{front.合约}**（{front.交割年}-{front.交割月:02d}）｜"
    f"最后交易日（交割月第10交易日）：**{front.最后交易日.date()}**｜"
    f"交割完成日（+3交易日）：**{front.交割完成日.date()}**"
)

# --------------------------
# 期限结构输入：全部手动
# --------------------------
st.subheader("期限结构输入（全部手动）")

base_df = pd.DataFrame({
    "合约": [s.合约 for s in specs],
    "交割年": [s.交割年 for s in specs],
    "交割月": [s.交割月 for s in specs],
    "最后交易日": [s.最后交易日.date().isoformat() for s in specs],
    "交割完成日": [s.交割完成日.date().isoformat() for s in specs],
    "到交割完成日天数": [days_to_delivery_done(今天, s.交割完成日) for s in specs],
    "期货价格（元/吨）": [0.0 for _ in specs],     # 手动填
    "新老货价差修正（元/吨）": [0.0 for _ in specs],  # 手动填 or 批量填
})

# 批量填充新老货修正
with st.expander("新老货修正（批量填充工具）", expanded=False):
    st.caption("你也可以逐合约直接在表里填；这里是为了少敲字。")
    批量价差 = st.number_input("批量新老货价差（元/吨）", value=0.0, step=5.0)
    起始合约 = st.selectbox("从哪个合约开始填？", options=base_df["合约"].tolist(), index=0)
    若覆盖已有 = st.checkbox("覆盖表格里已有的非零值", value=False)

# 先展示编辑器
edited = st.data_editor(
    base_df,
    use_container_width=True,
    num_rows="fixed",
    column_config={
        "期货价格（元/吨）": st.column_config.NumberColumn("期货价格（元/吨）", format="%.2f"),
        "新老货价差修正（元/吨）": st.column_config.NumberColumn("新老货价差修正（元/吨）", format="%.2f"),
    }
)

# 应用批量填充（在用户展开并设置时生效）
if 批量价差 != 0.0:
    start_idx = edited.index[edited["合约"] == 起始合约][0]
    for i in range(start_idx, len(edited)):
        if 若覆盖已有 or float(edited.loc[i, "新老货价差修正（元/吨）"]) == 0.0:
            edited.loc[i, "新老货价差修正（元/吨）"] = float(批量价差)

st.divider()

# --------------------------
# 计算（保持原计算方式：资金+仓储按天线性 + 固定费用 + 新老货修正）
# --------------------------
calc = edited.copy()

calc["到交割完成日天数"] = pd.to_numeric(calc["到交割完成日天数"], errors="coerce").fillna(0).astype(int)
calc["期货价格（元/吨）"] = pd.to_numeric(calc["期货价格（元/吨）"], errors="coerce").fillna(0.0)
calc["新老货价差修正（元/吨）"] = pd.to_numeric(calc["新老货价差修正（元/吨）"], errors="coerce").fillna(0.0)

# 资金成本（按天线性）
calc["资金成本（元/吨）"] = calc["期货价格（元/吨）"] * 占用比例 * 年化利率 * calc["到交割完成日天数"] / 365.0
# 仓储成本（元/吨/天 * 天数）
calc["仓储成本（元/吨）"] = 仓储单价 * calc["到交割完成日天数"]
# 资金+仓储
calc["资金+仓储（元/吨）"] = calc["资金成本（元/吨）"] + calc["仓储成本（元/吨）"]

# full carry（修正前/后）
calc["全carry（修正前）（元/吨）"] = calc["资金+仓储（元/吨）"] + 固定费用合计
calc["全carry（修正后）（元/吨）"] = calc["全carry（修正前）（元/吨）"] + calc["新老货价差修正（元/吨）"]

# 全覆盖理论期货价（F = 现货 + full carry）
calc["全覆盖理论价（修正后）（元/吨）"] = 现货价 + calc["全carry（修正后）（元/吨）"]

# 基差（现货-期货）
calc["市场基差（现货-期货）（元/吨）"] = 现货价 - calc["期货价格（元/吨）"]

# 正套空间：期限结构 - 全覆盖（按你要求）
calc["正套空间（期货-全覆盖）（元/吨）"] = calc["期货价格（元/吨）"] - calc["全覆盖理论价（修正后）（元/吨）"]

st.subheader("结果表（中文表头）")
展示列 = [
    "合约","交割年","交割月","到交割完成日天数",
    "期货价格（元/吨）",
    "资金成本（元/吨）","仓储成本（元/吨）","资金+仓储（元/吨）",
    "全carry（修正前）（元/吨）","新老货价差修正（元/吨）","全carry（修正后）（元/吨）",
    "全覆盖理论价（修正后）（元/吨）",
    "市场基差（现货-期货）（元/吨）",
    "正套空间（期货-全覆盖）（元/吨）"
]
st.dataframe(calc[展示列].round(4), use_container_width=True)

st.divider()

# --------------------------
# 可视化：期限结构(盘面) vs 全覆盖(理论) + 副轴柱状（正套空间）
# --------------------------
st.subheader("可视化（盘面 vs 全覆盖 + 正套空间）")

# 期限结构曲线
fig = make_subplots(specs=[[{"secondary_y": True}]])
fig.add_trace(
    go.Scatter(
        x=calc["合约"],
        y=calc["期货价格（元/吨）"],
        mode="lines+markers",
        name="盘面期货价格"
    ),
    secondary_y=False,
)
fig.add_trace(
    go.Scatter(
        x=calc["合约"],
        y=calc["全覆盖理论价（修正后）（元/吨）"],
        mode="lines+markers",
        name="全覆盖理论价（修正后）"
    ),
    secondary_y=False,
)

# 副轴：正套空间柱状
fig.add_trace(
    go.Bar(
        x=calc["合约"],
        y=calc["正套空间（期货-全覆盖）（元/吨）"],
        name="正套空间（期货-全覆盖）",
        opacity=0.55
    ),
    secondary_y=True,
)

fig.update_layout(
    title="PVC：盘面期限结构 vs 全覆盖理论线（副轴：正套空间）",
    barmode="overlay",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    margin=dict(l=20, r=20, t=60, b=20),
)

fig.update_yaxes(title_text="价格（元/吨）", secondary_y=False)
fig.update_yaxes(title_text="正套空间（元/吨）", secondary_y=True)

st.plotly_chart(fig, use_container_width=True)

st.divider()

# 成本拆解（可选保留）
st.subheader("全Carry 成本拆解（修正前/后）")
bar_df = calc[["合约","资金+仓储（元/吨）","全carry（修正前）（元/吨）","全carry（修正后）（元/吨）"]].melt(
    id_vars="合约",
    var_name="项目",
    value_name="金额"
)
fig_bar = px.bar(bar_df, x="合约", y="金额", color="项目", barmode="group")
st.plotly_chart(fig_bar, use_container_width=True)

# 下载
st.download_button(
    "下载结果CSV",
    calc.to_csv(index=False).encode("utf-8-sig"),
    file_name="PVC_全覆盖_全carry_结果.csv",
    mime="text/csv"
)

st.caption(
    "注：天数=从今天到交割完成日（最后交易日=交割月第10交易日，交割完成=+3交易日）。"
    "交易日用工作日近似，未扣除法定假期；如需精确交易所日历，可增加节假日/交易日历表。"
)
