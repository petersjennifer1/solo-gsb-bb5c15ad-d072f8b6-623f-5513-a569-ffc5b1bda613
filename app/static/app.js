/* 管网漏点隔离规划台 — 原生 JS 前端 */
"use strict";

const state = {
  nodes: [],
  segments: [],
  valves: [],
  candidates: [], // 选址候选: {id, segment_id, install_cost, close_cost}
  result: null,
  layout: new Map(), // nodeId -> {x,y}
};

const ROLE_TEXT = {
  inlet: "进水点",
  leak: "漏点",
  must_keep: "必保供水点",
  normal: "普通点",
};
const STATUS_TEXT = {
  inlet_supply: "进水点(供水)",
  supplied: "有供水",
  isolated: "漏点已隔离",
  leak_not_isolated: "漏点未隔离!",
  disconnected: "断水",
  flowing: "通水",
  closed: "已关阀",
  dead: "停水",
};
const SOLVER_TEXT = { base: "常规", failover: "单阀备援" };
const KIND_TEXT = {
  no_base_plan: "原本就没有常规可行方案",
  no_failover_plan: "仅缺少备援方案(存在常规可行方案)",
};

// 备援情形: 全关阀清单
function valveListTable(valveIds) {
  if (!valveIds.length) return "<p>无需关闭任何阀门。</p>";
  const valveCost = new Map(state.valves.map((v) => [v.id, v.cost]));
  return `
    <table><thead><tr><th>顺序</th><th>阀门编号</th><th>绑定管段</th><th>关闭代价</th></tr></thead>
    <tbody>${valveIds.map((id, k) => {
      const v = state.valves.find((x) => x.id === id) || {};
      return `<tr><td>${k + 1}</td><td><b>${esc(id)}</b></td>
      <td>${esc(v.segment_id || "")}</td><td>${esc(valveCost.get(id) ?? "")}</td></tr>`;
    }).join("")}</tbody></table>`;
}

function nodeStatusTable(nodeStatus) {
  return `
    <table><thead><tr><th>节点</th><th>角色</th><th>状态</th><th>连通的进水点</th></tr></thead>
    <tbody>${nodeStatus.map((n) => `
      <tr><td>${esc(n.id)}${n.label && n.label !== n.id ? ` <span class="role-name">(${esc(n.label)})</span>` : ""}</td>
      <td>${ROLE_TEXT[n.role]}</td>
      <td><span class="tag ${n.status}">${STATUS_TEXT[n.status]}</span></td>
      <td>${n.connected_inlets.length ? esc(n.connected_inlets.join(", ")) : "—"}</td></tr>`).join("")}
    </tbody></table>`;
}

function segmentStatusTable(segmentStatus) {
  return `
    <table><thead><tr><th>管段</th><th>端点</th><th>绑定阀门</th><th>状态</th></tr></thead>
    <tbody>${segmentStatus.map((s) => `
      <tr><td>${esc(s.id)}</td><td>${esc(s.endpoint_a)} — ${esc(s.endpoint_b)}</td>
      <td>${s.valve_id ? esc(s.valve_id) : "—"}</td>
      <td><span class="tag ${s.status}">${STATUS_TEXT[s.status]}</span></td></tr>`).join("")}
    </tbody></table>`;
}

// 一种执行情形(正常 / 某阀失效)的校验结果行
function scenarioCheckRows(checks) {
  return checks.map((chk) => {
    const name = chk.scenario === "normal"
      ? "正常执行(全部按计划关闭)"
      : `阀门 ${esc(chk.failed_valve)} 关不上` +
        (chk.segment_id ? ` <span class="role-name">(管段 ${esc(chk.segment_id)} 保持通水)</span>` : "");
    const leakCell = chk.leaks_isolated
      ? '<span class="tag supplied">全部断开</span>'
      : `<span class="tag leak_not_isolated">仍通水: ${esc(chk.leaks_reachable.join(", "))}</span>`;
    const keepCell = chk.keeps_supplied
      ? '<span class="tag supplied">全部有供水</span>'
      : `<span class="tag disconnected">断水: ${esc(chk.keeps_starved.join(", "))}</span>`;
    const okCell = chk.ok
      ? '<span class="tag supplied">通过</span>'
      : '<span class="tag leak_not_isolated">不通过</span>';
    return `<tr><td>${name}</td><td>${esc(chk.effective_closed_valves.join(", ") || "—")}</td>
    <td>${leakCell}</td><td>${keepCell}</td><td>${okCell}</td></tr>`;
  }).join("");
}

const $ = (sel) => document.querySelector(sel);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- API ----------
async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opt);
  let data = null;
  try { data = await resp.json(); } catch (_) { /* 非 JSON */ }
  if (!resp.ok) {
    const detail = data && data.detail;
    const errs = detail && detail.errors;
    const msg = errs ? errs.join("\n")
      : (typeof detail === "string" ? detail : `请求失败 (${resp.status})`);
    throw new Error(msg);
  }
  return data;
}

function payloadFromUI() {
  return {
    nodes: state.nodes
      .filter((n) => n.id.trim())
      .map((n) => ({ id: n.id.trim(), role: n.role, label: n.label.trim() })),
    segments: state.segments
      .filter((s) => s.id.trim())
      .map((s) => ({
        id: s.id.trim(),
        endpoint_a: s.endpoint_a.trim(),
        endpoint_b: s.endpoint_b.trim(),
      })),
    valves: state.valves
      .filter((v) => v.id.trim())
      .map((v) => ({ id: v.id.trim(), segment_id: v.segment_id.trim(), cost: v.cost })),
  };
}

