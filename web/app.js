const COLORS = ["#7fa6d6", "#d0a86a", "#86b89a", "#b597c9", "#c98f9f", "#8fb8c0"];
const TOOL_LABELS = {
  get_state: "读取状态", start_day: "开工", set_available: "设置可用时间", add_task: "新建任务",
  update_task: "修改任务", set_plan: "设置计划", switch_task: "记录切换", get_day_stats: "统计今天",
  end_day: "收工", recent_days: "查看最近几天", add_inbox: "记入收件箱", close_inbox: "处理收件箱",
  upsert_project: "更新项目", read_profile: "读取个人档案", write_profile: "写入个人档案",
  server_status: "查看服务器", scan_projects: "扫描项目", project_activity: "查看项目进展",
  read_remote_file: "读取服务器文件", tmux_tail: "查看 tmux 输出", get_dossier: "读取项目档案",
  write_dossier: "更新项目档案", sync_project: "同步项目档案", search_code: "搜索项目代码",
  search_history: "查过往记录", search_codex: "查 Codex 对话",
};
const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

const $ = (id) => document.getElementById(id);
let S = null;          // 最近一次 /api/state
let colorOf = {};      // 任务 id -> 颜色
let busy = false;

const parse = (s) => new Date(s.replace(" ", "T"));
const pad = (n) => String(n).padStart(2, "0");
const hm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function dur(min) {
  min = Math.max(0, Math.round(min));
  const h = Math.floor(min / 60), m = min % 60;
  return h ? `${h} 小时 ${m} 分` : `${m} 分`;
}

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || data.detail || `请求失败（${r.status}）`);
  return data;
}

// ---------- 状态与渲染 ----------

async function refresh() {
  S = await api("/api/state");
  colorOf = {};
  (S.day?.tasks || []).forEach((t, i) => (colorOf[t.id] = COLORS[i % COLORS.length]));
  renderHeader(); renderThread(); renderNow(); renderPlan(); renderInbox(); renderProjects();
  if (!$("model").options.length) {
    for (const m of S.models) $("model").add(new Option(m, m));
    $("model").value = localStorage.getItem("model") || S.default_model;
  }
}

function renderHeader() {
  const d = new Date();
  $("date").textContent = `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${WEEK[d.getDay()]}`;
  const day = S.day;
  if (day) {
    const avail = day.day.available_min ? `，计划 ${dur(day.day.available_min)}` : "";
    $("daymeta").textContent = `${hm(parse(day.day.start_ts))} 开工，已工作 ${dur(day.worked_min)}${avail}，切换 ${day.switch_count} 次`;
  } else {
    $("daymeta").textContent = "还没开工";
  }
  $("startBtn").hidden = !!day;
  $("endBtn").hidden = !day;
}

function renderThread() {
  const track = $("track"), axis = $("axis");
  track.innerHTML = ""; axis.innerHTML = "";
  const day = S.day;
  if (!day) {
    track.innerHTML = `<div class="empty">开工后，这里会画出今天每段时间花在了哪件事上</div>`;
    return;
  }
  const t0 = parse(day.day.start_ts).getTime();
  const now = Date.now();
  const plannedEnd = day.day.available_min ? t0 + day.day.available_min * 60000 : 0;
  const t1 = Math.max(now, plannedEnd, t0 + 3 * 3600000);
  const x = (t) => ((t - t0) / (t1 - t0)) * 100;

  if (plannedEnd > now) {
    track.insertAdjacentHTML("beforeend", `<div class="planned" style="left:${x(now)}%;width:${x(plannedEnd) - x(now)}%"></div>`);
  }
  for (const s of day.segments) {
    if (s.task == null) continue;
    const a = parse(s.start).getTime(), b = s.open ? now : parse(s.end).getTime();
    const el = document.createElement("div");
    el.className = "seg" + (s.open ? " open" : "");
    el.style.cssText = `left:${x(a)}%;width:${Math.max(x(b) - x(a), 0.2)}%;background:${colorOf[s.task] || COLORS[0]}`;
    const t = day.tasks.find((t) => t.id === s.task);
    el.dataset.tip = `<b>${esc(t?.title)}</b><br>${hm(new Date(a))} 到 ${hm(new Date(b))}，${dur((b - a) / 60000)}`;
    track.append(el);
  }
  for (const e of day.events) {
    if (!(e.kind === "switch" || e.kind === "pause") || e.from_task == null) continue;
    const el = document.createElement("div");
    el.className = `mark ${e.kind === "pause" ? "pause" : "r-" + (e.reason || "none")}`;
    el.style.left = x(parse(e.ts).getTime()) + "%";
    const from = day.tasks.find((t) => t.id === e.from_task);
    el.dataset.tip = `<b>${hm(parse(e.ts))} ${e.kind === "pause" ? "暂停" : "切换"}</b>（${esc(e.reason || "未记原因")}）<br>`
      + `${esc(from?.title)}<br>停在：${esc(e.stop_at || "未填")}<br>下一步：${esc(e.next_step || "未填")}`
      + (e.reason_text ? `<br>原话：${esc(e.reason_text)}` : "");
    track.append(el);
  }
  track.insertAdjacentHTML("beforeend", `<div class="now-line" style="left:${x(now)}%"></div>`);

  const step = (t1 - t0) > 10 * 3600000 ? 2 : 1;
  const first = new Date(t0); first.setMinutes(0, 0, 0);
  for (let t = first.getTime() + 3600000; t < t1; t += step * 3600000) {
    axis.insertAdjacentHTML("beforeend", `<span style="left:${x(t)}%">${pad(new Date(t).getHours())}:00</span>`);
  }
}

