from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, core, memory, remote
from .agent import Agent

cfg = config.load()
core.init()
agent = Agent(cfg)
app = FastAPI(title="AnotherMe")
WEB = config.ROOT / "web"
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.exception_handler(core.Error)
async def _biz_error(_: Request, e: core.Error):
    return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/state")
def state():
    st = core.state()
    for p in st["projects"]:
        p["summary"] = memory.section(p["name"], "一句话")
        p["job"] = memory.jobs.get(p["name"])
    return {**st, "models": [m.name for m in cfg.models], "default_model": cfg.default_model,
            "servers": [s.name for s in cfg.servers]}


@app.get("/api/project/{pid}/dossier")
def get_dossier(pid: int):
    p = core.get_project(pid)
    return {**p, "content": memory.read(p["name"]), "job": memory.jobs.get(p["name"])}


class DossierIn(BaseModel):
    content: str


@app.post("/api/project/{pid}/dossier")
def save_dossier(pid: int, b: DossierIn):
    p = core.get_project(pid)
    memory.write(p["name"], b.content)
    return {"ok": True}


class ProjectIn(BaseModel):
    name: str
    server: str
    path: str


@app.post("/api/project")
def add_project(b: ProjectIn):
    cfg.server(b.server)
    p = core.upsert_project(b.name.strip(), b.server, b.path.strip().rstrip("/"))
    memory.run_job(cfg, p["name"], "build", memory.build)
    core.add_message("note", f"添加项目 {p['name']}（{b.server}:{p['path']}），开始建档")
    return p


class SyncIn(BaseModel):
    full: bool = False


@app.post("/api/project/{pid}/sync")
def sync_project(pid: int, b: SyncIn):
    p = core.get_project(pid)
    if b.full or not p["synced_commit"]:
        started = memory.run_job(cfg, p["name"], "build", memory.build)
    else:
        started = memory.run_job(cfg, p["name"], "sync", memory.sync)
    if not started:
        raise core.Error("这个项目的档案已经在更新了")
    return {"ok": True}


class SwitchIn(BaseModel):
    to_task: int | None = None
    stop_at: str = ""
    next_step: str = ""
    reason: str = ""
    reason_text: str = ""


@app.post("/api/switch")
def switch(b: SwitchIn):
    target = core.switch(b.to_task, b.stop_at, b.next_step, b.reason, b.reason_text)
    to = f"切到 #{target['id']} {target['title']}" if target else "暂停"
    core.add_message("note", f"{to}。停在：{b.stop_at or '未填'}；下一步：{b.next_step or '未填'}；"
                             f"原因：{b.reason or '未填'} {b.reason_text}".strip())
    return {"target": target}


class TaskIn(BaseModel):
    title: str
    project: str | None = None
    done_when: str = ""
    estimate_min: int | None = None
    plan: bool = False


@app.post("/api/task")
def add_task(b: TaskIn):
    t = core.add_task(b.title, b.project, b.done_when, b.estimate_min)
    if b.plan and (d := core.active_day()):
        st = core.day_stats(d["id"])
        core.set_plan([x["id"] for x in st["tasks"] if x["planned"]] + [t["id"]])
    core.add_message("note", f"新建任务 #{t['id']} [{t['project']}] {t['title']}" + ("，加入今日计划" if b.plan else ""))
    return t


class TaskPatch(BaseModel):
    status: str | None = None
    title: str | None = None


@app.post("/api/task/{task_id}")
def update_task(task_id: int, b: TaskPatch):
    t = core.update_task(task_id, **b.model_dump())
    if b.status:
        core.add_message("note", f"任务 #{task_id} {t['title']} 标为 {b.status}")
    return t


class InboxIn(BaseModel):
    text: str


@app.post("/api/inbox")
def add_inbox(b: InboxIn):
    return core.add_inbox(b.text)


@app.post("/api/inbox/{item_id}/close")
def close_inbox(item_id: int):
    core.close_inbox(item_id)
    return {"ok": True}


class ChatIn(BaseModel):
    text: str
    model: str | None = None


@app.post("/api/chat")
def chat(b: ChatIn):
    return agent.chat(b.text, b.model)


@app.get("/api/messages")
def messages():
    return core.messages(limit=80)


@app.get("/api/servers")
def servers():
    with ThreadPoolExecutor(len(cfg.servers) or 1) as ex:
        return list(ex.map(lambda s: remote.status(cfg, s.name), cfg.servers))


@app.get("/api/days")
def days():
    return core.recent_days(14)