// ---------- 渲染输入表格 ----------
function nodeOptions(selected) {
  return state.nodes.map((n) =>
    `<option value="${esc(n.id)}"${n.id === selected ? " selected" : ""}>${esc(n.id)}</option>`
  ).join("");
}
function segmentOptions(selected) {
  return state.segments.map((s) =>
    `<option value="${esc(s.id)}"${s.id === selected ? " selected" : ""}>${esc(s.id)}</option>`
  ).join("");
}

function renderNodes() {
  const filter = $("#node-role-filter").value;
  const rows = state.nodes
    .map((n, i) => ({ n, i }))
    .filter(({ n }) => !filter || n.role === filter);
  $("#table-nodes tbody").innerHTML = rows.map(({ n, i }) => `
    <tr data-i="${i}">
      <td><input data-f="id" value="${esc(n.id)}" placeholder="如 S1"></td>
      <td><select data-f="role">
        ${Object.entries(ROLE_TEXT).map(([v, t]) =>
          `<option value="${v}"${n.role === v ? " selected" : ""}>${t}</option>`).join("")}
      </select></td>
      <td><input data-f="label" value="${esc(n.label)}" placeholder="可选"></td>
      <td class="act"><button data-del="node" title="删除">✕</button></td>
    </tr>`).join("");
}

function renderSegments() {
  $("#table-segments tbody").innerHTML = state.segments.map((s, i) => `
    <tr data-i="${i}">
      <td><input data-f="id" value="${esc(s.id)}" placeholder="如 P1"></td>
      <td><select data-f="endpoint_a"><option value=""></option>${nodeOptions(s.endpoint_a)}</select></td>
      <td><select data-f="endpoint_b"><option value=""></option>${nodeOptions(s.endpoint_b)}</select></td>
      <td class="act"><button data-del="segment" title="删除">✕</button></td>
    </tr>`).join("");
}

function renderValves() {
  $("#table-valves tbody").innerHTML = state.valves.map((v, i) => `
    <tr data-i="${i}">
      <td><input data-f="id" value="${esc(v.id)}" placeholder="如 V1"></td>
      <td><select data-f="segment_id"><option value=""></option>${segmentOptions(v.segment_id)}</select></td>
      <td><input data-f="cost" type="number" min="1" step="1" value="${esc(v.cost)}"></td>
      <td class="act"><button data-del="valve" title="删除">✕</button></td>
    </tr>`).join("");
}

// 选址候选: 仅允许选尚无阀门的管段, 且多个候选不能共用一条管段
function renderCandidates() {
  const usedByValve = new Set(state.valves.map((v) => v.segment_id).filter(Boolean));
  const taken = new Set(state.candidates.map((c) => c.segment_id).filter(Boolean));
  $("#table-candidates tbody").innerHTML = state.candidates.map((c, i) => {
    const opts = state.segments
      .map((s) => s.id)
      .filter((id) => id && (!usedByValve.has(id) || id === c.segment_id)
        && (!taken.has(id) || id === c.segment_id));
    const optHtml = opts.map((id) =>
      `<option value="${esc(id)}"${id === c.segment_id ? " selected" : ""}>${esc(id)}</option>`
    ).join("");
    return `
    <tr data-i="${i}">
      <td><input data-f="id" value="${esc(c.id)}" placeholder="如 NV1"></td>
      <td><select data-f="segment_id"><option value=""></option>${optHtml}</select></td>
      <td><input data-f="install_cost" type="number" min="1" step="1" value="${esc(c.install_cost)}"></td>
      <td><input data-f="close_cost" type="number" min="1" step="1" value="${esc(c.close_cost)}"></td>
      <td class="act"><button data-del="candidate" title="删除">✕</button></td>
    </tr>`;
  }).join("");
  $("#siting-hint").textContent =
    `原阀 ${state.valves.length} 个 + 候选 ${state.candidates.length} 个（合计 ≤ 14）；` +
    "安装费与关阀代价均为正整数，预览只读、不写入管网与历史方案";
}

function renderAll() {
  renderNodes();
  renderSegments();
  renderValves();
  renderCandidates();
  drawCanvas();
}

// ---------- 表格事件(委托) ----------
function bindTable(table, key, defaults) {
  table.addEventListener("input", (e) => {
    const td = e.target.closest("tr");
    if (!td) return;
    const i = Number(td.dataset.i);
    const f = e.target.dataset.f;
    if (f === undefined) return;
    let val = e.target.value;
    if (f === "cost" || f === "install_cost" || f === "close_cost") val = Number(val);
    state[key][i][f] = val;
    // 节点编号变化会影响管段下拉; 管段/阀门变化会影响候选管段下拉
    if (key === "nodes") renderSegments();
    if (key === "segments" || key === "valves") renderCandidates();
    drawCanvas();
  });
  table.addEventListener("change", () => {
    if (key === "nodes") renderSegments();
    if (key === "segments" || key === "valves") renderCandidates();
    drawCanvas();
  });
  table.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-del]");
    if (!btn) return;
    const i = Number(btn.closest("tr").dataset.i);
    state[key].splice(i, 1);
    renderAll();
  });
}

