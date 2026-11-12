"""角落清单的数据：当前条目和完整的编辑历史。

不依赖界面，AnotherMe 的助手也通过这里读取。条目存在 data/checklist/items.json，
每次增、删、改、移动都追加一行到 history.jsonl，记下时间和改动前后的内容。
"""

import json
import os
import threading
import uuid
from datetime import datetime

from workbench.config import DATA

DIR = DATA / "checklist"
ITEMS = DIR / "items.json"
HISTORY = DIR / "history.jsonl"
_lock = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def items() -> list[dict]:
    if not ITEMS.exists():
        return []
    return json.loads(ITEMS.read_text(encoding="utf-8"))["items"]


def _write(its: list[dict]):
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = ITEMS.with_suffix(".tmp")
    tmp.write_text(json.dumps({"items": its}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, ITEMS)


def _log(op: str, **kw):
    DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now(), "op": op, **kw}, ensure_ascii=False) + "\n")


def add(text: str, pos: int | None = None) -> dict:
    with _lock:
        its = items()
        it = {"id": uuid.uuid4().hex[:8], "text": text, "created": _now(), "updated": _now()}
        pos = len(its) if pos is None else max(0, min(pos, len(its)))
        its.insert(pos, it)
        _write(its)
        _log("add", id=it["id"], text=text, pos=pos)
        return it


def edit(item_id: str, text: str):
    with _lock:
        its = items()
        for it in its:
            if it["id"] == item_id:
                if it["text"] == text:
                    return
                _log("edit", id=item_id, before=it["text"], text=text)
                it["text"], it["updated"] = text, _now()
                _write(its)
                return


def delete(item_id: str):
    with _lock:
        its = items()
        for i, it in enumerate(its):
            if it["id"] == item_id:
                del its[i]
                _write(its)
                _log("delete", id=item_id, text=it["text"], pos=i)
                return


def move(item_id: str, pos: int):
    with _lock:
        its = items()
        for i, it in enumerate(its):
            if it["id"] == item_id:
                its.insert(max(0, min(pos, len(its) - 1)), its.pop(i))
                _write(its)
                _log("move", id=item_id, text=it["text"], frm=i, pos=pos)
                return


def history(since: str | None = None, limit: int = 200) -> list[dict]:
    if not HISTORY.exists():
        return []
    rows = [json.loads(l) for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()]
    if since:
        rows = [r for r in rows if r["ts"] >= since]
    return rows[-limit:]


def describe(row: dict) -> str:
    """把一条历史写成人能读的一句话，给助手看。"""
    t = row["ts"][:16]
    match row["op"]:
        case "add": return f"{t} 加了“{row['text']}”"
        case "edit": return f"{t} 把“{row['before']}”改成“{row['text']}”"
        case "delete": return f"{t} 删了“{row['text']}”"
        case "move": return f"{t} 把“{row['text']}”从第 {row['frm'] + 1} 条移到第 {row['pos'] + 1} 条"
    return f"{t} {row['op']}"
