"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const MODE_NAME = { paper: "가상매매", toss_real: "토스증권 실전", kis_demo: "KIS 모의투자", kis_real: "KIS 실전" };
const LIVE = new Set(["toss_real", "kis_real"]);
const MARKET_NAME = { KR: "국내", US: "미국" };
const STRAT_NAME = { trend: "추세추종", momentum: "모멘텀 순위", meanrev: "평균회귀", ai: "AI 투자 회의" };
const SRC_NAME = { toss: "토스증권", kis: "KIS", csv: "CSV" };
const PARAM_NAME = {
  fast: "단기 이평", slow: "장기 이평", vol_lookback: "변동성 기간", target_vol: "목표 변동성(연)",
  lookback: "모멘텀 기간", skip: "최근 제외일", top_k: "보유 종목 수", rebalance_days: "리밸런싱 주기",
  rsi_n: "RSI 기간", entry_rsi: "진입 RSI", exit_rsi: "청산 RSI", bb_n: "볼린저 기간", bb_k: "볼린저 배수",
  max_hold: "최대 보유일",
};
const state = { status: null, config: null, strategies: {}, tab: "mkt", plans: {} };

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "content-type": "application/json", "x-qtrader-token": window.QT_TOKEN, ...(opts.headers || {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isUS = (m) => m === "US" || m === "USD";
const money = (v, m) => v == null || isNaN(v) ? "-" : (isUS(m) ? "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 })
  : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: 0 }) + "원");
const pct = (v) => v == null || isNaN(v) ? "-" : (v > 0 ? "+" : "") + (v * 100).toFixed(2) + "%";
const cls = (v) => v > 0 ? "up" : v < 0 ? "down" : "";
const bigKRW = (v) => {
  const a = Math.abs(v);
  if (a >= 1e12) return (v / 1e12).toFixed(1) + "조";
  if (a >= 1e8) return Math.round(v / 1e8).toLocaleString() + "억";
  if (a >= 1e4) return Math.round(v / 1e4).toLocaleString() + "만";
  return Math.round(v).toLocaleString();
};

// 종목 로고 대신 쓰는 이니셜 아이콘 (실제 로고 이미지는 쓰지 않는다)
const LOGO_COLORS = ["#3182f6", "#f04452", "#00b386", "#ff8a00", "#8b5cf6", "#0ea5e9", "#e11d74", "#14b8a6", "#f59e0b", "#6366f1"];
function logo(symbol, name, size = 28) {
  let h = 0;
  for (const c of String(symbol)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const ch = String(name || symbol).replace(/^(KODEX|TIGER|KBSTAR|ACE|SOL|RISE)\s*/i, "").trim().charAt(0).toUpperCase();
  return `<span class="logo" style="width:${size}px;height:${size}px;font-size:${Math.round(size * 0.46)}px;background:${LOGO_COLORS[h % LOGO_COLORS.length]}">${esc(ch)}</span>`;
}

function banner(msg, kind = "") {
  const b = $("#banner");
  b.className = "banner " + kind + (msg ? "" : " hidden");
  b.textContent = msg || "";
}

function modal(html) {
  $("#modal-body").innerHTML = html;
  $("#modal").classList.remove("hidden");
}
$("#modal-x").onclick = () => $("#modal").classList.add("hidden");
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });

// 탭
function openTab(name) {
  state.tab = name;
  $$("nav button").forEach((x) => x.classList.toggle("on", x.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + name));
  if (name === "cfg") loadConfig();
  document.dispatchEvent(new CustomEvent("tab", { detail: name }));
}
$$("nav button").forEach((b) => b.addEventListener("click", () => openTab(b.dataset.tab)));

