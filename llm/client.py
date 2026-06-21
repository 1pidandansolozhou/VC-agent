import os
import time
from typing import Dict
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

PROVIDERS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "key_env": "DEEPSEEK_API_KEY"},
    "kimi": {
        "base_url": os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1"),
        "key_env": "MOONSHOT_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
    },
}

# 任务 -> (provider, model)。改这里即可切换/控成本。
TASK_MODEL = {
    "extract": ("deepseek", "deepseek-v4-flash"),   # 海量抽取，最便宜
    "classify": ("deepseek", "deepseek-v4-pro"),    # 疑难复核
    "verify": ("deepseek", "deepseek-v4-flash"),    # 反向核查
    "audit": ("deepseek", "deepseek-v4-flash"),     # 补漏覆盖检查
    "enrich": ("deepseek", "deepseek-v4-flash"),   # 第二轮信息补全（v6 新增）
    "write": ("kimi", "kimi-k2.6"),                 # 中文周报文笔
    # GPT 备用路由（质量更高，成本也高，适合疑难场景）
    "extract_gpt": ("openai", "gpt-4o-mini"),
    "verify_gpt": ("openai", "gpt-4o-mini"),
}

_clients: Dict[str, OpenAI] = {}


def _client(provider: str) -> OpenAI:
    if provider not in _clients:
        cfg = PROVIDERS[provider]
        key = os.getenv(cfg["key_env"])
        if not key:
            raise RuntimeError(f"Missing API key for provider '{provider}': {cfg['key_env']} not set")
        _clients[provider] = OpenAI(api_key=key, base_url=cfg["base_url"])
    return _clients[provider]


def chat(task: str, system: str, user: str, max_tokens: int = 2000, temperature: float = 0.3, json_mode: bool = False):
    provider, model = TASK_MODEL[task]
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last = None
    for i in range(3):
        try:
            return _client(provider).chat.completions.create(**kwargs).choices[0].message.content
        except Exception as e:
            last = e
            time.sleep(2 ** i)
    raise last
