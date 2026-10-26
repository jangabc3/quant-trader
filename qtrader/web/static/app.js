"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const MODE_NAME = { paper: "가상매매", kis_demo: "KIS 모의투자", kis_real: "KIS 실전" };
const MARKET_NAME = { KR: "국내", US: "미국" };
const STRAT_NAME = { trend: "추세추종", momentum: "모멘텀 순위", meanrev: "평균회귀" };
const PARAM_NAME = {
  fast: "단기 이평", slow: "장기 이평", vol_lookback: "변동성 기간", target_vol: "목표 변동성(연)",
  lookback: "모멘텀 기간", skip: "최근 제외일", top_k: "보유 종목 수", rebalance_days: "리밸런싱 주기",
  rsi_n: "RSI 기간", entry_rsi: "진입 RSI", exit_rsi: "청산 RSI", bb_n: "볼린저 기간", bb_k: "볼린저 배수",
  max_hold: "최대 보유일",
};
let state = { status: null, config: null, strategies: {} };

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
const money = (v, m) => v == null ? "-" : (m === "US" ? "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 })
  : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: 0 }) + "원");
const pct = (v) => v == null ? "-" : (v * 100).toFixed(2) + "%";
const cls = (v) => v > 0 ? "up" : v < 0 ? "down" : "";

function banner(msg, kind = "") {
  const b = $("#banner");
  b.className = "banner " + kind + (msg ? "" : " hidden");
  b.textContent = msg || "";
}

// 탭
$$("nav button").forEach((b) => b.addEventListener("click", () => {
  $$("nav button").forEach((x) => x.classList.toggle("on", x === b));
  $$(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + b.dataset.tab));
  if (b.dataset.tab === "cfg") loadConfig();
}));

// 대시보드
async function refresh() {
  try {
    const s = await api("/api/status");
    state.status = s;
    renderStatus(s);
    if (!s.error) {
      const [pos, ords, evs] = await Promise.all([api("/api/positions"), api("/api/orders?limit=50"), api("/api/events?limit=80")]);
      renderPositions(pos); renderOrders(ords); renderEvents(evs);
    }
  } catch (e) { banner("서버 응답 오류: " + e.message); }
}

function renderStatus(s) {
  const mb = $("#mode-badge");
  mb.textContent = MODE_NAME[s.mode] + (s.data_source ? ` · 시세 ${s.data_source === "kis" ? "KIS" : "CSV"}` : "");
  mb.className = "badge " + (s.mode === "kis_real" ? "red" : s.mode === "kis_demo" ? "amber" : "green");
  $("#auto-badge").textContent = s.auto_trading ? "자동매매 실행 중" : "자동매매 꺼짐";
  $("#auto-badge").className = "badge " + (s.auto_trading ? "green" : "");
  $("#kill-badge").textContent = s.kill_switch ? "킬 스위치 켜짐" : "킬 스위치 꺼짐";
  $("#kill-badge").className = "badge " + (s.kill_switch ? "red" : "");
  $("#auto-btn").textContent = s.auto_trading ? "자동매매 중지" : "자동매매 시작";
  $("#kill-btn").textContent = s.kill_switch ? "킬 스위치 끄기" : "킬 스위치 켜기";
  $("#kill-btn").classList.toggle("on", s.kill_switch);
  const msgs = [s.error, s.scheduler_error && "스케줄러: " + s.scheduler_error].filter(Boolean);
  if (msgs.length) banner(msgs.join(" / "));
  else if (s.data_source === "csv") banner("API 키가 없어 CSV 시세를 씁니다. 체험용 데이터는 'qtrader demo-data' 로 만들 수 있고, 실제 시세는 설정에서 KIS 키를 등록하세요.", "info");
  else banner("");

  $("#market-cards").innerHTML = Object.entries(s.markets).map(([m, x]) => `
    <div class="card">
      <div class="k">${MARKET_NAME[m]} · ${x.enabled ? STRAT_NAME[x.strategy] : "사용 안 함"} ${x.open ? "· 장중" : ""}</div>
      <div class="v">${x.enabled ? money(x.equity, m) : "-"}</div>
      <div class="s">${x.account_error ? esc(x.account_error) :
        x.enabled ? `현금 ${money(x.cash, m)} · 보유 ${x.positions ?? 0}종목 · 다음 실행 ${x.next_run.replace("T", " ").slice(0, 16)}` : ""}</div>
    </div>`).join("");
}

