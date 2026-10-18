"use strict";

const TOKEN_KEY = "fz3z_auth_token";
let AUTH_TOKEN = localStorage.getItem(TOKEN_KEY) || "";

async function api(method, url, body) {
  const headers = {};
  if (AUTH_TOKEN) headers["X-Auth-Token"] = AUTH_TOKEN;
  const opt = { method, headers };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(url, opt);
  if (res.status === 401 && url !== "/api/login") {
    logout();
    throw new Error("请先登录");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const msg = (data && data.error) || ("请求失败 (" + res.status + ")");
    throw new Error(msg);
  }
  return data;
}

// 登录界面 
function showLogin(show) {
  const el = document.getElementById("login-screen");
  if (el) el.hidden = !show;
}
function logout() {
  AUTH_TOKEN = "";
  localStorage.removeItem(TOKEN_KEY);
  showLogin(true);
}
async function doLogin() {
  const pwd = document.getElementById("login-pwd").value;
  const errEl = document.getElementById("login-err");
  errEl.textContent = "";
  try {
    const r = await api("POST", "/api/login", { password: pwd });
    AUTH_TOKEN = r.token;
    localStorage.setItem(TOKEN_KEY, AUTH_TOKEN);
    showLogin(false);
    loadDashboard();
  } catch (e) {
    errEl.textContent = e.message || "登录失败";
  }
}
document.getElementById("login-btn").addEventListener("click", doLogin);
document.getElementById("login-pwd").addEventListener("keydown", (e) => {
  if (e.key === "Enter") doLogin();
});
document.getElementById("btn-logout").addEventListener("click", logout);

let MEMBERS = [];   // 成员列表缓存
let TASKS = [];     // 任务列表缓存

let toastTimer = null;
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2200);
}

document.getElementById("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab");
  if (!btn) return;
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  btn.classList.add("active");
  document.getElementById("view-" + btn.dataset.view).classList.add("active");
  const view = btn.dataset.view;
  if (view === "dashboard") loadDashboard();
  if (view === "tasks") loadTasks();
  if (view === "members") loadMembers();
  if (view === "hours") loadHoursView();
});


async function loadDashboard() {
  const stats = await api("GET", "/api/stats");
  const t = stats.totals;
  const cards = [
    { label: "任务总数", value: t.task_count, cls: "ink" },
    { label: "登记志愿时长 (h)", value: t.recorded_hours, cls: "" },
    { label: "成员时长合计 (h)", value: t.allocated_total, cls: "green" },
    { label: "成员总数", value: t.member_count, cls: "amber" },
  ];
  document.getElementById("stat-cards").innerHTML = cards.map(c =>
    `<div class="stat-card ${c.cls}"><div class="label">${c.label}</div><div class="value">${c.value}</div></div>`
  ).join("");

  // 各成员时长柱状图
  renderBarChart(
    document.getElementById("chart-member-hours"),
    stats.per_member.map(m => ({ label: m.name, value: m.total })),
    { unit: "h" }
  );

  // 每月趋势 折线图
  renderLineChart(
    document.getElementById("chart-timeline"),
    stats.timeline.map(p => ({ label: p.month, value: p.hours }))
  );

  // 最近任务
  const recent = document.getElementById("recent-tasks");
  if (!stats.recent.length) {
    recent.innerHTML = `<p class="empty-hint">暂无任务。</p>`;
  } else {
    recent.innerHTML = stats.recent.map(r => `
      <div class="recent-item">
        <div>
          <div class="rt-title">${esc(r.title)}</div>
          <div class="rt-meta">${esc(r.task_date || "—")} · ${r.participant_count} 人参与 · ${r.total_hours}h</div>
        </div>
        <span class="tag ${statusClass(r.status)}">${esc(r.status)}</span>
      </div>`).join("");
  }
}


async function loadMembersCache() {
  const data = await api("GET", "/api/members");
  MEMBERS = data.members;
}

async function loadTasks() {
  await loadMembersCache();
  // 填充成员筛选下拉
  const sel = document.getElementById("task-filter-member");
  const cur = sel.value;
  sel.innerHTML = `<option value="">全部成员</option>` +
    MEMBERS.map(m => `<option value="${m.id}">${esc(m.name)}</option>`).join("");
  sel.value = cur;

  const data = await api("GET", "/api/tasks");
  TASKS = data.tasks;
  renderTaskTable();
}

