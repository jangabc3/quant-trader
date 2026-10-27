"use strict";
// 실시간 사무실: 회의가 없을 때도 장중 엔진 움직임을 캐릭터와 현황판으로 보여 준다 (AI 호출 없음).
// 트레이더 = 손절·익절 감시, 차트봇·검증관 = 다음 후보 재계산, 헤드라인 = 보유 종목 뉴스, 대표 = 오늘 주문·체결

const lv = { on: false, data: null, poll: null, tick: 0, rot: null, seen: 0, market: "KR" };
const SRC_ICON = { engine: "play", exit: "shield", sync: "check-circle-2", ai: "users", picks: "sparkles", alert: "bell-ring",
  order: "send", risk: "octagon-x", live: "radar", manual: "hand", report: "newspaper", approval: "hand" };

const hhmm = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false }); };
const nm = (x) => x.name && x.name !== x.symbol ? x.name : x.symbol;

function liveAgents(d) {
  const A = Object.fromEntries(office.roles.map((r) => [r.id, { status: "idle", bubble: "", report: "" }]));
  const t = lv.tick;
  const pick = (arr) => arr.length ? arr[t % arr.length] : null;
  const set = (id, status, bubble, report) => { if (A[id]) Object.assign(A[id], { status, bubble, report }); };
  // 트레이더: 감시 중 종목
  const w = pick(d.watch);
  set("trader", d.watch.length ? "working" : "idle",
    w ? (w.stop ? `${nm(w)} 손절까지 ${pct(w.to_stop)} · 익절까지 ${pct(w.to_take)}` : `${nm(w)} ${pct(w.pnl)} · 손절 계획 없음`) : "감시할 종목이 없어요",
    "### 감시 중인 종목\n\n" + (d.watch.map((x) => `- **${nm(x)}** ${x.qty}주 · 손익 ${pct(x.pnl)}` + (x.stop ? ` · 손절 ${x.stop.toLocaleString()} / 익절 ${x.take.toLocaleString()}` : " · 손절 계획 없음 (직접 산 종목)")).join("\n") || "- 없음")
    + "\n\n장중 2분마다 가격을 확인하고, 손절가·익절가에 닿으면 바로 팝니다.");
  // 차트봇·검증관: 다음 후보
  const ok = d.candidates.filter((c) => !c.missing.length), no = d.candidates.filter((c) => c.missing.length);
  const c1 = pick(ok), c2 = pick(no);
  set("chart", d.schedule.open ? "working" : "idle", c1 ? `${nm(c1)}: 규칙 통과 · 모멘텀 ${pct(c1.mom)}` : "통과한 후보가 아직 없어요",
    "### 다음 후보 (15분마다 다시 계산)\n\n" + (ok.map((c) => `- **${nm(c)}** 모멘텀 ${pct(c.mom)} · RSI ${c.rsi}`).join("\n") || "- 없음"));
  set("checker", d.schedule.open && c2 ? "working" : "idle", c2 ? `${nm(c2)}: ${c2.missing[0]}` : "",
    "### 아깝게 떨어진 종목\n\n" + (no.map((c) => `- **${nm(c)}**: ${c.missing.join(", ")}`).join("\n") || "- 없음"));
  // 헤드라인: 뉴스
  const n = pick(d.news);
  set("news", n ? "working" : "idle", n ? `${n.name}: ${n.title.slice(0, 34)}…` : "",
    "### 보유·후보 종목 뉴스\n\n" + (d.news.map((x) => `- [${x.name}] ${x.title}`).join("\n") || "- 새 뉴스 없음"));
  // 수급왕: 다음 일정
  set("flow", "idle", `다음 회의 ${hhmm(d.schedule.next_meeting)} · 실행 ${hhmm(d.schedule.next_run)}`, "");
  // 팀장: 재점검 현황
  const rc = Object.keys(d.rechecks).length;
  set("lead", rc ? "done" : "idle", `AI 긴급 재점검 ${rc}/${d.recheck_cap}`,
    "### 오늘 AI 긴급 재점검\n\n" + (Object.entries(d.rechecks).map(([s, a]) => `- ${s}: ${a === "sell" ? "일찍 매도" : a === "hold" ? "계속 보유" : a}`).join("\n") || "- 아직 없음")
    + "\n\n가진 종목이 장중 -3% 넘게 빠지거나 손절선에 2% 안으로 다가오면 그 종목만 짧게 다시 묻습니다.");
  // 대표: 오늘 한 일
  const last = d.timeline.find((e) => ["order", "exit", "sync", "manual", "engine", "live"].includes(e.source));
  set("pm", last ? "working" : "idle", last ? last.message.slice(0, 40) : (d.auto ? "오늘은 아직 주문이 없어요" : "자동매매가 꺼져 있어요"),
    "### 오늘 한 일\n\n" + (d.timeline.slice(0, 12).map((e) => `- ${hhmm(e.ts)} ${e.message}`).join("\n") || "- 아직 없음"));
  return A;
}