document.querySelectorAll(".add-row").forEach((btn) => {
  btn.addEventListener("click", () => {
    const t = btn.dataset.table;
    if (t === "nodes") state.nodes.push({ id: "", role: "normal", label: "" });
    if (t === "segments") state.segments.push({ id: "", endpoint_a: "", endpoint_b: "" });
    if (t === "valves") {
      if (state.valves.length >= 14) {
        showErrors(["阀门数量最多 14 个"]);
        return;
      }
      state.valves.push({ id: "", segment_id: "", cost: 1 });
    }
    if (t === "candidates") {
      if (state.candidates.length >= 6) {
        showErrors(["候选位置最多 6 个"]);
        return;
      }
      state.candidates.push({ id: "", segment_id: "", install_cost: 1, close_cost: 1 });
    }
    renderAll();
  });
});

bindTable($("#table-nodes"), "nodes");
bindTable($("#table-segments"), "segments");
bindTable($("#table-valves"), "valves");
bindTable($("#table-candidates"), "candidates");
$("#node-role-filter").addEventListener("change", renderNodes);

// ---------- 结果渲染 ----------
function showErrors(errors) {
  const box = $("#input-errors");
  box.innerHTML = `<b>输入存在问题:</b><ul>${errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`;
  box.classList.remove("hidden");
}
function clearErrors() {
  $("#input-errors").classList.add("hidden");
}

function renderResult(r) {
  state.result = r;
  const banner = $("#result-banner");
  banner.classList.remove("hidden", "ok", "bad");
  const body = $("#result-body");

  if (!r.feasible) {
    renderInfeasible(r, banner, body);
    drawCanvas();
    return;
  }

  if (r.solver === "failover") {
    renderFailover(r, banner, body);
    appendSequencePanel(r);
    appendJumperPanel(r);
    drawCanvas();
    return;
  }

  banner.classList.add("ok");
  banner.innerHTML =
    `<span class="big">✓ 已求得最优方案</span>${esc(r.message)}` +
    `<br><small>共枚举 ${r.subset_count} 个阀门组合, 其中 ${r.isolating_subset_count} 个可隔离全部漏点。</small>`;

  body.innerHTML = `
    <div class="card">
      <h3>关阀清单（${r.closed_valves.length} 个, 总代价 ${r.total_cost}）</h3>
      ${valveListTable(r.closed_valves)}
    </div>

    <div class="card">
      <h3>各节点隔离后供水状态</h3>
      ${nodeStatusTable(r.node_status)}
    </div>

    <div class="card">
      <h3>各管段状态</h3>
      ${segmentStatusTable(r.segment_status)}
    </div>`;
  appendSequencePanel(r);
  appendJumperPanel(r);
  drawCanvas();
}

function renderInfeasible(r, banner, body) {
  banner.classList.add("bad");
  let head = "无解";
  if (r.solver === "failover") {
    head = r.infeasible_kind === "no_failover_plan"
      ? "无备援方案"
      : "备援无解(常规规划本身无解)";
  }
  banner.innerHTML = `<span class="big">${head}</span>${esc(r.message)}`;
  if (r.reason_detail && r.reason_detail.witness_path) {
    banner.innerHTML += `<br><small>证据通路: ${esc(r.reason_detail.witness_path)}</small>`;
  }

  let kindNote = "";
  if (r.solver === "failover") {
    kindNote = `
      <p>无解类别: <code>${esc(r.infeasible_kind || "")}</code>
      （${esc(KIND_TEXT[r.infeasible_kind] || "")}）</p>`;
  }

  // 仅缺少备援方案时, 展示最优常规方案逐阀失效的校验结果
  let bestBase = "";
  const bp = r.reason_detail && r.reason_detail.best_base_plan;
  if (bp) {
    bestBase = `
      <div class="card">
        <h3>最优常规方案的单阀失效校验（说明为何缺少备援）</h3>
        <p class="role-name">常规方案关闭: ${esc((bp.closed_valves || []).join(", ") || "(无)")}
        ，总代价 ${esc(bp.total_cost)}。下列任一计划阀门关不上都会导致方案失效。</p>
        ${bp.failure_checks && bp.failure_checks.length ? `
        <table><thead><tr><th>失效情形</th><th>实际关闭阀门</th><th>漏点</th><th>必保点</th><th>校验</th></tr></thead>
        <tbody>${scenarioCheckRows(bp.failure_checks)}</tbody></table>` : "<p>该常规方案不关闭任何阀门。</p>"}
      </div>`;
  }

  body.innerHTML = `
    <div class="card"><h3>无解原因</h3>
      <p>原因代码: <code>${esc(r.reason_code)}</code></p>
      ${kindNote}
      <p>${esc(r.message)}</p>
      ${r.reason_detail ? `<pre>${esc(JSON.stringify(r.reason_detail, null, 2))}</pre>` : ""}
    </div>${bestBase}`;
}

function renderFailover(r, banner, body) {
  banner.classList.add("ok");
  banner.innerHTML =
    `<span class="big">✓ 已求得单阀失效备援方案</span>${esc(r.message)}` +
    `<br><small>共枚举 ${r.subset_count} 个阀门组合: 常规可行 ${r.base_feasible_subset_count} 个, ` +
    `其中 ${r.failover_candidate_count} 个可承受任意单阀关不上。</small>`;

  const allChecks = [r.normal_check, ...r.failure_checks];
  body.innerHTML = `
    <div class="card">
      <h3>备援关阀清单（${r.closed_valves.length} 个, 总代价 ${r.total_cost}）</h3>
      ${valveListTable(r.closed_valves)}
      <p class="role-name">目标: 最小化计划关闭阀门的代价总和; 同代价时取编号升序表字典序最小者。</p>
    </div>

    <div class="card">
      <h3>逐阀失效校验（共 ${allChecks.length} 种情形: 正常执行 + ${r.failure_checks.length} 种单阀关不上）</h3>
      <table><thead><tr>
        <th>情形</th><th>实际关闭阀门</th><th>全部漏点与进水点断开</th>
        <th>全部必保点仍有供水</th><th>校验</th>
      </tr></thead>
      <tbody>${scenarioCheckRows(allChecks)}</tbody></table>
    </div>

    <div class="card">
      <h3>各节点供水状态（正常执行时）</h3>
      ${nodeStatusTable(r.node_status)}
    </div>

    <div class="card">
      <h3>各管段状态（正常执行时）</h3>
      ${segmentStatusTable(r.segment_status)}
    </div>`;
}

