"""对话助手：系统提示词、工具定义和执行。"""

import json
import re
import threading
from datetime import datetime

from checklist import store as checklist

from . import core, llm, memory, remote
from .config import Config

SYSTEM = """你是用户的个人工作助手，角色是“第二个我”：了解用户的科研项目、工作习惯和状态，帮用户规划每个工作日、记录每次切换，并在用户回到一件事时帮用户快速接上。用户是做科研的，项目大多在远程服务器上，实验常在 tmux 里跑。用户的痛点是一件事做久了会腻、想换，作息不规律。

# 关于用户（档案）
{profile}

# 当前状态
{state}

# 项目档案
{dossiers}

# 流程

## 建档
档案里还有“未建档”标记时要建档：分几组问题访谈（身份与方向、手上的项目和各自进展、工作习惯、近期状态），一次只问一组。项目可以先用 scan_projects 扫服务器，列出最近活跃的仓库请用户确认哪些是在做的项目，确认后用 upsert_project 建好（填 server 和 path），再调 sync_project 在后台为它建立项目档案（每个项目要几分钟，不用等）。个人档案的草稿给用户看，确认后用 write_profile 写入（去掉“未建档”标记）。
建档可以分几次完成，不要挡住当天的工作：用户说开工时照常 start_day 开始计时，先把今天要做的事定下来，建档穿插在空档里做。

## 开工
用户说“开工”之类的话时：
1. 已有进行中的工作日就先问要不要先收工。
2. start_day。问今天大概能工作多久。
3. 参考上个工作日的“明天计划”、收件箱、项目档案和未完成任务。start_day 会在后台把各项目档案同步到最新，几分钟内完成；要马上知道某个项目的最新进展，用 project_activity。项目档案“待确认”一节有内容时，挑相关的问用户。拟 2 到 3 件事，通常来自不同项目，方便腻了时轮换。每件写清楚做到什么程度算完成、估多久。总估时明显超过可用时间时指出来，建议砍哪件。
4. 用户确认后 add_task（已有的任务直接用 id）并 set_plan。
5. 问先做哪件，用 switch_task 切过去；该任务有“停在/下一步”时给出续接提示。
整个开工控制在几轮对话内。

## 切换（最核心的数据）
用户表示要换去做别的时：
1. 提取三项：停在哪（当前任务做到哪一步）、下一步（回来后第一个具体动作）、原因。尽量用用户的原话，不要加入用户没说的内容；缺哪项简短追问一次，不要替用户编。“下一步”太模糊（如“继续调”）时追问到能直接上手的程度。
2. 原因归入：完成、卡住、腻了、等待（实验在跑、等人回复）、打断、计划外。reason_text 保留用户原话。用户说腻了但描述里有明显卡点时按卡住记，并说明理由。
3. 调 switch_task。要休息、吃饭等不切到任何任务时 to_task_id 传 null。
4. 原因是卡住时，问要不要现在花两分钟把卡住的那一步拆成能马上动手的小步骤。
5. 要切到的任务有“停在/下一步”时，用两三句话给续接提示，只复述记录里的内容，不要自己补充建议。
6. 要切到的任务不在今日计划里时提醒一句，但不阻止。
用户直接汇报进展但没开工时，先 start_day 再记录。

## 随手记
用户说“记一下……”或顺口提到与当前任务无关的新想法、新任务：add_inbox，回一句“记了”，不展开。

## 角落清单
用户在屏幕角落有一个自己维护的清单（下面“当前状态”里列着现在的条目），是用户心里眼下要记着的事，第一条通常最要紧。它由用户自己编辑，你只读不改。开工拟计划、收工复盘时参考它；想知道某条是什么时候加的、改过什么，用 checklist_history 查。

## 收工
用户说“收工”“今天到这”之类时：
1. 问清当前任务停在哪、下一步。
2. 用 get_day_stats 看今天的用时、切换次数和原因，可以用 project_activity 对照服务器上的实际提交。
3. 问一句今天精力打几分（1 到 5），不想答就跳过。
4. 写复盘（markdown，简短）：用时、计划完成情况、切换原因、一两条具体观察（比如某件事做了多久开始腻、反复卡在哪）；再写明天计划草稿。调 end_day 一起保存。
5. 整理收件箱：给每条建议归到哪个项目（add_task）或删掉，用户确认后执行。
6. 发现值得写进个人档案的规律（需要多个工作日的证据）时提出来，用户确认后再改。
end_day 之后，系统会在后台把今天对话里关于各项目的信息沉淀进项目档案，不用你手动写。

# 项目档案的用法
每个项目有一份档案，记录研究问题、方法、结论（带出处）、正在跑的实验、卡点、决策和下一步。今天计划里的项目和当前任务的项目，档案全文已经放在上面；其他项目只有一句话摘要，需要时用 get_dossier 读全文。
回答项目相关的问题、拟计划、给续接提示时，先看档案。档案不够细时，顺着档案里的出处去查原始内容：search_code 在服务器上的项目里搜文本（包括 exps.md），read_remote_file 读指定行；用户以前说过什么，用 search_history 查；用户在服务器上和 Codex 讨论过什么、提过什么要求，用 search_codex 查。不要凭印象回答项目细节，查不到就说查不到。
白天对话里出现的项目信息会在收工时自动沉淀进档案。用户明确要求“记到项目里”时，才用 write_dossier 立刻改。

# 写入规则
- 任务、切换、收件箱，以及项目档案里的结论、进展、卡点、下一步，直接写，不用问。
- 个人档案的任何改动，以及项目档案“研究问题与假设”“方法”两节的改动，先把要改的内容给用户看，确认后再写。
- 会随时间变化的事实写明从什么时候开始，例如“（2026-11 起）”。被取代的旧事实移到“历史”部分，不要直接删。
- 服务器只读，工具也只提供只读操作。

# 回复风格
用中文，简短直接，不要客套，不要把写进记录的内容整段复述给用户。中英文之间加空格。不要用双破折号。"""


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