// 전체 종목 검색 (위쪽 검색창)
let searchTimer;
$("#gsearch").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const q = $("#gsearch").value.trim();
    const box = $("#gsearch-list");
    if (!q) { box.classList.add("hidden"); return; }
    try {
      const rows = await api("/api/stocks/search?q=" + encodeURIComponent(q));
      box.innerHTML = rows.map((r) => `<div class="gs-item" data-open="${esc(r.symbol)}">${logo(r.symbol, r.name, 24)}<b>${esc(r.name)}</b><span class="note">${esc(r.symbol)} · ${esc(r.market || "")}</span></div>`).join("")
        || `<div class="gs-item note">검색 결과가 없습니다</div>`;
      box.classList.remove("hidden");
    } catch (e) { box.innerHTML = `<div class="gs-item note">${esc(e.message)}</div>`; box.classList.remove("hidden"); }
  }, 180);
});
$("#gsearch").addEventListener("keydown", async (e) => {
  if (e.key === "Enter") {
    // 검색 결과가 아직 바뀌기 전에 Enter 를 누르면 이전 결과가 열리지 않도록 지금 입력으로 다시 찾는다
    clearTimeout(searchTimer);
    const q = $("#gsearch").value.trim();
    if (!q) return;
    try {
      const rows = await api("/api/stocks/search?q=" + encodeURIComponent(q));
      if (rows.length) {
        $("#gsearch-list").classList.add("hidden");
        $("#gsearch").value = "";
        openTab("stock");
        window.openStock(rows[0].symbol);
      }
    } catch {}
  }
  if (e.key === "Escape") $("#gsearch-list").classList.add("hidden");
});
document.addEventListener("click", (e) => {
  const o = e.target.closest("[data-open]");
  if (o) {
    $("#gsearch-list").classList.add("hidden");
    $("#gsearch").value = "";
    openTab("stock");
    window.openStock && window.openStock(o.dataset.open);
    return;
  }
  if (!e.target.closest(".gsearch")) $("#gsearch-list").classList.add("hidden");
});

// 상태 · 내 계좌
async function refresh() {
  try {
    const s = await api("/api/status");
    state.status = s;
    renderStatus(s);
    if (!s.error && state.tab === "dash") {
      const [pos, ords, evs, plans, appr] = await Promise.all([api("/api/positions"), api("/api/orders?limit=50"),
        api("/api/events?limit=80"), api("/api/plans"), api("/api/approvals?status=pending")]);
      state.plans = Object.fromEntries(plans.map((p) => [p.symbol, p]));
      renderPositions(pos); renderOrders(ords); renderEvents(evs); renderApprovals(appr);
    }
  } catch (e) { banner("서버 응답 오류: " + e.message); }
}

function renderStatus(s) {
  const mb = $("#mode-badge");
  mb.textContent = MODE_NAME[s.mode] + (s.data_source ? ` · 시세 ${SRC_NAME[s.data_source] || s.data_source}` : "");
  mb.className = "badge " + (LIVE.has(s.mode) ? "red" : s.mode === "kis_demo" ? "amber" : "green");
  $("#auto-badge").textContent = s.auto_trading ? "자동매매 실행 중" : "자동매매 꺼짐";
  $("#auto-badge").className = "badge " + (s.auto_trading ? "green" : "");
  $("#kill-badge").textContent = s.kill_switch ? "킬 스위치 켜짐" : "킬 스위치 꺼짐";
  $("#kill-badge").className = "badge " + (s.kill_switch ? "red" : "");
  $("#auto-btn").textContent = s.auto_trading ? "자동매매 중지" : "자동매매 시작";
  $("#kill-btn").textContent = s.kill_switch ? "킬 스위치 끄기" : "킬 스위치 켜기";
  $("#kill-btn").classList.toggle("on", s.kill_switch);
  const msgs = [s.error, s.scheduler_error && "스케줄러: " + s.scheduler_error].filter(Boolean);
  if (msgs.length) banner(msgs.join(" / "));
  else if (!s.keys.toss && ["mkt", "stock"].includes(state.tab)) banner("토스증권 키가 없어 체험용 데이터를 보여줍니다. 설정에서 토스증권 Client ID/Secret 을 등록하면 실제 시세로 바뀝니다.", "info");
  else if (!s.keys.anthropic && state.tab === "office") banner("AI 회의와 비서를 쓰려면 설정에서 Claude API 키를 등록하세요.", "info");
  else banner("");

  $("#market-cards").innerHTML = Object.entries(s.markets).map(([m, x]) => `
    <div class="card">
      <div class="k">${MARKET_NAME[m]} · ${x.enabled ? STRAT_NAME[x.strategy] || x.strategy : "사용 안 함"} ${x.open ? "· 장중" : ""}</div>
      <div class="v">${x.enabled ? money(x.equity, m) : "-"}</div>
      <div class="s">${x.account_error ? esc(x.account_error) :
        x.enabled ? `현금 ${money(x.cash, m)} · 보유 ${x.positions ?? 0}종목 · 다음 실행 ${x.next_run.replace("T", " ").slice(0, 16)}` : ""}</div>
    </div>`).join("");
}

