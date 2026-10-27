"use strict";
// 내 계좌 다시 그리기: 보유 종목(토스식 목록) · 거래 기록(날짜별 타임라인) · 하루 리포트(하루 시간표) · 손절·익절 게이지

// 손절~익절 사이 지금 위치. 사무실 현황판과 보유 종목에서 같이 쓴다
function guardGauge(x, m) {
  if (!x.stop || !x.take || x.take <= x.stop) return "";
  const px = x.price ?? x.last_price, entry = x.entry ?? x.avg ?? x.avg_price;
  const pos = (v) => Math.max(0, Math.min(100, (v - x.stop) / (x.take - x.stop) * 100));
  const toStop = px / x.stop - 1, toTake = x.take / px - 1;
  const lvl = toStop < 0.01 ? "danger" : toStop < 0.03 ? "warn" : "safe";
  const lvlKo = { danger: "손절 임박", warn: "주의", safe: "안전" }[lvl];
  return `<div class="gg ${lvl}">
    <div class="gg-bar"><i class="gg-loss" style="width:${pos(entry)}%"></i><i class="gg-gain" style="left:${pos(entry)}%;width:${100 - pos(entry)}%"></i>
      <i class="gg-entry" style="left:${pos(entry)}%" title="산 가격"></i><i class="gg-now" style="left:${pos(px)}%"><em>${money(px, m)}</em></i></div>
    <div class="gg-lbl"><span class="down">손절 ${money(x.stop, m)}<small>${pct(-toStop)} 남음</small></span><span class="gg-chip">${lvlKo}</span>
      <span class="up">익절 ${money(x.take, m)}<small>+${(toTake * 100).toFixed(1)}% 남음</small></span></div></div>`;
}
window.guardGauge = guardGauge;