function tickTimer() {
  const el = $("timer");
  if (el && S?.day?.since) {
    const sec = Math.max(0, Math.floor((Date.now() - parse(S.day.since).getTime()) / 1000));
    el.textContent = `${pad(Math.floor(sec / 3600))}:${pad(Math.floor(sec / 60) % 60)}:${pad(sec % 60)}`;
  }
}

function renderNow() {
  const box = $("now"), day = S.day;
  box.className = "now";
  if (!day) {
    box.classList.add("idle");
    const draft = S.last_day?.tomorrow ? `<div class="draft">上次收工时写的计划：\n${esc(S.last_day.tomorrow)}</div>` : "";
    const hint = S.onboarded ? "还没开工" : "第一次使用，开工时助手会先了解你的项目和习惯";
    box.innerHTML = `<p class="title">${hint}</p>${draft}<div class="btns"><button id="nowStart">开工</button></div>`;
    $("nowStart").onclick = () => send("开工");
    return;
  }
  const cur = day.current;
  if (!cur) {
    box.classList.add("idle");
    box.innerHTML = `<p class="title">休息中</p><div class="sub">从今日计划里选一件继续</div>
      <div class="timer" id="timer"></div>`;
    tickTimer();
    return;
  }
  const spent = day.tasks.find((t) => t.id === cur.id)?.spent_min || 0;
  const est = cur.estimate_min ? `，估 ${dur(cur.estimate_min)}` : "";
  const resume = cur.stop_at || cur.next_step
    ? `<div class="resume">上次停在：<b>${esc(cur.stop_at || "未记")}</b><br>下一步：<b>${esc(cur.next_step || "未记")}</b></div>` : "";
  box.innerHTML = `
    <p class="proj" style="color:${colorOf[cur.id]}">${esc(cur.project || "无项目")}</p>
    <p class="title">${esc(cur.title)}</p>
    <div class="timer" id="timer"></div>
    <div class="sub">今天在这件事上共 ${dur(spent)}${est}</div>
    ${resume}
    <div class="btns"><button id="switchBtn">切换</button><button class="ghost" id="pauseBtn">暂停</button><button class="ghost" id="doneBtn">完成</button></div>`;
  tickTimer();
  $("switchBtn").onclick = () => openSwitch();
  $("pauseBtn").onclick = () => openSwitch(null, "pause");
  $("doneBtn").onclick = () => openSwitch(undefined, "done");
}

function renderPlan() {
  const ul = $("plan"); ul.innerHTML = "";
  const day = S.day;
  const tasks = day ? day.tasks : [];
  if (!tasks.length) {
    ul.innerHTML = `<li class="emptyline">${day ? "还没有计划，可以让助手帮你拟，或者新建任务" : "开工后在这里列出今天要推进的 2 到 3 件事"}</li>`;
    return;
  }
  for (const t of tasks) {
    const li = document.createElement("li");
    if (t.status === "done") li.classList.add("done");
    if (day.current?.id === t.id) li.classList.add("current");
    const est = t.estimate_min ? ` / ${dur(t.estimate_min)}` : "";
    li.innerHTML = `<span class="bar" style="background:${colorOf[t.id]}"></span>
      <div><div class="t">${esc(t.title)}</div><div class="p">${esc(t.project || "")}${t.planned ? "" : "，计划外"}</div></div>
      <div class="m">${dur(t.spent_min)}${est}</div>`;
    li.tabIndex = 0;
    const go = () => { if (day.current?.id !== t.id && t.status !== "done") openSwitch(t.id); };
    li.onclick = go;
    li.onkeydown = (e) => e.key === "Enter" && go();
    ul.append(li);
  }
}