function renderPositions(rows) {
  $("#pos-table tbody").innerHTML = rows.length ? rows.map((p) => {
    const pl = state.plans[p.symbol];
    return `<tr data-open="${esc(p.symbol)}" class="clickable">
    <td>${MARKET_NAME[p.market]}</td><td>${logo(p.symbol, p.symbol, 20)} ${esc(p.symbol)}</td><td class="r">${p.qty.toLocaleString()}</td>
    <td class="r">${money(p.avg_price, p.market)}</td><td class="r">${money(p.last_price, p.market)}</td>
    <td class="r ${cls(p.pnl)}">${pct(p.pnl)}</td>
    <td class="r down">${pl ? money(pl.stop, p.market) : "-"}</td><td class="r up">${pl ? money(pl.take, p.market) : "-"}</td></tr>`;
  }).join("") : `<tr><td colspan="8" class="note">보유 종목이 없습니다.</td></tr>`;
}

function reasonHtml(text) {
  return String(text || "").split(" | ").map((x) => `<li>${esc(x)}</li>`).join("");
}

function renderOrders(rows) {
  const st = { submitted: "접수", filled: "체결", rejected: "거부", dry_run: "계산만" };
  state.orders = rows;
  $("#ord-table tbody").innerHTML = rows.length ? rows.map((o, i) => `<tr class="clickable" data-ord="${i}">
    <td>${esc(o.trade_date)}</td><td>${esc(o.symbol)}</td>
    <td class="${o.side === "buy" ? "up" : "down"}">${o.side === "buy" ? "매수" : "매도"}${o.strategy === "ai-exit" ? " (자동)" : ""}</td>
    <td class="r">${o.qty.toLocaleString()}</td><td class="r">${money(o.price, o.market)}</td>
    <td>${st[o.status] || o.status}</td></tr>`).join("")
    : `<tr><td colspan="6" class="note">주문이 없습니다.</td></tr>`;
}
$("#ord-table").addEventListener("click", (e) => {
  const tr = e.target.closest("[data-ord]");
  if (!tr) return;
  const o = state.orders[tr.dataset.ord];
  modal(`<h3>${o.side === "buy" ? "매수" : "매도"} ${esc(o.symbol)} ${o.qty}주 · ${money(o.price, o.market)}</h3>
    <p class="note">${esc(o.ts)} · ${esc(o.strategy || "")} · ${esc(o.status)}</p><h4>이 주문의 근거</h4><ul class="why">${reasonHtml(o.message)}</ul>`);
});

