/* AfterPilot console: zero-build vanilla JS. Polls API; admin calls carry X-Admin-Token. */
const $ = (s) => document.querySelector(s);
const tokenEl = $('#token');
tokenEl.value = localStorage.getItem('ap_token') || 'dev-admin-token';
tokenEl.onchange = () => localStorage.setItem('ap_token', tokenEl.value);
const H = () => ({ 'Content-Type': 'application/json', 'X-Admin-Token': tokenEl.value });
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: H(), ...opts });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const st = (s) => `<span class="st ${esc(s)}">${esc(s)}</span>`;
const fmt = (iso) => (iso || '').replace('T', ' ').slice(0, 19);

/* tabs */
document.querySelectorAll('#tabs button').forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll('#tabs button').forEach((x) => x.classList.toggle('on', x === b));
    document.querySelectorAll('main section').forEach((s) => s.classList.toggle('on', s.id === 'tab-' + b.dataset.tab));
    refresh(b.dataset.tab);
  };
});

/* chat */
let ticketId = null, pollTimer = null;
async function loadSpecs() {
  const specs = await api('/api/admin/specs');
  $('#spec').innerHTML = specs.map((s) => `<option>${esc(s.name)}</option>`).join('');
}
async function sendMsg(text) {
  const body = { customer_id: $('#customer').value, message: text, spec: $('#spec').value || undefined };
  if (ticketId) body.ticket_id = ticketId;
  const r = await api('/api/chat', { method: 'POST', body: JSON.stringify(body) });
  ticketId = r.ticket_id;
  startPoll();
}
function startPoll() {
  clearInterval(pollTimer);
  const tick = async () => {
    if (!ticketId) return;
    const v = await api('/api/tickets/' + ticketId);
    $('#ticket-meta').innerHTML = `${esc(v.ticket.id.slice(0, 8))} · ${st(v.ticket.status)} · ${esc(v.ticket.type || '-')} · ${esc(v.ticket.release_mode)}/${esc(v.ticket.prompt_version)} · ¥${v.cost.total_cost}`;
    $('#transcript').innerHTML = v.messages.map((m) => `<div class="bubble ${esc(m.role)}">${esc(m.content)}</div>`).join('');
    $('#transcript').scrollTop = 1e9;
    if (['RESOLVED', 'CLOSED', 'ESCALATED', 'WAIT_APPROVAL'].includes(v.ticket.status)) clearInterval(pollTimer);
  };
  tick();
  pollTimer = setInterval(tick, 1500);
}
$('#send').onclick = () => { const t = $('#msg').value.trim(); if (t) { $('#msg').value = ''; sendMsg(t); } };
$('#msg').onkeydown = (e) => { if (e.key === 'Enter') $('#send').click(); };
$('#new-ticket').onclick = () => { ticketId = null; clearInterval(pollTimer); $('#transcript').innerHTML = ''; $('#ticket-meta').textContent = '新工单'; };
document.querySelectorAll('.quick button').forEach((b) => { b.onclick = () => sendMsg(b.dataset.msg); });
$('#fb-good').onclick = () => ticketId && api(`/api/tickets/${ticketId}/feedback`, { method: 'POST', body: JSON.stringify({ value: 'good' }) }).then(startPoll);
$('#fb-bad').onclick = () => ticketId && api(`/api/tickets/${ticketId}/feedback`, { method: 'POST', body: JSON.stringify({ value: 'bad' }) }).then(startPoll);

/* queue */
async function loadQueue() {
  const rows = await api('/api/admin/tickets?status=' + encodeURIComponent($('#q-status').value));
  $('#q-table tbody').innerHTML = rows.map((t) => `<tr><td>${esc(t.id.slice(0, 8))}</td><td>${esc(t.customer_id)}</td><td>${esc(t.type || '-')}</td><td>${st(t.status)}</td><td>${esc(t.release_mode)}/${esc(t.version)}</td><td>${fmt(t.created_at)}</td><td><button class="ghost" data-open="${t.id}">查看</button></td></tr>`).join('');
  document.querySelectorAll('[data-open]').forEach((b) => { b.onclick = () => { ticketId = b.dataset.open; document.querySelector('[data-tab="chat"]').click(); startPoll(); }; });
}
$('#q-refresh').onclick = loadQueue;
$('#q-status').onchange = loadQueue;

