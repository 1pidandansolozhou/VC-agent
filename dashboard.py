"""
VC 项目雷达 — AI 融资监控看板
纯原生 Streamlit，不手写 HTML。
"""

import math
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

import pandas as pd
import streamlit as st

from config.settings import DB_PATH
from storage.paths import master_path

st.set_page_config(page_title="VC 项目雷达", page_icon="📡", layout="wide")

# ── 赛道配色 ──
TRACK_COLORS = {
    "AI2C": "#7c3aed",
    "AI2B": "#0284c7",
    "具身": "#b45309",
    "ai4S": "#059669",
    "前沿科技": "#4f46e5",
}
TRACK_LABELS = {
    "AI2C": "AI2C · 消费",
    "AI2B": "AI2B · 企业",
    "具身": "具身智能",
    "ai4S": "AI4S · 科学",
    "前沿科技": "前沿科技",
}
TRACK_ORDER = ["AI2C", "AI2B", "具身", "ai4S", "前沿科技"]

PAGE_SIZE = 10


def _run_main(*args):
    cmd = [sys.executable, "main.py", *args]
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(cmd, **kwargs)


@st.cache_data(ttl=10)
def _load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    try:
        return pd.read_sql("SELECT * FROM deals ORDER BY updated_at DESC", sqlite3.connect(DB_PATH))
    except Exception:
        return pd.DataFrame()