function renderApprovals(rows) {
  $("#approvals-panel").classList.toggle("hidden", !rows.length);
  $("#appr-list").innerHTML = rows.map((a) => `<div class="appr ${a.side}">
    <div class="appr-head">${logo(a.symbol, a.symbol, 32)}<div><b>${a.side === "buy" ? "매수" : "매도"} ${esc(a.symbol)} ${a.qty.toLocaleString()}주</b>
      <div class="note">${esc(a.trade_date)} · 주문가 ${money(a.price, a.market)} · 약 ${money(a.price * a.qty, a.market)}</div></div>
      <div class="appr-btns"><button class="primary" data-appr="${a.id}" data-ok="1">근거 확인했고 승인</button><button data-appr="${a.id}" data-ok="0">거절</button></div></div>
    <ul class="why">${reasonHtml(a.reason)}</ul></div>`).join("");
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-appr]");
  if (!b) return;
  const ok = b.dataset.ok === "1";
  if (ok && LIVE.has(state.status?.mode) && prompt("실제 돈으로 주문합니다. 진행하려면 '실전주문' 이라고 입력하세요.") !== "실전주문") return;
  try {
    const r = await api(`/api/approvals/${b.dataset.appr}`, { method: "POST", body: { approve: ok } });
    alert(ok ? `주문 결과: ${r.status} ${r.message || ""}` : "거절했습니다.");
  } catch (err) { alert(err.message); }
  refresh();
});

function renderEvents(rows) {
  $("#events").innerHTML = rows.map((e) => `<li class="${e.level}"><span class="t">${esc(e.ts.replace("T", " ").slice(5, 19))}</span>${esc(e.message)}</li>`).join("")
    || `<li class="note">로그가 없습니다.</li>`;
}

async function runNow(dry) {
  const m = $("#run-market").value;
  const s = state.status;
  if (!dry) {
    if (!confirm(`${MODE_NAME[s.mode]} 계좌로 ${MARKET_NAME[m]} 주문을 지금 냅니다. 계속할까요?`)) return;
    if (LIVE.has(s.mode) && prompt("실제 돈으로 주문합니다. 진행하려면 '실전주문' 이라고 입력하세요.") !== "실전주문") return;
  }
  $("#run-out").textContent = "실행 중...";
  try {
    const r = await api("/api/run-once", { method: "POST", body: { market: m, dry_run: dry } });
    $("#run-out").textContent = formatRun(r);
  } catch (e) { $("#run-out").textContent = "실패: " + e.message; }
  refresh();
}

function formatRun(r) {
  if (r.error || r.skipped) return r.error || r.skipped;
  const lines = [`${r.trade_date} · ${STRAT_NAME[r.strategy] || r.strategy} · 운용금액 ${Math.round(r.capital).toLocaleString()}${r.dry_run ? " · 계산만" : ""}`];
  lines.push("목표 비중: " + (Object.entries(r.target).map(([k, v]) => `${k} ${(v * 100).toFixed(1)}%`).join(", ") || "전부 현금"));
  if (r.ai && r.ai.length) { lines.push("", "AI 회의 반영"); r.ai.forEach((a) => lines.push(`  ${a.symbol}: ${a.action}`)); }
  lines.push("", `주문 ${r.orders.length}건`);
  r.orders.forEach((o) => lines.push(`  ${o.side === "buy" ? "매수" : "매도"} ${o.symbol} ${o.qty}주 @ ${o.price} | ${o.status} ${o.message || ""}`));
  if (r.blocked.length) { lines.push("", `차단 ${r.blocked.length}건`); r.blocked.forEach((b) => lines.push(`  ${b.symbol}: ${b.reason}`)); }
  if (r.data_errors.length) { lines.push("", "데이터 오류"); r.data_errors.forEach((e) => lines.push("  " + e)); }
  if (r.kill_switch) lines.push("", "킬 스위치가 켜져 있어 신규 매수를 하지 않았습니다.");
  return lines.join("\n");
}