function renderInbox() {
  const ul = $("inbox"); ul.innerHTML = "";
  $("inboxCount").textContent = S.inbox.length ? `${S.inbox.length} 条` : "";
  for (const i of S.inbox) {
    const li = document.createElement("li");
    li.innerHTML = `<time>${i.ts.slice(11, 16)}</time><span>${esc(i.text)}</span><button class="link" title="处理掉">处理</button>`;
    li.querySelector("button").onclick = async () => { await api(`/api/inbox/${i.id}/close`, {}); refresh(); };
    ul.append(li);
  }
}

function syncText(p) {
  const j = p.job;
  if (j?.state === "running") return { cls: "run", text: j.msg };
  if (j?.state === "error") return { cls: "err", text: `更新失败：${j.msg}` };
  if (!p.synced_ts) return { cls: "", text: p.server ? "还没建档" : "没有设置服务器路径" };
  return { cls: "", text: `档案同步于 ${p.synced_ts.slice(5, 16)}` };
}

function renderProjects() {
  const ul = $("projects"); ul.innerHTML = "";
  $("projectList").innerHTML = S.projects.map((p) => `<option value="${esc(p.name)}">`).join("");
  if (!$("serverSelect").options.length) for (const s of S.servers) $("serverSelect").add(new Option(s, s));
  if (!S.projects.length) {
    ul.innerHTML = `<li class="emptyline">建档时助手会扫描服务器，和你一起确认在做的项目</li>`;
    return;
  }
  for (const p of S.projects) {
    const st = syncText(p);
    const li = document.createElement("li");
    li.innerHTML = `<div class="pname"><button class="link open">${esc(p.name)}</button>
        ${p.server && st.cls !== "run" ? `<button class="link sync">同步</button>` : ""}</div>
      ${p.summary ? `<p class="summary">${esc(p.summary)}</p>` : ""}
      <small class="${st.cls}">${esc(st.text)}</small>`;
    li.querySelector(".open").onclick = () => openDossier(p.id);
    const sb = li.querySelector(".sync");
    if (sb) sb.onclick = () => syncProject(p.id, false);
    ul.append(li);
  }
}

async function syncProject(id, full) {
  try { await api(`/api/project/${id}/sync`, { full }); } catch (e) { alert(e.message); }
  refresh();
}

let dossierId = null;
async function openDossier(id) {
  dossierId = id;
  const d = await api(`/api/project/${id}/dossier`);
  const st = syncText(d);
  $("dossierTitle").textContent = d.name;
  $("dossierMeta").textContent = `${d.server ? d.server + ":" + d.path : "没有设置服务器路径"}，${st.text}`;
  $("dossierView").innerHTML = md(d.content);
  $("dossierText").value = d.content;
  $("dossierView").hidden = false; $("dossierText").hidden = true;
  $("dossierEdit").textContent = "编辑";
  $("dossierRebuild").hidden = !d.server;
  if (!$("dossierDlg").open) $("dossierDlg").showModal();
}

$("dossierEdit").onclick = async () => {
  if ($("dossierText").hidden) {
    $("dossierView").hidden = true; $("dossierText").hidden = false;
    $("dossierEdit").textContent = "保存"; $("dossierText").focus();
    return;
  }
  try {
    await api(`/api/project/${dossierId}/dossier`, { content: $("dossierText").value });
    openDossier(dossierId);
  } catch (e) { alert(e.message); }
};
$("dossierClose").onclick = () => $("dossierDlg").close();
$("dossierRebuild").onclick = async () => {
  if (!confirm("重新通读会花几分钟，期间档案按新读到的内容重写，旧版本会备份。继续吗？")) return;
  await syncProject(dossierId, true);
  $("dossierDlg").close();
};

$("addProjectBtn").onclick = () => { $("newProject").hidden = false; $("newProject").name.focus(); };
$("cancelProject").onclick = () => ($("newProject").hidden = true);
$("newProject").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    await api("/api/project", { name: f.name.value, server: f.server.value, path: f.path.value });
    f.reset(); f.hidden = true; refresh(); loadMessages();
  } catch (err) { alert(err.message); }
};

