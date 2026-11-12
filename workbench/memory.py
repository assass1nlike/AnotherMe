"""项目档案：每个项目一份 markdown，记录助手对这个项目的认识。

三个来源更新它：首次建档时通读服务器上的说明文档、提交历史、exps.md 和用户与 Codex 的对话；之后每次同步只读新增的提交、
exps.md 的改动和新的 Codex 对话；收工时从当天的对话和切换记录里沉淀。每次改写前把旧版本存进 .history，可以回退。
"""

import difflib
import re
import threading
from datetime import datetime

from . import core, llm, remote
from .config import Config

CHUNK = 60_000  # 每次交给模型的材料字符数上限
CODEX_CHUNK = 100_000  # Codex 对话信息密度低一些，每段可以长一些

TEMPLATE = """# {name}

## 一句话

## 研究问题与假设

## 方法

## 目前的结论

## 正在跑的实验

## 卡点与待解决

## 决策记录

## 下一步

## 待确认

## 历史
"""

RULES = """你在维护一个科研项目的档案。档案是“第二个我”对这个项目的全部认识：之后用户每次开工、切换、做决定，助手都靠它了解项目，所以要具体到能直接指导下一步工作，而不是泛泛的概括。

各节的写法：
- 一句话：项目在做什么，现在处于哪个阶段。
- 研究问题与假设：要回答的问题、核心假设、和已有工作的区别。
- 方法：方法的关键设计、用到的模型和数据集、关键超参。
- 目前的结论：已经得到的实验结果和结论，带关键数字。每条注明出处，如（exps.md「某节标题」）、（commit a1b2c3d）、（2026-11-03 对话）。
- 正在跑的实验：在哪台机器、哪个目录或 tmux 会话、什么时候开始、预期产出。
- 卡点与待解决：没解决的问题、可疑的结果、已知 bug。
- 决策记录：日期、决定、理由。
- 下一步：具体到能直接动手的动作。
- 待确认：材料暗示研究问题、假设或方法可能要改，但还没定下来的事。
- 历史：被推翻或过时的内容，注明日期和被什么取代。

规则：
- 只写材料里有的，不推测，不编数字。
- 保留档案里已有的内容，除非新材料表明它过时了；过时的内容移到“历史”，不要直接删。
- 全文控制在 4000 字以内。超了就压缩较旧、较次要的内容，先压缩“历史”。
- 材料以 git diff 形式给出时，“+”开头的行是新增，“-”开头的行是删除。
- 材料是用户与 Codex（在服务器上帮用户写代码、跑实验的编程助手）的对话时：用户的话反映意图、要求和决定，是决策记录和下一步的重要来源；Codex 的话是它的汇报，可能有误，数字优先以 exps.md 为准。出处写成（Codex 对话 2026-10-16）。Codex 的执行细节（改了哪个文件、修了什么小 bug）一般不用记，除非影响结论或后续工作。
- 修改一节时，没受新材料影响的条目保留原文，不要换说法、调格式。
- 输出格式：只输出需要修改的节，每节以“## 节名”开头，后面是该节修改后的完整内容。没有变化的节不要输出，程序会原样保留。节名必须是档案里已有的。没有任何需要修改的内容时，只输出“无变化”。不要任何解释。"""

CONSOLIDATE_RULES = RULES + """

这次的材料是用户今天和助手的对话及切换记录。额外规则：
- 不要改“一句话”“研究问题与假设”“方法”三节。如果对话表明它们需要改，把建议写进“待确认”。
- 只提取和这个项目有关的信息，比如结果、发现、决定、卡点、想法、下一步。和项目无关的闲聊忽略。
- 对话里没有这个项目的新信息时，只输出“无变化”。"""

_locks: dict[str, threading.Lock] = {}
jobs: dict[str, dict] = {}  # 项目名 -> {"state": running|done|error, "kind", "msg", "ts"}


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def _dir():
    return core.DATA / "projects"