function liveRun(d) {
  const r = office.run?.id === "live" ? office.run : { id: "live", symbol: "", name: "실시간 사무실", handoffs: [], stages: {}, mode: "live" };
  r.status = "running";
  r.stage = d.schedule.open ? "장중 감시" : "장 마감";
  r.started_at = r.started_at || new Date().toISOString();
  r.agents = liveAgents(d);
  // 새 주문·체결이 생기면 서류가 트레이더 -> 대표로 날아간다
  const newest = d.timeline[0]?.id || 0;
  if (lv.seen && newest > lv.seen && d.timeline.some((e) => e.id > lv.seen && ["order", "exit", "sync", "manual"].includes(e.source)))
    r.handoffs.push({ from: "trader", to: "pm" });
  if (lv.seen && newest > lv.seen && d.timeline.some((e) => e.id > lv.seen && e.source === "picks"))
    r.handoffs.push({ from: "chart", to: "lead" });
  lv.seen = newest;
  return r;
}

function liveHud(d) {
  const hud = $("#office-hud");
  hud.className = "office-hud " + (d.schedule.open ? "running" : "");
  hud.innerHTML = `<span class="hud-dot"></span><div><b>실시간 사무실</b> <small>${d.market === "KR" ? "국내" : "미국"}</small>
    <div>${d.schedule.open ? "<b class='blink'>● 장중</b> 감시·후보 계산 중" : "장 마감 · 다음 장을 기다리는 중"}</div>
    <div class="note">${d.auto ? "자동매매 켜짐" : "자동매매 꺼짐"}${d.kill ? " · 긴급 정지 중" : ""} · AI 호출 없이 움직임만 보여 줘요</div></div>`;
  $("#stage-line").innerHTML = "";
}

