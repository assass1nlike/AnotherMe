"use strict";

const $ = (id) => document.getElementById(id);
let api = null;
let items = [];             // [{id, text}]
const pending = new Map();  // id -> 计时器：停手 2 秒或离开这一行时才记一次修改，避免历史里全是半句话
let dragId = null;

function grow(t) { t.style.height = "auto"; t.style.height = t.scrollHeight + "px"; }
// 窗口该有的高度：各部分的自然高度之和。列表用 scrollHeight，这样窗口被限高、列表出现滚动条时也算得对
function fit() {
  if (!api) return;
  const p = $("panel");
  const h = [...p.children].reduce((a, el) => a + (el.id === "list" ? el.scrollHeight : el.offsetHeight), 0);
  api.fit(Math.ceil(h) + 2);
}

function render(focusId, caretEnd = true) {
  // 重画前先把各行还没记下的修改记下，否则正在改的那行会显示回旧内容
  document.querySelectorAll("#list li").forEach((li) => {
    const it = items.find((x) => x.id === li.dataset.id);
    if (it) commit(it, li.querySelector("textarea"));
  });
  const ul = $("list"); ul.innerHTML = "";
  for (const it of items) {
    const li = document.createElement("li");
    li.dataset.id = it.id;
    li.innerHTML = `<span class="grip" draggable="true" title="拖动排序（Alt+↑/↓）">⋮⋮</span>
      <div class="text"><textarea rows="1" spellcheck="false"></textarea></div>
      <button class="del" title="删除" aria-label="删除">×</button>`;
    const ta = li.querySelector("textarea");
    ta.value = it.text;
    wire(li, ta, it);
    ul.append(li);
  }
  $("count").textContent = items.length ? `${items.length} 条` : "";
  requestAnimationFrame(() => {
    ul.querySelectorAll("textarea").forEach(grow);
    if (focusId) {
      const ta = ul.querySelector(`li[data-id="${focusId}"] textarea`);
      if (ta) { ta.focus(); const p = caretEnd ? ta.value.length : 0; ta.setSelectionRange(p, p); }
    }
    fit();
  });
}

function commit(it, ta) {
  clearTimeout(pending.get(it.id)); pending.delete(it.id);
  const text = ta.value.replace(/\n/g, " ").trim();
  if (text === it.text) return;
  it.text = text;
  api.edit(it.id, text);
}

async function remove(it, focusPrev) {
  clearTimeout(pending.get(it.id)); pending.delete(it.id);
  const i = items.indexOf(it);
  items.splice(i, 1);
  await api.delete(it.id);
  const prev = items[Math.max(0, i - 1)];
  render(focusPrev && prev ? prev.id : null);
}

async function moveBy(it, k) {
  const i = items.indexOf(it), j = i + k;
  if (j < 0 || j >= items.length) return;
  items.splice(j, 0, items.splice(i, 1)[0]);
  await api.move(it.id, j);
  render(it.id);
}

function wire(li, ta, it) {
  ta.addEventListener("input", () => {
    grow(ta); fit();
    clearTimeout(pending.get(it.id));
    pending.set(it.id, setTimeout(() => commit(it, ta), 2000));
  });
  ta.addEventListener("blur", () => commit(it, ta));
  ta.addEventListener("keydown", async (e) => {
    const i = items.indexOf(it);
    if (e.key === "Enter" && !e.isComposing) {
      e.preventDefault(); commit(it, ta);
      const n = await api.add("", i + 1);
      items.splice(i + 1, 0, n); render(n.id);
    } else if (e.key === "Backspace" && ta.value === "" ) {
      e.preventDefault(); remove(it, true);
    } else if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      e.preventDefault(); commit(it, ta); moveBy(it, e.key === "ArrowUp" ? -1 : 1);
    } else if (e.key === "ArrowUp" && ta.selectionStart === 0 && i > 0) {
      e.preventDefault(); focusAt(i - 1, true);
    } else if (e.key === "ArrowDown" && ta.selectionEnd === ta.value.length) {
      e.preventDefault(); i < items.length - 1 ? focusAt(i + 1, false) : $("newItem").focus();
    }
  });
  li.querySelector(".del").onclick = () => remove(it, false);

  const grip = li.querySelector(".grip");
  grip.addEventListener("dragstart", (e) => { dragId = it.id; li.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
  grip.addEventListener("dragend", () => { dragId = null; document.querySelectorAll("#list li").forEach((x) => x.classList.remove("dragging", "drop-before", "drop-after")); });
  li.addEventListener("dragover", (e) => {
    if (!dragId || dragId === it.id) return;
    e.preventDefault();
    const after = e.offsetY > li.offsetHeight / 2;
    li.classList.toggle("drop-after", after); li.classList.toggle("drop-before", !after);
  });
  li.addEventListener("dragleave", () => li.classList.remove("drop-before", "drop-after"));
  li.addEventListener("drop", async (e) => {
    e.preventDefault();
    const after = li.classList.contains("drop-after");
    const from = items.findIndex((x) => x.id === dragId);
    const moved = items.splice(from, 1)[0];
    let to = items.indexOf(it) + (after ? 1 : 0);
    items.splice(to, 0, moved);
    await api.move(moved.id, to);
    render();
  });
}

function focusAt(i, end) {
  const ta = document.querySelectorAll("#list textarea")[i];
  if (!ta) return;
  ta.focus(); const p = end ? ta.value.length : 0; ta.setSelectionRange(p, p);
}

// 页面从后台被唤出时调用：光标放到“写一条”里，方便直接记
window.focusLast = () => $("newItem").focus();

const nw = $("newItem");
nw.addEventListener("input", () => { grow(nw); fit(); });
nw.addEventListener("keydown", async (e) => {
  if (e.key === "Enter" && !e.isComposing) {
    e.preventDefault();
    const text = nw.value.replace(/\n/g, " ").trim();
    if (!text) return;
    const n = await api.add(text, null);
    items.push(n); nw.value = ""; grow(nw); render(); nw.focus();
  } else if (e.key === "ArrowUp" && nw.selectionStart === 0 && items.length) {
    e.preventDefault(); focusAt(items.length - 1, true);
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { document.activeElement?.blur(); api.collapse(); }
});
$("fold").onclick = () => { document.activeElement?.blur(); api.collapse(); };

window.addEventListener("pywebviewready", async () => {
  api = window.pywebview.api;
  const r = await api.load();
  items = r.items;
  const hk = r.hotkey.split("+").map((k) => k[0].toUpperCase() + k.slice(1)).join("+");
  $("hint").textContent = `${hk} 或鼠标移到这个角落停一下，就能收起和展开`;
  render();
});