// 보유 종목: 토스 '내 주식'처럼 한 줄 목록, 누르면 펼쳐서 손절·익절과 버튼
const hd = { sort: "value", open: null };
window.renderHoldings = (pos, m) => {
  const box = $("#holdings");
  const tv = pos.reduce((s, p) => s + p.value, 0), cost = pos.reduce((s, p) => s + p.qty * p.avg_price, 0), tg = tv - cost;
  $("#hold-sum").innerHTML = "";
  if (!pos.length) {
    box.innerHTML = `<div class="empty"><i data-lucide="inbox"></i><p>가진 주식이 없어요</p><span class="note">자동매매가 사거나, 종목 화면의 주문창에서 직접 살 수 있어요.</span></div>`;
    return;
  }
  const sorted = pos.slice().sort((a, b) => hd.sort === "pnl" ? b.pnl - a.pnl : hd.sort === "name" ? (a.name || a.symbol).localeCompare(b.name || b.symbol) : b.value - a.value);
  box.innerHTML = `<div class="hl-sum"><div><span>평가금액</span><b>${money(tv, m)}</b></div><div><span>투자 원금</span><b>${money(cost, m)}</b></div>
      <div><span>평가 손익</span><b class="${cls(tg)}">${tg > 0 ? "+" : ""}${money(tg, m)} <small>${pct(cost ? tg / cost : 0)}</small></b></div>
      <div class="seg hl-sort" id="hl-sort">${[["value", "평가금액순"], ["pnl", "수익률순"], ["name", "이름순"]].map(([k, n]) => `<button data-v="${k}" class="${hd.sort === k ? "on" : ""}">${n}</button>`).join("")}</div></div>
    <div class="hl-list">${sorted.map((p) => {
      const pl = state.plans[p.symbol], gain = p.value - p.qty * p.avg_price, w = tv ? p.value / tv : 0;
      const open = hd.open === p.symbol;
      return `<div class="hl-row ${open ? "open" : ""}" data-row="${esc(p.symbol)}">
        <div class="hl-main">${logo(p.symbol, p.name || p.symbol, 38)}
          <div class="hl-name"><b>${esc(p.name || p.symbol)}${p.event ? ` <em class="cd-tag">공시</em>` : ""}</b><span>${p.qty.toLocaleString()}주 · 비중 ${(w * 100).toFixed(0)}%</span></div>
          <div class="hl-guard" title="${p.server_guard ? "토스 서버가 손절·익절을 지켜요 (PC가 꺼져도)" : pl ? "QTrader 가 손절·익절을 지켜요" : "손절·익절 없음"}">${p.server_guard ? `<i data-lucide="server" class="srv"></i>` : pl ? `<i data-lucide="shield-check"></i>` : `<i data-lucide="shield-off" class="off"></i>`}</div>
          <div class="hl-val"><b>${money(p.value, p.market)}</b><em class="hl-px">현재가 <span data-key="p:${esc(p.symbol)}" data-val="${p.last_price}">${money(p.last_price, p.market)}</span></em><span class="${cls(p.pnl)}">${gain > 0 ? "+" : ""}${money(gain, p.market)} (${pct(p.pnl)})</span>${p.day_pnl != null ? `<em class="hl-day">오늘 <span class="${cls(p.day_pnl)}">${p.day_pnl > 0 ? "+" : ""}${money(p.day_pnl, p.market)}</span></em>` : ""}</div></div>
        <div class="hl-more">
          <div class="hl-facts"><div><span>평균 단가</span><b>${money(p.avg_price, p.market)}</b></div><div><span>현재가</span><b>${money(p.last_price, p.market)}</b></div>
            <div><span>투자 원금</span><b>${money(p.qty * p.avg_price, p.market)}</b></div></div>
          ${p.event ? `<div class="srv-note ev"><i data-lucide="megaphone"></i> 최근 공시: ${esc(p.event.title)}${p.event.kind === "split" ? " — 분할·병합이 반영될 때까지 QTrader 가 지켜 보고, 수량이 바뀌면 손절·익절을 같은 비율로 고쳐요." : ""}</div>` : ""}
          ${p.server_guard ? `<div class="srv-note"><i data-lucide="server"></i> 토스 서버에 손절·익절(OCO)이 걸려 있어요. 이 PC가 꺼져 있어도 국내는 정규장, 미국은 모든 시간에 토스가 팔아 줘요.</div>` : ""}
          ${pl ? guardGauge({ ...pl, price: p.last_price, avg: p.avg_price }, p.market) : `<div class="hold-noplan"><i data-lucide="shield-off"></i><span>손절·익절이 없어 자동으로 팔지 않아요. 정해 두면 장중 2분마다 지켜 봅니다.</span></div>`}
          <div class="hold-acts"><button data-plan="${esc(p.symbol)}"><i data-lucide="shield-check"></i> ${pl ? "손절·익절 바꾸기" : "손절·익절 정하기"}</button>
            ${pl ? `<button data-unplan="${esc(p.symbol)}">감시 끄기</button>` : ""}<button data-open="${esc(p.symbol)}"><i data-lucide="candlestick-chart"></i> 차트·주문</button></div>
        </div></div>`;
    }).join("")}</div>`;
};
$("#holdings").addEventListener("click", (e) => {
  const s = e.target.closest("#hl-sort button");
  if (s) { hd.sort = s.dataset.v; window.renderDash(dash.data); return; }
  if (e.target.closest("button")) return; // 버튼은 각자 처리
  const r = e.target.closest("[data-row]");
  if (r) { e.stopPropagation(); hd.open = hd.open === r.dataset.row ? null : r.dataset.row; window.renderDash(dash.data); }
}, true);

