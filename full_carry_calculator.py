# full_carry_calculator.py
# PVC 全覆盖 / 全Carry 计算器（Streamlit）
# 修正口径（按你的最新要求）：
# - 期货行情：只做地域升贴水修正（修正后期货价 = 期货价 + 地域升贴水）
# - 新老货价差：只修正“全覆盖/全carry”，并且通过选择一个节点合约开始生效（从该合约起统一加同一价差）
# - 其它计算方式不改：资金=价格*占用比例*r*天数/365；仓储=元/吨/天*天数；+固定费用

from dataclasses import dataclass
from datetime import date
import pandas as pd
import streamlit as st
from plotly.subplots import make_subplots
import plotly.graph_objects as go
import plotly.express as px

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

    out = []
    for j in range(12):
        y = front_y + (front_m - 1 + j) // 12
        m = (front_m - 1 + j) % 12 + 1
        last_trade = nth_trading_day_of_month(y, m, 10)
        delivery_done = add_trading_days(last_trade, 3)
        out.append(ContractSpec(合约=f"v{m:02d}", 交割年=y, 交割月=m, 最后交易日=last_trade, 交割完成日=delivery_done))
    return out

def days_to_delivery_done(today: date, delivery_done: pd.Timestamp) -> int:
    t = pd.Timestamp(today)
    d = int((delivery_done.normalize() - t.normalize()).days)
    return max(d, 0)

# --------------------------
# 输入区（参数）
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

# 地域升贴水（只修正期货行情）
地域预设 = {
    "华东（默认）": 0.0,
    "华北(山东)": 0.0,
    "华北(天津)": 0.0,
    "自定义": 0.0,
}
地域 = st.selectbox("地域（用于期货行情升贴水修正）", list(地域预设.keys()), index=0)
地域升贴水默认 = float(地域预设.get(地域, 0.0))
地域升贴水 = st.number_input("地域升贴水（元/吨，可手动覆盖）", value=地域升贴水默认, step=5.0)

占用比例 = 1.0 if 资金占用口径 == "全额(100%)" else float(保证金比例)
仓储单价 = float(仓储_现货) if 仓储口径 == "用现货仓储" else float(仓储_仓单)

# 固定费用（每吨）：这里不包含“地域升贴水”，因为地域升贴水只用于修正期货行情
固定费用合计 = float(交割手续费 + 出入库费 + 质检费 + 其它成本)

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
# 12个期货行情输入框（只填期货价）
# --------------------------
st.subheader("12个合约期货行情输入（手动）")

# session_state 记忆输入
for s in specs:
    key_p = f"p_{s.合约}"
    if key_p not in st.session_state:
        st.session_state[key_p] = 0.0

cols = st.columns(6)
for i, s in enumerate(specs):
    with cols[i % 6]:
        st.session_state[f"p_{s.合约}"] = st.number_input(
            label=f"{s.合约}",
            value=float(st.session_state[f"p_{s.合约}"]),
            step=1.0,
            format="%.2f",
            key=f"inp_price_{s.合约}",
        )

st.divider()

# --------------------------
# 新老货修正：只修正全覆盖，并通过“节点合约”开始生效
# --------------------------
st.subheader("新老货价差修正（仅作用于全覆盖：从节点合约开始生效）")

新老货价差 = st.number_input("新老货价差（元/吨）", value=0.0, step=5.0)
节点合约 = st.selectbox("从哪个合约开始计入新老货价差？", options=[s.合约 for s in specs], index=0)
节点索引 = [s.合约 for s in specs].index(节点合约)

st.caption("解释：从“节点合约”及其之后的合约，全覆盖理论价会统一 + 新老货价差；盘面期货价格不受新老货价差影响。")

st.divider()

