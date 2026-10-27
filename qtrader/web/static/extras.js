"use strict";
// 알림(윈도우·화면), 내 계좌 추가 정보(실현 손익·세금, 업종 쏠림, 마감 요약, 가격 알림), 설정 4단계, 지난 회의 비교

// 알림: 서버 기록 중 'notice' 를 15초마다 확인해 화면 알림 + 윈도우 알림
const ntf = { last: null };
async function pollNotices() {
  try {
    const rows = await api("/api/notices?after=" + (ntf.last ?? 0));
    if (!rows.length) { if (ntf.last == null) ntf.last = 0; return; }
    const first = ntf.last == null;
    ntf.last = Math.max(ntf.last ?? 0, ...rows.map((r) => r.id));
    if (first) return; // 켤 때 예전 알림은 다시 띄우지 않는다
    rows.slice().reverse().forEach((r) => {
      const text = r.message.split("\n")[0];
      toast(text, r.source === "exit" ? "shield-alert" : r.source === "alert" ? "bell-ring" : "bell");
      if (window.Notification && Notification.permission === "granted") {
        try { new Notification("QTrader", { body: r.message.slice(0, 300), tag: "qt" + r.id }); } catch {}
      }
    });
  } catch {}
}
pollNotices();
setInterval(pollNotices, 15000);

// 내 계좌 추가 정보
window.loadDashExtras = async () => {
  const m = dash.market;
  const [rz, conc, rep, alerts] = await Promise.all([
    api("/api/account/realized").catch(() => null), api("/api/account/concentration").catch(() => ({})),
    api("/api/report?market=" + m).catch(() => null), api("/api/alerts").catch(() => []),
  ]);
  if (rz) {
    const r = m === "KR" ? rz.kr : rz.us;
    const best = Object.entries(r.by_symbol).sort((a, b) => b[1] - a[1]);
    $("#realized").innerHTML = `<div class="rz">
      <div><span class="note">${rz.year}년 ${MARKET_NAME[m]} 실현 손익</span><b class="${cls(r.total)}">${money(r.total, m)}</b>
        <em class="note">매도 ${r.trades}번${r.win_rate == null ? "" : ` · 이익 본 매도 ${Math.round(r.win_rate * 100)}%`}</em></div>
      ${m === "US" ? `<div><span class="note">해외주식 양도세 추정</span><b>${money(rz.us_tax_krw, "KR")}</b>
        <em class="note">원화 환산 ${money(rz.us_krw, "KR")} − 공제 250만원 → 과세 ${money(rz.us_taxable_krw, "KR")} × 22%</em></div>`
        : `<div><span class="note">국내 상장주식 양도세</span><b>0원</b><em class="note">대주주가 아니면 비과세 (거래세는 수수료에 포함)</em></div>`}
    </div>
    ${best.length ? `<div class="rz-list">${best.slice(0, 6).map(([s, v]) => `<span>${esc(s)} <b class="${cls(v)}">${money(v, m)}</b></span>`).join("")}</div>` : ""}
    <p class="note">${esc(rz.note)}</p>`;
  }
  const c = conc[m];
  $("#conc-warn").innerHTML = c?.warnings?.length
    ? `<div class="warn-box"><i data-lucide="alert-triangle"></i><div><b>한쪽으로 쏠려 있어요</b>${c.warnings.map((w) => `<div>${esc(w)}</div>`).join("")}
       <div class="note">총자산 대비 업종 비중: ${c.sectors.map((s) => `${esc(s.name)} ${Math.round(s.weight * 100)}%`).join(" · ")}</div></div></div>` : "";
  if (rep?.text) $("#day-report").innerHTML = `<pre class="report-pre">${esc(rep.text)}</pre>`;
  renderAlerts(alerts);
  icons();
};

