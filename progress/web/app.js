"use strict";

const $ = (id) => document.getElementById(id);
const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const UNKNOWN = "?"; // 在别处被改、还没标日期的字

const S = {
  docs: [], today: "", pen: "",
  cur: null,          // {name, text, dates[], mtime}
  dirty: false, saving: false, saveTimer: null,
  undo: [], redo: [], lastPush: 0,
  jumpAt: {},         // 日期 -> 上次跳到第几段
  before: null,       // beforeinput 时的选区
};

// ---------- 日期与颜色 ----------

const dayNum = (d) => Math.round(Date.parse(d + "T00:00:00Z") / 86400000);
// 同一天永远是同一种墨色。12 个色相按“乘 7 取模”分给日期，相邻几天的色相隔得开；奇偶日再错开亮度，
// 色相接近的两天（如相隔 5 天）亮度也不同
function ink(d) {
  if (!d) return "var(--old)";
  if (d === UNKNOWN) return "var(--warn)";
  const n = dayNum(d);
  const h = (((n * 7) % 12) + 12) % 12 * 30 + 20;
  const l = n % 2 ? 0.84 : 0.74;
  return `oklch(${l} 0.12 ${h})`;
}
function label(d, withWeek = true) {
  if (!d) return "开始记录前就有";
  if (d === UNKNOWN) return "未知日期";
  const [y, m, dd] = d.split("-").map(Number);
  const w = WEEK[new Date(y, m - 1, dd).getDay()];
  const yr = y === Number(S.today.slice(0, 4)) ? "" : `${y} 年 `;
  return `${yr}${m} 月 ${dd} 日${withWeek ? " " + w : ""}`;
}
const shift = (d, k) => new Date(Date.parse(d + "T00:00:00Z") + k * 86400000).toISOString().slice(0, 10);
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const explode = (runs) => runs.flatMap(([n, d]) => Array(n).fill(d ?? null));
function compress(dates) {
  const runs = [];
  for (const d of dates) {
    const last = runs[runs.length - 1];
    if (last && last[1] === d) last[0]++; else runs.push([1, d]);
  }
  return runs;
}

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `请求失败（${r.status}）`);
  return data;
}

// ---------- 笔 ----------

function setPen(d) {
  if (!/^\d{4}-\d\d-\d\d$/.test(d)) return;
  S.pen = d;
  document.documentElement.style.setProperty("--pen", ink(d));
  $("penText").textContent = label(d);
  $("penInput").value = d;
  $("selMark").innerHTML = `标为 ${label(d, false)}<kbd>Ctrl D</kbd>`;
  $("menuMark").innerHTML = `把选中的文字标为 ${label(d, false)}<kbd>Ctrl D</kbd>`;
  renderDays();
}
$("prev").onclick = () => setPen(shift(S.pen, -1));
$("next").onclick = () => setPen(shift(S.pen, 1));
$("today").onclick = () => setPen(S.today);
$("penInput").onchange = (e) => e.target.value && setPen(e.target.value);

// ---------- 项目列表 ----------

async function loadDocs() {
  const r = await api("/api/docs");
  S.docs = r.docs; S.today = r.today;
  $("root").textContent = r.root;
  renderDocs();
}

function renderDocs() {
  const ul = $("docs"); ul.innerHTML = "";
  for (const d of S.docs) {
    const counts = S.cur?.name === d.name ? countDates(S.cur.dates) : d.counts;
    const dated = Object.entries(counts).filter(([k]) => k && k !== UNKNOWN).sort(([a], [b]) => a.localeCompare(b));
    const total = dated.reduce((a, [, n]) => a + n, 0) || 1; // 色带只看带日期的部分，各天占多少
    const spectrum = dated.map(([k, n]) => `<span style="width:${(n / total) * 100}%;background:${ink(k)}"></span>`).join("");
    const chars = Object.values(counts).reduce((a, b) => a + b, 0);
    const latest = dated.length ? `最近写于 ${label(dated[dated.length - 1][0], false)}` : (chars ? "还没有带日期的内容" : "空文件");
    const li = document.createElement("li");
    li.innerHTML = `<button aria-current="${S.cur?.name === d.name}"><span class="name">${esc(d.name)}</span>
      <span class="spectrum">${spectrum}</span><span class="meta">${latest}</span></button>`;
    li.firstChild.onclick = () => openDoc(d.name);
    ul.append(li);
  }
}