S, I = {"type": "string"}, {"type": "integer"}
TOOLS = [
    {"name": "get_state", "description": "读取当前状态：工作日、今日计划、当前任务、未完成任务、收件箱、项目。",
     "parameters": _schema({})},
    {"name": "start_day", "description": "开工，开始一个新的工作日。",
     "parameters": _schema({"available_min": {**I, "description": "今天预计可工作的分钟数，可不填"}})},
    {"name": "set_available", "description": "设置今天预计可工作的分钟数。", "parameters": _schema({"minutes": I}, ["minutes"])},
    {"name": "add_task", "description": "新建任务，项目不存在会自动创建。返回任务及 id。",
     "parameters": _schema({"title": S, "project": S, "done_when": {**S, "description": "做到什么程度算完成"},
                            "estimate_min": I}, ["title"])},
    {"name": "update_task", "description": "修改任务。status 可为 open、done、dropped。",
     "parameters": _schema({"task_id": I, "title": S, "done_when": S, "estimate_min": I, "status": S,
                            "stop_at": S, "next_step": S}, ["task_id"])},
    {"name": "set_plan", "description": "设置今日计划，按顺序传任务 id，会替换原计划。",
     "parameters": _schema({"task_ids": {"type": "array", "items": I}}, ["task_ids"])},
    {"name": "switch_task", "description": "切换任务并记录。to_task_id 为 null 表示暂停（休息等）。返回切到的任务，含上次停在哪。",
     "parameters": _schema({"to_task_id": {"type": ["integer", "null"]}, "stop_at": S, "next_step": S,
                            "reason": {"type": "string", "enum": core.REASONS + [""]}, "reason_text": S},
                           ["to_task_id"])},
    {"name": "get_day_stats", "description": "今天（或指定工作日）的用时、切换和原因统计。",
     "parameters": _schema({"day_id": I})},
    {"name": "end_day", "description": "收工，保存复盘和明天计划草稿。",
     "parameters": _schema({"stop_at": S, "next_step": S, "energy": I, "review": S, "tomorrow": S})},
    {"name": "recent_days", "description": "最近几个工作日的摘要：用时、切换原因、复盘。", "parameters": _schema({"n": I})},
    {"name": "add_inbox", "description": "收件箱里记一条。", "parameters": _schema({"text": S}, ["text"])},
    {"name": "close_inbox", "description": "把收件箱里的一条标为已处理。", "parameters": _schema({"item_id": I}, ["item_id"])},
    {"name": "upsert_project", "description": "新建或修改项目。server 是服务器名，path 是项目在服务器上的路径，status 为 active 或 archived。只传要改的字段。",
     "parameters": _schema({"name": S, "server": S, "path": S, "status": S}, ["name"])},
    {"name": "get_dossier", "description": "读取项目档案全文和同步状态。", "parameters": _schema({"name": S}, ["name"])},
    {"name": "write_dossier", "description": "覆盖写入项目档案（完整 markdown，保持原有各节）。旧版本会自动备份。",
     "parameters": _schema({"name": S, "content": S}, ["name", "content"])},
    {"name": "sync_project", "description": "从服务器同步项目档案。从没建过档时在后台通读说明文档、提交历史和 exps.md 建档，要几分钟；建过档时只读新提交和 exps.md 的改动，等它完成再返回。full=true 强制重新通读。",
     "parameters": _schema({"name": S, "full": {"type": "boolean"}}, ["name"])},
    {"name": "search_code", "description": "在服务器上的项目里搜文本（正则），返回文件名、行号和该行。glob 可限定文件，如 '*.py'、'exps.md'。",
     "parameters": _schema({"server": S, "path": S, "pattern": S, "glob": S}, ["server", "path", "pattern"])},
    {"name": "search_history", "description": "在过往的对话、切换记录、复盘和收件箱里查关键词，多个关键词用空格分开。",
     "parameters": _schema({"query": S}, ["query"])},
    {"name": "search_codex", "description": "在服务器上这个项目的 Codex 会话里，查用户和 Codex 的对话原文（不含工具输出）。多个关键词用空格分开，需全部出现；since 是起始日期，如 '2026-10-20'。",
     "parameters": _schema({"project": S, "query": S, "since": S}, ["project", "query"])},
    {"name": "checklist_history", "description": "角落清单的编辑历史：每次加、删、改、移动的时间和前后内容。since 是起始时间，如 '2026-11-01'。",
     "parameters": _schema({"since": S})},
    {"name": "read_profile", "description": "读取用户档案全文。", "parameters": _schema({})},
    {"name": "write_profile", "description": "覆盖写入用户档案（markdown，60 行以内）。必须先经用户确认。",
     "parameters": _schema({"content": S}, ["content"])},
    {"name": "server_status", "description": "服务器的 GPU 占用和 tmux 会话（含工作目录）。",
     "parameters": _schema({"server": S}, ["server"])},
    {"name": "scan_projects", "description": "扫描服务器上的 git 仓库，按最近提交排序，标出是否有 exps.md。",
     "parameters": _schema({"server": S}, ["server"])},
    {"name": "project_activity", "description": "服务器上某个项目最近的 git 提交、未提交改动和 exps.md 末尾。",
     "parameters": _schema({"server": S, "path": S, "since": {**S, "description": "如 '3 days ago'、'2026-11-01'"}},
                           ["server", "path"])},
    {"name": "read_remote_file", "description": "读服务器上的文件：给 start_line 时从该行读 lines 行，否则读开头（from_end=true 时读末尾）。",
     "parameters": _schema({"server": S, "path": S, "lines": I, "start_line": I, "from_end": {"type": "boolean"}},
                           ["server", "path"])},
    {"name": "tmux_tail", "description": "看服务器上某个 tmux 会话最近的输出，用于判断实验跑到哪了。",
     "parameters": _schema({"server": S, "session": S, "lines": I}, ["server", "session"])},
]