# --------------------------
# 计算表
# --------------------------
rows = []
for i, s in enumerate(specs):
    天数 = days_to_delivery_done(今天, s.交割完成日)

    期货价_原始 = float(st.session_state.get(f"p_{s.合约}", 0.0))
    # 期货行情地域修正：只修正盘面
    期货价_地域修正后 = 期货价_原始 + float(地域升贴水)

    # 全覆盖侧的新老货修正（从节点开始）
    新老货修正 = float(新老货价差) if i >= 节点索引 else 0.0

    # 资金/仓储（不改口径：仍然用“期货价格”为基数；这里用“地域修正后的期货价”更贴近你做地区可比）
    资金成本 = 期货价_地域修正后 * 占用比例 * 年化利率 * 天数 / 365.0
    仓储成本 = 仓储单价 * 天数
    资金仓储 = 资金成本 + 仓储成本

    全carry修正前 = 资金仓储 + 固定费用合计
    全carry修正后 = 全carry修正前 + 新老货修正

    全覆盖理论价 = 现货价 + 全carry修正后

    # 基差/正套空间：基于“地域修正后的期货价”
    市场基差 = 现货价 - 期货价_地域修正后
    正套空间 = 期货价_地域修正后 - 全覆盖理论价

    rows.append({
        "合约": s.合约,
        "交割年": s.交割年,
        "交割月": s.交割月,
        "最后交易日": s.最后交易日.date().isoformat(),
        "交割完成日": s.交割完成日.date().isoformat(),
        "到交割完成日天数": 天数,

        "期货价格（原始）": 期货价_原始,
        "地域升贴水（期货）": float(地域升贴水),
        "期货价格（地域修正后）": 期货价_地域修正后,

        "新老货价差修正（全覆盖）": 新老货修正,

        "资金成本（元/吨）": 资金成本,
        "仓储成本（元/吨）": 仓储成本,
        "资金+仓储（元/吨）": 资金仓储,

        "全carry（修正前）（元/吨）": 全carry修正前,
        "全carry（修正后）（元/吨）": 全carry修正后,
        "全覆盖理论价（修正后）（元/吨）": 全覆盖理论价,

        "市场基差（现货-期货修正后）（元/吨）": 市场基差,
        "正套空间（期货修正后-全覆盖）（元/吨）": 正套空间,
    })

calc = pd.DataFrame(rows)

st.subheader("结果表（中文表头）")
st.dataframe(calc.round(4), use_container_width=True)

st.divider()

# --------------------------
# 可视化：盘面(地域修正后) vs 全覆盖 + 副轴正套空间柱状
# --------------------------
st.subheader("可视化（盘面 vs 全覆盖 + 副轴正套空间）")

fig = make_subplots(specs=[[{"secondary_y": True}]])

fig.add_trace(
    go.Scatter(
        x=calc["合约"],
        y=calc["期货价格（地域修正后）"],
        mode="lines+markers",
        name="盘面期货价格（地域修正后）"
    ),
    secondary_y=False
)
fig.add_trace(
    go.Scatter(
        x=calc["合约"],
        y=calc["全覆盖理论价（修正后）（元/吨）"],
        mode="lines+markers",
        name="全覆盖理论价（含新老货修正）"
    ),
    secondary_y=False
)
fig.add_trace(
    go.Bar(
        x=calc["合约"],
        y=calc["正套空间（期货修正后-全覆盖）（元/吨）"],
        name="正套空间（期货-全覆盖）",
        opacity=0.55
    ),
    secondary_y=True
)

fig.update_layout(
    title="PVC：盘面期限结构（地域修正） vs 全覆盖理论线（副轴：正套空间）",
    barmode="overlay",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    margin=dict(l=20, r=20, t=60, b=20),
)

fig.update_yaxes(title_text="价格（元/吨）", secondary_y=False)
fig.update_yaxes(title_text="正套空间（元/吨）", secondary_y=True)

st.plotly_chart(fig, use_container_width=True)

st.divider()

# 成本拆解
st.subheader("全Carry 成本拆解（修正前/后）")
bar_df = calc[["合约","资金+仓储（元/吨）","全carry（修正前）（元/吨）","全carry（修正后）（元/吨）"]].melt(
    id_vars="合约", var_name="项目", value_name="金额"
)
fig_bar = px.bar(bar_df, x="合约", y="金额", color="项目", barmode="group")
st.plotly_chart(fig_bar, use_container_width=True)

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
