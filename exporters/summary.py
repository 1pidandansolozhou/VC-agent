from typing import List

from llm.client import chat
from models.schema import Deal


def weekly_summary(deals: List[Deal]) -> str:
    if not deals:
        return "本周暂未抓取到早期融资项目。"

    hi = [d.project_name for d in deals if d.importance == "high"] or [d.project_name for d in deals][:5]
    brief = [
        f"{d.project_name}|{d.region_class}|{d.track}/{d.sub_tag}|{d.round}|{d.amount}|{d.importance}"
        for d in deals
    ]
    sys = (
        "你是 VC 周报主笔。写一段 250–350 字《本周综述》：先点名值得关注的早期项目"
        f"（重点：{','.join(hi)}），再归纳 2–3 条赛道主线（含中外格局），凝练专业、连续成段、不用列表。"
    )
    # 注：kimi-k2.6 仅支持 temperature=1（其他值会 400），不要改回 0.x
    return chat("write", sys, "本周项目：\n" + "\n".join(brief), max_tokens=800, temperature=1.0)