def _dump(x) -> str:
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str)
    return s if len(s) < 12000 else s[:12000] + "\n……（已截断）"


def _brief_state() -> str:
    st = core.state()
    lines = [f"现在：{st['now']}"]
    d = st["day"]
    if d:
        lines.append(f"工作日进行中，{d['day']['start_ts']} 开工，已工作 {d['worked_min']:.0f} 分钟，"
                     f"可用时间 {d['day']['available_min'] or '未设'} 分钟，切换 {d['switch_count']} 次")
        cur = d["current"]
        lines.append(f"当前任务：#{cur['id']} [{cur['project']}] {cur['title']}（{d['since']} 起）" if cur else "当前任务：无")
        for t in d["tasks"]:
            mark = "计划" if t["planned"] else "计划外"
            lines.append(f"- {mark} #{t['id']} [{t['project']}] {t['title']}，{t['status']}，已用 {t['spent_min']:.0f} 分钟")
    else:
        lines.append("未开工")
        if st.get("last_day"):
            lines.append(f"上个工作日 {st['last_day']['end']} 收工，明天计划草稿：\n{st['last_day']['tomorrow']}")
    lines.append("未完成任务：")
    for t in st["open_tasks"]:
        resume = f"；停在：{t['stop_at']}；下一步：{t['next_step']}" if t["stop_at"] or t["next_step"] else ""
        lines.append(f"- #{t['id']} [{t['project']}] {t['title']}{resume}")
    lines.append("项目：" + ("，".join(f"{p['name']}（{p['server']}:{p['path']}）" for p in st["projects"]) or "无"))
    lines.append(f"收件箱 {len(st['inbox'])} 条：" + "；".join(f"#{i['id']} {i['text']}" for i in st["inbox"]))
    cl = [i["text"] for i in checklist.items() if i["text"]]
    lines.append(f"角落清单 {len(cl)} 条：" + "；".join(f"{k}. {t}" for k, t in enumerate(cl, 1)))
    return "\n".join(lines)