function countDates(dates) {
  const c = {};
  for (const d of dates) c[d ?? ""] = (c[d ?? ""] || 0) + 1;
  return c;
}

// ---------- 打开与保存 ----------

async function openDoc(name, keepView = false) {
  if (S.cur && S.cur.name !== name) await flush();
  const r = await api(`/api/doc?name=${encodeURIComponent(name)}`);
  const view = keepView ? { top: $("sheet").scrollTop, a: $("editor").selectionStart, b: $("editor").selectionEnd } : null;
  S.cur = { name, text: r.text, dates: explode(r.runs), mtime: r.mtime };
  S.undo = []; S.redo = []; S.jumpAt = {}; S.dirty = false;
  localStorage.setItem("doc", name);
  $("title").textContent = name;
  document.title = `${name} · 日期进度`;
  $("editor").value = r.text;
  render();
  renderDocs();
  if (view) {
    $("sheet").scrollTop = view.top;
    $("editor").setSelectionRange(Math.min(view.a, r.text.length), Math.min(view.b, r.text.length));
  } else {
    $("sheet").scrollTop = 0;
    $("editor").setSelectionRange(0, 0);
  }
  showUnknownNotice();
  status(r.text.length ? "" : "空文件，直接开始写");
}

function scheduleSave() {
  S.dirty = true;
  status("未保存");
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(flush, 1200);
}

async function flush(force = false) {
  clearTimeout(S.saveTimer);
  if (!S.cur || !S.dirty || S.saving) return;
  S.saving = true;
  status("保存中");
  try {
    const r = await api("/api/save", { name: S.cur.name, text: S.cur.text, runs: compress(S.cur.dates), mtime: S.cur.mtime, force });
    if (r.conflict) {
      showConflict();
      status("没有保存", true);
    } else {
      S.cur.mtime = r.mtime; S.dirty = false;
      const t = new Date();
      status(`已保存 ${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`);
    }
  } catch (e) {
    status(`保存失败：${e.message}`, true);
  } finally {
    S.saving = false;
  }
}

function status(text, error = false) {
  $("saved").textContent = text;
  $("saved").classList.toggle("error", error);
}

// 别处改了文件：没改动时直接重新读入，有改动时等保存时再处理冲突
setInterval(async () => {
  if (!S.cur || document.hidden) return;
  try {
    const m = await api("/api/mtimes");
    const now = m[S.cur.name];
    if (now && Math.abs(now - S.cur.mtime) > 1e-6 && !S.dirty && !S.saving) openDoc(S.cur.name, true);
  } catch { status("程序已经退出，重新打开 progress.bat", true); }
}, 3000);
window.addEventListener("beforeunload", () => { if (S.dirty) flush(); });

// ---------- 提示条 ----------

function notice(html, wire) {
  $("notice").innerHTML = html; $("notice").hidden = false; wire?.();
}
const hideNotice = () => ($("notice").hidden = true);

function showUnknownNotice() {
  const n = S.cur.dates.filter((d) => d === UNKNOWN).length;
  if (!n) return hideNotice();
  notice(`<span>这份文件在别处改过，有 ${n} 个字不知道是哪天写的，记为</span>
    <input type="date" id="extDate" value="${S.pen}"><button id="extOk">确定</button>
    <button class="quiet" id="extNone">不标日期</button>`, () => {
    const assign = (d) => {
      S.cur.dates = S.cur.dates.map((x) => (x === UNKNOWN ? d : x));
      hideNotice(); render(); scheduleSave();
    };
    $("extOk").onclick = () => $("extDate").value && assign($("extDate").value);
    $("extNone").onclick = () => assign(null);
  });
}