function renderTaskTable() {
  const kw = document.getElementById("task-search").value.trim().toLowerCase();
  const fstatus = document.getElementById("task-filter-status").value;
  const fmember = document.getElementById("task-filter-member").value;

  let rows = TASKS.filter(t => {
    if (fstatus && t.status !== fstatus) return false;
    if (fmember && !t.participants.some(p => String(p.id) === fmember)) return false;
    if (kw && !(t.title.toLowerCase().includes(kw) || (t.content || "").toLowerCase().includes(kw))) return false;
    return true;
  });

  const tb = document.getElementById("task-tbody");
  document.getElementById("task-empty").hidden = rows.length > 0;
  tb.innerHTML = rows.map(t => `
    <tr>
      <td class="wrap"><strong>${esc(t.title)}</strong></td>
      <td>${esc(t.task_date || "—")}</td>
      <td>${t.participant_count}</td>
      <td>${t.total_hours}</td>
      <td>${t.rule === "full" ? "全员计满" : "平均分配"}</td>
      <td><span class="tag ${statusClass(t.status)}">${esc(t.status)}</span></td>
      <td>
        <button class="btn small" data-act="view" data-id="${t.id}">详情</button>
        <button class="btn small" data-act="edit" data-id="${t.id}">编辑</button>
        <button class="btn small danger" data-act="del" data-id="${t.id}">删除</button>
      </td>
    </tr>`).join("");
}

document.getElementById("task-search").addEventListener("input", renderTaskTable);
document.getElementById("task-filter-status").addEventListener("change", renderTaskTable);
document.getElementById("task-filter-member").addEventListener("change", renderTaskTable);

document.getElementById("task-tbody").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  const act = btn.dataset.act;
  if (act === "view") openDetail(id);
  else if (act === "edit") openTaskModal(id);
  else if (act === "del") {
    if (!confirm("确定删除该任务？此操作不可恢复。")) return;
    await api("DELETE", "/api/tasks/" + id);
    toast("已删除");
    loadTasks();
  }
});

document.getElementById("btn-add-task").addEventListener("click", () => openTaskModal(null));

function buildMemberChecklist(selectedIds) {
  const box = document.getElementById("member-checklist");
  if (!MEMBERS.length) {
    box.innerHTML = `<span class="hint">暂无成员，请先到「成员管理」添加。</span>`;
    return;
  }
  box.innerHTML = MEMBERS.map(m => `
    <label><input type="checkbox" value="${m.id}" ${selectedIds.includes(m.id) ? "checked" : ""}/> ${esc(m.name)}</label>
  `).join("");
}

async function openTaskModal(id) {
  await loadMembersCache();
  const isEdit = !!id;
  document.getElementById("task-modal-title").textContent = isEdit ? "编辑任务" : "新增任务";
  const form = document.getElementById("task-form");
  form.reset();
  document.getElementById("f-id").value = id || "";
  buildMemberChecklist([]);
  if (isEdit) {
    const t = await api("GET", "/api/tasks/" + id);
    document.getElementById("f-title").value = t.title;
    document.getElementById("f-date").value = t.task_date || "";
    document.getElementById("f-start").value = t.start_time || "";
    document.getElementById("f-end").value = t.end_time || "";
    document.getElementById("f-hours").value = t.total_hours;
    document.getElementById("f-rule").value = t.rule;
    document.getElementById("f-status").value = t.status;
    document.getElementById("f-location").value = t.location || "";
    document.getElementById("f-content").value = t.content || "";
    buildMemberChecklist(t.participants.map(p => p.id));
  }
  showModal("task-modal");
}

document.getElementById("task-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("f-id").value;
  const member_ids = Array.from(document.querySelectorAll("#member-checklist input:checked")).map(c => Number(c.value));
  const body = {
    title: document.getElementById("f-title").value.trim(),
    task_date: document.getElementById("f-date").value,
    start_time: document.getElementById("f-start").value,
    end_time: document.getElementById("f-end").value,
    total_hours: Number(document.getElementById("f-hours").value) || 0,
    rule: document.getElementById("f-rule").value,
    status: document.getElementById("f-status").value,
    location: document.getElementById("f-location").value.trim(),
    content: document.getElementById("f-content").value.trim(),
    member_ids,
  };
  try {
    if (id) await api("PUT", "/api/tasks/" + id, body);
    else await api("POST", "/api/tasks", body);
    hideModal("task-modal");
    toast(id ? "已更新" : "已添加");
    loadTasks();
  } catch (err) { toast("错误：" + err.message); }
});