$("#dry-btn").onclick = () => runNow(true);
$("#run-btn").onclick = () => runNow(false);
$("#auto-btn").onclick = async () => {
  const s = state.status;
  const on = !s.auto_trading;
  if (on) {
    const c = state.config || await api("/api/config");
    const lines = Object.entries(c.markets).filter(([, mc]) => mc.enabled).map(([m, mc]) =>
      `· ${MARKET_NAME[m]}: ${STRAT_NAME[mc.strategy]} 전략, 매 거래일 ${mc.run_time}(${m === "KR" ? "한국" : "뉴욕"} 시간), 한도 ${money(mc.budget, m)}`);
    const rules = c.markets.KR.strategy === "ai" || c.markets.US.strategy === "ai"
      ? "\n· AI 회의 결과가 매수여도 체크리스트(추세·모멘텀·과열·변동성·손익비·팩트체크)를 모두 넘어야 삽니다.\n· 산 종목은 손절가·익절가에 닿으면 자동으로 팝니다." + (c.ai.require_approval ? "\n· 주문은 승인 대기에 먼저 올라오고, 근거를 보고 승인해야 나갑니다." : "")
      : "";
    if (!confirm(`${MODE_NAME[s.mode]} 계좌로 자동매매를 시작합니다.\n\n${lines.join("\n")}${rules}\n\n종목당 최대 ${Math.round(c.risk.max_weight * 100)}%, 하루 손실 ${Math.round(c.risk.daily_loss_limit * 100)}% 에 닿으면 킬 스위치가 켜집니다. 계속할까요?`)) return;
    if (LIVE.has(s.mode) && prompt("실전 자동매매입니다. 진행하려면 '실전주문' 이라고 입력하세요.") !== "실전주문") return;
  }
  await api("/api/auto", { method: "POST", body: { on } }).catch((e) => alert(e.message));
  refresh();
};
$("#kill-btn").onclick = async () => {
  await api("/api/kill-switch", { method: "POST", body: { on: !state.status.kill_switch } }).catch((e) => alert(e.message));
  refresh();
};
document.addEventListener("tab", (e) => refresh());

function readParams(root) {
  const out = {};
  root.querySelectorAll("[data-param]").forEach((i) => { if (i.value !== "") out[i.dataset.param] = Number(i.value); });
  return out;
}