function renderAlerts(list) {
  $("#alert-list").innerHTML = list.length ? `<div class="alerts">${list.map((a, i) => `
    <div class="alert-row">${logo(a.symbol, a.name || a.symbol, 24)}<b>${esc(a.name || a.symbol)}</b>
      <span>${Number(a.price).toLocaleString()} ${a.op === "above" ? "이상 ▲" : "이하 ▼"}</span>
      <button data-del="${i}" title="삭제"><i data-lucide="x"></i></button></div>`).join("")}</div>`
    : `<p class="note">알림이 없습니다. 관심 종목이 원하는 가격에 오면 알려 드릴게요.</p>`;
  icons();
}
$("#alert-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const list = await api("/api/alerts", { method: "POST", body: { symbol: $("#al-sym").value, op: $("#al-op").value, price: Number($("#al-px").value) } });
    $("#al-sym").value = ""; $("#al-px").value = "";
    renderAlerts(list);
    toast("가격 알림을 추가했어요", "bell-ring");
  } catch (err) { toast(err.message, "alert-triangle"); }
});
$("#alert-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-del]");
  if (b) renderAlerts(await api("/api/alerts/" + b.dataset.del, { method: "DELETE" }));
});

// 설정 4단계
function ntfState() {
  const p = window.Notification ? Notification.permission : "unsupported";
  $("#ntf-state").textContent = { granted: "윈도우 알림 켜짐", denied: "브라우저에서 알림이 막혀 있습니다 (주소창 자물쇠 → 알림 허용)", default: "", unsupported: "이 브라우저는 알림을 지원하지 않습니다" }[p];
}
$("#ntf-perm").onclick = async () => { if (window.Notification) await Notification.requestPermission(); ntfState(); };
$("#ntf-test").onclick = async () => {
  try {
    const r = await api("/api/notify/test", { method: "POST" });
    toast(r.telegram ? "테스트 알림을 보냈어요 (화면 + 텔레그램)" : "테스트 알림을 보냈어요. 15초 안에 화면 알림이 뜹니다", "send");
  } catch (e) { toast(e.message, "alert-triangle"); }
};
$("#sys-autostart").onchange = async (e) => {
  try { await api("/api/system/autostart", { method: "POST", body: { on: e.target.checked } }); toast(e.target.checked ? "윈도우 시작 시 자동 실행을 켰어요" : "자동 실행을 껐어요"); }
  catch (err) { e.target.checked = !e.target.checked; toast(err.message, "alert-triangle"); }
};
async function loadSystem() {
  ntfState();
  const [sys, cost] = await Promise.all([api("/api/system").catch(() => null), api("/api/ai/cost").catch(() => null)]);
  if (sys) { $("#sys-autostart").checked = sys.autostart; $("#sys-autostart").disabled = !sys.windows; }
  if (cost) $("#ai-cost-now").textContent = `이번 달 사용 ${money(cost.month_krw, "KR")}` + (cost.cap_krw ? ` / 상한 ${money(cost.cap_krw, "KR")}` : "");
}
document.querySelector('[data-tab="cfg"]')?.addEventListener("click", loadSystem);
loadSystem();