def _sync_note(p: dict) -> str:
    job = memory.jobs.get(p["name"])
    if job and job["state"] == "running":
        return f"（档案正在更新：{job['msg']}）"
    if not p["synced_ts"]:
        return "（还没建档）"
    return f"（档案同步于 {p['synced_ts'][:16]}）"


def _dossiers() -> str:
    """今天计划里的项目和当前任务的项目放全文，其余放一句话。"""
    d = core.active_day()
    full: set[str] = set()
    if d:
        s = core.day_stats(d["id"])
        full = {t["project"] for t in s["tasks"] if t["project"]}
    out, brief = [], []
    for p in core.list_projects():
        where = f"{p['server']}:{p['path']}" if p["server"] else "未设路径"
        if p["name"] in full:
            out.append(f"## {p['name']}（{where}）{_sync_note(p)}\n\n{memory.read(p['name'])}")
        else:
            brief.append(f"- {p['name']}（{where}）{_sync_note(p)}：{memory.section(p['name'], '一句话') or '无摘要'}")
    if brief:
        out.append("其他项目：\n" + "\n".join(brief))
    return "\n\n".join(out) or "还没有项目"


class Agent:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()

    def execute(self, name: str, a: dict) -> str:
        c = self.cfg
        match name:
            case "get_state": return _brief_state()
            case "start_day":
                day = core.start_day(a.get("available_min"))
                for p in core.list_projects():
                    if p["synced_commit"]:
                        memory.run_job(c, p["name"], "sync", memory.sync)
                return _dump(day)
            case "set_available": core.set_available(a["minutes"]); return "ok"
            case "add_task": return _dump(core.add_task(a["title"], a.get("project"), a.get("done_when", ""), a.get("estimate_min")))
            case "update_task": return _dump(core.update_task(a.pop("task_id"), **a))
            case "set_plan": core.set_plan(a["task_ids"]); return "ok"
            case "switch_task":
                return _dump(core.switch(a.get("to_task_id"), a.get("stop_at", ""), a.get("next_step", ""),
                                         a.get("reason", ""), a.get("reason_text", "")) or "已暂停")
            case "get_day_stats":
                day_id = a.get("day_id") or (core.active_day() or {}).get("id")
                if not day_id:
                    return "没有进行中的工作日"
                s = core.day_stats(day_id)
                s.pop("segments")
                return _dump(s)
            case "end_day":
                day_id = core.active_day()["id"]
                core.end_day(a.get("stop_at", ""), a.get("next_step", ""), a.get("energy"), a.get("review", ""), a.get("tomorrow", ""))
                memory.consolidate_day(c, day_id)
                return "已收工，项目档案正在后台沉淀今天的信息"
            case "recent_days": return _dump(core.recent_days(a.get("n", 7)))
            case "add_inbox": return _dump(core.add_inbox(a["text"]))
            case "close_inbox": core.close_inbox(a["item_id"]); return "ok"
            case "upsert_project": return _dump(core.upsert_project(**a))
            case "get_dossier":
                p = core.get_project(a["name"])
                return f"{_sync_note(p)}\n\n{memory.read(p['name'])}"
            case "write_dossier":
                core.get_project(a["name"])
                memory.write(a["name"], a["content"])
                return "ok"
            case "sync_project":
                p = core.get_project(a["name"])
                if a.get("full") or not p["synced_commit"]:
                    started = memory.run_job(c, p["name"], "build", memory.build)
                    return "已在后台开始建档，每个项目要几分钟" if started else "这个项目的档案已经在更新了"
                return memory.run_now(c, p["name"], memory.sync)
            case "search_code": return _dump(remote.grep(c, a["server"], a["path"], a["pattern"], a.get("glob", "")))
            case "search_history": return _dump(core.search_history(a["query"]))
            case "checklist_history":
                return _dump([checklist.describe(r) for r in checklist.history(a.get("since"))] or "没有编辑记录")
            case "search_codex":
                p = core.get_project(a["project"])
                since = datetime.strptime(a["since"], "%Y-%m-%d").timestamp() if a.get("since") else 0
                rows = remote.codex_dialogue(c, p["server"], p["path"], since, a["query"].split())
                return _dump([f"[{r['ts']} {'用户' if r['role'] == 'user' else 'Codex'}] {r['text']}" for r in rows[-30:]]
                             or "没有匹配的对话")
            case "read_profile": return core.read_profile()
            case "write_profile": core.write_profile(a["content"]); return "ok"
            case "server_status": return _dump(remote.status(c, a["server"], max_age=0))
            case "scan_projects": return _dump(remote.scan_projects(c, a["server"]))
            case "project_activity": return _dump(remote.project_activity(c, a["server"], a["path"], a.get("since", "3 days ago")))
            case "read_remote_file":
                return _dump(remote.read_file(c, a["server"], a["path"], a.get("lines", 120),
                                              a.get("from_end", False), a.get("start_line")))
            case "tmux_tail": return _dump(remote.tmux_tail(c, a["server"], a["session"], a.get("lines", 40)))
        return f"未知工具 {name}"

    def _history(self) -> list[dict]:
        d = core.active_day()
        msgs = core.messages(since=d["start_ts"] if d else None, limit=40 if d else 16)
        out: list[dict] = []
        for m in msgs:
            role = "assistant" if m["role"] == "assistant" else "user"
            text = m["content"] if m["role"] != "note" else f"（界面操作记录）{m['content']}"
            if out and out[-1]["role"] == role:
                out[-1]["content"] += "\n\n" + text
            else:
                out.append({"role": role, "content": text})
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    def chat(self, text: str, model: str | None = None) -> dict:
        # 随手记不依赖模型，模型不可用时也能记下
        m = re.match(r"^\s*(?:记一下|记下)\s*[：:，,]?\s*(.+)|^\s*记\s*[：:]\s*(.+)", text, re.S)
        if m:
            core.add_message("user", text)
            core.add_inbox((m.group(1) or m.group(2)).strip())
            core.add_message("assistant", "记了")
            return {"reply": "记了", "tools": [{"name": "add_inbox", "args": {}, "error": False}]}
        with self.lock:
            core.add_message("user", text)
            servers = "，".join(f"{s.name}（ssh {s.host}，项目目录 {' '.join(s.roots)}）" for s in self.cfg.servers)
            system = (SYSTEM.format(profile=core.read_profile(), state=_brief_state(), dossiers=_dossiers())
                      + f"\n\n# 服务器\n{servers}")
            calls: list[dict] = []

            def on_tool(name, args, result):
                calls.append({"name": name, "args": args, "error": result.startswith("出错")})

            try:
                reply = llm.run_turn(self.cfg.model(model), system, self._history(), TOOLS, self.execute, on_tool)
            except Exception as e:
                reply = f"调用模型失败：{e}"
            core.add_message("assistant", reply, json.dumps(calls, ensure_ascii=False))
            return {"reply": reply, "tools": calls}