// 설정
async function loadConfig() {
  const c = await api("/api/config");
  const pr = await api("/api/principles");
  state.config = c;
  $$("input[name=mode]").forEach((i) => (i.checked = i.value === c.mode));
  $("#live-confirmed").checked = !!c.live_confirmed;
  $("#data-source").value = c.data_source || "auto";
  $("#kis-account").value = c.kis.account || "";
  $("#toss-loc").checked = !!c.toss.us_loc;
  $("#sec-contact").value = c.sec_contact || "";
  $("#r-maxw").value = c.risk.max_weight; $("#r-maxe").value = c.risk.max_exposure;
  $("#r-loss").value = c.risk.daily_loss_limit;
  $("#r-ordkr").value = c.risk.max_order_value.KR; $("#r-ordus").value = c.risk.max_order_value.US;
  $("#watchlist").value = (c.watchlist || []).join(", ");
  $("#news-feeds").value = (c.news?.feeds || []).map((f) => `${f.name}|${f.url}`).join("\n");
  $("#ai-mode").value = c.ai.mode || "fast";
  $("#ai-quick").value = c.ai.quick_model || ""; $("#ai-deep").value = c.ai.deep_model || c.ai.model || "";
  $("#ai-effort").value = c.ai.effort; $("#ai-profile").value = c.ai.profile || "";
  $("#ai-approve").checked = c.ai.require_approval !== false;
  $("#ai-exit").checked = c.ai.auto_exit !== false;
  $("#ai-debate").value = c.ai.debate_rounds;
  $("#ai-before").value = c.ai.run_before_minutes; $("#ai-max").value = c.ai.max_symbols_per_day;
  $("#ai-conf").value = c.ai.min_confidence; $("#ai-web").checked = !!c.ai.web_research;
  const R = pr.rules;
  $("#rl-conf").value = R.min_confidence; $("#rl-rr").value = R.min_reward_risk; $("#rl-rsi").value = R.max_rsi;
  $("#rl-vol").value = R.max_vol; $("#rl-fact").value = R.min_fact_ok; $("#rl-buys").value = R.max_new_buys_per_day;
  $("#rl-cool").value = R.cooldown_days; $("#rl-trend").checked = R.require_trend; $("#rl-mom").checked = R.require_momentum;
  const stratOptions = [...Object.keys(state.strategies), "ai"];
  $("#market-cfg").innerHTML = Object.entries(c.markets).map(([m, mc]) => `
    <div class="panel" data-market="${m}">
      <h3>${MARKET_NAME[m]} 주식</h3>
      <label class="check"><input type="checkbox" data-f="enabled" ${mc.enabled ? "checked" : ""}> 이 시장 사용</label>
      <div class="form-row">
        <label>전략 <select data-f="strategy">${stratOptions.map((k) => `<option value="${k}" ${k === mc.strategy ? "selected" : ""}>${STRAT_NAME[k]}</option>`).join("")}</select></label>
        <label>실행 시각 (${m === "KR" ? "한국" : "뉴욕"}) <input data-f="run_time" value="${esc(mc.run_time)}"></label>
        <label>운용 한도 (${m === "KR" ? "원" : "$"}) <input data-f="budget" type="number" value="${mc.budget}"></label>
      </div>
      <label>종목 (쉼표로 구분${m === "US" ? ", 나스닥 외에는 NYSE:KO 처럼" : ""}) <input data-f="universe" value="${esc(mc.universe.join(", "))}"></label>
      <div class="form-row params" data-params></div>
    </div>`).join("");
  $$("#market-cfg [data-market]").forEach((el) => {
    const m = el.dataset.market;
    const draw = () => {
      const name = el.querySelector("[data-f=strategy]").value;
      const vals = name === c.markets[m].strategy ? c.markets[m].params : {};
      el.querySelector("[data-params]").innerHTML = name === "ai"
        ? `<p class="note">실행 시각 ${c.ai.run_before_minutes}분 전부터 종목마다 AI 투자 회의를 열고, 최종 등급과 매수 체크리스트로 비중을 정합니다. 보유·확신 부족·규칙 미통과면 그대로 둡니다.</p>`
        : Object.entries(state.strategies[name] || {}).map(([k, v]) =>
          `<label>${PARAM_NAME[k] || k}<input data-param="${k}" type="number" step="any" value="${vals[k] ?? v}"></label>`).join("");
    };
    el.querySelector("[data-f=strategy]").onchange = draw;
    draw();
  });
  refreshKeyState();
}

const KEY_LABELS = {
  toss: ["Client ID", "Client Secret"], anthropic: ["API 키", null], dart: ["인증키 (40자리)", null],
  kis_demo: ["앱키", "앱시크릿"], kis_real: ["앱키", "앱시크릿"],
};
function refreshKeyState() {
  const kind = $("#key-kind").value;
  const [a, b] = KEY_LABELS[kind];
  $("#key-label1").textContent = a;
  $("#key-secret-wrap").classList.toggle("hidden", !b);
  if (b) $("#key-label2").textContent = b;
  const s = state.status;
  if (s) $("#key-state").innerHTML = s.keys[kind] ? "<b class='up'>저장된 키 있음</b>" : "저장된 키 없음";
}
$("#key-kind").onchange = refreshKeyState;