async function renderServers() {
  const box = $("servers");
  if (!box.children.length) box.innerHTML = `<p class="count">连接中</p>`;
  let list;
  try { list = await api("/api/servers"); } catch (e) { box.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  box.innerHTML = list.map((s) => {
    if (!s.ok) return `<div class="server"><div class="name">${esc(s.server)}</div><p class="err">${esc(s.error)}</p></div>`;
    const busyN = s.gpus.filter((g) => g.util > 10).length;
    const gpus = s.gpus.map((g) => `<div class="gpu" title="GPU ${g.index}：利用率 ${g.util}%，显存 ${Math.round(g.mem / 1024)}/${Math.round(g.total / 1024)} GB">
        <div class="mem" style="height:${(g.mem / g.total) * 100}%"></div><div class="util" style="height:${g.util}%"></div></div>`).join("");
    const short = (p) => p.split("/").filter(Boolean).slice(-2).join("/");
    const tmux = s.tmux.map((t) => `<li><span>${esc(t.name)}</span><span title="${esc(t.path)}">${esc(short(t.path))}</span></li>`).join("");
    return `<div class="server"><div class="name">${esc(s.server)}<small>${busyN}/${s.gpus.length} 张卡在跑</small></div>
      <div class="gpus">${gpus}</div><div class="legend">底色是显存，中间细条是利用率</div>
      <ul class="tmux">${tmux}</ul></div>`;
  }).join("");
}

// ---------- 工作线悬停提示 ----------

$("track").addEventListener("mousemove", (e) => {
  const el = e.target.closest("[data-tip]"), tip = $("tip");
  if (!el) { tip.hidden = true; return; }
  tip.innerHTML = el.dataset.tip; tip.hidden = false;
  const box = $("track").parentElement.getBoundingClientRect();
  tip.style.left = Math.min(e.clientX - box.left + 12, box.width - 330) + "px";
  tip.style.top = "60px";
});
$("track").addEventListener("mouseleave", () => ($("tip").hidden = true));

// ---------- 切换 ----------

let switchMode = "switch";
function openSwitch(target, mode = "switch") {
  switchMode = mode;
  const f = $("switchForm"); f.reset();
  const sel = $("switchTarget"); sel.innerHTML = "";
  const day = S.day, cur = day?.current?.id;
  const seen = new Set();
  const add = (t, label) => { if (t.id !== cur && !seen.has(t.id) && t.status !== "done") { seen.add(t.id); sel.add(new Option(label, t.id)); } };
  (day?.tasks || []).filter((t) => t.planned).forEach((t) => add(t, `${t.title}（${t.project || "无项目"}）`));
  S.open_tasks.forEach((t) => add(t, `计划外：${t.title}（${t.project || "无项目"}）`));
  sel.add(new Option("不切到任何任务（休息）", ""));
  $("switchTitle").textContent = { switch: "切换", pause: "暂停", done: "完成并切换" }[mode];
  if (target !== undefined) sel.value = target ?? "";
  if (mode === "pause") sel.value = "";
  $("reasons").querySelectorAll("label").forEach((l) => l.remove());
  for (const r of S.reasons) {
    $("reasons").insertAdjacentHTML("beforeend", `<label><input type="radio" name="reason" value="${r}"><span>${r}</span></label>`);
  }
  if (mode === "done") $("reasons").querySelector('input[value="完成"]').checked = true;
  if (!cur) { f.stop_at.closest("label").hidden = true; f.next_step.closest("label").hidden = true; $("reasons").hidden = true; }
  else { f.stop_at.closest("label").hidden = false; f.next_step.closest("label").hidden = false; $("reasons").hidden = false; }
  $("switchDlg").showModal();
  (cur ? f.stop_at : sel).focus();
}

$("cancelSwitch").onclick = () => $("switchDlg").close();
$("switchForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    to_task: f.to_task.value === "" ? null : Number(f.to_task.value),
    stop_at: f.stop_at.value.trim(), next_step: f.next_step.value.trim(),
    reason: f.reason?.value || "", reason_text: f.reason_text.value.trim(),
  };
  try {
    await api("/api/switch", body);
    $("switchDlg").close();
    await refresh(); loadMessages();
  } catch (err) { alert(err.message); }
};

// ---------- 任务与收件箱 ----------

$("addTaskBtn").onclick = () => { $("newTask").hidden = false; $("newTask").title.focus(); };
$("cancelTask").onclick = () => ($("newTask").hidden = true);
$("newTask").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    await api("/api/task", {
      title: f.title.value.trim(), project: f.project.value.trim() || null, done_when: f.done_when.value.trim(),
      estimate_min: f.estimate_min.value ? Number(f.estimate_min.value) : null, plan: f.plan.checked && !!S.day,
    });
    f.reset(); f.hidden = true; refresh();
  } catch (err) { alert(err.message); }
};
$("inboxForm").onsubmit = async (e) => {
  e.preventDefault();
  const v = $("inboxInput").value.trim();
  if (!v) return;
  await api("/api/inbox", { text: v });
  $("inboxInput").value = ""; refresh();
};