// ---------- 新增阀门选址预览 ----------
// 只读预览: 不写管网、不入库方案; 结果展示在独立的 ③ 选址预览区。
function renderSitingResult(r) {
  const banner = $("#siting-banner");
  const body = $("#siting-body");
  banner.classList.remove("hidden", "ok", "bad");

  if (!r.feasible) {
    banner.classList.add("bad");
    banner.innerHTML = `<span class="big">✗ 候选范围内不可修复</span>${esc(r.message)}`;
    if (r.reason_detail && r.reason_detail.witness_path) {
      banner.innerHTML += `<br><small>证据通路: ${esc(r.reason_detail.witness_path)}</small>`;
    }
    body.innerHTML = `
      <div class="card"><h3>无解原因</h3>
        <p>原因代码: <code>${esc(r.reason_code)}</code></p>
        <p>${esc(r.message)}</p>
        ${r.reason_detail ? `<pre>${esc(JSON.stringify(r.reason_detail, null, 2))}</pre>` : ""}
      </div>`;
    return;
  }

  banner.classList.add("ok");
  banner.innerHTML =
    `<span class="big">✓ 选址预览方案</span>${esc(r.message)}` +
    `<br><small>共枚举 ${r.subset_count} 个「原阀 + 候选」关阀组合, 其中 ` +
    `${r.isolating_subset_count} 个可隔离全部漏点。本结果为只读预览, 未写入管网与历史方案。</small>`;

  const newValveTable = r.new_valves.length ? `
    <table><thead><tr><th>新阀编号</th><th>安装管段</th><th>安装费</th><th>关阀代价</th></tr></thead>
    <tbody>${r.new_valves.map((v) => `
      <tr><td><b>${esc(v.id)}</b></td><td>${esc(v.segment_id)}</td>
      <td>${esc(v.install_cost)}</td><td>${esc(v.close_cost)}</td></tr>`).join("")}
    </tbody></table>` : "<p>无需增设新阀, 仅关闭原有阀门即可。</p>";

  const closedTable = r.closed_valve_details.length ? `
    <table><thead><tr><th>顺序</th><th>阀门编号</th><th>来源</th><th>绑定管段</th><th>安装费</th><th>关闭代价</th></tr></thead>
    <tbody>${r.closed_valve_details.map((d, k) => `
      <tr><td>${k + 1}</td><td><b>${esc(d.id)}</b></td>
      <td>${d.is_new ? '<span class="tag flowing">新增</span>' : '<span class="tag dead">原有</span>'}</td>
      <td>${esc(d.segment_id)}</td>
      <td>${d.is_new ? esc(d.install_cost) : "—"}</td>
      <td>${esc(d.close_cost)}</td></tr>`).join("")}
    </tbody></table>` : "<p>无需关闭任何阀门。</p>";

  body.innerHTML = `
    <div class="card">
      <h3>新增位置（${r.new_valves.length} 个）</h3>
      ${newValveTable}
    </div>

    <div class="card">
      <h3>费用</h3>
      <div class="summary">
        <span>安装费合计(仅实际关闭的候选阀): <b>${r.install_cost_total}</b></span>
        <span>本次关阀代价合计: <b>${r.close_cost_total}</b></span>
      </div>
      <p class="role-name">优化次序: 先最小化安装费, 再最小化本次关阀代价(含新阀关阀代价与原有阀门关闭代价);
      仍并列时按新增管段编号列表、关阀编号列表字典序决胜。</p>
    </div>

    <div class="card">
      <h3>关阀清单（${r.closed_valves.length} 个）</h3>
      ${closedTable}
    </div>

    <div class="card">
      <h3>各节点供水状态（方案执行后）</h3>
      ${nodeStatusTable(r.node_status)}
    </div>

    <div class="card">
      <h3>各管段状态（方案执行后）</h3>
      ${segmentStatusTable(r.segment_status)}
    </div>`;
}

$("#btn-plan-siting").addEventListener("click", async () => {
  clearErrors();
  try {
    const r = await api("POST", "/api/plan-siting", {
      network: payloadFromUI(),
      candidates: state.candidates
        .filter((c) => c.id.trim() || c.segment_id.trim())
        .map((c) => ({
          id: c.id.trim(),
          segment_id: c.segment_id.trim(),
          install_cost: c.install_cost,
          close_cost: c.close_cost,
        })),
    });
    renderSitingResult(r);
    $("#section-siting").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    showErrors(String(err.message).split("\n"));
  }
});