def path(name: str):
    return _dir() / f"{_safe(name)}.md"


def read(name: str) -> str:
    p = path(name)
    return p.read_text(encoding="utf-8") if p.exists() else TEMPLATE.format(name=name)


def write(name: str, content: str):
    _dir().mkdir(parents=True, exist_ok=True)
    p = path(name)
    if p.exists():
        hist = _dir() / ".history" / _safe(name)
        hist.mkdir(parents=True, exist_ok=True)
        (hist / f"{datetime.now():%Y%m%d-%H%M%S}.md").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    p.write_text(content.strip() + "\n", encoding="utf-8")


def section(name: str, title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}\s*\n(.*?)(?=^## |\Z)", read(name), re.S | re.M)
    return m.group(1).strip() if m else ""


def _chunks(text: str, size: int = CHUNK, boundary: str = r"(?m)^(?=#{1,3} )") -> list[str]:
    """在 boundary 处切块（默认是 markdown 标题），每块不超过 size 字符。"""
    out, cur = [], ""
    for part in re.split(boundary, text):
        while len(part) > size:
            if cur:
                out.append(cur); cur = ""
            out.append(part[:size]); part = part[size:]
        if len(cur) + len(part) > size:
            out.append(cur); cur = ""
        cur += part
    if cur.strip():
        out.append(cur)
    return out


def _split(text: str) -> tuple[str, list[list[str]]]:
    """把档案拆成开头部分和 [节名, 内容] 列表。"""
    parts = re.split(r"(?m)^## (.+?)\s*$", text)
    return parts[0], [[parts[i].strip(), parts[i + 1]] for i in range(1, len(parts), 2)]


def _update(cfg: Config, name: str, dossier: str, material: str, source: str, rules: str = RULES) -> str:
    """让模型只给出要改的节，再合并回档案，没提到的节保持原文。"""
    prompt = (f"当前档案：\n\n{dossier}\n\n---\n\n新材料（{source}）：\n\n{material}\n\n---\n\n"
              "按规则只输出需要修改的节。")
    out = llm.complete(cfg.model(cfg.sync_model), rules, prompt)
    out = re.sub(r"^```\w*\n|\n```$", "", out.strip())
    if out.strip() == "无变化":
        return dossier
    head, secs = _split(dossier)
    index = {sec[0]: sec for sec in secs}
    applied = 0
    for title, body in _split(out)[1]:
        if title in index:
            index[title][1] = body
            applied += 1
    if not applied:
        raise RuntimeError("模型返回的内容里没有可识别的档案节，已保留原档案")
    # 统一空行格式，避免每次合并都产生只有空行的改动
    return head.strip() + "\n\n" + "".join(f"## {t}\n\n{b.strip()}\n\n" if b.strip() else f"## {t}\n\n" for t, b in secs)


def _progress(name: str, msg: str):
    jobs[name] = {**jobs.get(name, {}), "msg": msg}


def _dialogue(rows: list[dict]) -> str:
    # 每条消息前空一行，切块时按消息边界切
    return "".join(f"\n\n[{r['ts']} {'用户' if r['role'] == 'user' else 'Codex'}] {r['text']}" for r in rows).strip()


def _read_codex(cfg: Config, name: str, p: dict, dossier: str, since: float) -> tuple[str, int]:
    """把上次同步以来、在这个项目目录里的 Codex 对话按时间顺序读进档案。"""
    if not cfg.codex:
        return dossier, 0
    _progress(name, "提取 Codex 对话记录")
    rows = remote.codex_dialogue(cfg, p["server"], p["path"], since)
    chunks = _chunks(_dialogue(rows), CODEX_CHUNK, r"\n\n(?=\[\d{4}-\d\d-\d\d \d\d:\d\d (?:用户|Codex)\])")
    for i, ch in enumerate(chunks, 1):
        _progress(name, f"通读 Codex 对话，第 {i}/{len(chunks)} 段")
        dossier = _update(cfg, name, dossier, ch, f"用户与 Codex 的对话记录，第 {i}/{len(chunks)} 段，按时间顺序")
        write(name, dossier)
    return dossier, len(rows)


def _exps_cache(name: str):
    return _dir() / ".exps" / f"{_safe(name)}.md"


def _save_exps(name: str, text: str):
    _exps_cache(name).parent.mkdir(parents=True, exist_ok=True)
    _exps_cache(name).write_text(text, encoding="utf-8")


def _exps_diff(name: str, text: str) -> str:
    """和上次读到的 exps.md 比较，只返回新的改动。不依赖 git，未提交的改动也只会被读一次。"""
    cache = _exps_cache(name)
    if not cache.exists():  # 早于这个缓存机制建的档案：当作已读过，从现在开始比对
        return ""
    old = cache.read_text(encoding="utf-8")
    if old == text:
        return ""
    lines = difflib.unified_diff(old.splitlines(), text.splitlines(), "上次读到的 exps.md", "现在的 exps.md",
                                 lineterm="", n=2)
    return "\n".join(lines)


def build(cfg: Config, name: str):
    """首次建档：通读说明文档、提交历史、整份 exps.md 和全部 Codex 对话。"""
    p = core.get_project(name)
    if not (p["server"] and p["path"]):
        raise core.Error(f"项目 {name} 没有设置服务器和路径")
    started = core.now()
    commit = remote.head_commit(cfg, p["server"], p["path"])
    dossier = read(name)
    _progress(name, "读取说明文档和提交历史")
    dossier = _update(cfg, name, dossier, remote.overview(cfg, p["server"], p["path"]), "项目目录、说明文档和提交历史")
    write(name, dossier)
    exps = remote.read_whole(cfg, p["server"], f"{p['path']}/exps.md")
    chunks = _chunks(exps)
    for i, ch in enumerate(chunks, 1):
        _progress(name, f"通读 exps.md，第 {i}/{len(chunks)} 段")
        dossier = _update(cfg, name, dossier, ch, f"exps.md 第 {i}/{len(chunks)} 段，按时间顺序")
        write(name, dossier)
    _save_exps(name, exps)
    dossier, n = _read_codex(cfg, name, p, dossier, 0)
    core.mark_synced(name, commit, started)
    return f"建档完成，读了说明文档、提交历史、{len(chunks)} 段 exps.md 和 {n} 条 Codex 对话"


def sync(cfg: Config, name: str) -> str:
    """增量同步：只读上次同步以来的新提交、exps.md 的改动和新的 Codex 对话。从没同步过、或历史被改写时改为完整建档。"""
    p = core.get_project(name)
    if not (p["server"] and p["path"]):
        raise core.Error(f"项目 {name} 没有设置服务器和路径")
    if not p["synced_commit"] or not remote.has_commit(cfg, p["server"], p["path"], p["synced_commit"]):
        return build(cfg, name)
    started = core.now()
    commit = remote.head_commit(cfg, p["server"], p["path"])
    _progress(name, "读取新的提交和 exps.md 改动")
    log_and_stat = remote.changes_since(cfg, p["server"], p["path"], p["synced_commit"])
    log = log_and_stat.split("## 改动文件统计", 1)[0].replace("## 新提交", "").strip()
    exps = remote.read_whole(cfg, p["server"], f"{p['path']}/exps.md")
    exps_diff = _exps_diff(name, exps)
    dossier = read(name)
    if log or exps_diff:
        material = (log_and_stat if log else "") + (f"\n\n## exps.md 的改动\n{exps_diff}" if exps_diff else "")
        chunks = _chunks(material)
        for i, ch in enumerate(chunks, 1):
            _progress(name, f"整理改动，第 {i}/{len(chunks)} 段")
            dossier = _update(cfg, name, dossier, ch, f"上次同步以来的变化，第 {i}/{len(chunks)} 段")
        write(name, dossier)
    _save_exps(name, exps)
    since = datetime.strptime(p["synced_ts"], "%Y-%m-%d %H:%M:%S").timestamp() if p["synced_ts"] else 0
    dossier, n = _read_codex(cfg, name, p, dossier, since)
    core.mark_synced(name, commit, started)
    if not (log or exps_diff or n):
        return "没有新的提交、exps.md 改动或 Codex 对话，档案不变"
    commits = len([l for l in log.splitlines() if l.strip()])
    return f"同步完成：{commits} 个新提交" + ("，exps.md 有改动" if exps_diff else "") + f"，{n} 条新的 Codex 对话"


def consolidate(cfg: Config, name: str, material: str) -> str:
    before = read(name)
    after = _update(cfg, name, before, material, "今天的对话和切换记录", CONSOLIDATE_RULES)
    if after.strip() != before.strip():
        write(name, after)
        return "已更新"
    return "无变化"


def run_job(cfg: Config, name: str, kind: str, fn, *args) -> bool:
    """在后台线程跑档案任务。同一项目同时只跑一个，已在跑时返回 False。"""
    lock = _locks.setdefault(name, threading.Lock())
    if not lock.acquire(blocking=False):
        return False
    jobs[name] = {"state": "running", "kind": kind, "msg": "开始", "ts": core.now()}

    def work():
        try:
            jobs[name] = {**jobs[name], "state": "done", "msg": fn(cfg, name, *args), "ts": core.now()}
        except Exception as e:
            jobs[name] = {**jobs[name], "state": "error", "msg": str(e)[:300], "ts": core.now()}
        finally:
            lock.release()

    threading.Thread(target=work, daemon=True).start()
    return True


def run_now(cfg: Config, name: str, fn, *args) -> str:
    """在当前线程跑，供需要等结果的场合（如开工前同步今天要做的项目）。"""
    lock = _locks.setdefault(name, threading.Lock())
    if not lock.acquire(blocking=False):
        return "这个项目的档案正在后台更新，稍后再看"
    jobs[name] = {"state": "running", "kind": fn.__name__, "msg": "开始", "ts": core.now()}
    try:
        msg = fn(cfg, name, *args)
        jobs[name] = {**jobs[name], "state": "done", "msg": msg, "ts": core.now()}
        return msg
    except Exception as e:
        jobs[name] = {**jobs[name], "state": "error", "msg": str(e)[:300], "ts": core.now()}
        raise
    finally:
        lock.release()


def day_material(day_id: int, project: str) -> str:
    """收工沉淀用：当天的对话全文，以及这个项目相关任务的切换记录。"""
    s = core.day_stats(day_id)
    ids = {t["id"]: t for t in s["tasks"] if t["project"] == project}
    lines = [f"工作日：{s['day']['start_ts'][:16]} 开工", "", "## 切换记录"]
    for e in s["events"]:
        if e["from_task"] in ids:
            lines.append(f"- {e['ts']} 离开「{ids[e['from_task']]['title']}」：停在 {e['stop_at']}；下一步 {e['next_step']}；"
                         f"原因 {e['reason']} {e['reason_text']}")
    lines.append("\n## 今天的对话")
    for m in core.messages(since=s["day"]["start_ts"], limit=400):
        lines.append(f"[{m['ts'][:16]} {m['role']}] {m['content']}")
    return "\n".join(lines)


def projects_touched(day_id: int) -> list[str]:
    s = core.day_stats(day_id)
    names = {t["project"] for t in s["tasks"] if t["project"]}
    text = "\n".join(m["content"] for m in core.messages(since=s["day"]["start_ts"], limit=400))
    names |= {p["name"] for p in core.list_projects() if p["name"] in text}
    return sorted(names)


def consolidate_day(cfg: Config, day_id: int):
    """收工后在后台把当天的信息沉淀进每个涉及的项目档案。"""
    for name in projects_touched(day_id):
        run_job(cfg, name, "consolidate", lambda c, n: consolidate(c, n, day_material(day_id, n)))