async function openDetail(id) {
  const t = await api("GET", "/api/tasks/" + id);
  document.getElementById("detail-title").textContent = t.title;
  const parts = t.participants.length
    ? t.participants.map(p => `<span class="tag doing">${esc(p.name)}</span>`).join("")
    : `<span class="hint">无</span>`;
  document.getElementById("detail-body").innerHTML = `
    <div class="row"><span class="k">任务内容</span><span>${esc(t.content || "—")}</span></div>
    <div class="row"><span class="k">任务日期</span><span>${esc(t.task_date || "—")}</span></div>
    <div class="row"><span class="k">时间</span><span>${esc((t.start_time || "—") + " ~ " + (t.end_time || "—"))}</span></div>
    <div class="row"><span class="k">志愿时长</span><span>${t.total_hours} 小时</span></div>
    <div class="row"><span class="k">分配规则</span><span>${t.rule === "full" ? "全员计满" : "平均分配"}</span></div>
    <div class="row"><span class="k">状态</span><span><span class="tag ${statusClass(t.status)}">${esc(t.status)}</span></span></div>
    <div class="row"><span class="k">地点</span><span>${esc(t.location || "—")}</span></div>
    <div class="row"><span class="k">参与成员</span><span class="chips">${parts}</span></div>
    <div class="row"><span class="k">创建时间</span><span>${esc(t.created_at || "—")}</span></div>
    <div class="row"><span class="k">更新时间</span><span>${esc(t.updated_at || "—")}</span></div>
  `;
  showModal("detail-modal");
}
document.getElementById("detail-close").addEventListener("click", () => hideModal("detail-modal"));
document.getElementById("detail-close2").addEventListener("click", () => hideModal("detail-modal"));

// 成员管理

async function loadMembers() {
  await loadMembersCache();
  const tb = document.getElementById("member-tbody");
  if (!MEMBERS.length) {
    tb.innerHTML = `<tr><td colspan="4" class="empty-hint">暂无成员，先在上方添加。</td></tr>`;
    return;
  }
  tb.innerHTML = MEMBERS.map(m => `
    <tr>
      <td><strong>${esc(m.name)}</strong></td>
      <td>${esc(m.student_id || "—")}</td>
      <td>${esc(m.note || "—")}</td>
      <td>
        <button class="btn small danger" data-del-member="${m.id}">删除</button>
      </td>
    </tr>`).join("");
}

document.getElementById("btn-add-member").addEventListener("click", async () => {
  const name = document.getElementById("member-name").value.trim();
  if (!name) { toast("请填写成员姓名"); return; }
  try {
    await api("POST", "/api/members", {
      name,
      student_id: document.getElementById("member-sid").value.trim(),
      note: document.getElementById("member-note").value.trim(),
    });
    document.getElementById("member-name").value = "";
    document.getElementById("member-sid").value = "";
    document.getElementById("member-note").value = "";
    toast("已添加成员");
    loadMembers();
  } catch (err) { toast("错误：" + err.message); }
});

document.getElementById("member-tbody").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-del-member]");
  if (!btn) return;
  if (!confirm("删除该成员会同步移除其所有任务参与记录，确定？")) return;
  await api("DELETE", "/api/members/" + btn.dataset.delMember);
  toast("已删除成员");
  loadMembers();
});


//  时长统计

async function loadHoursView() {
  await loadMembersCache();
  const stats = await api("GET", "/api/stats");
  const per = stats.per_member;

  // 排行榜下拉
  const sel = document.getElementById("hours-member-select");
  const cur = sel.value;
  sel.innerHTML = `<option value="">— 请选择 —</option>` +
    per.map(m => `<option value="${m.id}">${esc(m.name)}</option>`).join("");
  sel.value = cur;

  // 排行榜表格 + 图表
  const tb = document.getElementById("hours-ranking-tbody");
  if (!per.length) {
    tb.innerHTML = `<tr><td colspan="4" class="empty-hint">暂无数据。</td></tr>`;
  } else {
    tb.innerHTML = per.map((m, i) => `
      <tr>
        <td>${i + 1}</td>
        <td><strong>${esc(m.name)}</strong></td>
        <td>${m.count}</td>
        <td>${m.total}</td>
      </tr>`).join("");
  }
  renderBarChart(document.getElementById("hours-ranking-chart"), per.map(m => ({ label: m.name, value: m.total })), { unit: "h" });
}