// ---------- 现场执行顺序规划 ----------
// 入口: 可行方案详情(历史方案"查看"或刚求解出的方案)下方的规划卡片。
// 只读计算, 不修改原方案。
function appendSequencePanel(r) {
  const body = $("#result-body");
  const card = document.createElement("div");
  card.className = "card";
  card.id = "sequence-card";

  if (r.id == null) {
    return; // 未入库结果不提供规划入口
  }

  const leakIds = (r.node_status || [])
    .filter((n) => n.role === "leak")
    .map((n) => n.id);

  if (!r.closed_valves.length) {
    card.innerHTML = `
      <h3>现场执行顺序规划</h3>
      <p class="role-name">该可行方案不关闭任何阀门, 无操作时段, 累计暴露量为 0, 无需规划执行顺序。</p>`;
    body.appendChild(card);
    return;
  }

  const valveById = new Map(state.valves.map((v) => [v.id, v]));
  card.innerHTML = `
    <h3>现场执行顺序规划 <span class="role-name">(只读计算, 不修改原方案)</span></h3>
    <p class="role-name">
      按方案快照为<b>每枚计划阀门</b>录入正整数操作时长(分钟), 为<b>每个漏点</b>录入正整数漏水权重。
      操作期间该阀仍开启; 每步完成后按多进水点连通性更新仍通水漏点;
      最小化各时段「时长 × 仍通水漏点权重之和」的累计暴露量, 同暴露量取阀门编号序列字典序最小的唯一顺序。
    </p>
    <div class="grid-two">
      <div>
        <h4>计划阀门操作时长（分钟）</h4>
        <table id="table-durations"><thead><tr>
          <th>阀门编号</th><th>绑定管段</th><th>关闭代价</th><th>操作时长</th>
        </tr></thead><tbody>
        ${r.closed_valves.map((id) => {
          const v = valveById.get(id) || {};
          return `<tr><td><b>${esc(id)}</b></td>
            <td>${esc(v.segment_id || "—")}</td>
            <td>${esc(v.cost ?? "—")}</td>
            <td><input data-dur="${esc(id)}" type="number" min="1" step="1" value="1"></td></tr>`;
        }).join("")}
        </tbody></table>
      </div>
      <div>
        <h4>漏点漏水权重</h4>
        <table id="table-weights"><thead><tr>
          <th>漏点编号</th><th>漏水权重</th>
        </tr></thead><tbody>
        ${leakIds.map((id) => `<tr><td><b>${esc(id)}</b></td>
          <td><input data-w="${esc(id)}" type="number" min="1" step="1" value="1"></td></tr>`).join("")}
        </tbody></table>
      </div>
    </div>
    <p>
      <button id="btn-sequence" type="button" class="primary">计算最优执行顺序</button>
    </p>
    <div id="sequence-errors" class="errors hidden"></div>
    <div id="sequence-result"></div>`;
  body.appendChild(card);
  $("#btn-sequence").addEventListener("click", () => computeSequence(r));
}

async function computeSequence(r) {
  const errBox = $("#sequence-errors");
  const resBox = $("#sequence-result");
  errBox.classList.add("hidden");
  resBox.innerHTML = "";

  const collect = (attr) => {
    const out = {};
    document.querySelectorAll(`#sequence-card input[data-${attr}]`).forEach((inp) => {
      out[inp.dataset[attr]] = inp.value.trim() === "" ? null : Number(inp.value);
    });
    return out;
  };
  const payload = { durations: collect("dur"), leak_weights: collect("w") };

  try {
    const res = await api("POST", `/api/plans/${r.id}/sequence`, payload);
    renderSequenceResult(res);
  } catch (err) {
    errBox.innerHTML = `<b>无法规划执行顺序:</b><ul>${
      String(err.message).split("\n").map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`;
    errBox.classList.remove("hidden");
  }
}

function renderSequenceResult(res) {
  const box = $("#sequence-result");
  const leakCell = (arr) => arr.length
    ? esc(arr.join(", "))
    : `<span class="tag isolated">无（全部隔离）</span>`;
  box.innerHTML = `
    <div class="banner ok">
      <span class="big">✓ 最优执行顺序: ${esc(res.sequence.join(" → "))}</span>
      总时长 <b>${res.total_duration}</b> 分钟, 累计暴露量 <b>${res.total_exposure}</b>
      <br><small>执行前仍通水漏点: ${
        esc(res.initial_flowing_leaks.join(", ")) || "无"}（权重和 ${
        res.initial_flowing_weight}）；全部步骤完成后仍通水漏点: ${
        esc(res.final_flowing_leaks.join(", ")) || "无"}。
        第 k 步暴露量 = 该阀时长 × 操作期间(该阀仍开启)仍通水漏点权重之和。</small>
    </div>
    <table><thead><tr>
      <th>步骤</th><th>关闭阀门</th><th>绑定管段</th><th>时长(分钟)</th>
      <th>起止时间(分钟)</th><th>操作期间仍通水漏点(权重和)</th><th>本步暴露量</th>
      <th>刚隔离的漏点</th><th>完成后仍通水漏点(权重和)</th><th>累计暴露量</th>
    </tr></thead><tbody>
    ${res.steps.map((st) => `<tr>
      <td>${st.order}</td>
      <td><b>${esc(st.valve_id)}</b></td>
      <td>${esc(st.segment_id)}</td>
      <td>${st.duration}</td>
      <td>${st.start} → ${st.end}</td>
      <td>${leakCell(st.flowing_leaks_during)}（${st.flowing_weight_during}）</td>
      <td>${st.exposure}</td>
      <td>${st.newly_isolated_leaks.length ? esc(st.newly_isolated_leaks.join(", ")) : "—"}</td>
      <td>${leakCell(st.flowing_leaks_after)}（${st.flowing_weight_after}）</td>
      <td><b>${st.cumulative_exposure}</b></td>
    </tr>`).join("")}
    </tbody></table>`;
}