// 지난 회의와 비교 (같은 종목)
const _renderResult = window.renderResult;
const cmp = { key: null };
window.renderResult = (run) => {
  _renderResult(run);
  if (!run || run.status !== "done") { $("#compare").classList.add("hidden"); return; }
  if (cmp.key === run.id) return;
  cmp.key = run.id;
  loadCompare(run);
};
async function loadCompare(run) {
  const box = $("#compare");
  const rows = await api("/api/ai/compare/" + encodeURIComponent(run.symbol)).catch(() => []);
  if (rows.length < 2) { box.classList.add("hidden"); return; }
  const m = run.market;
  const tone = (r) => ({ Buy: "up", Overweight: "up", Sell: "down", Underweight: "down" })[r] || "";
  box.classList.remove("hidden");
  box.innerHTML = `<h3><i data-lucide="git-compare"></i> 지난 회의와 비교 <small>${esc(run.name || run.symbol)} · 같은 종목의 최근 결론이 어떻게 바뀌었는지</small></h3>
    <table class="cmp"><thead><tr><th>날짜</th><th>결론</th><th class="r">확신</th><th class="r">당시 가격</th><th class="r">손절</th><th class="r">익절</th><th>체크리스트</th><th>요지</th></tr></thead>
    <tbody>${rows.map((r) => `<tr class="${r.id === run.id ? "cur" : ""}" data-run="${esc(r.id)}">
      <td>${esc(r.date)}${r.id === run.id ? " <span class='badge'>지금</span>" : ""}</td>
      <td><b class="${tone(r.rating)}">${esc(r.rating_ko || r.rating || "-")}</b></td>
      <td class="r">${r.confidence == null ? "-" : Math.round(r.confidence * 100) + "%"}</td>
      <td class="r">${r.plan?.entry ? money(r.plan.entry, m) : "-"}</td>
      <td class="r down">${r.plan?.stop ? money(r.plan.stop, m) : "-"}</td>
      <td class="r up">${r.plan?.take ? money(r.plan.take, m) : "-"}</td>
      <td>${r.total ? `${r.passed}/${r.total} 통과` : "-"}</td>
      <td class="note">${esc(r.summary)}</td></tr>`).join("")}</tbody></table>
    <p class="note">줄을 누르면 그 회의 결과를 엽니다. 결론이 자주 뒤집힌다면 신호가 약하다는 뜻이니 비중을 줄이는 편이 안전합니다.</p>`;
  box.querySelectorAll("[data-run]").forEach((tr) => tr.onclick = () => window.showRunById && window.showRunById(tr.dataset.run));
  icons();
}

// 오늘 자동매매 흐름: 회의 -> 체크리스트 -> 승인 -> 주문 -> 장중 감시 -> 마감 요약
async function renderFlow() {
  const s = state.status, c = state.config;
  if (!s || !$("#flow")) return;
  const m = dash.market, mi = s.markets[m];
  const [runs, appr, orders, plans] = await Promise.all([api("/api/ai/runs?limit=60").catch(() => []),
    api("/api/approvals?status=pending").catch(() => []), api("/api/orders?limit=80").catch(() => []), api("/api/plans").catch(() => [])]);
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: m === "KR" ? "Asia/Seoul" : "America/New_York" });
  const isM = (sym) => (m === "KR") === /^\d/.test(sym);
  const nRuns = runs.filter((r) => r.trade_date === today && isM(r.symbol)).length;
  const nOrd = orders.filter((o) => o.trade_date === today && o.market === m && o.status !== "dry_run").length;
  const nPlan = plans.filter((p) => p.market === m).length;
  const nAppr = appr.filter((a) => a.market === m).length;
  const ai = mi?.strategy === "ai", rt = mi?.run_time || "-", needOk = !!c?.ai?.require_approval;
  // [이름, 끝났나, 지금 설명]
  const steps = [
    ["AI 회의", !ai || nRuns > 0, ai ? `${rt} 전에 종목마다 AI 회의를 엽니다` : "규칙 전략이라 회의는 건너뜁니다"],
    ["매수 점검", nOrd > 0 || nAppr > 0, `${rt}에 체크리스트를 모두 넘은 종목만 고릅니다`],
    ...(needOk ? [["내 승인", nOrd > 0 && nAppr === 0, `승인 대기 ${nAppr}건이 있어요. 근거를 보고 승인해 주세요`]] : []),
    ["주문", nOrd > 0, s.mode === "paper" ? "가상 계좌로 주문합니다 (돈은 움직이지 않아요)" : "증권사로 실제 주문합니다"],
    ["손절·익절 감시", nPlan > 0 && !mi?.open, `장중 2분마다 ${nPlan}종목의 손절가·익절가를 확인합니다`],
    ["마감 요약", false, "장 마감 5분 뒤 오늘 한 일을 정리해 알려 드립니다"],
  ];
  let cur = steps.findIndex((x) => !x[1]);
  if (cur < 0) cur = steps.length - 1;
  const live = s.auto_trading;
  $("#flow").innerHTML = `<div class="fl-now ${live ? "on" : ""}"><span class="fl-dot"></span>
      <div><b>${live ? `지금: ${esc(steps[cur][0])}` : "자동매매가 꺼져 있어요"}</b>
      <span>${live ? esc(steps[cur][2]) : `켜면 매 거래일 아래 순서로 혼자 움직입니다. 다음 실행 ${(mi?.next_run || "").replace("T", " ").slice(5, 16)}`}</span></div>
      ${!live && s.mode === "paper" ? `<button class="primary" id="flow-start"><i data-lucide="play"></i> 가상매매로 시작</button>` : ""}</div>
    <ol class="fl-bar">${steps.map(([name, done], k) => `<li class="${done && k < cur ? "done" : k === cur && live ? "now" : ""}">
      <i>${done && k < cur ? "✓" : k + 1}</i><span>${esc(name)}</span></li>`).join("")}</ol>
    <div class="fl-stats"><span>오늘 회의 <b>${nRuns}</b></span><span>주문 <b>${nOrd}</b></span>${needOk ? `<span>승인 대기 <b>${nAppr}</b></span>` : ""}<span>감시 중 <b>${nPlan}</b>종목</span></div>`;
  $("#flow-sub").textContent = `${MODE_NAME[s.mode]} · ${MARKET_NAME[m]}`;
  $("#flow-cta").innerHTML = "";
  if ($("#flow-start")) $("#flow-start").onclick = () => $("#auto-btn").click();
  icons();
}
const _loadDashExtras = window.loadDashExtras;
window.loadDashExtras = async () => { await Promise.all([_loadDashExtras(), renderFlow()]); };