function renderBoard(d) {
  const box = $("#live-board");
  const first = !box.dataset.ready;
  box.dataset.ready = "1";
  box.innerHTML = `
    <section class="lb-guard"><h4><i data-lucide="shield-check"></i> 손절·익절 지키는 중 <small>${d.watch.length}종목</small>
        <span class="lb-help" title="산 종목마다 정해 둔 손절가(이 아래로 내리면 판다)와 익절가(이 위로 오르면 판다)를 장중 2분마다 확인해서, 닿으면 사람 손 없이 자동으로 팝니다.">?</span></h4>
      <p class="lb-desc">산 주식이 정해 둔 가격에 닿으면 자동으로 팔아요. 흰 점이 지금 가격이에요.</p>
      ${d.watch.map((w) => `<div class="lb-watch" data-open="${esc(w.symbol)}"><div class="lb-top">${logo(w.symbol, nm(w), 24)}<b>${esc(nm(w))}</b><span class="${cls(w.pnl)}">${pct(w.pnl)}</span></div>
        ${w.stop ? guardGauge(w, d.market) : `<div class="lb-nobar">손절·익절이 없어 자동으로 팔지 않아요 · 내 계좌에서 정할 수 있어요</div>`}</div>`).join("")
        || `<div class="lb-emptyg"><i data-lucide="shield"></i><span>가진 주식이 없어요. 사면 여기서 손절~익절 사이 지금 위치를 실시간으로 보여 줘요.</span></div>`}</section>
    <section><h4><i data-lucide="sparkles"></i> 다음 후보 <small>15분마다 다시 계산</small></h4>
      ${d.candidates.slice(0, 6).map((c) => `<div class="lb-cand" data-open="${esc(c.symbol)}">${logo(c.symbol, nm(c), 22)}<div class="lb-ct"><b>${esc(nm(c))}</b>
        ${c.missing.length ? `<span class="lb-miss">${esc(c.missing[0])}</span>` : `<span class="lb-ok">규칙 통과</span>`}</div></div>`).join("") || `<p class="note">후보가 없어요.</p>`}</section>
    <section><h4><i data-lucide="list"></i> 오늘 한 일</h4><div class="lb-tl">
      ${d.timeline.slice(0, 10).map((e) => `<div class="lb-ev ${e.level}"><i data-lucide="${SRC_ICON[e.source] || "dot"}"></i><time>${hhmm(e.ts)}</time><span>${esc(e.message)}</span></div>`).join("")
        || `<p class="note">아직 기록이 없어요.</p>`}</div></section>
    <section><h4><i data-lucide="clock"></i> 다음 일정</h4>
      <div class="lb-sched"><div><span>AI 회의</span><b data-cd="${esc(d.schedule.next_meeting)}"></b></div><div><span>주문 실행</span><b data-cd="${esc(d.schedule.next_run)}"></b></div>
        <div><span>장 마감</span><b data-cd="${esc(d.schedule.close)}"></b></div><div><span>AI 재점검</span><b>${Object.keys(d.rechecks).length} / ${d.recheck_cap}</b></div></div>
      <h4 style="margin-top:12px"><i data-lucide="newspaper"></i> 관련 뉴스</h4>
      ${d.news.slice(0, 4).map((n) => `<a class="lb-news" href="${esc(n.link)}" target="_blank" rel="noopener noreferrer"><b>${esc(n.name)}</b> ${esc(n.title)}</a>`).join("") || `<p class="note">보유·후보 종목의 새 뉴스가 없어요.</p>`}
    </section>`;
  countdowns();
  if (first) animateIn("#live-board section", { y: 12, step: 60 });
  icons();
}

function countdowns() {
  $$("#live-board [data-cd]").forEach((el) => {
    const ms = Date.parse(el.dataset.cd) - Date.now();
    if (isNaN(ms)) { el.textContent = "-"; return; }
    if (ms <= 0) { el.textContent = "지남"; return; }
    const h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60, s = Math.floor(ms / 1000) % 60;
    el.textContent = h > 23 ? `${Math.floor(h / 24)}일 뒤 ${hhmm(el.dataset.cd)}` : `${hhmm(el.dataset.cd)} · ${h ? h + "시간 " : ""}${m}분 ${h ? "" : s + "초"} 남음`;
  });
}

