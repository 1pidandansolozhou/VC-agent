import os
import sqlite3
import subprocess
import sys
from datetime import datetime

import pandas as pd
import streamlit as st

from config.settings import DB_PATH
from storage.paths import master_path

st.set_page_config(page_title="VC 项目雷达", layout="wide")
st.title("📡 VC 一级市场项目雷达")


def _run_main(*args):
    """后台启动 main.py，Windows 下避免弹出命令行窗口。"""
    cmd = [sys.executable, "main.py", *args]
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(cmd, **kwargs)


def main():
    c1, c2, c3 = st.columns(3)

    if c1.button("▶️ 立即运行（本周窗口）"):
        _run_main()
        st.success("已后台启动，1-2 分钟后刷新")

    since = c2.date_input("自定义起始日期", value=datetime.now())
    if c3.button("▶️ 按自定义窗口运行"):
        _run_main("--since", str(since))
        st.success("已启动")

    if not os.path.exists(DB_PATH):
        st.info("还没有数据，先点上面运行")
        return

    try:
        df = pd.read_sql("SELECT * FROM deals ORDER BY updated_at DESC", sqlite3.connect(DB_PATH))
        if df.empty:
            st.info("数据库为空，先点上面运行")
            return
    except Exception as e:
        st.warning(f"读取数据库失败（可能表尚未创建）：{e}")
        return

    a, b, c = st.columns(3)
    tk = a.multiselect("赛道", sorted(df["track"].unique()))
    rc = b.multiselect("国内/海外", sorted(df["region_class"].dropna().unique()))
    kw = c.text_input("搜索（项目/投资方/标题）")

    if tk:
        df = df[df["track"].isin(tk)]
    if rc:
        df = df[df["region_class"].isin(rc)]
    if kw:
        df = df[df.apply(lambda r: kw.lower() in " ".join(map(str, r.values)).lower(), axis=1)]

    st.caption(f"共 {len(df)} 个项目")
    for tr in sorted(df["track"].unique()):
        sub = df[df["track"] == tr]
        st.subheader(f"{tr}（{len(sub)}）")
        st.dataframe(
            sub[["project_name", "region_class", "sub_tag", "round", "amount", "valuation", "investors", "region", "verified_date", "date_status"]],
            width="stretch",
            hide_index=True,
        )

    mp = master_path()
    if mp.exists():
        st.download_button("⬇️ 总库 Excel", open(mp, "rb"), mp.name)


if __name__ == "__main__":
    main()
