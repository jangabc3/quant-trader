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
  if (rep) renderDayReport(rep, m);
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
const aiCmp = { key: null };
window.renderResult = (run) => {
  _renderResult(run);
  if (!run || run.status !== "done") { $("#compare").classList.add("hidden"); return; }
  if (aiCmp.key === run.id) return;
  aiCmp.key = run.id;
  loadRunCompare(run);
};
async function loadRunCompare(run) {
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
// renderFlow 는 acct.js (하루 시간표)
const _loadDashExtras = window.loadDashExtras;
window.loadDashExtras = async () => { await Promise.all([_loadDashExtras(), renderFlow()]); };

// renderWhy 는 acct.js (왜 사고팔았나요 타임라인)
const _loadDashExtras2 = window.loadDashExtras;
window.loadDashExtras = async () => { await Promise.all([_loadDashExtras2(), renderWhy()]); };

// 오늘 AI 가 고른 회의 후보 (AI 사무실 위)
const pk = { market: "KR" };
// 후보를 어떻게 고르는지 (한눈에)
const PICK_STEPS = [
  ["filter", "1. 넓게 모으기", "보유·관심 + 성장·기술주 + 1·3개월 급상승 + 토스 인기 + 거래대금·거래량 상위 + 강세 섹터 주도주 (최대 120종목)"],
  ["shield-check", "2. 숫자로 거르기", "200일선 위 · 12개월 모멘텀 양수 · 과열(RSI)·변동성 상한 안쪽만"],
  ["bar-chart-3", "3. 점수 매기기", "모멘텀 35 · 52주 고가 근접 25 · 저변동 20 · 거래량 증가 10 · 단기 급등 감점 10"],
  ["file-search", "4. 공시·재무 점검", "유상증자·전환사채·감자·횡령 공시가 있으면 제외, 적자+매출 감소면 감점"],
];
const FACT = [["mom", "모멘텀", (v) => Math.max(0, Math.min(1, (v + 0.2) / 0.8))], ["near_high", "52주 고가", (v) => Math.max(0, Math.min(1, (v - 0.6) / 0.4))],
  ["vol", "안정성", (v) => Math.max(0, Math.min(1, 1 - v / 0.8))], ["vol_ratio", "거래량", (v) => Math.max(0, Math.min(1, (v - 0.5) / 1.5))]];
// '오늘 AI 회의' 판: 지금 자동으로 무엇을 하는지 한 줄 + 후보마다 단계 (대기 → 회의 중 → 보류/샀어요/…)
const TD_STATE = { wait: ["회의 대기", "clock"], meeting: ["회의 중", "loader-2"], bought: ["샀어요", "shopping-cart"], approval: ["승인 대기", "hand"],
  hold: ["보류", "pause-circle"], nobuy: ["안 삼", "shield-alert"], sell: ["매도 의견", "trending-down"], held: ["보유 중", "wallet"], error: ["회의 실패", "alert-triangle"] };
const TD_TONE = { busy: "busy", wait: "wait", done: "done", warn: "warn", off: "off" };
async function loadPicks(refresh = false) {
  const box = $("#picks"), head = $("#today-head");
  if (!box) return;
  const first = !box.dataset.ready;
  if (first || refresh) box.innerHTML = `<div class="skeleton" style="height:44px"></div>`;
  const [res, plan] = await Promise.all([
    api(`/api/picks?market=${pk.market}&stats=true${refresh ? "&refresh=true" : ""}`).catch((e) => ({ error: e.message })),
    api(`/api/ai/today?market=${pk.market}`).catch(() => null)]);
  if (res.error) { box.innerHTML = `<p class="note">${esc(res.error)}</p>`; renderPicksWhy(null); return; }
  renderPicksWhy(res.excluded);
  if (plan && head) {
    const pctUsed = plan.cap ? Math.min(100, plan.used / plan.cap * 100) : 0;
    const nx = plan.next_at && plan.tone === "busy" ? new Date(plan.next_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false }) : "";
    head.className = "td-head " + (TD_TONE[plan.tone] || "");
    head.innerHTML = `<span class="td-dot"></span><p>${esc(plan.headline)}</p>
      <div class="td-meta"><span>오늘 회의 <b>${plan.used}/${plan.cap}</b><i class="td-bar"><b style="width:${pctUsed}%"></b></i></span>
        ${nx ? `<span>다음 회의 <b>${nx}</b>쯤</span>` : ""}${plan.session ? `<span class="td-sess">${esc(plan.session)}</span>` : ""}
        ${!plan.auto && plan.tone === "off" && plan.cap ? `<button class="primary td-auto" id="td-auto"><i data-lucide="play"></i> 자동매매 켜기</button>` : ""}</div>`;
  }
  const rows = plan?.items?.length ? plan.items : (res.items || []).map((p) => ({ ...p, state: p.held ? "held" : p.met ? "hold" : "wait", note: "" }));
  const origin = (r) => (r.reason || "").split(" · ")[0];
  box.innerHTML = rows.length ? `<div class="pk-list">${rows.map((p, i) => {
    const sc = Math.round((p.score || 0) * 100);
    const [label, ic] = TD_STATE[p.state] || [p.state, "dot"];
    const tip = (p.reason || "") + (p.factors ? `\n모멘텀 ${pct(p.factors.mom)} · 52주 고가의 ${Math.round((p.factors.near_high || 0) * 100)}% · 변동성 ${Math.round((p.factors.vol || 0) * 100)}% · 거래량 ${(p.factors.vol_ratio || 1).toFixed(1)}배` : "");
    const act = p.state === "wait" || p.state === "held" ? `<button class="pk-go" data-meet="${esc(p.symbol)}" title="이 종목을 지금 AI 회의">지금 회의</button>`
      : p.run_id ? `<button class="pk-go ghost" data-run="${esc(p.run_id)}" title="회의 내용 보기">결과</button>` : "";
    return `<div class="pk-row" data-open="${esc(p.symbol)}" title="${esc(tip)}">
      <span class="pk-n">${i + 1}</span>${logo(p.symbol, p.name, 26)}
      <span class="pk-nm"><b>${esc(p.name)}</b><em title="${esc(p.note || "")}">${esc(["nobuy", "error", "approval"].includes(p.state) && p.note ? p.note : origin(p))}</em></span>
      <span class="pk-sc"><i><b style="width:${Math.max(4, sc)}%"></b></i><strong>${sc}</strong></span>
      <span class="pk-st st-${esc(p.state)}"><i data-lucide="${ic}"></i>${label}</span>${act}</div>`;
  }).join("")}</div>`
    : `<p class="note pk-none">오늘은 '사도 되는 조건'을 통과한 종목이 없어요. 내리막·과열 종목뿐이라 회의를 쉬어요 (쉬는 날도 손실을 줄이는 방법이에요). 새로 통과한 종목이 생기면 바로 회의해요.</p>`;
  if (first) {
    box.dataset.ready = "1";
    animateIn("#picks .pk-row", { step: 30 });
    if (!REDUCED && window.anime) anime({ targets: "#picks .pk-sc b", scaleX: [0, 1], duration: 700, delay: anime.stagger(30), easing: "easeOutCubic" });
  }
  icons();
}
document.addEventListener("click", (e) => {
  const m = e.target.closest("[data-meet]"), r = e.target.closest("#picks [data-run]");
  if (m) { e.stopPropagation(); window.liveOff && window.liveOff(); window.startMeeting && window.startMeeting(m.dataset.meet); }
  else if (r) { e.stopPropagation(); window.liveOff && window.liveOff(); window.showRunById && window.showRunById(r.dataset.run); $("#office-wrap")?.scrollIntoView({ behavior: "smooth", block: "start" }); }
  else if (e.target.closest("#td-auto")) { e.stopPropagation(); $("#auto-btn")?.click(); setTimeout(() => loadPicks(), 2500); }
}, true);
setInterval(() => { if (state.tab === "office" && !document.hidden) loadPicks(); }, 15000);
// 왜 후보가 적은지: 규칙마다 몇 종목이 빠졌는지 (한 종목이 여러 규칙에 걸리면 각각 센다)
function renderPicksWhy(st) {
  const line = $("#picks-why"), detail = $("#picks-why-detail");
  if (!line || !detail) return;
  if (!st || !st.pool) { line.innerHTML = ""; detail.innerHTML = ""; return; }
  const rules = [
    ["trend", "200일선 아래", "1년 가까이 내리막인 종목"],
    ["momentum", "모멘텀 음수", "최근 1년 수익률이 마이너스"],
    ["rsi", "과열 (RSI)", "짧은 기간에 너무 많이 올라 되돌림이 걱정되는 종목"],
    ["vol", "변동성", "하루하루 오르내림이 너무 큰 종목"],
    ["illiquid", "거래가 너무 적음", "하루 거래대금이 너무 작아 원하는 값에 사고팔기 어려운 종목"],
    ["disclosure", "공시·재무", "유상증자·전환사채·횡령 같은 나쁜 공시가 있는 종목"],
    ["basic", "종목 기본 정보", "거래정지·레버리지·인버스·우선주·새로 상장한 종목"],
  ];
  const out = st.rules_out + (st.basic || 0) + (st.disclosure || 0);
  const passed = st.passed ?? Math.max(0, st.pool - out - (st.no_data || 0));
  const sc = st.scan;
  const scanLine = sc?.running && sc.total ? ` <span class="pk-scan"><i class="spin"></i>국내 전체 ${Number(sc.total).toLocaleString()}종목을 훑는 중 (${Number(sc.done).toLocaleString()}종목째) · 끝나면 후보가 늘어나요</span>`
    : sc?.running ? ` <span class="pk-scan"><i class="spin"></i>국내 전체 종목 목록을 받는 중</span>` : "";
  line.innerHTML = `<p class="note pk-why">${st.full_scan ? `국내 전체 ${Number(st.full_scan).toLocaleString()}종목과 순위·관심 종목까지` : "오늘"} 살펴본 ${st.pool}종목 중 <b>${passed}종목</b>만 '사도 되는 조건'(오르는 추세 · 과열 아님 · 출렁임 적당 · 나쁜 공시 없음)을 통과해 회의 후보가 됐어요.
    ${st.no_data ? `상장한 지 얼마 안 되는 등 자료가 부족한 ${st.no_data}종목은 뺐어요. ` : ""}<button class="link" id="picks-why-btn">어떤 조건에 걸렸는지 보기</button>${scanLine}</p>`;
  detail.innerHTML = `<div class="pk-why-box"><b>오늘 빠진 종목 (규칙별)</b>
    <ul>${rules.map(([k, n, d]) => `<li class="${st[k] ? "" : "zero"}"><span>${n}<em>${d}</em></span><strong>${st[k] || 0}종목</strong></li>`).join("")}</ul>
    <p class="note">모은 ${st.pool}종목 → 차트 자료로 본 ${st.checked}종목 → 규칙 통과 ${st.passed}종목 → 오늘 회의 후보 ${st.picked}종목.
      한 종목이 규칙 여러 개에 걸리면 각각 세요.${st.weak_finance ? ` 적자이면서 매출이 줄어 점수를 깎은 종목 ${st.weak_finance}개.` : ""}
      ${st.no_data ? `자료가 모자란 ${st.no_data}종목은 아직 볼 수 없었어요 (키 없이 체험 중이면 일부 종목 자료만 있어요).` : ""}</p>
    <p class="note">기준은 <b>설정 → 고급 설정 → 사기 전 확인 규칙</b>에서 풀 수 있어요. 풀면 후보는 늘지만 위험한 종목도 같이 들어와요.</p></div>`;
}
document.addEventListener("click", (e) => {
  if (!e.target.closest("#picks-why-btn")) return;
  e.stopPropagation();
  $("#picks-how")?.classList.remove("hidden");
}, true);
// 기준 보기 (작은 팝오버)
document.addEventListener("click", (e) => {
  const b = e.target.closest("#picks-how-btn");
  const pop = $("#picks-how");
  if (b) { pop.classList.toggle("hidden"); return; }
  if (pop && !pop.classList.contains("hidden") && !e.target.closest("#picks-how")) pop.classList.add("hidden");
});
document.addEventListener("click", (e) => { if (e.target.closest("#picks-refresh")) loadPicks(true); });
$("#picks-mkt")?.addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  pk.market = b.dataset.v;
  $$("#picks-mkt button").forEach((x) => x.classList.toggle("on", x === b));
  loadPicks();
});
document.addEventListener("tab", (e) => { if (e.detail === "office") loadPicks(); });