function showConflict() {
  notice(`<span>这份文件在别处也被改了，两边的改动不能同时保留。</span>
    <button id="cfKeep">保留这里的，覆盖别处的</button>
    <button class="quiet" id="cfLoad">读入别处的版本，放弃这里没保存的修改</button>`, () => {
    $("cfKeep").onclick = () => { hideNotice(); S.dirty = true; flush(true); };
    $("cfLoad").onclick = () => { hideNotice(); S.dirty = false; openDoc(S.cur.name, true); };
  });
}

// ---------- 着色层 ----------

function render() {
  const { text, dates } = S.cur;
  // 每个字的样式：日期 + 是否在标题行 + 是否是标题前的 # 号
  const kind = new Uint8Array(text.length);
  let i = 0;
  while (i < text.length) {
    let j = text.indexOf("\n", i); if (j < 0) j = text.length;
    if (text[i] === "#") {
      kind.fill(1, i, j);
      let k = i; while (k < j && (text[k] === "#" || text[k] === " ")) k++;
      kind.fill(2, i, k);
    }
    i = j + 1;
  }
  let html = "", s = 0;
  for (let p = 1; p <= text.length; p++) {
    if (p < text.length && dates[p] === dates[s] && kind[p] === kind[s]) continue;
    const d = dates[s];
    const cls = kind[s] === 2 ? "h hm" : kind[s] === 1 ? "h" : "";
    const style = d ? ` style="color:${ink(d)}"` : "";
    html += `<span data-s="${s}" data-d="${d ?? ""}"${cls ? ` class="${cls}"` : ""}${style}>${esc(text.slice(s, p))}</span>`;
    s = p;
  }
  $("ink").innerHTML = html + "\n ";
  renderDays();
  scheduleMap();
}

// ---------- 编辑 ----------

const ed = $("editor");

function snapshot() {
  return { text: S.cur.text, dates: S.cur.dates.slice(), a: ed.selectionStart, b: ed.selectionEnd };
}
function pushUndo(coalesce) {
  const now = Date.now();
  if (coalesce && S.undo.length && now - S.lastPush < 1000) { S.lastPush = now; return; }
  S.lastPush = coalesce ? now : 0;
  S.undo.push(snapshot());
  if (S.undo.length > 300) S.undo.shift();
  S.redo = [];
}
function restore(from, to) {
  if (!S.cur || !from.length) return;
  to.push(snapshot());
  const s = from.pop();
  S.cur.text = s.text; S.cur.dates = s.dates;
  ed.value = s.text; ed.setSelectionRange(s.a, s.b);
  S.lastPush = 0;
  render(); scheduleSave(); renderDocs();
}

ed.addEventListener("beforeinput", (e) => {
  if (!S.cur) return e.preventDefault();
  if (e.inputType === "historyUndo") { e.preventDefault(); return restore(S.undo, S.redo); }
  if (e.inputType === "historyRedo") { e.preventDefault(); return restore(S.redo, S.undo); }
  S.before = { a: ed.selectionStart, b: ed.selectionEnd };
  pushUndo(e.inputType === "insertText" || e.inputType === "insertCompositionText" || e.inputType === "deleteContentBackward");
});

ed.addEventListener("input", () => {
  if (!S.cur) return;
  const old = S.cur.text, oldDates = S.cur.dates, neu = ed.value;
  // 新写的字的位置：优先用编辑前后的选区，对不上时退回到公共前后缀
  let p, s;
  const a = S.before?.a ?? 0, b = S.before?.b ?? 0, c = ed.selectionEnd;
  if (S.before && neu.slice(0, a) === old.slice(0, a) && c >= a && neu.slice(c) === old.slice(b)) {
    p = a; s = old.length - b;
  } else {
    p = 0; const m = Math.min(old.length, neu.length);
    while (p < m && old[p] === neu[p]) p++;
    s = 0; while (s < m - p && old[old.length - 1 - s] === neu[neu.length - 1 - s]) s++;
  }
  S.cur.text = neu;
  S.cur.dates = [...oldDates.slice(0, p), ...Array(neu.length - p - s).fill(S.pen), ...oldDates.slice(old.length - s)];
  S.before = null;
  render(); scheduleSave();
  clearTimeout(S.docsTimer); S.docsTimer = setTimeout(renderDocs, 800);
});

