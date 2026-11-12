"""对 Anthropic 和 OpenAI 两种接口的薄封装：一轮对话里循环调用工具，直到模型给出最终回复。"""

import json
import time
from typing import Callable

from .config import ModelCfg

# 工具定义统一用 {"name", "description", "parameters"(JSON Schema)}
ToolExec = Callable[[str, dict], str]
MAX_STEPS = 16


def _retry(fn):
    """中转服务偶尔返回临时错误，重试两次。认证错误不重试。"""
    for i in range(3):
        try:
            return fn()
        except Exception as e:
            if getattr(e, "status_code", None) in (401, 403) or i == 2:
                raise
            time.sleep(3 * (i + 1) ** 2)


def run_turn(cfg: ModelCfg, system: str, history: list[dict], tools: list[dict],
             execute: ToolExec, on_tool: Callable[[str, dict, str], None] | None = None) -> str:
    """history 是 [{"role": "user"|"assistant", "content": str}]，返回模型的最终文字回复。"""
    if not cfg.key():
        raise RuntimeError(f"模型 {cfg.name} 没有配置 API key（config.toml 的 api_key 或环境变量 {cfg.api_key_env}）")
    fn = _anthropic if cfg.provider == "anthropic" else _openai
    return fn(cfg, system, history, tools, execute, on_tool)


def complete(cfg: ModelCfg, system: str, prompt: str, max_tokens: int = 12000) -> str:
    """不带工具的单次调用，用于整理项目档案。"""
    if not cfg.key():
        raise RuntimeError(f"模型 {cfg.name} 没有配置 API key")
    if cfg.provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=cfg.key(), base_url=cfg.base_url or None)
        resp = _retry(lambda: client.messages.create(model=cfg.model, max_tokens=max_tokens, system=system,
                                                     messages=[{"role": "user", "content": prompt}]))
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    import openai
    client = openai.OpenAI(api_key=cfg.key(), base_url=cfg.base_url or None)
    resp = _retry(lambda: client.chat.completions.create(
        model=cfg.model, messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}]))
    return (resp.choices[0].message.content or "").strip()


def _call(execute, on_tool, name, args) -> str:
    try:
        result = execute(name, args)
    except Exception as e:  # 工具出错时把错误交给模型，让它自己处理
        result = f"出错：{e}"
    if on_tool:
        on_tool(name, args, result)
    return result


def _anthropic(cfg, system, history, tools, execute, on_tool) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=cfg.key(), base_url=cfg.base_url or None)
    msgs = [{"role": m["role"], "content": m["content"]} for m in history]
    spec = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
    for _ in range(MAX_STEPS):
        resp = _retry(lambda: client.messages.create(model=cfg.model, max_tokens=4096, system=system,
                                                     messages=msgs, tools=spec))
        msgs.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]})
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses:
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": b.id, "content": _call(execute, on_tool, b.name, b.input)}
            for b in uses]})
    return "（工具调用次数过多，已停止）"


def _openai(cfg, system, history, tools, execute, on_tool) -> str:
    import openai
    client = openai.OpenAI(api_key=cfg.key(), base_url=cfg.base_url or None)
    msgs = [{"role": "system", "content": system}] + [{"role": m["role"], "content": m["content"]} for m in history]
    spec = [{"type": "function", "function": t} for t in tools]
    for _ in range(MAX_STEPS):
        resp = _retry(lambda: client.chat.completions.create(model=cfg.model, messages=msgs, tools=spec))
        msg = resp.choices[0].message
        msgs.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return (msg.content or "").strip()
        for c in msg.tool_calls:
            args = json.loads(c.function.arguments or "{}")
            msgs.append({"role": "tool", "tool_call_id": c.id, "content": _call(execute, on_tool, c.function.name, args)})
    return "（工具调用次数过多，已停止）"