// 거래 기록: 날짜별 타임라인. 판 거래에는 결과(손익)까지
const wy = { filter: "all", open: null, perf: null };
async function renderWhy() {
  const [rows, perf] = await Promise.all([api("/api/trades/why?limit=60").catch(() => []), api("/api/performance?market=" + dash.market).catch(() => null)]);
  const m = dash.market;
  const result = {};
  (perf?.trades || []).forEach((t) => { const k = t.symbol + t.sell_date; result[k] = (result[k] || 0) + t.pnl; });
  let list = rows.filter((r) => r.market === m);
  if (wy.filter === "buy") list = list.filter((r) => r.side === "buy");
  if (wy.filter === "sell") list = list.filter((r) => r.side === "sell");
  if (wy.filter === "auto") list = list.filter((r) => r.strategy !== "manual");
  if (wy.filter === "manual") list = list.filter((r) => r.strategy === "manual");
  const tag = (t) => t.strategy === "ai-exit" ? (t.summary.startsWith("손절") ? ["손절", "down"] : t.summary.startsWith("익절") ? ["익절", "up"] : ["자동 매도", "down"])
    : t.strategy === "manual" ? [t.side === "buy" ? "직접 매수" : "직접 매도", t.side === "buy" ? "up" : "down"] : [t.side === "buy" ? "AI 매수" : "AI 매도", t.side === "buy" ? "up" : "down"];
  const groups = {};
  list.forEach((t) => (groups[t.date] = groups[t.date] || []).push(t));
  const head = `<div class="wy-bar"><div class="seg" id="wy-filter">${[["all", "전체"], ["buy", "산 것"], ["sell", "판 것"], ["auto", "자동매매"], ["manual", "직접 주문"]]
      .map(([k, n]) => `<button data-v="${k}" class="${wy.filter === k ? "on" : ""}">${n}</button>`).join("")}</div>
    <button id="wy-export"><i data-lucide="download"></i> 엑셀(CSV)로 받기</button></div>`;
  $("#why-list").innerHTML = head + (list.length ? Object.entries(groups).map(([d, ts]) => `<div class="wy-day"><div class="wy-date">${esc(d)}</div>
    ${ts.map((t) => {
      const [tg, tc] = tag(t), r = t.run, res = t.side === "sell" ? result[t.symbol + t.date] : null, open = wy.open === t.id;
      const reasons = r?.reasons?.length ? r.reasons : t.detail.filter(Boolean);
      return `<div class="wy-row ${open ? "open" : ""}" data-wy="${t.id}">
        <div class="wy-main"><span class="wy-tag ${tc}">${tg}</span>${logo(t.symbol, t.name || t.symbol, 30)}
          <div class="wy-t"><b>${esc(t.name || t.symbol)}</b><span>${esc(r?.headline || t.summary)}</span></div>
          <div class="wy-amt"><b>${t.qty.toLocaleString()}주 · ${money(t.price, m)}</b>${res != null ? `<span class="${cls(res)}">${res > 0 ? "+" : ""}${money(res, m)}</span>` : `<span class="note">${money(t.qty * t.price, m)}${t.status === "submitted" ? " · 체결 대기" : ""}</span>`}</div>
          <i data-lucide="chevron-down" class="wy-chev"></i></div>
        <div class="wy-more">
          ${reasons.length ? `<div class="wy-sec"><h5>이렇게 판단했어요</h5><ol>${reasons.slice(0, 4).map((x) => `<li>${esc(x)}</li>`).join("")}</ol></div>` : ""}
          ${r ? `<div class="wy-chips"><span>AI ${esc(r.rating_ko || r.rating || "")} · 확신 ${Math.round((r.confidence || 0) * 100)}%</span>
            ${r.rules ? `<span class="ok">안전 점검 ${r.passed.length}/${r.rules} 통과</span>` : ""}
            ${r.plan ? `<span class="down">손절 ${money(r.plan.stop, m)}</span><span class="up">익절 ${money(r.plan.take, m)}</span>` : ""}</div>
            ${r.worry ? `<p class="note">⚠ 걱정되는 점: ${esc(Array.isArray(r.worry) ? r.worry.join(", ") : r.worry)}</p>` : ""}
            <button class="why-open" data-run="${esc(r.id)}">AI 회의 전체 보기 <i data-lucide="chevron-right"></i></button>`
            : `<p class="note">${t.strategy === "manual" ? "직접 낸 주문이에요." : esc(t.summary)}</p>`}
        </div></div>`;
    }).join("")}</div>`).join("")
    : `<div class="empty"><i data-lucide="message-square-quote"></i><p>아직 거래가 없어요</p><span class="note">자동매매나 직접 주문으로 사고팔면 무엇을, 왜, 어떤 근거로 했는지와 결과가 날짜별로 남아요.</span></div>`);
  if (wy.animKey !== m + wy.filter) { wy.animKey = m + wy.filter; animateIn("#why-list .wy-row", { step: 25 }); }
  icons();
}
$("#why-list").addEventListener("click", async (e) => {
  const f = e.target.closest("#wy-filter button");
  if (f) { wy.filter = f.dataset.v; return renderWhy(); }
  if (e.target.closest("#wy-export")) { location.href = "/api/export/trades.csv?market=" + dash.market; return; }
  const run = e.target.closest("[data-run]");
  if (run) { openTab("office"); window.showRunById && showRunById(run.dataset.run); return; }
  const r = e.target.closest("[data-wy]");
  if (r && !e.target.closest(".wy-more")) { wy.open = wy.open === Number(r.dataset.wy) ? null : Number(r.dataset.wy); renderWhy(); }
});