/* approvals */
async function loadApprovals() {
  const rows = await api('/api/admin/approvals?status=pending');
  $('#appr-count').textContent = rows.length;
  $('#appr-list').innerHTML = rows.map((a) => `<div class="item"><div class="head"><b>${esc(a.kind)}</b> ${st(a.status)} <span class="muted">ticket ${esc(a.ticket_id.slice(0, 8))} · ${fmt(a.created_at)}</span></div><pre>${esc(JSON.stringify(a.payload, null, 1))}</pre><div class="row"><input placeholder="审批理由" id="reason-${a.id}"><button data-appr="${a.id}" data-d="approved">批准</button><button class="ghost" data-appr="${a.id}" data-d="rejected">拒绝</button></div></div>`).join('') || '<p class="muted">暂无待审批</p>';
  document.querySelectorAll('[data-appr]').forEach((b) => {
    b.onclick = async () => {
      const reason = ($('#reason-' + b.dataset.appr) || {}).value || '';
      await api(`/api/admin/approvals/${b.dataset.appr}/decide`, { method: 'POST', body: JSON.stringify({ decision: b.dataset.d, reason }) });
      loadApprovals();
    };
  });
}

/* traces */
async function loadRuns() {
  const tid = $('#trace-ticket').value.trim();
  const rows = await api('/api/admin/runs' + (tid ? '?ticket_id=' + tid : ''));
  $('#runs-table tbody').innerHTML = rows.map((r) => `<tr><td>${esc(r.id.slice(0, 8))}</td><td>${esc(r.ticket_id.slice(0, 8))}</td><td>${st(r.status)}</td><td>${r.step_idx}</td><td>${esc(r.version)}</td><td>${r.tokens_in}/${r.tokens_out}</td><td>${r.cost}</td><td><button class="ghost" data-trace="${r.trace_id}">trace</button></td></tr>`).join('');
  document.querySelectorAll('[data-trace]').forEach((b) => { b.onclick = async () => {
    const spans = await api('/api/admin/traces/' + b.dataset.trace);
    $('#span-tree').innerHTML = spans.map((s) => `<div class="spanrow"><span class="kd">${esc(s.kind)}</span><span class="nm">${esc(s.name)}</span><span class="muted">${s.end_ms ? Math.round(s.end_ms - s.start_ms) + 'ms' : 'open'}</span><span class="muted">${s.tokens_in || s.tokens_out ? s.tokens_in + '/' + s.tokens_out + ' tok ¥' + s.cost : ''}</span><span class="muted">${esc(JSON.stringify(s.attrs).slice(0, 160))}</span></div>`).join('');
  }; });
}
$('#trace-load').onclick = loadRuns;

/* evals */
document.querySelectorAll('[data-suite]').forEach((b) => {
  b.onclick = async () => {
    $('#eval-status').textContent = '运行中…';
    const r = await api('/api/admin/evals/run?suite=' + b.dataset.suite, { method: 'POST' });
    $('#eval-status').textContent = `${r.suite}: pass_rate=${r.metrics.pass_rate} passed=${r.passed}`;
    $('#eval-detail').textContent = JSON.stringify(r.details.filter((d) => !d.pass), null, 1) || '[]';
    loadEvals();
  };
});
async function loadEvals() {
  const rows = await api('/api/admin/evals/runs');
  $('#eval-table tbody').innerHTML = rows.map((r) => `<tr><td>${esc(r.suite)}</td><td>${esc(r.version)}</td><td>${esc(r.provider)}</td><td>${r.metrics.n}</td><td>${r.metrics.pass_rate}</td><td>${r.passed ? '✅' : '❌'}</td><td>${fmt(r.created_at)}</td></tr>`).join('');
}