function markSelection(d) {
  const a = ed.selectionStart, b = ed.selectionEnd;
  if (!S.cur || a === b) return;
  pushUndo(false);
  S.cur.dates.fill(d, a, b);
  render(); scheduleSave(); renderDocs(); updateSelbar();
  flashRange(a);
}

ed.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey)) return;
  const k = e.key.toLowerCase();
  if (k === "z" && !e.shiftKey) { e.preventDefault(); restore(S.undo, S.redo); }
  else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); restore(S.redo, S.undo); }
  else if (k === "d") { e.preventDefault(); markSelection(S.pen); }
  else if (k === "s") { e.preventDefault(); flush(); }
});

// ---------- 选区操作条与右键菜单 ----------

function updateSelbar() {
  const n = ed.selectionEnd - ed.selectionStart;
  const show = document.activeElement === ed && n > 0;
  $("selbar").hidden = !show;
  if (show) $("selInfo").textContent = `已选 ${n} 字`;
}
document.addEventListener("selectionchange", updateSelbar);
ed.addEventListener("blur", () => setTimeout(updateSelbar, 150));
$("selbar").addEventListener("mousedown", (e) => e.preventDefault()); // 点按钮时不让输入框失焦
$("selMark").onclick = () => markSelection(S.pen);
$("selClear").onclick = () => markSelection(null);

ed.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  const has = ed.selectionEnd > ed.selectionStart;
  $("menuMark").disabled = !has; $("menuClear").disabled = !has;
  const m = $("menu"); m.hidden = false;
  m.style.left = Math.min(e.clientX, innerWidth - m.offsetWidth - 8) + "px";
  m.style.top = Math.min(e.clientY, innerHeight - m.offsetHeight - 8) + "px";
});
$("menu").addEventListener("mousedown", (e) => e.preventDefault());
$("menuMark").onclick = () => { $("menu").hidden = true; markSelection(S.pen); };
$("menuClear").onclick = () => { $("menu").hidden = true; markSelection(null); };
document.addEventListener("mousedown", (e) => { if (!$("menu").contains(e.target)) $("menu").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("menu").hidden = true; });

// ---------- 悬停：这里是哪天写的 ----------

let tipFrame = 0;
ed.addEventListener("mousemove", (e) => {
  cancelAnimationFrame(tipFrame);
  tipFrame = requestAnimationFrame(() => {
    const span = document.elementsFromPoint(e.clientX, e.clientY).find((el) => el.parentElement === $("ink"));
    const tip = $("tip");
    if (!span || !span.textContent.trim()) { tip.hidden = true; return; }
    const d = span.dataset.d || null;
    tip.style.setProperty("--c", ink(d));
    tip.innerHTML = d && d !== UNKNOWN ? `写于 <b>${label(d)}</b>` : `<b>${label(d)}</b>`;
    tip.hidden = false;
    tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8) + "px";
    tip.style.top = e.clientY + 18 + "px";
  });
});
ed.addEventListener("mouseleave", () => ($("tip").hidden = true));
ed.addEventListener("keydown", () => ($("tip").hidden = true));

// ---------- 右栏：各日期的墨迹 ----------

function renderDays() {
  if (!S.cur) return;
  const counts = countDates(S.cur.dates);
  if (!(S.pen in counts)) counts[S.pen] = 0;
  const dated = Object.keys(counts).filter((k) => k && k !== UNKNOWN).sort().reverse();
  const order = [...dated, ...(counts[UNKNOWN] ? [UNKNOWN] : []), ...(counts[""] ? [""] : [])];
  const max = Math.max(1, ...dated.map((k) => counts[k]));
  const ul = $("days"); ul.innerHTML = "";
  for (const k of order) {
    const d = k || null;
    const li = document.createElement("li");
    if (k === S.pen) li.className = "current";
    if (!k) li.className = "undated";
    const w = Math.min(100, (counts[k] / max) * 100);
    li.innerHTML = `<button style="--c:${ink(d)}"><span class="d">${label(d)}</span><span class="n">${counts[k]} 字</span>
      <span class="bar"><span style="width:${w}%"></span></span></button>`;
    const btn = li.firstChild;
    let clickTimer;
    btn.onclick = () => { clearTimeout(clickTimer); clickTimer = setTimeout(() => jump(d), 220); };
    if (d && d !== UNKNOWN) btn.ondblclick = () => { clearTimeout(clickTimer); setPen(d); };
    ul.append(li);
  }
}