// 하루 리포트: 하루 시간표(회의·주문·마감 표시와 '지금' 선) + 숫자 + 있었던 일
async function renderFlow() {
  const s = state.status;
  if (!s || !$("#flow")) return;
  const m = dash.market;
  const d = await api("/api/live?market=" + m).catch(() => null);
  if (!d) return;
  const tz = m === "KR" ? "Asia/Seoul" : "America/New_York";
  const toMin = (iso) => { const t = new Date(new Date(iso).toLocaleString("en-US", { timeZone: tz })); return t.getHours() * 60 + t.getMinutes(); };
  const [a, b] = m === "KR" ? [8 * 60, 20 * 60] : [4 * 60, 20 * 60];
  const X = (min) => Math.max(0, Math.min(100, (min - a) / (b - a) * 100));
  const reg = m === "KR" ? [9 * 60, 15 * 60 + 30] : [9 * 60 + 30, 16 * 60];
  const marks = [["AI 회의", d.schedule.next_meeting, "users"], ["주문 실행", d.schedule.next_run, "send"], ["장 마감", d.schedule.close, "flag"]];
  const nowMin = toMin(new Date().toISOString());
  const evs = d.timeline.filter((e) => ["order", "exit", "sync", "manual", "engine", "live", "picks", "ai"].includes(e.source));
  const hours = [];
  for (let h = Math.ceil(a / 60); h <= b / 60; h += 2) hours.push(h);
  const hm = (iso) => new Date(iso).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz });
  const nowTxt = new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz });
  $("#flow").innerHTML = `<div class="dt">
      <div class="dt-legend">${marks.map(([n, iso, ic], k) => `<span class="dt-chip ${toMin(iso) <= nowMin ? "past" : ""}"><i class="dt-num">${k + 1}</i><i data-lucide="${ic}"></i>${hm(iso)} ${n}</span>`).join("")}
        <span class="dt-chip now"><i class="dt-nowdot"></i>지금 ${nowTxt}</span></div>
      <div class="dt-track"><i class="dt-reg" style="left:${X(reg[0])}%;width:${X(reg[1]) - X(reg[0])}%"></i>
        ${hours.map((h) => `<span class="dt-h" style="left:${X(h * 60)}%">${h}시</span>`).join("")}
        ${marks.map(([n, iso], k) => `<i class="dt-pin ${toMin(iso) <= nowMin ? "past" : ""}" style="left:${X(toMin(iso))}%" title="${hm(iso)} ${n}">${k + 1}</i>`).join("")}
        ${evs.slice(0, 20).map((e) => `<i class="dt-ev ${e.level}" style="left:${X(toMin(e.ts))}%" title="${esc(e.message)}"></i>`).join("")}
        ${nowMin >= a && nowMin <= b ? `<div class="dt-now" style="left:${X(nowMin)}%"></div>` : ""}
      </div>
      <p class="note dt-cap">${d.schedule.open ? "지금 사고팔 수 있는 시간이에요. 2분마다 손절·익절을 확인합니다." : "지금은 장이 닫혀 있어요."} 파란 띠 = ${m === "KR" ? "정규장 09:00~15:30" : "정규장 뉴욕 09:30~16:00"} · 숫자 = 일정 · 흰 점 = 오늘 있었던 일 (올리면 내용)</p>
    </div>
    <div class="dt-feed"><h4>오늘 있었던 일</h4>${evs.slice(0, 12).map((e) => `<div class="dt-row ${e.level}"><time>${new Date(e.ts).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false })}</time><span>${esc(e.message)}</span></div>`).join("")
      || `<p class="note">아직 없어요. 자동매매가 켜져 있으면 회의·주문·체결이 시간 순서대로 여기 쌓여요.</p>`}</div>`;
  $("#flow-sub").textContent = `${MODE_NAME[s.mode]} · ${MARKET_NAME[m]} · ${s.auto_trading ? "자동매매 켜짐" : "자동매매 꺼짐"}`;
  if (fl.key !== m) { fl.key = m; animateIn("#flow .dt-pin, #flow .dt-ev, #flow .dt-chip", { y: -6, step: 40 }); }
  icons();
}
const fl = { key: null };

