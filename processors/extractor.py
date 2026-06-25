import json
from concurrent.futures import ThreadPoolExecutor
from typing import List

from config.settings import EXTRACT_WORKERS
from config.taxonomy import ALLOWED, TRACK_TAGS
from llm.client import chat
from models.schema import Article, Deal

SYSTEM = f"""你是专注一级市场早期投资的资深 VC 分析师助手。从中文/英文融资新闻提取结构化项目信息并严格分类。
【赛道与 Tag】（track 五选一；sub_tag 取该 track 内一个，不得自创）
{json.dumps(TRACK_TAGS, ensure_ascii=False, indent=2)}
【硬规则 —— 务必逐条遵守】
1. 只保留 A 轮及以前：种子/天使/Pre-seed/Pre-A/A/A+ 保留；B 轮及以后/战略/IPO/并购/老股 → {{"skip":true}}。
2. ★ amount(融资金额)：能确定写精确值（如"6600万美元""100万美元"）；不确定务必写【量级】(超200万美元/数百万美元/数千万美元/数亿美元/数百万元人民币/数千万元人民币/近千万元人民币/亿元级人民币/数亿元人民币/超10亿元人民币/千万元级人民币 等)。★★★ 禁止写「未披露」★★★ —— 只要能从上下文推断量级就必须填量级。valuation(估值)同理。
3. region_class：依公司主体所在地填「国内」或「海外」。region：国内填省/市（如"北京市""广东省""上海市"），海外填国家（如"美国""英国"），未知填"-"。
4. 一文多个早期项目→JSON 数组；无早期项目→{{"skip":true}}。
5. importance：high(明星团队/大额/技术稀缺/头部领投)/mid/low，控详略与排序。
6. ★ detail：150–300字连续散文（禁止列表/分点），严格按「行业痛点→公司方案/技术壁垒→商业模式/产品阶段/客户」三段式撰写。高质量范本：
   "手机截图往往在保存后被遗忘，原始链接与后续行动也随之丢失。Pool以相册截图为入口，利用人工智能识别截图中的商品、食谱等内容，自动补回来源链接并按主题组织，使截图从静态图片转化为可搜索的个人记忆库。产品已上线App Store，并通过社交传播获得约1500万次自然浏览。"
7. ★ team：必须详细写出核心创始人的【姓名、职位、学历（学校+专业+学位）、此前任职公司与角色】。多人的用分号隔开或"；"分隔。高质量范本：
   "吴尚，创始人兼CEO，中南大学本科、北京大学光华管理学院MBA就读，曾任职中兴、阿里、腾讯和小米；技术负责人喜哥曾任小爱端侧模型核心工程师；产品设计负责人美杰曾在字节跳动大力教育负责软硬件设计。"
   信息不足时写明"未披露"或从原文可推断的最小信息，但绝不可只写"团队经验丰富"这类空话。
8. ★ title：完整大标题 =「{{公司名}}完成{{轮次}}{{金额或量级}}融资，{{用/以/为/面向/研发/推进/构建/提供+一句话核心定位}}」。一句话定位用动词开头，说清公司做什么、解决什么问题。高质量范本：
   "Pool完成种子轮融资，用人工智能将截图整理为可搜索的个人记忆库"
   "分子之心完成A轮融资，以人工智能蛋白质设计构建生物研发基础设施"
   "芯界光核完成种子轮融资，研发面向人工智能集群的硅光芯片与光互联"
   title 同时用于 Excel 标题列与 Word 粗体小标题，必须精炼有力。
返回严格 JSON，字段：project_name,track,sub_tag,founded_year,title,team,round,amount,valuation,
investors,region,region_class,detail,importance,official_site。无解释、无代码块。"""


def _extract_one(a: Article) -> List[Deal]:
    # ★ v1 修复：content 为空时回退到 title + summary，不让文章白白浪费
    body = a.content.strip()
    if not body:
        body = f"{a.title}\n{a.summary}".strip()
    if not body:
        return []

    try:
        raw = chat(
            "extract",
            SYSTEM,
            f"标题：{a.title}\n来源：{a.source}\n链接：{a.url}\n\n正文：\n{body[:12000]}",
            max_tokens=3000,
            json_mode=True,
        )
    except Exception:
        return []

    try:
        data = json.loads(raw)
    except Exception:
        return []

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        if data.get("skip"):
            return []
        items = data.get("items", [data])
    else:
        return []

    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("skip"):
            continue
        if it.get("track") in ALLOWED and it.get("sub_tag") not in ALLOWED[it["track"]]:
            it["sub_tag"] = list(ALLOWED[it["track"]])[0]
        # ★ v2 修复：LLM 返回的 track 可能不在 Track Literal 中，容错映射
        if it.get("track") not in ALLOWED:
            from loguru import logger
            raw_track = it.get("track", "?")
            logger.warning(f"[extractor] 无效 track='{raw_track}' for {it.get('project_name','?')}，降为 前沿科技")
            it["track"] = "前沿科技"
            it["sub_tag"] = list(ALLOWED["前沿科技"])[0]
        if not it.get("region_class") or it.get("region_class") == "未知":
            if a.region_hint != "未知":
                it["region_class"] = a.region_hint

        # ★ v1 修复：LLM 返回字段类型可能与 Pydantic 模型不匹配，统一转换
        # founded_year: int → str
        if isinstance(it.get("founded_year"), int):
            it["founded_year"] = str(it["founded_year"])
        # amount / valuation: int/float → str
        for f in ("amount", "valuation"):
            if isinstance(it.get(f), (int, float)):
                it[f] = str(it[f])
        # investors: list → "A，B，C"（LLM 有时返回数组）
        if isinstance(it.get("investors"), list):
            it["investors"] = "，".join(it["investors"])
        # team: list → "A；B；C"（LLM 有时返回数组）
        if isinstance(it.get("team"), list):
            it["team"] = "；".join(it["team"])
        # ★ v2: None → "" 防护（LLM 常返回 null 给 optional 字段）
        for nil_field in ("official_site", "founded_year", "valuation"):
            if it.get(nil_field) is None:
                it[nil_field] = ""

        try:
            out.append(
                Deal(
                    **it,
                    source_url=a.url,
                    source_date=str(a.published_at or "")[:10],
                    sources=[a.source],
                )
            )
        except Exception as e:
            from loguru import logger
            logger.warning(f"[extractor] Deal 构造失败 {it.get('project_name', '?')}: {e}")
            logger.warning(f"  → track={it.get('track')} round={it.get('round')} region_class={it.get('region_class')} importance={it.get('importance')}")
            continue
    return out


def extract(a: Article) -> List[Deal]:
    return _extract_one(a)


def extract_all(arts: List[Article], workers: int = EXTRACT_WORKERS) -> List[Deal]:
    if not arts:
        return []
    deals: List[Deal] = []
    with ThreadPoolExecutor(max_workers=min(workers, len(arts))) as ex:
        for r in ex.map(_extract_one, arts):
            deals += r
    return deals