function runsOf(d) {
  const out = [], dates = S.cur.dates;
  for (let i = 0; i < dates.length;) {
    let j = i; while (j < dates.length && dates[j] === dates[i]) j++;
    if (dates[i] === d) out.push([i, j]);
    i = j;
  }
  return out.filter(([a, b]) => S.cur.text.slice(a, b).trim());
}

function jump(d) {
  const runs = runsOf(d);
  if (!runs.length) return;
  const key = d ?? "";
  const k = ((S.jumpAt[key] ?? -1) + 1) % runs.length;
  S.jumpAt[key] = k;
  const [a, b] = runs[k];
  ed.focus({ preventScroll: true });
  ed.setSelectionRange(a, b);
  flashRange(a, true);
}

function spanAt(i) {
  let best = null;
  for (const el of $("ink").children) { if (Number(el.dataset.s) <= i) best = el; else break; }
  return best;
}
function flashRange(a, scroll = false) {
  const el = spanAt(a);
  if (!el) return;
  if (scroll) {
    const sheet = $("sheet"), r = el.getBoundingClientRect(), box = sheet.getBoundingClientRect();
    sheet.scrollTo({ top: sheet.scrollTop + r.top - box.top - box.height / 3, behavior: "smooth" });
  }
  el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
}

// ---------- 墨迹图 ----------

let mapTimer;
function scheduleMap() { clearTimeout(mapTimer); mapTimer = setTimeout(renderMap, 250); }
function renderMap() {
  const map = $("map"), inkEl = $("ink");
  map.querySelectorAll("i").forEach((x) => x.remove());
  const total = $("page").scrollHeight, H = map.clientHeight, top0 = inkEl.getBoundingClientRect().top - $("page").getBoundingClientRect().top;
  if (!total || !H) return;
  const base = inkEl.getBoundingClientRect().top;
  const frag = document.createDocumentFragment();
  for (const el of inkEl.children) {
    const d = el.dataset.d;
    if (!d || !el.textContent.trim()) continue;
    for (const r of el.getClientRects()) {
      const y = ((r.top - base + top0) / total) * H, h = Math.max(2, (r.height / total) * H);
      const m = document.createElement("i");
      m.style.cssText = `top:${y}px;height:${h}px;background:${ink(d)}`;
      frag.append(m);
    }
  }
  map.append(frag);
  renderView();
}
function renderView() {
  const sheet = $("sheet"), H = $("map").clientHeight, total = $("page").scrollHeight || 1;
  $("view").style.top = (sheet.scrollTop / total) * H + "px";
  $("view").style.height = Math.min(H, (sheet.clientHeight / total) * H) + "px";
}
$("sheet").addEventListener("scroll", renderView, { passive: true });
window.addEventListener("resize", scheduleMap);
$("map").addEventListener("click", (e) => {
  const r = $("map").getBoundingClientRect(), sheet = $("sheet");
  sheet.scrollTo({ top: ((e.clientY - r.top) / r.height) * $("page").scrollHeight - sheet.clientHeight / 2, behavior: "smooth" });
});

// ---------- 启动 ----------

(async () => {
  try {
    await loadDocs();
  } catch (e) {
    $("title").textContent = "读不到研究目录";
    notice(`<span>${esc(e.message)}</span>`);
    return;
  }
  setPen(S.today);
  const last = localStorage.getItem("doc");
  const first = S.docs.find((d) => d.name === last) || S.docs[0];
  if (first) openDoc(first.name);
})();