// ---------- 临时供水跨接预览 ----------
// 入口: 可行方案详情(历史方案"查看"或刚求解出的方案)下方的预览卡片。
// 只读计算, 不修改管网与历史方案。
function appendJumperPanel(r) {
  if (r.id == null) return; // 未入库结果不提供预览入口
  const body = $("#result-body");
  const card = document.createElement("div");
  card.className = "card";
  card.id = "jumper-card";
  card.innerHTML = `
    <h3>临时供水跨接预览 <span class="role-name">(只读计算, 不修改管网与历史方案)</span></h3>
    <p class="role-name">
      基于方案快照与已关阀管段(方案执行后的管网), 录入至多 6 条候选跨接:
      每条含<b>唯一编号</b>、<b>两个不同的现有节点</b>与<b>正整数铺设费</b>,
      跨接视为新增通水管段。枚举候选组合, 始终保持全部漏点与进水点断开、
      全部必保点有水; 先最大化由断水转为有水的普通节点数, 再最小化铺设费合计,
      并列时按跨接编号升序列表字典序选取; 无法恢复普通节点时返回空方案。
    </p>
    <table id="table-jumpers"><thead><tr>
      <th>跨接编号</th><th>端点A</th><th>端点B</th><th>铺设费</th><th></th>
    </tr></thead><tbody></tbody></table>
    <button id="btn-add-jumper" class="add-row" type="button">＋ 添加候选跨接</button>
    <p>
      <button id="btn-jumpers" type="button" class="primary">跨接预览求解</button>
      <span class="role-name" id="jumper-hint"></span>
    </p>
    <div id="jumper-errors" class="errors hidden"></div>
    <div id="jumper-result"></div>`;
  body.appendChild(card);

  const jumpers = []; // 本地候选: {id, endpoint_a, endpoint_b, cost}
  const tbody = card.querySelector("#table-jumpers tbody");
  const nodeOpts = (sel) => state.nodes.map((n) =>
    `<option value="${esc(n.id)}"${n.id === sel ? " selected" : ""}>${esc(n.id)}</option>`
  ).join("");

  function renderRows() {
    tbody.innerHTML = jumpers.map((c, i) => `
      <tr data-i="${i}">
        <td><input data-f="id" value="${esc(c.id)}" placeholder="如 J1"></td>
        <td><select data-f="endpoint_a"><option value=""></option>${nodeOpts(c.endpoint_a)}</select></td>
        <td><select data-f="endpoint_b"><option value=""></option>${nodeOpts(c.endpoint_b)}</select></td>
        <td><input data-f="cost" type="number" min="1" step="1" value="${esc(c.cost)}"></td>
        <td class="act"><button data-del="${i}" title="删除">✕</button></td>
      </tr>`).join("");
    card.querySelector("#jumper-hint").textContent =
      `候选跨接 ${jumpers.length} 条（至多 6 条）；跨接视为新增通水管段，预览只读`;
  }

  card.querySelector("#btn-add-jumper").addEventListener("click", () => {
    if (jumpers.length >= 6) {
      const errBox = card.querySelector("#jumper-errors");
      errBox.innerHTML = "<b>候选跨接最多 6 条</b>";
      errBox.classList.remove("hidden");
      return;
    }
    jumpers.push({ id: "", endpoint_a: "", endpoint_b: "", cost: 1 });
    renderRows();
  });
  tbody.addEventListener("input", (e) => {
    const tr = e.target.closest("tr");
    if (!tr) return;
    const i = Number(tr.dataset.i);
    const f = e.target.dataset.f;
    if (f === undefined) return;
    jumpers[i][f] = (f === "cost") ? Number(e.target.value) : e.target.value;
  });
  tbody.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-del]");
    if (!btn) return;
    jumpers.splice(Number(btn.dataset.del), 1);
    renderRows();
  });
  card.querySelector("#btn-jumpers").addEventListener("click", async () => {
    const errBox = card.querySelector("#jumper-errors");
    const resBox = card.querySelector("#jumper-result");
    errBox.classList.add("hidden");
    resBox.innerHTML = "";
    const candidates = jumpers
      .filter((c) => c.id.trim() || c.endpoint_a || c.endpoint_b)
      .map((c) => ({
        id: c.id.trim(), endpoint_a: c.endpoint_a,
        endpoint_b: c.endpoint_b, cost: c.cost,
      }));
    try {
      const res = await api("POST", `/api/plans/${r.id}/jumpers`, { candidates });
      renderJumperResult(res, resBox);
    } catch (err) {
      errBox.innerHTML = `<b>无法求解跨接预览:</b><ul>${
        String(err.message).split("\n").map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`;
      errBox.classList.remove("hidden");
    }
  });
  renderRows();
}

