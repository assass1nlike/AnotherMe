"""数据与业务逻辑。所有入口（界面按钮、对话里的模型、以后的 MCP）都通过这里读写。"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime

from .config import DATA

DB = DATA / "workbench.db"
PROFILE = DATA / "profile.md"

REASONS = ["完成", "卡住", "腻了", "等待", "打断", "计划外"]

PROFILE_TEMPLATE = """# 关于我

<!-- 未建档 -->

## 身份与研究方向

## 工作习惯

## 近期状态

## 历史
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, server TEXT DEFAULT '', path TEXT DEFAULT '',
  status TEXT DEFAULT 'active', created_at TEXT, synced_commit TEXT DEFAULT '', synced_ts TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, project_id INTEGER, title TEXT NOT NULL, done_when TEXT DEFAULT '',
  estimate_min INTEGER, status TEXT DEFAULT 'open',
  stop_at TEXT DEFAULT '', next_step TEXT DEFAULT '', stopped_ts TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS days(
  id INTEGER PRIMARY KEY, start_ts TEXT NOT NULL, end_ts TEXT, status TEXT DEFAULT 'active',
  available_min INTEGER, energy INTEGER, review TEXT DEFAULT '', tomorrow TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS plan(day_id INTEGER, task_id INTEGER, pos INTEGER, PRIMARY KEY(day_id, task_id));
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, day_id INTEGER NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL,
  from_task INTEGER, to_task INTEGER, stop_at TEXT DEFAULT '', next_step TEXT DEFAULT '',
  reason TEXT DEFAULT '', reason_text TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS inbox(id INTEGER PRIMARY KEY, ts TEXT, text TEXT, status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, ts TEXT, role TEXT, content TEXT, meta TEXT DEFAULT '');
"""


class Error(Exception):
    """可以直接展示给用户的业务错误。"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _minutes(a: str, b: str) -> float:
    fmt = "%Y-%m-%d %H:%M:%S"
    return (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).total_seconds() / 60


@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with db() as con:
        con.executescript(SCHEMA)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(projects)")}
        for col in ("synced_commit", "synced_ts"):
            if col not in cols:
                con.execute(f"ALTER TABLE projects ADD COLUMN {col} TEXT DEFAULT ''")
    if not PROFILE.exists():
        PROFILE.write_text(PROFILE_TEMPLATE, encoding="utf-8")


# ---------- 档案 ----------

def read_profile() -> str:
    return PROFILE.read_text(encoding="utf-8")


def write_profile(content: str):
    PROFILE.write_text(content, encoding="utf-8")


# ---------- 项目 ----------

def list_projects(include_archived=False) -> list[dict]:
    with db() as con:
        q = "SELECT * FROM projects" + ("" if include_archived else " WHERE status='active'") + " ORDER BY id"
        return [dict(r) for r in con.execute(q)]


def get_project(name_or_id) -> dict:
    with db() as con:
        r = con.execute("SELECT * FROM projects WHERE name=? OR id=?", (str(name_or_id), name_or_id)).fetchone()
    if not r:
        raise Error(f"没有项目 {name_or_id}")
    return dict(r)


def upsert_project(name: str, server: str | None = None, path: str | None = None,
                   status: str | None = None) -> dict:
    with db() as con:
        r = con.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
        if not r:
            con.execute("INSERT INTO projects(name, created_at) VALUES(?, ?)", (name, now()))
        for col, val in (("server", server), ("path", path), ("status", status)):
            if val is not None:
                con.execute(f"UPDATE projects SET {col}=? WHERE name=?", (val, name))
    return get_project(name)


def mark_synced(name: str, commit: str, ts: str | None = None):
    """ts 是这次同步开始的时间，下次从这里接着读 Codex 对话，避免漏掉同步过程中产生的新对话。"""
    with db() as con:
        con.execute("UPDATE projects SET synced_commit=?, synced_ts=? WHERE name=?", (commit, ts or now(), name))


# ---------- 任务 ----------

def _task_row(con, task_id) -> dict:
    r = con.execute(
        "SELECT t.*, p.name AS project FROM tasks t LEFT JOIN projects p ON p.id=t.project_id WHERE t.id=?",
        (task_id,)).fetchone()
    if not r:
        raise Error(f"没有任务 {task_id}")
    return dict(r)


def get_task(task_id: int) -> dict:
    with db() as con:
        return _task_row(con, task_id)


def add_task(title: str, project: str | None = None, done_when: str = "", estimate_min: int | None = None) -> dict:
    with db() as con:
        pid = None
        if project:
            r = con.execute("SELECT id FROM projects WHERE name=?", (project,)).fetchone()
            if not r:
                con.execute("INSERT INTO projects(name, created_at) VALUES(?, ?)", (project, now()))
                r = con.execute("SELECT id FROM projects WHERE name=?", (project,)).fetchone()
            pid = r["id"]
        cur = con.execute(
            "INSERT INTO tasks(project_id, title, done_when, estimate_min, created_at) VALUES(?,?,?,?,?)",
            (pid, title, done_when, estimate_min, now()))
        return _task_row(con, cur.lastrowid)


def update_task(task_id: int, **fields) -> dict:
    allowed = {"title", "done_when", "estimate_min", "status", "stop_at", "next_step"}
    with db() as con:
        _task_row(con, task_id)
        for k, v in fields.items():
            if k in allowed and v is not None:
                con.execute(f"UPDATE tasks SET {k}=? WHERE id=?", (v, task_id))
        return _task_row(con, task_id)


def open_tasks() -> list[dict]:
    with db() as con:
        rows = con.execute(
            "SELECT t.*, p.name AS project FROM tasks t LEFT JOIN projects p ON p.id=t.project_id "
            "WHERE t.status='open' ORDER BY t.id").fetchall()
    return [dict(r) for r in rows]


# ---------- 工作日 ----------

def active_day() -> dict | None:
    with db() as con:
        r = con.execute("SELECT * FROM days WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def _require_day() -> dict:
    d = active_day()
    if not d:
        raise Error("还没开工")
    return d


def last_closed_day() -> dict | None:
    with db() as con:
        r = con.execute("SELECT * FROM days WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def start_day(available_min: int | None = None) -> dict:
    if active_day():
        raise Error("上一个工作日还没收工")
    ts = now()
    with db() as con:
        cur = con.execute("INSERT INTO days(start_ts, available_min) VALUES(?, ?)", (ts, available_min))
        con.execute("INSERT INTO events(day_id, ts, kind) VALUES(?, ?, 'start')", (cur.lastrowid, ts))
    return active_day()


def set_available(minutes: int):
    d = _require_day()
    with db() as con:
        con.execute("UPDATE days SET available_min=? WHERE id=?", (minutes, d["id"]))


def set_plan(task_ids: list[int]):
    d = _require_day()
    with db() as con:
        for t in task_ids:
            _task_row(con, t)
        con.execute("DELETE FROM plan WHERE day_id=?", (d["id"],))
        for i, t in enumerate(task_ids):
            con.execute("INSERT INTO plan(day_id, task_id, pos) VALUES(?,?,?)", (d["id"], t, i))


def _events(con, day_id) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM events WHERE day_id=? ORDER BY ts, id", (day_id,))]


def _current(events: list[dict]) -> tuple[int | None, str | None]:
    """返回当前任务和开始时间。"""
    cur, since = None, None
    for e in events:
        if e["kind"] in ("start", "switch", "pause", "end"):
            cur, since = e["to_task"], e["ts"]
    return cur, since


def _save_resume(con, task_id, stop_at, next_step, ts):
    if task_id and (stop_at or next_step):
        con.execute("UPDATE tasks SET stop_at=?, next_step=?, stopped_ts=? WHERE id=?",
                    (stop_at or "", next_step or "", ts, task_id))


def switch(to_task: int | None, stop_at: str = "", next_step: str = "", reason: str = "",
           reason_text: str = "") -> dict:
    """切到 to_task；to_task 为 None 表示暂停。返回要切到的任务（含上次停在哪），用于续接提示。"""
    d = _require_day()
    if reason and reason not in REASONS:
        raise Error(f"原因必须是 {'、'.join(REASONS)} 之一")
    ts = now()
    with db() as con:
        cur, _ = _current(_events(con, d["id"]))
        if to_task is not None:
            target = _task_row(con, to_task)
            if cur == to_task:
                raise Error("已经在做这个任务了")
        kind = "switch" if to_task is not None else "pause"
        con.execute(
            "INSERT INTO events(day_id, ts, kind, from_task, to_task, stop_at, next_step, reason, reason_text) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (d["id"], ts, kind, cur, to_task, stop_at, next_step, reason, reason_text))
        _save_resume(con, cur, stop_at, next_step, ts)
        if cur and reason == "完成":
            con.execute("UPDATE tasks SET status='done' WHERE id=?", (cur,))
        return target if to_task is not None else {}


def end_day(stop_at: str = "", next_step: str = "", energy: int | None = None,
            review: str = "", tomorrow: str = "") -> dict:
    d = _require_day()
    ts = now()
    with db() as con:
        cur, _ = _current(_events(con, d["id"]))
        con.execute("INSERT INTO events(day_id, ts, kind, from_task, stop_at, next_step) VALUES(?,?, 'end', ?,?,?)",
                    (d["id"], ts, cur, stop_at, next_step))
        _save_resume(con, cur, stop_at, next_step, ts)
        con.execute("UPDATE days SET status='done', end_ts=?, energy=?, review=?, tomorrow=? WHERE id=?",
                    (ts, energy, review, tomorrow, d["id"]))
    return day_stats(d["id"])


def save_review(day_id: int, review: str | None = None, tomorrow: str | None = None, energy: int | None = None):
    with db() as con:
        for col, val in (("review", review), ("tomorrow", tomorrow), ("energy", energy)):
            if val is not None:
                con.execute(f"UPDATE days SET {col}=? WHERE id=?", (val, day_id))


# ---------- 统计 ----------

def day_stats(day_id: int) -> dict:
    with db() as con:
        day = dict(con.execute("SELECT * FROM days WHERE id=?", (day_id,)).fetchone())
        events = _events(con, day_id)
        plan_ids = [r["task_id"] for r in con.execute("SELECT task_id FROM plan WHERE day_id=? ORDER BY pos", (day_id,))]
        segments, cur, since = [], None, None
        for e in events:
            if since is not None and e["kind"] != "start":
                segments.append({"task": cur, "start": since, "end": e["ts"]})
            cur, since = e["to_task"], e["ts"]
        if day["status"] == "active" and since is not None:
            segments.append({"task": cur, "start": since, "end": now(), "open": True})
        spent: dict[int, float] = {}
        for s in segments:
            s["minutes"] = round(_minutes(s["start"], s["end"]), 1)
            if s["task"] is not None:
                spent[s["task"]] = spent.get(s["task"], 0) + s["minutes"]
        task_ids = list(dict.fromkeys(plan_ids + [t for t in spent]))
        tasks = [_task_row(con, t) for t in task_ids]
    for t in tasks:
        t["spent_min"] = round(spent.get(t["id"], 0), 1)
        t["planned"] = t["id"] in plan_ids
    # 从“没在做任何任务”进入一个任务（开工后第一次、休息回来）不算切换
    switches = [e for e in events if e["kind"] in ("switch", "pause") and e["from_task"] is not None]
    by_reason: dict[str, int] = {}
    for e in switches:
        if e["reason"]:
            by_reason[e["reason"]] = by_reason.get(e["reason"], 0) + 1
    return {
        "day": day,
        "tasks": tasks,
        "segments": segments,
        "events": events,
        "switch_count": len([e for e in switches if e["kind"] == "switch"]),
        "by_reason": by_reason,
        "worked_min": round(sum(s["minutes"] for s in segments if s["task"] is not None), 1),
    }


def recent_days(n: int = 7) -> list[dict]:
    with db() as con:
        rows = con.execute("SELECT id FROM days WHERE status='done' ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    out = []
    for r in rows:
        s = day_stats(r["id"])
        out.append({
            "start": s["day"]["start_ts"], "end": s["day"]["end_ts"], "energy": s["day"]["energy"],
            "worked_min": s["worked_min"], "switch_count": s["switch_count"], "by_reason": s["by_reason"],
            "review": s["day"]["review"], "tomorrow": s["day"]["tomorrow"],
            "tasks": [{"id": t["id"], "project": t["project"], "title": t["title"], "spent_min": t["spent_min"],
                       "status": t["status"]} for t in s["tasks"]],
        })
    return out


# ---------- 收件箱 ----------

def add_inbox(text: str) -> dict:
    with db() as con:
        cur = con.execute("INSERT INTO inbox(ts, text) VALUES(?, ?)", (now(), text))
        return dict(con.execute("SELECT * FROM inbox WHERE id=?", (cur.lastrowid,)).fetchone())


def list_inbox() -> list[dict]:
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM inbox WHERE status='open' ORDER BY id")]


def close_inbox(item_id: int):
    with db() as con:
        con.execute("UPDATE inbox SET status='done' WHERE id=?", (item_id,))


# ---------- 当前状态 ----------

def state() -> dict:
    d = active_day()
    out = {"now": now(), "day": None, "inbox": list_inbox(), "reasons": REASONS,
           "open_tasks": open_tasks(), "projects": list_projects(),
           "onboarded": "未建档" not in read_profile()}
    if d:
        s = day_stats(d["id"])
        with db() as con:
            cur, since = _current(_events(con, d["id"]))
        out["day"] = {**s, "current": get_task(cur) if cur else None, "since": since}
    else:
        last = last_closed_day()
        out["last_day"] = {"end": last["end_ts"], "tomorrow": last["tomorrow"]} if last else None
    return out


# ---------- 对话记录 ----------

def add_message(role: str, content: str, meta: str = ""):
    with db() as con:
        con.execute("INSERT INTO messages(ts, role, content, meta) VALUES(?,?,?,?)", (now(), role, content, meta))


def messages(since: str | None = None, limit: int = 60) -> list[dict]:
    with db() as con:
        if since:
            rows = con.execute("SELECT * FROM messages WHERE ts>=? ORDER BY id DESC LIMIT ?", (since, limit))
        else:
            rows = con.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in reversed(rows.fetchall())]


# ---------- 检索过往记录 ----------

def search_history(query: str, limit: int = 30) -> list[dict]:
    """在对话、切换记录、复盘和收件箱里按关键词查找，多个关键词用空格分开，需全部出现。"""
    words = [w for w in query.split() if w]
    if not words:
        return []
    sources = [
        ("对话", "SELECT ts, role || '：' || content AS text FROM messages"),
        ("切换", "SELECT ts, '停在：' || stop_at || '；下一步：' || next_step || '；原因：' || reason || ' ' || reason_text AS text "
                 "FROM events WHERE kind IN ('switch','pause','end')"),
        ("复盘", "SELECT start_ts AS ts, review || char(10) || '明天计划：' || tomorrow AS text FROM days"),
        ("收件箱", "SELECT ts, text FROM inbox"),
    ]
    out = []
    with db() as con:
        for kind, sql in sources:
            cond = " AND ".join("text LIKE ?" for _ in words)
            rows = con.execute(f"SELECT * FROM ({sql}) WHERE {cond} ORDER BY ts DESC LIMIT ?",
                               [f"%{w}%" for w in words] + [limit])
            for r in rows:
                text = r["text"]
                i = max(0, text.find(words[0]) - 120)
                out.append({"source": kind, "ts": r["ts"], "text": text[i:i + 400]})
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out[:limit]