async function liveFetch() {
  if (!lv.on || state.tab !== "office") return;
  try {
    const [d, runs] = await Promise.all([api("/api/live?market=" + lv.market), api("/api/ai/runs?limit=5").catch(() => [])]);
    lv.data = d;
    // 정기 회의가 열리면 그 회의를 보여 준다
    const running = runs.find((r) => r.status === "running");
    if (running) { liveOff(); window.showRunById(running.id); toast("정기 AI 회의가 시작돼 회의 화면으로 바꿨어요", "users"); return; }
    if (office.run && office.run.id !== "live") return liveOff();
    office.run = liveRun(d);
    liveHud(d); updateOverlay(); renderBoard(d);
    if (office.selected) renderReport();
  } catch (e) { $("#live-board").innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}

function liveOn() {
  (office.demoTimers || []).forEach(clearTimeout);
  clearTimeout(office.poll);
  lv.on = true; lv.seen = 0;
  office.run = null; office.papers = []; office.seenHandoffs = 0;
  $("#result").innerHTML = "";
  $("#live-panel").classList.remove("hidden");
  $$("#office-mode button").forEach((b) => b.classList.toggle("on", b.dataset.v === "live"));
  clearInterval(lv.poll); clearInterval(lv.rot);
  lv.poll = setInterval(liveFetch, 10000);
  lv.rot = setInterval(() => { // 말풍선을 6초마다 다음 종목으로, 카운트다운은 1초마다
    countdowns();
    if (lv.on && lv.data && Date.now() % 6000 < 1000) { lv.tick++; office.run = liveRun(lv.data); updateOverlay(); }
  }, 1000);
  liveFetch();
}
function liveOff() {
  lv.on = false;
  clearInterval(lv.poll); clearInterval(lv.rot);
  $("#live-panel").classList.add("hidden");
  $$("#office-mode button").forEach((b) => b.classList.toggle("on", b.dataset.v === "meeting"));
}
window.liveOn = liveOn;
window.liveHudRefresh = () => lv.data && liveHud(lv.data);

// 화면 요소
$(".office-bar").insertAdjacentHTML("afterbegin", `<div class="seg" id="office-mode"><button data-v="live"><span class="live-dot"></span> 실시간</button><button data-v="meeting" class="on">회의 보기</button></div>`);
$("#office-wrap").insertAdjacentHTML("afterend", `<div class="panel hidden" id="live-panel">
  <div class="rank-bar"><h3 style="margin:0"><i data-lucide="activity"></i> 실시간 현황판 <small>10초마다 새로 고침 · AI 호출 없음</small></h3>
    <div class="seg" id="live-mkt"><button data-v="KR" class="on">국내</button><button data-v="US">미국</button></div></div>
  <div id="live-board" class="live-board"></div></div>`);
$("#office-mode").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.v === "live") liveOn();
  else { liveOff(); office.run = null; loadRuns(); }
});
$("#live-mkt").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  lv.market = b.dataset.v;
  $$("#live-mkt button").forEach((x) => x.classList.toggle("on", x === b));
  $("#live-board").dataset.ready = "";
  liveFetch();
});
["#meet-start", "#meet-demo"].forEach((s) => $(s)?.addEventListener("click", liveOff, true));
$("#meet-runs").addEventListener("change", liveOff);
// AI 사무실을 열 때: 자동매매가 켜져 있거나 장중이면 실시간으로 시작
document.addEventListener("tab", (e) => {
  if (e.detail !== "office") return;
  if (lv.on) return liveFetch();
  const s = state.status;
  const open = s && Object.values(s.markets || {}).some((m) => m.enabled && m.open);
  if (s?.auto_trading || open) liveOn();
});
icons();

// 이번 달 AI 비용 (사무실 위)
async function loadAiCost() {
  const c = await api("/api/ai/cost").catch(() => null);
  if (!c) return;
  let el = $("#ai-cost-chip");
  if (!el) { el = document.createElement("span"); el.id = "ai-cost-chip"; $(".office-bar").appendChild(el); }
  const r = c.cap_krw ? c.month_krw / c.cap_krw : 0;
  el.className = "ai-cost " + (r >= 1 ? "full" : r >= 0.8 ? "warn" : "");
  el.title = "회의·재점검에 쓴 Claude 비용 (환율 1,400원 기준 대략값). 상한은 설정 4번에서 바꿀 수 있어요";
  el.innerHTML = `<i data-lucide="coins"></i> 이번 달 AI 비용 <b>${Math.round(c.month_krw).toLocaleString()}원</b>${c.cap_krw ? ` / ${Math.round(c.cap_krw).toLocaleString()}원 <span class="bar"><i style="width:${Math.min(100, r * 100)}%"></i></span>` : ""}`;
  icons();
}
document.addEventListener("tab", (e) => { if (e.detail === "office") loadAiCost(); });
setInterval(() => { if (state.tab === "office") loadAiCost(); }, 60000);