function renderJumperResult(res, box) {
  const selTable = res.selected_jumpers.length ? `
    <table><thead><tr><th>跨接编号</th><th>端点A</th><th>端点B</th><th>铺设费</th></tr></thead>
    <tbody>${res.selected_jumpers.map((j) => `
      <tr><td><b>${esc(j.id)}</b></td><td>${esc(j.endpoint_a)}</td>
      <td>${esc(j.endpoint_b)}</td><td>${esc(j.cost)}</td></tr>`).join("")}
    </tbody></table>` : "<p>空方案: 不铺设任何跨接。</p>";

  const restored = res.restored_nodes.length
    ? res.restored_nodes.map((id) => `<span class="tag supplied">${esc(id)}</span>`).join(" ")
    : "无";
  const segRows = res.segment_status.map((s) => `
    <tr><td>${esc(s.id)}</td><td>${esc(s.endpoint_a)} — ${esc(s.endpoint_b)}</td>
    <td>${s.is_jumper ? '<span class="tag flowing">跨接</span>' : "原有"}</td>
    <td>${s.valve_id ? esc(s.valve_id) : "—"}</td>
    <td><span class="tag ${s.status}">${STATUS_TEXT[s.status]}</span></td></tr>`).join("");

  box.innerHTML = `
    <div class="banner ok">
      <span class="big">✓ ${res.restored_count ? "跨接预览方案" : "空方案"}</span>${esc(res.message)}
      <br><small>共枚举 ${res.combination_count} 个候选组合, 其中
      ${res.feasible_combination_count} 个保持漏点隔离与必保供水。
      本结果为只读预览, 未修改管网与历史方案。</small>
    </div>
    <h4>选中跨接（${res.selected_jumpers.length} 条, 铺设费合计 ${res.lay_cost_total}）</h4>
    ${selTable}
    <p class="role-name">优化次序: 先最大化恢复供水的普通节点数, 再最小化铺设费合计;
    并列时按跨接编号升序列表字典序选取。</p>
    <h4>恢复供水节点（${res.restored_count} 个）</h4>
    <p>由断水转为有水: ${restored}</p>
    <p class="role-name">方案执行后断水的普通节点: ${
      res.disconnected_normal_before.map(esc).join(", ") || "无"}；铺设跨接后仍断水: ${
      res.still_disconnected_normal.map(esc).join(", ") || "无"}。</p>
    <h4>各节点供水状态（铺设跨接后）</h4>
    ${nodeStatusTable(res.node_status)}
    <h4>各管段状态（含新增跨接管段）</h4>
    <table><thead><tr><th>管段</th><th>端点</th><th>来源</th><th>绑定阀门</th><th>状态</th></tr></thead>
    <tbody>${segRows}</tbody></table>`;
}

// ---------- 历史 ----------
async function loadHistory() {
  const data = await api("GET", "/api/plans?limit=20");
  $("#table-plans tbody").innerHTML = data.plans.length
    ? data.plans.map((p) => `
      <tr>
        <td>${p.id}</td><td>${esc(p.created_at.replace("+00:00", "Z"))}</td>
        <td><span class="tag ${p.solver === "failover" ? "flowing" : "dead"}">${esc(SOLVER_TEXT[p.solver] || p.solver || "常规")}</span></td>
        <td>${p.feasible ? '<span class="tag supplied">可行</span>' : '<span class="tag leak_not_isolated">无解</span>'}</td>
        <td>${p.total_cost ?? "—"}</td>
        <td>${p.closed_valves.map(esc).join(", ") || "—"}</td>
        <td><button data-view="${p.id}" class="secondary">查看</button></td>
      </tr>`).join("")
    : `<tr><td colspan="7" class="role-name">暂无历史方案</td></tr>`;
}

$("#table-plans").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-view]");
  if (!btn) return;
  const plan = await api("GET", `/api/plans/${btn.dataset.view}`);
  const snap = plan.network_snapshot;
  state.nodes = snap.nodes.map((n) => ({ ...n }));
  state.segments = snap.segments.map((s) => ({ ...s }));
  state.valves = snap.valves.map((v) => ({ ...v }));
  renderAll();
  delete plan.network_snapshot;
  renderResult(plan);
  $("#section-result").scrollIntoView({ behavior: "smooth" });
});

// ---------- 顶部按钮 ----------
$("#btn-demo").addEventListener("click", async () => {
  const net = await api("POST", "/api/demo");
  applyNetwork(net.network);
  clearErrors();
});

$("#btn-clear").addEventListener("click", async () => {
  if (!confirm("确定清空全部节点、管段和阀门吗?")) return;
  await api("POST", "/api/clear");
  state.nodes = []; state.segments = []; state.valves = [];
  state.candidates = [];
  state.result = null;
  $("#result-body").innerHTML = "";
  $("#result-banner").classList.add("hidden");
  clearSitingResult();
  renderAll();
  loadHistory();
});

$("#btn-save").addEventListener("click", async () => {
  clearErrors();
  try {
    const res = await api("PUT", "/api/network", payloadFromUI());
    alert(`保存成功: ${res.counts.nodes} 个节点, ${res.counts.segments} 条管段, ${res.counts.valves} 个阀门`);
    loadHistory();
  } catch (err) {
    showErrors(String(err.message).split("\n"));
  }
});

$("#btn-plan").addEventListener("click", async () => {
  clearErrors();
  try {
    const r = await api("POST", "/api/plan", payloadFromUI());
    renderResult(r);
    loadHistory();
    $("#section-result").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    showErrors(String(err.message).split("\n"));
  }
});

$("#btn-plan-failover").addEventListener("click", async () => {
  clearErrors();
  try {
    const r = await api("POST", "/api/plan-failover", payloadFromUI());
    renderResult(r);
    loadHistory();
    $("#section-result").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    showErrors(String(err.message).split("\n"));
  }
});

function clearSitingResult() {
  $("#siting-body").innerHTML = "";
  $("#siting-banner").classList.add("hidden");
}