function renderPositions(rows) {
  $("#pos-table tbody").innerHTML = rows.length ? rows.map((p) => `<tr>
    <td>${MARKET_NAME[p.market]}</td><td>${esc(p.symbol)}</td><td class="r">${p.qty.toLocaleString()}</td>
    <td class="r">${money(p.avg_price, p.market)}</td><td class="r">${money(p.last_price, p.market)}</td>
    <td class="r">${money(p.value, p.market)}</td><td class="r ${cls(p.pnl)}">${pct(p.pnl)}</td></tr>`).join("")
    : `<tr><td colspan="7" class="note">보유 종목이 없습니다.</td></tr>`;
}

function renderOrders(rows) {
  const st = { submitted: "접수", filled: "체결", rejected: "거부", dry_run: "계산만" };
  $("#ord-table tbody").innerHTML = rows.length ? rows.map((o) => `<tr title="${esc(o.message)}">
    <td>${esc(o.trade_date)}</td><td>${esc(o.symbol)}</td>
    <td class="${o.side === "buy" ? "up" : "down"}">${o.side === "buy" ? "매수" : "매도"}</td>
    <td class="r">${o.qty.toLocaleString()}</td><td class="r">${money(o.price, o.market)}</td>
    <td>${st[o.status] || o.status}</td></tr>`).join("")
    : `<tr><td colspan="6" class="note">주문이 없습니다.</td></tr>`;
}

function renderEvents(rows) {
  $("#events").innerHTML = rows.map((e) => `<li class="${e.level}"><span class="t">${esc(e.ts.replace("T", " ").slice(5, 19))}</span>${esc(e.message)}</li>`).join("")
    || `<li class="note">로그가 없습니다.</li>`;
}