document.getElementById("btn-view-hours").addEventListener("click", async () => {
  const mid = document.getElementById("hours-member-select").value;
  if (!mid) { toast("请先选择成员"); return; }
  const info = await api("GET", "/api/members/" + mid + "/hours");
  document.getElementById("hours-detail-title").textContent = info.name + " 的志愿时长明细";
  document.getElementById("hours-detail-total").textContent = "总计：" + info.total + " 小时 · 参与 " + info.count + " 项任务";
  const tb = document.getElementById("hours-detail-tbody");
  if (!info.details.length) {
    tb.innerHTML = `<tr><td colspan="4" class="empty-hint">该成员暂无参与任务。</td></tr>`;
  } else {
    tb.innerHTML = info.details.map(d => `
      <tr>
        <td class="wrap">${esc(d.title)}</td>
        <td>${esc(d.date || "—")}</td>
        <td>${d.rule === "full" ? "全员计满" : "平均分配"}</td>
        <td><strong>${d.hours}</strong></td>
      </tr>`).join("");
  }
  document.getElementById("hours-detail-panel").hidden = false;
});

function statusClass(s) {
  if (s === "已完成") return "done";
  if (s === "进行中") return "doing";
  return "todo";
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function showModal(id) {
  // 先关闭其它弹窗，避免多个弹窗叠加
  document.querySelectorAll(".modal-mask").forEach(m => { m.hidden = true; });
  document.getElementById(id).hidden = false;
}
function hideModal(id) { document.getElementById(id).hidden = true; }

// 按 Esc 关闭当前弹窗
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.querySelectorAll(".modal-mask").forEach(m => { m.hidden = true; });
  }
});
document.getElementById("task-modal-close").addEventListener("click", () => hideModal("task-modal"));
document.getElementById("task-modal-cancel").addEventListener("click", () => hideModal("task-modal"));
document.querySelectorAll(".modal-mask").forEach(m => m.addEventListener("click", (e) => {
  if (e.target === m) m.hidden = true;
}));

// 修改口令

document.getElementById("btn-pwd").addEventListener("click", () => showModal("pwd-modal"));
document.getElementById("pwd-modal-close").addEventListener("click", () => hideModal("pwd-modal"));
document.getElementById("pwd-modal-cancel").addEventListener("click", () => hideModal("pwd-modal"));
document.getElementById("pwd-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const oldPwd = document.getElementById("pwd-old").value;
  const newPwd = document.getElementById("pwd-new").value;
  const confirmPwd = document.getElementById("pwd-confirm").value;
  if (newPwd.length < 6) { toast("新口令至少 6 位"); return; }
  if (newPwd !== confirmPwd) { toast("两次输入的新口令不一致"); return; }
  if (newPwd === oldPwd) { toast("新口令不能与当前口令相同"); return; }
  try {
    await api("POST", "/api/password", { old: oldPwd, new: newPwd });
    toast("口令已修改，请牢记新口令");
    hideModal("pwd-modal");
    document.getElementById("pwd-form").reset();
  } catch (err) { toast("错误：" + err.message); }
});


