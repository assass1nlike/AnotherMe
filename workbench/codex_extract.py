"""在服务器上运行（只用标准库）：从 Codex 会话记录里提取用户和 Codex 之间的对话。

只保留用户的发言和 Codex 的文字回复，跳过工具调用、工具输出、推理过程和自动注入的上下文。
用法：python3 - <项目路径> <起始 epoch 秒> [关键词 ...] < codex_extract.py
输出每行一个 JSON：{"ts": UTC ISO 时间, "role": "user"|"codex", "text", "session"}。
"""

import glob
import json
import os
import re
import sys
from datetime import datetime

project = os.path.realpath(sys.argv[1])
since = float(sys.argv[2])
words = [w.lower() for w in sys.argv[3:]]

INJECTED = ("<environment_context>", "<user_instructions>", "<permissions", "# AGENTS.md", "<turn_aborted>",
            "<subagent_notification>", "<user_shell_command>")
CITATION = re.compile(r"<oai-mem-citation>.*?</oai-mem-citation>", re.S)
TAGGED = re.compile(r"^<[a-z_]+[\s>]")  # 以标签开头的用户消息是系统注入的，不是用户打的字
MAX_LEN = 3000


def homes():
    """Codex 的数据目录：~/.codex 和 CODEX_HOME，以及大盘目录下的 .codex（有人把 home 放在数据盘）。"""
    cands = [os.path.expanduser("~/.codex"), os.environ.get("CODEX_HOME", "")]
    cands += glob.glob("/data*/" + os.environ.get("USER", "*") + "/.codex")
    seen = set()
    for c in cands:
        if c and os.path.isdir(os.path.join(c, "sessions")):
            r = os.path.realpath(c)
            if r not in seen:
                seen.add(r)
                yield r


def epoch(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def in_project(cwd):
    cwd = os.path.realpath(cwd or "")
    return cwd == project or cwd.startswith(project + "/")


seen = set()
out = []
for home in homes():
    for f in glob.glob(os.path.join(home, "sessions", "**", "*.jsonl"), recursive=True):
        if os.path.getmtime(f) < since:
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                first = json.loads(fh.readline())
                if first.get("type") != "session_meta" or not in_project(first["payload"].get("cwd")):
                    continue
                sid = first["payload"].get("id", os.path.basename(f))
                for line in fh:
                    if '"message"' not in line:
                        continue
                    o = json.loads(line)
                    p = o.get("payload", {})
                    if o.get("type") != "response_item" or p.get("type") != "message" or p.get("role") not in ("user", "assistant"):
                        continue
                    # Codex 干活时的过程旁白（“我先检查一下……”）标为 commentary，只保留正式回复
                    if p["role"] == "assistant" and p.get("phase") == "commentary":
                        continue
                    if epoch(o["timestamp"]) < since:
                        continue
                    text = "\n".join(x.get("text", "") for x in p.get("content", []) if isinstance(x, dict)).strip()
                    if not text or text.startswith(INJECTED) or (p["role"] == "user" and TAGGED.match(text)):
                        continue
                    text = CITATION.sub("", text).strip()
                    if words and not all(w in text.lower() for w in words):
                        continue
                    key = (o["timestamp"][:19], p["role"], text[:200])  # 分叉出的子会话会重复父会话的消息
                    if key in seen:
                        continue
                    seen.add(key)
                    if len(text) > MAX_LEN:
                        text = text[:MAX_LEN] + "……（截断）"
                    out.append({"ts": o["timestamp"], "role": "user" if p["role"] == "user" else "codex",
                                "text": text, "session": sid[:8]})
        except (OSError, ValueError, KeyError):
            continue

out.sort(key=lambda x: x["ts"])
for x in out:
    print(json.dumps(x, ensure_ascii=False))