function applyNetwork(net) {
  state.nodes = (net.nodes || []).map((n) => ({ ...n, label: n.label || "" }));
  state.segments = (net.segments || []).map((s) => ({ ...s }));
  state.valves = (net.valves || []).map((v) => ({ ...v }));
  state.candidates = [];
  state.layout.clear();
  state.result = null;
  $("#result-body").innerHTML = "";
  $("#result-banner").classList.add("hidden");
  clearSitingResult();
  renderAll();
}

// ---------- Canvas 力导向示意 ----------
const canvas = $("#canvas");
const ctx = canvas.getContext("2d");
const COLOR = {
  inlet: "#1c7fb3", leak: "#d13221", must_keep: "#2f9e44", normal: "#8a98a6",
};

function initLayout(w, h) {
  const cx = w / 2, cy = h / 2;
  state.nodes.forEach((n, i) => {
    if (!state.layout.has(n.id)) {
      const ang = (i / Math.max(1, state.nodes.length)) * Math.PI * 2;
      state.layout.set(n.id, {
        x: cx + Math.cos(ang) * Math.min(w, h) * 0.32,
        y: cy + Math.sin(ang) * Math.min(w, h) * 0.32,
      });
    }
  });
  // 删除已不存在的节点坐标
  for (const id of [...state.layout.keys()]) {
    if (!state.nodes.some((n) => n.id === id)) state.layout.delete(id);
  }
}

function simulation() {
  const pos = state.layout;
  const ids = state.nodes.map((n) => n.id);
  const idset = new Set(ids);
  const links = state.segments
    .filter((s) => idset.has(s.endpoint_a) && idset.has(s.endpoint_b))
    .map((s) => [s.endpoint_a, s.endpoint_b]);
  const W = canvas.width - 80, H = canvas.height - 80;
  for (let iter = 0; iter < 240; iter++) {
    const force = new Map(ids.map((id) => [id, { x: 0, y: 0 }]));
    // 斥力
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = pos.get(ids[i]), b = pos.get(ids[j]);
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy || 1;
        const f = 9000 / d2;
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        force.get(ids[i]).x += dx * f; force.get(ids[i]).y += dy * f;
        force.get(ids[j]).x -= dx * f; force.get(ids[j]).y -= dy * f;
      }
    }
    // 弹簧
    for (const [u, v] of links) {
      const a = pos.get(u), b = pos.get(v);
      let dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - 130) * 0.02;
      dx /= d; dy /= d;
      force.get(u).x += dx * f; force.get(u).y += dy * f;
      force.get(v).x -= dx * f; force.get(v).y -= dy * f;
    }
    for (const id of ids) {
      const p = pos.get(id), fr = force.get(id);
      p.x = Math.max(40, Math.min(W, p.x + fr.x));
      p.y = Math.max(40, Math.min(H, p.y + fr.y));
    }
  }
}

function drawCanvas() {
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (!state.nodes.length) {
    ctx.fillStyle = "#8a98a6";
    ctx.font = "15px sans-serif";
    ctx.fillText("暂无管网数据 — 点击右上角「载入示例管网」快速体验", 220, h / 2);
    return;
  }
  initLayout(w, h);
  simulation();

  const closedSet = new Set((state.result && state.result.closed_valves) || []);
  const segStatus = new Map(
    ((state.result && state.result.segment_status) || []).map((s) => [s.id, s.status])
  );
  const nodeStatus = new Map(
    ((state.result && state.result.node_status) || []).map((n) => [n.id, n.status])
  );

  // 管段
  for (const s of state.segments) {
    const a = state.layout.get(s.endpoint_a);
    const b = state.layout.get(s.endpoint_b);
    if (!a || !b) continue;
    let color = "#5a6b7c", dash = [], width = 2;
    const st = segStatus.get(s.id);
    if (s.valve_id && closedSet.has(s.valve_id)) {
      color = "#d13221"; dash = [7, 5]; width = 3;
    } else if (st === "flowing") {
      color = "#1c7fb3"; width = 3;
    } else if (st === "dead") {
      color = "#b9c2cb";
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    ctx.setLineDash([]);

    // 阀门标记: 管段中点画小方块 + 编号
    if (s.valve_id) {
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const closed = closedSet.has(s.valve_id);
      ctx.fillStyle = closed ? "#d13221" : "#fff";
      ctx.strokeStyle = "#444";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.rect(mx - 6, my - 6, 12, 12);
      ctx.fill(); ctx.stroke();
      ctx.fillStyle = closed ? "#d13221" : "#333";
      ctx.font = "10px sans-serif";
      ctx.fillText(s.valve_id, mx - 7, my - 10);
    }
  }

  // 节点
  for (const n of state.nodes) {
    const p = state.layout.get(n.id);
    let color = COLOR[n.role] || COLOR.normal;
    const st = nodeStatus.get(n.id);
    if (st === "disconnected") color = "#b9c2cb";
    ctx.beginPath();
    ctx.arc(p.x, p.y, 9, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = "#22303f";
    ctx.font = "bold 12px sans-serif";
    ctx.fillText(n.id, p.x + 11, p.y - 8);
    if (n.label && n.label !== n.id) {
      ctx.font = "10px sans-serif";
      ctx.fillStyle = "#5a6b7c";
      ctx.fillText(n.label, p.x + 11, p.y + 5);
    }
  }
}

// ---------- 启动 ----------
(async function init() {
  try {
    const net = await api("GET", "/api/network");
    applyNetwork(net);
    await loadHistory();
  } catch (err) {
    showErrors([err.message]);
  }
  window.addEventListener("resize", drawCanvas);
})();