def main():
    # ── 标题 ──
    st.title("📡 VC 项目雷达")
    st.caption("AI 一级市场融资监控 · 每日跟踪早期信号")

    df = _load_data()

    if df.empty:
        st.info("暂无数据。点击下方按钮运行采集，或用 Excel 初始化数据库。", icon="📡")
        c1, c2, c3 = st.columns([1, 1, 3])
        with c1:
            if st.button("▶ 运行（今天+昨天）", type="primary", use_container_width=True):
                _run_main()
                st.toast("已后台启动，1–2 分钟后刷新", icon="🚀")
        with c2:
            since = st.date_input("起始日", value=datetime.now(), label_visibility="collapsed")
            if st.button("▶ 按日期运行", use_container_width=True):
                _run_main("--since", str(since))
                st.toast("已启动", icon="🚀")
        return

    # ── 统计 ──
    total = len(df)
    cn = int((df["region_class"] == "国内").sum()) if "region_class" in df.columns else 0
    gl = int((df["region_class"] == "海外").sum()) if "region_class" in df.columns else 0
    tracks_n = df["track"].nunique() if "track" in df.columns else 0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("收录项目", total)
    m2.metric("国内", cn)
    m3.metric("海外", gl)
    m4.metric("覆盖赛道", tracks_n)
    # 最新项目日期
    latest = df["updated_at"].max() if "updated_at" in df.columns else "—"
    if isinstance(latest, str) and len(latest) > 10:
        latest = latest[:10]
    m5.metric("最近更新", latest if latest else "—")

    st.divider()

    # ── 运行按钮 ──
    c1, c2, c3 = st.columns([1, 1, 3])
    with c1:
        if st.button("▶ 运行（今天+昨天）", type="primary", use_container_width=True):
            _run_main()
            st.toast("已后台启动，1–2 分钟后刷新", icon="🚀")
    with c2:
        since = st.date_input("起始日", value=datetime.now(), label_visibility="collapsed")
        if st.button("▶ 按日期运行", use_container_width=True):
            _run_main("--since", str(since))
            st.toast("已启动", icon="🚀")

    st.divider()

    # ── 筛选 ──
    f1, f2, f3, f4 = st.columns([1, 0.8, 2, 1])
    with f1:
        tracks_avail = sorted([t for t in TRACK_ORDER if t in df["track"].unique()])
        tk = st.multiselect("赛道", tracks_avail,
                            format_func=lambda x: TRACK_LABELS.get(x, x),
                            placeholder="全部赛道")
    with f2:
        rc_avail = sorted(df["region_class"].dropna().unique())
        rc = st.multiselect("区域", rc_avail, placeholder="全部区域")
    with f3:
        kw = st.text_input("搜索", placeholder="项目名 / 投资方 / 标题…", label_visibility="collapsed")
    with f4:
        view = st.radio("视图", ["卡片", "表格"], horizontal=True, label_visibility="collapsed")

    filtered = df.copy()
    if tk:
        filtered = filtered[filtered["track"].isin(tk)]
    if rc:
        filtered = filtered[filtered["region_class"].isin(rc)]
    if kw:
        kw_lower = kw.lower()
        filtered = filtered[
            filtered.apply(lambda r: kw_lower in " ".join(str(v) for v in r.values if v).lower(), axis=1)
        ]

    total_filtered = len(filtered)
    st.caption(f"共 {total_filtered} 个项目")

    # ── 表格视图 ──
    if view == "表格":
        display_df = filtered[[
            "project_name", "track", "sub_tag", "round", "amount",
            "investors", "region", "region_class", "verified_date"
        ]].copy()
        display_df.columns = ["项目", "赛道", "细分", "轮次", "金额", "投资方", "地区", "区域", "日期"]
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            height=min(35 * len(display_df) + 38, 700),
        )
        # 展开详情
        if len(filtered) > 0:
            st.divider()
            st.caption("点击项目名查看详情")
            selected = st.selectbox(
                "选择项目",
                filtered["project_name"].tolist(),
                label_visibility="collapsed",
                placeholder="选择项目查看详情…",
            )
            if selected:
                row = filtered[filtered["project_name"] == selected].iloc[0]
                st.markdown(f"**{row['title']}**")
                st.caption(f"{row.get('round','')} | {row.get('amount','')} | {row.get('investors','')}")
                st.caption(f"📍 {row.get('region','')}  |  🏷 {row.get('track','')} · {row.get('sub_tag','')}")
                if row.get("team"):
                    with st.expander("👥 团队"):
                        st.write(row["team"])
                if row.get("detail"):
                    with st.expander("📋 详情"):
                        st.write(row["detail"])
    else:
        # ── 卡片视图 ──
        if "card_page" not in st.session_state:
            st.session_state.card_page = 1

        total_pages = max(1, math.ceil(total_filtered / PAGE_SIZE))
        if st.session_state.card_page > total_pages:
            st.session_state.card_page = total_pages

        start_idx = (st.session_state.card_page - 1) * PAGE_SIZE
        page_df = filtered.iloc[start_idx:start_idx + PAGE_SIZE]

        for _, row in page_df.iterrows():
            track = row.get("track", "")
            color = TRACK_COLORS.get(track, "#6b7280")

            with st.container(border=True):
                h1, h2 = st.columns([4, 1])
                with h1:
                    # 标题行: 项目名 + 赛道标签
                    st.markdown(
                        f"**{row['project_name']}**  "
                        f":{color}[`{track}`]  "
                        f"*{row.get('sub_tag', '')}*"
                    )
                    st.caption(row.get("title", ""))
                with h2:
                    st.caption(f"{row.get('round', '')}  |  {row.get('amount', '')}")

                # 投资方 + 地区
                meta_cols = []
                if row.get("investors") and row["investors"] != "未披露":
                    meta_cols.append(f"💰 {row['investors']}")
                if row.get("region"):
                    meta_cols.append(f"📍 {row['region']}")
                region_class = row.get("region_class", "")
                if region_class and region_class != "未知":
                    flag = "🇨🇳" if region_class == "国内" else "🌍"
                    meta_cols.append(f"{flag} {region_class}")
                if meta_cols:
                    st.caption("  ·  ".join(meta_cols))

                # 可展开：团队 + 详情
                with st.expander("展开详情"):
                    if row.get("team"):
                        st.markdown("**团队**")
                        st.write(row["team"])
                    if row.get("detail"):
                        st.markdown("**具体信息**")
                        st.write(row["detail"])
                    if row.get("founded_year"):
                        st.caption(f"成立时间: {row['founded_year']}")

        # 分页
        if total_pages > 1:
            pg1, pg2, pg3 = st.columns([1, 2, 1])
            with pg1:
                if st.button("← 上一页", disabled=(st.session_state.card_page <= 1)):
                    st.session_state.card_page -= 1
                    st.rerun()
            with pg2:
                st.caption(
                    f"第 {st.session_state.card_page} / {total_pages} 页 · 共 {total_filtered} 项",
                )
            with pg3:
                if st.button("下一页 →", disabled=(st.session_state.card_page >= total_pages)):
                    st.session_state.card_page += 1
                    st.rerun()

    # ── 下载 ──
    st.divider()
    mp = master_path()
    if mp.exists():
        with open(mp, "rb") as f:
            st.download_button("⬇ 下载总库 Excel", f, mp.name, use_container_width=True)

    st.caption(f"VC 项目雷达 · {len(df)} 个早期项目 · {datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    main()