$("#cfg-save").onclick = async () => {
  const c = structuredClone(state.config);
  c.mode = ($$("input[name=mode]").find((i) => i.checked) || {}).value || "paper";
  c.live_confirmed = $("#live-confirmed").checked;
  if (LIVE.has(c.mode) && c.live_confirmed && !state.config.live_confirmed &&
      prompt("실전 주문을 허용합니다. 진행하려면 '실전주문' 이라고 입력하세요.") !== "실전주문") return;
  c.data_source = $("#data-source").value;
  c.kis.account = $("#kis-account").value.trim();
  c.toss.us_loc = $("#toss-loc").checked;
  c.sec_contact = $("#sec-contact").value.trim();
  c.risk.max_weight = Number($("#r-maxw").value); c.risk.max_exposure = Number($("#r-maxe").value);
  c.risk.daily_loss_limit = Number($("#r-loss").value);
  c.risk.max_order_value = { KR: Number($("#r-ordkr").value), US: Number($("#r-ordus").value) };
  c.watchlist = $("#watchlist").value.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean);
  c.news = { feeds: $("#news-feeds").value.split("\n").map((l) => l.split("|")).filter((p) => p.length === 2 && /^https?:\/\//.test(p[1].trim()))
    .map(([n, u]) => ({ name: n.trim(), url: u.trim() })) };
  Object.assign(c.ai, {
    mode: $("#ai-mode").value, quick_model: $("#ai-quick").value.trim(), deep_model: $("#ai-deep").value.trim(),
    effort: $("#ai-effort").value, profile: $("#ai-profile").value.trim(), require_approval: $("#ai-approve").checked,
    auto_exit: $("#ai-exit").checked, debate_rounds: Number($("#ai-debate").value),
    run_before_minutes: Number($("#ai-before").value), max_symbols_per_day: Number($("#ai-max").value),
    min_confidence: Number($("#ai-conf").value), web_research: $("#ai-web").checked,
    rules: { min_confidence: Number($("#rl-conf").value), min_reward_risk: Number($("#rl-rr").value),
      max_rsi: Number($("#rl-rsi").value), max_vol: Number($("#rl-vol").value), min_fact_ok: Number($("#rl-fact").value),
      max_new_buys_per_day: Number($("#rl-buys").value), cooldown_days: Number($("#rl-cool").value),
      require_trend: $("#rl-trend").checked, require_momentum: $("#rl-mom").checked },
  });
  $$("#market-cfg [data-market]").forEach((el) => {
    const mc = c.markets[el.dataset.market];
    mc.enabled = el.querySelector("[data-f=enabled]").checked;
    mc.strategy = el.querySelector("[data-f=strategy]").value;
    mc.run_time = el.querySelector("[data-f=run_time]").value.trim();
    mc.budget = Number(el.querySelector("[data-f=budget]").value);
    mc.universe = el.querySelector("[data-f=universe]").value.split(",").map((x) => x.trim()).filter(Boolean);
    if (mc.strategy !== "ai") mc.params = readParams(el.querySelector("[data-params]"));
  });
  try {
    state.config = await api("/api/config", { method: "PUT", body: c });
    $("#cfg-msg").textContent = "저장했습니다.";
    refresh();
  } catch (e) { $("#cfg-msg").textContent = "저장 실패: " + e.message; }
};

$("#key-save").onclick = async () => {
  try {
    const r = await api("/api/secrets", { method: "PUT", body: { kind: $("#key-kind").value, key: $("#key-app").value, secret: $("#key-secret").value } });
    $("#key-app").value = ""; $("#key-secret").value = "";
    await refresh(); refreshKeyState();
    alert(r.keys[$("#key-kind").value] ? "키를 저장했습니다." : "저장이 확인되지 않았습니다. 다시 시도하세요.");
  } catch (e) { alert("저장 실패: " + e.message + (e.message.includes("토큰") ? "\n페이지를 새로고침(F5)한 뒤 다시 저장하세요." : "")); }
};
$("#key-del").onclick = async () => {
  if (!confirm("이 키를 삭제할까요?")) return;
  await api("/api/secrets/" + $("#key-kind").value, { method: "DELETE" }).catch((e) => alert(e.message));
  await refresh(); refreshKeyState();
};

// 시작
(async () => {
  state.strategies = await api("/api/strategies");
  await refresh();
  setInterval(refresh, 15000);
})();