// 조건 주문 목록 (알림 탭)
async function loadConds() {
  const el = $("#cond-list");
  if (!el) return;
  const rows = await api("/api/conditional").catch(() => []);
  const ST = { watching: ["지켜보는 중", ""], done: ["실행됨", "ok"], canceled: ["취소됨", "off"], expired: ["만료", "off"], failed: ["실패", "bad"] };
  el.innerHTML = rows.length ? `<div class="cd-list">${rows.map((r) => {
    const [st, sc] = ST[r.status] || [r.status, ""];
    const mine = !r.external && !r.guard;
    const tag = r.guard ? `<em class="cd-tag srv">서버 보호</em>` : r.external ? `<em class="cd-tag">토스 앱</em>` : "";
    return `<div class="cd-row ${sc}">${logo(r.symbol, r.name, 28)}<div><b>${esc(r.name)} ${tag}</b><span>${money(r.trigger, r.market)}에 닿으면 ${r.qty}주 ${r.side === "buy" ? "매수" : "매도"}${r.then_stop ? ` → 사지면 ${money(r.then_stop, r.market)} 손절` : ""}${r.note ? " · " + esc(r.note) : ""}</span></div>
      <span class="cd-st">${st}</span><span class="note">~${esc((r.expire || "").slice(5))}</span>${r.status === "watching" && mine ? `<span class="cd-acts"><button data-cond-e="${esc(r.id)}" data-trig="${r.trigger}" data-qty="${r.qty}">수정</button><button data-cond-x="${esc(r.id)}">취소</button></span>` : r.external ? `<span class="note">토스 앱에서 관리</span>` : "<span></span>"}</div>`;
  }).join("")}</div>` : `<div class="empty"><i data-lucide="crosshair"></i><p>걸어 둔 조건 주문이 없어요</p><span class="note">종목 화면 주문창에서 '조건' 또는 '나눠서'를 고르면 여기 모여요.</span></div>`;
  icons();
}
$("#cond-list")?.addEventListener("click", async (e) => {
  const ed = e.target.closest("[data-cond-e]");
  if (ed) {
    const t = await ask("새 감시 가격", { input: "", value: ed.dataset.trig });
    if (!t) return;
    const q = await ask("수량 (그대로면 확인)", { input: "", value: ed.dataset.qty });
    if (!q) return;
    await api(`/api/conditional/${ed.dataset.condE}/modify`, { method: "POST", body: { trigger: Number(t.replace(/,/g, "")), qty: Number(q) } })
      .then(() => toast("조건 주문을 바꿨어요", "crosshair")).catch((err) => toast(err.message));
    loadConds();
    return;
  }
  const b = e.target.closest("[data-cond-x]");
  if (b && await ask("이 조건 주문을 취소할까요?")) { await api("/api/conditional/" + b.dataset.condX, { method: "DELETE" }).catch((err) => toast(err.message)); loadConds(); }
});
const _loadDashExtras4 = window.loadDashExtras;
window.loadDashExtras = async () => { await Promise.all([_loadDashExtras4(), loadConds()]); };

// 가상 계좌 처음부터 다시 (가상매매일 때만 보인다). 확인은 화면 안 창으로, '다시 시작'을 적어야 진행
const _renderDash6 = window.renderDash;
window.renderDash = (d) => {
  _renderDash6(d);
  const paper = state.status?.mode === "paper" && d.overview?.broker === "paper";
  $("#paper-bar")?.classList.toggle("hidden", !paper);
};
$("#paper-reset")?.addEventListener("click", async () => {
  const all = $("#paper-reset-mkt").value === "ALL";
  const m = dash.market;
  const which = all ? "국내·미국 가상 계좌 둘 다" : `${MARKET_NAME[m]} 가상 계좌`;
  const cfg = state.config || await api("/api/config").catch(() => null);
  const cash = cfg?.paper?.initial_cash || {};
  const start = all ? `국내 ${money(cash.KR || 0, "KR")} · 미국 ${money(cash.US || 0, "US")}` : money(cash[m] || 0, m);
  const typed = await ask(`${which}를 처음 상태로 되돌려요.\n\n• 가진 주식·주문 기록·자산 추이가 지워지고 현금이 ${start}으로 돌아가요\n• 손절·익절 감시와 가상 조건 주문도 꺼져요\n• AI 회의 기록과 이벤트 기록은 그대로 남아요\n\n되돌릴 수 없어요. 계속하려면 아래에 '다시 시작'이라고 적어 주세요.`,
    { title: "가상 계좌 처음부터 다시", danger: true, ok: "처음부터 다시", input: "확인 글자", value: "" });
  if (typed === null) return;
  if (typed !== "다시 시작") { toast("'다시 시작'을 똑같이 적어야 진행돼요. 아무것도 바꾸지 않았어요", "info"); return; }
  try {
    await api("/api/paper/reset", { method: "POST", body: { market: all ? "ALL" : m } });
    toast(`${which}를 처음부터 다시 시작했어요`, "rotate-ccw");
    state.plans = {};
    await refresh();
  } catch (e) { toast(e.message); }
});