// 왜 사고팔았나요
async function renderWhy() {
  const rows = await api("/api/trades/why").catch(() => []);
  const m = dash.market;
  const list = rows.filter((r) => r.market === m);
  $("#why-list").innerHTML = list.length ? list.map((t) => {
    const buy = t.side === "buy", r = t.run, cur = m;
    const exit = t.strategy === "ai-exit";
    const tag = exit ? (t.summary.startsWith("손절") ? "손절" : "익절") : buy ? "매수" : "매도";
    const reasons = r?.reasons?.length ? r.reasons : t.detail.filter(Boolean);
    return `<article class="why ${buy ? "buy" : "sell"}">
      <header>${logo(t.symbol, r?.name || t.symbol, 32)}<div><b>${esc(r?.name || t.symbol)}</b><span class="note">${esc(t.date)} · ${t.qty.toLocaleString()}주 @ ${money(t.price, cur)}${t.status === "submitted" ? " · 체결 대기" : ""}</span></div>
        <span class="why-tag ${buy ? "up" : "down"}">${tag}</span></header>
      <p class="why-head">${esc(r?.headline || t.summary)}</p>
      ${reasons.length ? `<ul>${reasons.slice(0, 4).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <div class="why-chips">
        ${r ? `<span>AI ${esc(r.rating_ko || r.rating)} · 확신 ${Math.round((r.confidence || 0) * 100)}%</span>` : ""}
        ${r?.rules ? `<span class="ok">체크리스트 ${r.passed.length}/${r.rules} 통과</span>` : ""}
        ${r?.plan ? `<span class="down">손절 ${money(r.plan.stop, cur)}</span><span class="up">익절 ${money(r.plan.take, cur)}</span>` : ""}
      </div>
      ${r?.worry ? `<p class="note why-worry">⚠ 걱정되는 점: ${esc(r.worry)}</p>` : ""}
      ${r ? `<button class="why-open" data-run="${esc(r.id)}">회의 전체 보기 <i data-lucide="chevron-right"></i></button>` : ""}
    </article>`;
  }).join("") : `<div class="empty"><i data-lucide="message-square-quote"></i><p>아직 자동매매 주문이 없어요</p>
    <span class="note">주문이 나가면 무엇을, 왜, 어떤 근거로 샀는지 여기에 정리됩니다.</span></div>`;
  $$("#why-list [data-run]").forEach((b) => b.onclick = () => { openTab("office"); window.showRunById && showRunById(b.dataset.run); });
  icons();
}
const _loadDashExtras2 = window.loadDashExtras;
window.loadDashExtras = async () => { await Promise.all([_loadDashExtras2(), renderWhy()]); };