// 오늘 마감 요약: 숫자 카드 + 최근 10일 자산 막대 + 오늘 거래 목록 + 감시 중 종목
function renderDayReport(rep, m) {
  const el = $("#day-report");
  const buys = rep.orders.filter((o) => o.side === "buy"), sells = rep.orders.filter((o) => o.side === "sell");
  const dr = rep.daily_return, yr = rep.realized_year || {};
  const hist = rep.history || [];
  const lo = Math.min(...hist.map((h) => h.equity)), hi = Math.max(...hist.map((h) => h.equity));
  const bars = hist.map((h, i) => {
    const prev = i ? hist[i - 1].equity : h.equity, up = h.equity >= prev;
    const ht = hi > lo ? 18 + (h.equity - lo) / (hi - lo) * 82 : 60;
    return `<div class="dr-bar ${up ? "up" : "down"}" style="--h:${ht}%" title="${esc(h.date)} ${money(h.equity, m)}"><i></i><span>${esc(h.date.slice(5))}</span></div>`;
  }).join("");
  el.className = "day-report";
  el.innerHTML = `<div class="dr-head"><span class="note">${esc(rep.date)} · ${rep.final ? "장 마감 확정" : "장중 실시간 (마감 5분 뒤 확정)"}</span></div>
    <div class="dr-kpis">
      <div class="dr-k big"><span>오늘 수익률</span><b class="${cls(dr)}" id="dr-ret">${dr == null ? "-" : pct(dr)}</b><em>${rep.equity ? "자산 " + money(rep.equity, m) : "자산 기록 전"}</em></div>
      <div class="dr-k"><span>매수</span><b class="up" id="dr-buy">${buys.length}</b><em>건</em></div>
      <div class="dr-k"><span>매도</span><b class="down" id="dr-sell">${sells.length}</b><em>건</em></div>
      <div class="dr-k"><span>올해 실현 손익</span><b class="${cls(yr.total)}">${money(yr.total || 0, m)}</b><em>${yr.trades || 0}번 매도</em></div>
    </div>
    ${hist.length > 1 ? `<div class="dr-chart"><span class="note">최근 ${hist.length}거래일 자산</span><div class="dr-bars">${bars}</div></div>` : ""}
    <div class="dr-lists">
      <div><h4>오늘 거래</h4>${rep.orders.length ? rep.orders.map((o) => `<div class="dr-row" data-open="${esc(o.symbol)}">
          ${logo(o.symbol, o.name || o.symbol, 26)}<b>${esc(o.name || o.symbol)}</b><span class="side ${o.side}">${o.side === "buy" ? "매수" : "매도"}</span>
          <span class="r">${o.qty.toLocaleString()}주 · ${money(o.price, m)}</span><em>${esc(String(o.message || "").split(" | ")[0])}</em></div>`).join("")
        : `<p class="note">오늘은 사고판 것이 없어요. 규칙을 넘는 종목이 없으면 쉬는 것도 전략입니다.</p>`}</div>
      <div><h4>내일도 지켜보는 종목</h4>${rep.plans.length ? rep.plans.map((p) => `<div class="dr-row">${logo(p.symbol, p.name || p.symbol, 26)}<b>${esc(p.name || p.symbol)}</b>
          <span class="r"><span class="down">손절 ${money(p.stop, m)}</span> · <span class="up">익절 ${money(p.take, m)}</span></span></div>`).join("")
        : `<p class="note">손절·익절을 지켜보는 종목이 없어요.</p>`}</div>
    </div>`;
  const key = rep.date + m + rep.orders.length + hist.length;
  const fresh = renderDayReport.key !== key;
  renderDayReport.key = key;
  if (fresh && !REDUCED && window.anime) {
    anime({ targets: "#day-report .dr-bar i", scaleY: [0, 1], delay: anime.stagger(40), duration: 600, easing: "easeOutCubic" });
    animateIn("#day-report .dr-k", { step: 60 });
  }
  if (fresh && dr != null) countTo($("#dr-ret"), dr * 100, { decimals: 2, format: (v) => (v > 0 ? "+" : "") + v.toFixed(2) + "%" });
  icons();
}

// 텔레그램 대화방 번호: 내 봇에게 온 메시지에서 찾아 저장 (다른 봇이 필요 없음)
$("#tg-find")?.addEventListener("click", async (e) => {
  const b = e.currentTarget;
  b.disabled = true;
  try {
    const r = await api("/api/telegram/find-chat", { method: "POST" });
    $("#tg-chat").value = r.chat_id;
    if (state.config) state.config.notify = { ...(state.config.notify || {}), telegram_chat_id: r.chat_id };
    toast(`찾았어요: ${r.name || "내 대화방"} (${r.chat_id}). 저장했고, 텔레그램으로 확인 메시지를 보냈어요`, "check");
  } catch (err) { toast(err.message, "alert-triangle"); }
  b.disabled = false;
});