async function runNow(dry) {
  const m = $("#run-market").value;
  const s = state.status;
  if (!dry) {
    const what = `${MODE_NAME[s.mode]} 계좌로 ${MARKET_NAME[m]} 주문을 지금 냅니다.`;
    if (!confirm(what + " 계속할까요?")) return;
    if (s.mode === "kis_real" && prompt("실제 돈으로 주문합니다. 진행하려면 '실전주문' 이라고 입력하세요.") !== "실전주문") return;
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
  lines.push("", `주문 ${r.orders.length}건`);
  r.orders.forEach((o) => lines.push(`  ${o.side === "buy" ? "매수" : "매도"} ${o.symbol} ${o.qty}주 @ ${o.price} — ${o.status} ${o.message || ""}`));
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
  if (on && s.mode !== "paper" && !confirm(`${MODE_NAME[s.mode]} 계좌로 자동매매를 시작합니다. 매 거래일 설정한 시각에 주문이 나갑니다. 계속할까요?`)) return;
  if (on && s.mode === "kis_real" && prompt("실전 자동매매입니다. 진행하려면 '실전주문' 이라고 입력하세요.") !== "실전주문") return;
  await api("/api/auto", { method: "POST", body: { on } }).catch((e) => alert(e.message));
  refresh();
};
$("#kill-btn").onclick = async () => {
  await api("/api/kill-switch", { method: "POST", body: { on: !state.status.kill_switch } }).catch((e) => alert(e.message));
  refresh();
};

// 백테스트
let chart, eqSeries, bmSeries;
function strategyParamsForm(name, values = {}) {
  const defs = state.strategies[name] || {};
  $("#bt-params").innerHTML = Object.entries(defs).map(([k, v]) =>
    `<label>${PARAM_NAME[k] || k}<input data-param="${k}" type="number" step="any" value="${values[k] ?? v}"></label>`).join("");
}
function readParams(root) {
  const out = {};
  root.querySelectorAll("[data-param]").forEach((i) => { if (i.value !== "") out[i.dataset.param] = Number(i.value); });
  return out;
}
$("#bt-strategy").onchange = () => strategyParamsForm($("#bt-strategy").value);
$("#bt-run").onclick = async () => {
  const btn = $("#bt-run");
  btn.disabled = true; btn.textContent = "계산 중...";
  const uni = $("#bt-universe").value.split(",").map((x) => x.trim()).filter(Boolean);
  try {
    const r = await api("/api/backtest", { method: "POST", body: {
      market: $("#bt-market").value, strategy: $("#bt-strategy").value, params: readParams($("#bt-params")),
      universe: uni.length ? uni : null, days: Number($("#bt-days").value) } });
    renderBacktest(r);
  } catch (e) { alert("백테스트 실패: " + e.message); }
  btn.disabled = false; btn.textContent = "백테스트 실행";
};

function renderBacktest(r) {
  const m = r.metrics;
  const card = (k, v, s = "", c = "") => `<div class="card"><div class="k">${k}</div><div class="v ${c}">${v}</div><div class="s">${s}</div></div>`;
  $("#bt-metrics").innerHTML = [
    card("누적 수익률", pct(m.total_return), `${m.days}거래일`, cls(m.total_return)),
    card("연평균 수익률(CAGR)", pct(m.cagr), `동일비중 보유 ${pct(m.benchmark_cagr)}`, cls(m.cagr)),
    card("최대 낙폭(MDD)", pct(m.max_drawdown), `동일비중 보유 ${pct(m.benchmark_max_drawdown)}`),
    card("샤프 지수", m.sharpe, `소르티노 ${m.sortino}`),
    card("연 변동성", pct(m.volatility), `연 회전율 ${m.annual_turnover}배`),
    card("현재 목표 비중", Object.keys(r.last_weights).length + "종목",
      Object.entries(r.last_weights).map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`).join(", ") || "전부 현금"),
  ].join("") + (r.data_errors.length ? card("데이터 오류", r.data_errors.length + "건", esc(r.data_errors.join(" / "))) : "");

  if (!window.LightweightCharts) { $("#bt-chart").textContent = "차트 라이브러리를 불러오지 못했습니다(인터넷 연결 확인)."; return; }
  if (!chart) {
    chart = LightweightCharts.createChart($("#bt-chart"), {
      layout: { background: { color: "transparent" }, textColor: "#8b98a5" },
      grid: { vertLines: { color: "#1e262f" }, horzLines: { color: "#1e262f" } },
      rightPriceScale: { borderColor: "#26303b" }, timeScale: { borderColor: "#26303b" }, autoSize: true,
    });
    bmSeries = chart.addLineSeries({ color: "#6b7785", lineWidth: 1 });
    eqSeries = chart.addLineSeries({ color: "#4c8dff", lineWidth: 2 });
  }
  eqSeries.setData(r.equity); bmSeries.setData(r.benchmark); chart.timeScale().fitContent();
}

// 설정
async function loadConfig() {
  const c = await api("/api/config");
  state.config = c;
  $$("input[name=mode]").forEach((i) => (i.checked = i.value === c.mode));
  $("#live-confirmed").checked = !!c.live_confirmed;
  $("#kis-account").value = c.kis.account || "";
  $("#r-maxw").value = c.risk.max_weight; $("#r-maxe").value = c.risk.max_exposure;
  $("#r-loss").value = c.risk.daily_loss_limit;
  $("#r-ordkr").value = c.risk.max_order_value.KR; $("#r-ordus").value = c.risk.max_order_value.US;
  $("#market-cfg").innerHTML = Object.entries(c.markets).map(([m, mc]) => `
    <div class="panel" data-market="${m}">
      <h3>${MARKET_NAME[m]} 주식</h3>
      <label class="check"><input type="checkbox" data-f="enabled" ${mc.enabled ? "checked" : ""}> 이 시장 사용</label>
      <div class="form-row">
        <label>전략 <select data-f="strategy">${Object.keys(state.strategies).map((k) => `<option value="${k}" ${k === mc.strategy ? "selected" : ""}>${STRAT_NAME[k]}</option>`).join("")}</select></label>
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
      el.querySelector("[data-params]").innerHTML = Object.entries(state.strategies[name] || {}).map(([k, v]) =>
        `<label>${PARAM_NAME[k] || k}<input data-param="${k}" type="number" step="any" value="${vals[k] ?? v}"></label>`).join("");
    };
    el.querySelector("[data-f=strategy]").onchange = draw;
    draw();
  });
  refreshKeyState();
}