// ---------- 对话 ----------

function md(src) {
  const blocks = esc(src).split(/```/);
  return blocks.map((b, i) => {
    if (i % 2) return `<pre><code>${b.replace(/^\w*\n/, "")}</code></pre>`;
    const inline = (s) => s.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    const out = []; let list = null;
    let table = null;
    const flushTable = () => {
      if (!table) return;
      const [hd, ...rows] = table;
      out.push(`<table><thead><tr>${hd.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>`
        + rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("") + "</tbody></table>");
      table = null;
    };
    for (const line of b.split("\n")) {
      if (/^\s*\|.*\|\s*$/.test(line)) {
        if (list) { out.push(`</${list}>`); list = null; }
        if (/^\s*\|[\s:|-]+\|\s*$/.test(line)) continue; // 表头分隔行
        (table ||= []).push(line.trim().slice(1, -1).split("|").map((c) => c.trim()));
        continue;
      }
      flushTable();
      const m = line.match(/^\s*(?:([-*])|(\d+)\.)\s+(.*)/);
      if (m) {
        const tag = m[1] ? "ul" : "ol";
        if (list !== tag) { if (list) out.push(`</${list}>`); out.push(`<${tag}>`); list = tag; }
        out.push(`<li>${inline(m[3])}</li>`); continue;
      }
      if (list) { out.push(`</${list}>`); list = null; }
      const h = line.match(/^#{1,4}\s+(.*)/);
      if (/^#\s/.test(line) && out.length === 0) continue; // 档案的一级标题已在对话框标题里
      if (h) out.push(`<h4>${inline(h[1])}</h4>`);
      else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
    }
    flushTable();
    if (list) out.push(`</${list}>`);
    return out.join("");
  }).join("");
}

function msgEl(m) {
  const el = document.createElement("div");
  el.className = `msg ${m.role}`;
  if (m.role === "assistant") {
    el.innerHTML = md(m.content);
    const calls = m.meta ? JSON.parse(m.meta) : m.tools || [];
    if (calls.length) {
      const names = [];
      for (const c of calls) { const n = TOOL_LABELS[c.name] || c.name; if (!names.some((x) => x.n === n)) names.push({ n, err: c.error }); }
      el.insertAdjacentHTML("beforeend", `<div class="tools">${names.map((x) => `<span class="${x.err ? "err" : ""}">${esc(x.n)}</span>`).join("")}</div>`);
    }
  } else {
    el.textContent = m.content;
  }
  return el;
}

async function loadMessages() {
  const msgs = await api("/api/messages");
  const log = $("log"); log.innerHTML = "";
  if (!msgs.length) {
    log.innerHTML = `<div class="welcome">点“开工”，或者直接说你现在在做什么。切换、想法、收工都可以在这里随口说。</div>`;
    return;
  }
  msgs.forEach((m) => log.append(msgEl(m)));
  log.scrollTop = log.scrollHeight;
}

async function send(text) {
  text = text.trim();
  if (!text || busy) return;
  busy = true; $("send").disabled = true;
  const log = $("log");
  log.querySelector(".welcome")?.remove();
  log.append(msgEl({ role: "user", content: text }));
  const pending = document.createElement("div");
  pending.className = "msg pending"; pending.textContent = "助手在处理";
  log.append(pending); log.scrollTop = log.scrollHeight;
  try {
    const r = await api("/api/chat", { text, model: $("model").value });
    pending.replaceWith(msgEl({ role: "assistant", content: r.reply, tools: r.tools }));
  } catch (e) {
    pending.replaceWith(msgEl({ role: "assistant", content: `出错了：${e.message}` }));
  }
  log.scrollTop = log.scrollHeight;
  busy = false; $("send").disabled = false;
  refresh();
}

$("composer").onsubmit = (e) => { e.preventDefault(); const v = $("input").value; $("input").value = ""; send(v); };
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
});
$("startBtn").onclick = () => send("开工");
$("endBtn").onclick = () => send("收工");
$("model").onchange = () => localStorage.setItem("model", $("model").value);
$("refreshServers").onclick = renderServers;

// ---------- 启动 ----------

refresh().then(loadMessages);
renderServers();
setInterval(tickTimer, 1000);
setInterval(() => { if (!busy) refresh(); }, 30000);
// 有档案在后台更新时，更频繁地刷新进度
setInterval(() => { if (!busy && S?.projects.some((p) => p.job?.state === "running")) refresh(); }, 4000);
setInterval(renderServers, 60000);
