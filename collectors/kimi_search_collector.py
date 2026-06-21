import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Literal

from loguru import logger

from config.settings import KIMI_SEARCH_MAX_CHARS, KIMI_SEARCH_MAX_QUERIES
from models.schema import Article


def _has_cjk(s: str) -> bool:
    return bool(re.search(r"[一-鿿]", s))


def _extract_articles_from_content(content: str, region_hint: str) -> List[Article]:
    """从 Kimi 最终回答中按「标题 | URL | 摘要」格式提取 Article。"""
    arts = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        # 找 URL
        url_match = re.search(r"(https?://[^\s\|）\]\)]+)", line)
        if not url_match:
            continue
        url = url_match.group(1).rstrip(".,;:!?")
        # 标题：URL 前或第一个 | 前的文本
        title = url
        parts = [p.strip() for p in re.split(r"[\|\t]", line) if p.strip()]
        if parts:
            # 去掉序号前缀如 1. / -
            first = re.sub(r"^\s*[-\d\.\*]+\s*", "", parts[0])
            if first and not first.startswith("http"):
                title = first
            elif len(parts) >= 2 and not parts[1].startswith("http"):
                title = re.sub(r"^\s*[-\d\.\*]+\s*", "", parts[1])
        # 正文：优先用 | 分隔后的摘要段；不足时回退到整行去掉 URL 的部分
        snippet_parts = [p for p in parts[2:] if not p.startswith("http")] if len(parts) >= 3 else []
        if snippet_parts:
            snippet = " | ".join(snippet_parts)
        else:
            # 回退：整行去掉 URL 和已识别的标题作为正文
            rest = re.sub(r"https?://[^\s\|）\]\)]+", "", line).strip()
            rest = re.sub(r"^\s*[-\d\.\*]+\s*", "", rest)  # 去序号前缀
            if rest == title or len(rest) < 10:
                rest = ""
            snippet = rest
        arts.append(
            Article(
                title=title,
                url=url,
                content=snippet[:KIMI_SEARCH_MAX_CHARS],
                source="kimi-search",
                source_type="search",
                region_hint=region_hint,
            )
        )
    return arts


def _kimi_search_one(
    query: str,
    region_hint: Literal["国内", "海外", "未知"] = "未知",
) -> List[Article]:
    """使用 Kimi (Moonshot) 内置 $web_search 工具完成联网搜索。

    流程（按官方文档）：
    1. 提交 builtin_function/$web_search + 用户问题，禁用 thinking；
    2. 若 finish_reason=tool_calls，把 tool_call.arguments 原样以 role=tool 提交回去；
    3. Kimi 自己执行搜索并返回最终答案；
    4. 从最终答案中按「标题 | URL | 摘要」格式解析 Article。
    """
    key = os.getenv("MOONSHOT_API_KEY")
    base_url = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
    if not key:
        return []

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai not installed, skip Kimi search")
        return []

    client = OpenAI(api_key=key, base_url=base_url)

    hint = region_hint
    if hint == "未知":
        hint = "国内" if _has_cjk(query) else "海外"

    messages = [
        {"role": "system", "content": "你是 Kimi，擅长联网搜索并整理结构化结果。"},
        {
            "role": "user",
            "content": (
                f"请联网搜索：{query}\n"
                "请给出最相关的 3-5 条结果，每条严格按「标题 | URL | 一句话摘要」格式输出，"
                "确保 URL 真实可访问。只输出结果列表，不要多余解释。"
            ),
        },
    ]

    try:
        finish_reason = None
        choice = None
        max_rounds = 3
        for _ in range(max_rounds):
            resp = client.chat.completions.create(
                model="kimi-k2.6",
                messages=messages,
                tools=[{"type": "builtin_function", "function": {"name": "$web_search"}}],
                extra_body={"thinking": {"type": "disabled"}},
                max_tokens=4096,
            )
            choice = resp.choices[0]
            finish_reason = choice.finish_reason
            if finish_reason != "tool_calls":
                break

            # 把 assistant 的 tool_calls 消息加回上下文
            messages.append(choice.message)
            for tc in choice.message.tool_calls:
                if tc.function.name == "$web_search":
                    args = tc.function.arguments
                    try:
                        args_obj = json.loads(args)
                    except Exception:
                        args_obj = args
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": "$web_search",
                        "content": json.dumps(args_obj),
                    })
                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.function.name,
                        "content": json.dumps({"error": "unsupported tool"}),
                    })

        if not choice or not choice.message.content:
            return []

        content = choice.message.content
        return _extract_articles_from_content(content, hint)
    except Exception as e:
        logger.warning(f"Kimi search failed for '{query}': {e}")
        return []


def collect_kimi_search(queries: List[str]) -> List[Article]:
    """并发跑一批 Kimi 联网搜索查询，控制成本。"""
    if not queries:
        return []
    if not os.getenv("MOONSHOT_API_KEY"):
        return []

    queries = list(dict.fromkeys(queries))[:KIMI_SEARCH_MAX_QUERIES]
    all_arts: List[Article] = []
    with ThreadPoolExecutor(max_workers=min(4, len(queries))) as pool:
        futures = {pool.submit(_kimi_search_one, q, "国内" if _has_cjk(q) else "海外"): q for q in queries}
        for fut in as_completed(futures):
            q = futures[fut]
            try:
                arts = fut.result()
                logger.info(f"KimiSearch '{q[:30]}...' -> {len(arts)} results")
                all_arts.extend(arts)
            except Exception as e:
                logger.warning(f"KimiSearch query '{q[:30]}...' failed: {e}")
    return all_arts