function refreshKeyState() {
  const s = state.status;
  if (!s) return;
  const env = $("#key-env").value;
  $("#key-state").textContent = s.keys[env] ? "저장된 키 있음" : "저장된 키 없음";
}
$("#key-env").onchange = refreshKeyState;

$("#cfg-save").onclick = async () => {
  const c = structuredClone(state.config);
  c.mode = ($$("input[name=mode]").find((i) => i.checked) || {}).value || "paper";
  c.live_confirmed = $("#live-confirmed").checked;
  if (c.mode === "kis_real" && c.live_confirmed && !state.config.live_confirmed &&
      prompt("실전 주문을 허용합니다. 진행하려면 '실전주문' 이라고 입력하세요.") !== "실전주문") return;
  c.kis.account = $("#kis-account").value.trim();
  c.risk.max_weight = Number($("#r-maxw").value); c.risk.max_exposure = Number($("#r-maxe").value);
  c.risk.daily_loss_limit = Number($("#r-loss").value);
  c.risk.max_order_value = { KR: Number($("#r-ordkr").value), US: Number($("#r-ordus").value) };
  $$("#market-cfg [data-market]").forEach((el) => {
    const mc = c.markets[el.dataset.market];
    mc.enabled = el.querySelector("[data-f=enabled]").checked;
    mc.strategy = el.querySelector("[data-f=strategy]").value;
    mc.run_time = el.querySelector("[data-f=run_time]").value.trim();
    mc.budget = Number(el.querySelector("[data-f=budget]").value);
    mc.universe = el.querySelector("[data-f=universe]").value.split(",").map((x) => x.trim()).filter(Boolean);
    mc.params = readParams(el.querySelector("[data-params]"));
  });
  try {
    state.config = await api("/api/config", { method: "PUT", body: c });
    $("#cfg-msg").textContent = "저장했습니다.";
    refresh();
  } catch (e) { $("#cfg-msg").textContent = "저장 실패: " + e.message; }
};

$("#key-save").onclick = async () => {
  try {
    await api("/api/secrets", { method: "PUT", body: { env: $("#key-env").value, app_key: $("#key-app").value, app_secret: $("#key-secret").value } });
    $("#key-app").value = ""; $("#key-secret").value = "";
    await refresh(); refreshKeyState();
    alert("키를 저장했습니다.");
  } catch (e) { alert("저장 실패: " + e.message); }
};
$("#key-del").onclick = async () => {
  if (!confirm("이 구분의 키를 삭제할까요?")) return;
  await api("/api/secrets/" + $("#key-env").value, { method: "DELETE" });
  await refresh(); refreshKeyState();
};

// 시작
(async () => {
  state.strategies = await api("/api/strategies");
  $("#bt-strategy").innerHTML = Object.keys(state.strategies).map((k) => `<option value="${k}">${STRAT_NAME[k]}</option>`).join("");
  $("#bt-strategy").value = "momentum";
  strategyParamsForm("momentum");
  await refresh();
  setInterval(refresh, 15000);
})();