/* costs */
async function loadCosts() {
  const r = await api('/api/admin/costs');
  const s = r.summary;
  $('#cost-summary').innerHTML = [['模型成本¥', s.model_cost_total], ['人工成本¥', s.human_cost_total], ['有效解决数', s.effective_resolutions], ['单有效解决¥', s.cost_per_effective_resolution ?? '-']].map(([k, v]) => `<div class="card"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');
  $('#cost-table tbody').innerHTML = r.tickets.map((t) => `<tr><td>${esc(t.ticket_id.slice(0, 8))}</td><td>${t.model_cost}</td><td>${t.human_cost}</td><td>${t.total_cost}</td><td>${t.tool_calls}</td><td>${t.handoffs}</td></tr>`).join('');
}

/* handoffs closed loop */
async function loadHandoffs() {
  const rows = await api('/api/admin/handoffs');
  $('#handoff-list').innerHTML = rows.map((h) => `<div class="item"><div class="head"><b>${esc(h.reason)}</b> ${h.reviewed ? st('done') : st('pending')} <span class="muted">ticket ${esc(h.ticket_id.slice(0, 8))}</span></div><pre>${esc(JSON.stringify(h.snapshot, null, 1).slice(0, 900))}</pre>${h.reviewed ? '' : `<div class="row"><textarea id="corr-${h.id}" rows="2" placeholder='纠正期望 JSON，如 {"expect":{"reply_contains":["停滞"]}}'></textarea><button data-rev="${h.id}" data-a="1">采纳为评测用例</button><button class="ghost" data-rev="${h.id}" data-a="0">退役</button></div>`}</div>`).join('') || '<p class="muted">暂无转人工</p>';
  document.querySelectorAll('[data-rev]').forEach((b) => {
    b.onclick = async () => {
      let correction = null;
      try { correction = JSON.parse(($('#corr-' + b.dataset.rev) || {}).value || 'null'); } catch (e) { alert('纠正 JSON 无效'); return; }
      await api(`/api/admin/handoffs/${b.dataset.rev}/review`, { method: 'POST', body: JSON.stringify({ approve: b.dataset.a === '1', correction }) });
      loadHandoffs();
    };
  });
}

/* kb */
async function loadKb() {
  const rows = await api('/api/admin/kb');
  $('#kb-list').innerHTML = rows.map((d) => `<div class="item"><div class="head"><b>${esc(d.title)}</b><span class="muted">${esc(d.slug)} v${d.version}</span></div><pre>${esc(d.body)}</pre></div>`).join('');
}
$('#kb-save').onclick = async () => {
  await api('/api/admin/kb', { method: 'POST', body: JSON.stringify({ slug: $('#kb-slug').value, title: $('#kb-title').value, body: $('#kb-body').value }) });
  loadKb();
};

/* releases */
async function loadReleases() {
  const specs = await api('/api/admin/specs');
  $('#rel-current').innerHTML = specs.map((s) => `<div class="card"><div class="v">${esc(s.release.version)}</div><div class="k">${esc(s.name)} · ${esc(s.release.mode)} ${s.release.percent}%</div></div>`).join('');
  const rows = await api('/api/admin/releases');
  $('#rel-table tbody').innerHTML = rows.map((r) => `<tr><td>${esc(r.version)}</td><td>${esc(r.mode)}</td><td>${r.percent}</td><td>${r.active ? '✅' : ''}</td><td>${fmt(r.created_at)}</td></tr>`).join('');
}
$('#rel-set').onclick = async () => {
  try {
    const r = await api('/api/admin/releases', { method: 'POST', body: JSON.stringify({ version: $('#rel-version').value, mode: $('#rel-mode').value, percent: +$('#rel-percent').value }) });
    $('#rel-msg').textContent = JSON.stringify(r);
  } catch (e) { $('#rel-msg').textContent = '发布被拒: ' + e.message; }
  loadReleases();
};
$('#rel-guard').onclick = async () => { $('#rel-msg').textContent = JSON.stringify(await api('/api/admin/guardrails/check', { method: 'POST' })); loadReleases(); };
$('#rel-shadow').onclick = async () => { const r = await api('/api/admin/shadow', { method: 'POST', body: JSON.stringify({ customer_id: 'c1', message: 'o1004 我要退货' }) }); $('#rel-msg').textContent = 'shadow ticket ' + r.ticket_id; };

/* boot + periodic badges */
async function refresh(tab) {
  try {
    if (tab === 'queue') await loadQueue();
    if (tab === 'approvals') await loadApprovals();
    if (tab === 'traces') await loadRuns();
    if (tab === 'evals') await loadEvals();
    if (tab === 'costs') await loadCosts();
    if (tab === 'handoffs') await loadHandoffs();
    if (tab === 'kb') await loadKb();
    if (tab === 'releases') await loadReleases();
  } catch (e) { console.warn(e); }
}
(async () => {
  try {
    const h = await api('/health');
    $('#health').textContent = `provider=${h.provider} db=${h.db}`;
    await loadSpecs();
    loadApprovals();
    setInterval(() => loadApprovals().catch(() => {}), 5000);
  } catch (e) { $('#health').textContent = 'API 不可达'; }
})();