from openpyxl import Workbook

from storage.db import all_rows
from storage.paths import atomic_replace, master_path, snapshot_master

HEADERS = [
    "项目名称", "赛道", "细分tag", "轮次", "融资金额", "最新估值", "投资方", "业务简介", "地区", "国内/海外",
    "成立时间", "标题", "团队", "具体信息", "重要度", "官网", "真实融资日期", "日期状态", "核查置信",
    "来源链接", "来源日期", "多来源", "首次窗口", "更新时间",
]

KEYS = [
    "project_name", "track", "sub_tag", "round", "amount", "valuation", "investors", "business", "region", "region_class",
    "founded_year", "title", "team", "detail", "importance", "official_site", "verified_date", "date_status",
    "date_confidence", "source_url", "source_date", "sources", "first_seen_window", "updated_at",
]


def rebuild_master() -> str:
    snapshot_master()
    final = master_path()
    tmp = final.with_suffix(".tmp.xlsx")

    wb = Workbook()
    ws = wb.active
    ws.title = "全部项目"
    ws.append(HEADERS)
    for r in all_rows():
        ws.append([r.get(k, "") for k in KEYS])
    ws.freeze_panes = "A2"

    wb.save(tmp)
    atomic_replace(str(tmp), str(final))
    return str(final)