function renderBarChart(container, items, opts = {}) {
  container.innerHTML = "";
  if (!items.length || items.every(i => i.value <= 0)) {
    container.innerHTML = `<p class="empty-hint">暂无数据</p>`;
    return;
  }
  const W = Math.max(320, Math.min(680, items.length * 70));
  const H = 240, padL = 40, padB = 46, padT = 16, padR = 12;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const maxV = Math.max(...items.map(i => i.value), 0.0001);
  const niceMax = niceCeil(maxV);
  const n = items.length;
  const slot = plotW / n;
  const bw = Math.min(46, slot * 0.6);

  let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img">`;
  // y 轴网格
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const y = padT + plotH * (1 - i / ticks);
    const val = (niceMax * i / ticks).toFixed(niceMax < 10 ? 1 : 0);
    svg += `<line class="grid-line" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}"/>`;
    svg += `<text class="bar-label" x="${padL - 6}" y="${y + 4}" text-anchor="end">${val}</text>`;
  }
  svg += `<line class="axis-line" x1="${padL}" y1="${padT + plotH}" x2="${W - padR}" y2="${padT + plotH}"/>`;

  items.forEach((it, i) => {
    const h = plotH * (it.value / niceMax);
    const x = padL + slot * i + (slot - bw) / 2;
    const y = padT + plotH - h;
    const label = it.label.length > 6 ? it.label.slice(0, 6) + "…" : it.label;
    svg += `<rect class="bar-rect" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="4" fill="#2f6fed"><title>${esc(it.label)}: ${it.value}${opts.unit || ""}</title></rect>`;
    svg += `<text class="bar-value" x="${(x + bw / 2).toFixed(1)}" y="${(y - 5).toFixed(1)}" text-anchor="middle">${it.value}</text>`;
    svg += `<text class="bar-label" x="${(x + bw / 2).toFixed(1)}" y="${(padT + plotH + 16).toFixed(1)}" text-anchor="middle">${esc(label)}</text>`;
  });
  svg += `</svg>`;
  container.innerHTML = svg;
}

function renderLineChart(container, points) {
  container.innerHTML = "";
  if (!points.length) {
    container.innerHTML = `<p class="empty-hint">暂无数据</p>`;
    return;
  }
  const W = 680, H = 240, padL = 40, padB = 40, padT = 16, padR = 12;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const maxV = Math.max(...points.map(p => p.value), 0.0001);

  const niceMax = niceCeil(maxV);
  const n = points.length;
  const stepX = n > 1 ? plotW / (n - 1) : 0;
  const single = n === 1;

  let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img">`;
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const y = padT + plotH * (1 - i / ticks);
    const val = (niceMax * i / ticks).toFixed(niceMax < 10 ? 1 : 0);
    svg += `<line class="grid-line" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}"/>`;
    svg += `<text class="bar-label" x="${padL - 6}" y="${y + 4}" text-anchor="end">${val}</text>`;
  }
  svg += `<line class="axis-line" x1="${padL}" y1="${padT + plotH}" x2="${W - padR}" y2="${padT + plotH}"/>`;

  const coords = points.map((p, i) => {
    const x = single ? padL + plotW / 2 : padL + stepX * i;
    const y = padT + plotH * (1 - p.value / niceMax);
    return [x, y, p];
  });
  // 面积 + 折线
  let area = `M ${coords[0][0]} ${padT + plotH}`;
  coords.forEach(c => area += ` L ${c[0].toFixed(1)} ${c[1].toFixed(1)}`);
  area += ` L ${coords[coords.length - 1][0]} ${padT + plotH} Z`;
  svg += `<path d="${area}" fill="#2f6fed" opacity="0.12"/>`;
  let line = coords.map((c, i) => (i ? "L" : "M") + ` ${c[0].toFixed(1)} ${c[1].toFixed(1)}`).join(" ");
  svg += `<path d="${line}" fill="none" stroke="#2f6fed" stroke-width="2.5"/>`;
  coords.forEach(c => {
    svg += `<circle cx="${c[0].toFixed(1)}" cy="${c[1].toFixed(1)}" r="4" fill="#2f6fed"><title>${esc(c[2].label)}: ${c[2].value}h</title></circle>`;
    const lbl = n > 6 && c[2].label.length > 6 ? c[2].label.slice(2) : c[2].label;
    svg += `<text class="bar-label" x="${c[0].toFixed(1)}" y="${(padT + plotH + 16).toFixed(1)}" text-anchor="middle">${esc(lbl)}</text>`;
  });
  svg += `</svg>`;
  container.innerHTML = svg;
}

function niceCeil(v) {
  if (v <= 0) return 1;
  const exp = Math.floor(Math.log10(v));
  const base = Math.pow(10, exp);
  const f = v / base;
  let nf;
  if (f <= 1) nf = 1; else if (f <= 2) nf = 2; else if (f <= 5) nf = 5; else nf = 10;
  return nf * base;
}

(function init() {
  if (AUTH_TOKEN) {
    api("GET", "/api/stats")
      .then(() => { showLogin(false); loadDashboard(); })
      .catch(() => { AUTH_TOKEN = ""; localStorage.removeItem(TOKEN_KEY); showLogin(true); });
  } else {
    showLogin(true);
  }
})();
