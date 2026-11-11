"use strict";
// 계산기: 목표 자산 복리 계산기, 포지션 크기 계산기, 미국 주식 양도소득세 계산기 (모두 이 PC 안에서만 계산)

const num = (id) => Number($(id).value) || 0;
const won = (v) => Math.round(v).toLocaleString("ko-KR") + "원";
const man = (v) => (Math.round(v / 1e4)).toLocaleString("ko-KR") + "만원";
let cpChart, cpSeries, cpGoalSeries;

// 목표 자산 복리 계산기: 매달 같은 금액을 넣을 때 목표(오늘 가치)에 도달하려면 얼마가 필요한가
function compound() {
  $("#cp-ret-v").textContent = $("#cp-ret").value + "%";
  $("#cp-inf-v").textContent = $("#cp-inf").value + "%";
  const years = Math.max(1, num("#cp-goalage") - num("#cp-age"));
  const n = years * 12;
  const r = num("#cp-ret") / 100 / 12;
  const inf = num("#cp-inf") / 100;
  const goal = num("#cp-goal") * 1e4 * Math.pow(1 + inf, years); // 미래 가치로 환산한 목표
  const pv = num("#cp-now") * 1e4;
  const fvNow = pv * Math.pow(1 + r, n);
  const factor = r ? (Math.pow(1 + r, n) - 1) / r : n;
  const monthly = Math.max(0, (goal - fvNow) / factor);
  const paid = pv + monthly * n;
  $("#cp-out").innerHTML = `
    <div class="big-res"><span>매달 넣어야 할 돈</span><b>${won(monthly)}</b><em>${years}년 동안</em></div>
    <div><span>목표 (${years}년 뒤 물가 반영)</span><b>${man(goal)}</b></div>
    <div><span>총 납입 원금</span><b>${man(paid)}</b></div>
    <div><span>복리로 불어난 몫</span><b class="up">${man(goal - paid)}</b></div>`;
  if (!window.LightweightCharts) return;
  if (!cpChart) {
    cpChart = LightweightCharts.createChart($("#cp-chart"), {
      layout: { background: { color: "transparent" }, textColor: "#8b98a5" }, autoSize: true,
      grid: { vertLines: { color: "#1e262f" }, horzLines: { color: "#1e262f" } },
      rightPriceScale: { borderColor: "#26303b" }, timeScale: { borderColor: "#26303b" },
      localization: { priceFormatter: (v) => man(v) },
    });
    cpSeries = cpChart.addAreaSeries({ lineColor: "#3182f6", topColor: "rgba(49,130,246,.35)", bottomColor: "rgba(0,0,0,0)" });
    cpGoalSeries = cpChart.addLineSeries({ color: "#ffd34d", lineWidth: 1, lineStyle: 2 });
  }
  const start = new Date().getFullYear();
  const pts = [], gl = [];
  let bal = pv;
  for (let y = 0; y <= years; y++) {
    pts.push({ time: `${start + y}-01-01`, value: bal });
    gl.push({ time: `${start + y}-01-01`, value: goal });
    for (let k = 0; k < 12; k++) bal = bal * (1 + r) + monthly;
  }
  cpSeries.setData(pts); cpGoalSeries.setData(gl); cpChart.timeScale().fitContent();
}
["#cp-age", "#cp-goalage", "#cp-goal", "#cp-now", "#cp-ret", "#cp-inf"].forEach((id) => $(id).addEventListener("input", compound));

// 포지션 크기: (계좌 × 최대 손실%) ÷ (진입가 - 손절가)
function position() {
  const acct = num("#ps-acct"), risk = num("#ps-risk") / 100, entry = num("#ps-entry"), stop = num("#ps-stop");
  const per = entry - stop;
  if (per <= 0 || entry <= 0) { $("#ps-out").innerHTML = `<div class="down">손절가는 진입가보다 낮아야 합니다</div>`; return; }
  const qty = Math.floor(acct * risk / per);
  const cost = qty * entry;
  $("#ps-out").innerHTML = `
    <div class="big-res"><span>살 수량</span><b>${qty.toLocaleString()}주</b><em>이만큼 사면 손절돼도 계좌의 ${(risk * 100).toFixed(1)}%만 잃습니다</em></div>
    <div><span>투입 금액</span><b>${won(cost)}</b><em>계좌의 ${(cost / acct * 100).toFixed(1)}%</em></div>
    <div><span>손절되면</span><b class="down">-${won(qty * per)}</b><em>손절 폭 ${(per / entry * 100).toFixed(1)}%</em></div>`;
}
["#ps-acct", "#ps-risk", "#ps-entry", "#ps-stop"].forEach((id) => $(id).addEventListener("input", position));

// 미국 주식 양도소득세 (한국 거주자): 연간 해외주식 손익 통산 - 기본공제 250만원, 세율 22%
const TX_KEY = "qtrader.tax.rows";
function txRows() { try { return JSON.parse(localStorage.getItem(TX_KEY)) || null; } catch { return null; } }
function txSave(rows) { try { localStorage.setItem(TX_KEY, JSON.stringify(rows)); } catch {} }
let tx = txRows() || [{ sym: "AAPL", qty: 10, buy: 180, bfx: 1350, sell: 230, sfx: 1400, fee: 15000 }];

function txRender() {
  $("#tx-table tbody").innerHTML = tx.map((t, i) => `<tr>
    ${["sym", "qty", "buy", "bfx", "sell", "sfx", "fee"].map((k) => `<td class="${k === "sym" ? "" : "r"}"><input data-tx="${i}" data-k="${k}" value="${esc(t[k])}" ${k === "sym" ? "" : 'type="number" step="any"'}></td>`).join("")}
    <td class="r" id="tx-g${i}"></td><td><button data-txdel="${i}">삭제</button></td></tr>`).join("");
  txCalc();
}
function txCalc() {
  let total = 0;
  tx.forEach((t, i) => {
    const g = Number(t.qty) * (Number(t.sell) * Number(t.sfx) - Number(t.buy) * Number(t.bfx)) - Number(t.fee || 0);
    total += g;
    const el = $("#tx-g" + i);
    if (el) { el.textContent = won(g); el.className = "r " + cls(g); }
  });
  const base = Math.max(0, total - 2_500_000);
  const tax = base * 0.22;
  $("#tx-out").innerHTML = `
    <div><span>연간 손익 합계</span><b class="${cls(total)}">${won(total)}</b></div>
    <div><span>기본공제</span><b>2,500,000원</b></div>
    <div><span>과세표준</span><b>${won(base)}</b></div>
    <div><span>예상 세금 (22%)</span><b class="down">${won(tax)}</b></div>
    <div><span>세후 손익</span><b>${won(total - tax)}</b></div>`;
  txSave(tx);
}
$("#tx-table").addEventListener("input", (e) => {
  const i = e.target.dataset.tx;
  if (i == null) return;
  tx[i][e.target.dataset.k] = e.target.dataset.k === "sym" ? e.target.value : Number(e.target.value);
  txCalc();
});
$("#tx-table").addEventListener("click", (e) => {
  const d = e.target.closest("[data-txdel]");
  if (d) { tx.splice(Number(d.dataset.txdel), 1); txRender(); }
});
$("#tx-add").onclick = () => { tx.push({ sym: "", qty: 0, buy: 0, bfx: 1400, sell: 0, sfx: 1400, fee: 0 }); txRender(); };

document.addEventListener("tab", (e) => { if (e.detail === "calc") { position(); txRender(); compound(); } });


$("#calc-tab").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  $$("#calc-tab button").forEach((x) => x.classList.toggle("on", x === b));
  ["pos", "cp", "tax", "sig"].forEach((k) => $("#calc-" + k).classList.toggle("hidden", k !== b.dataset.v));
  if (b.dataset.v === "cp") compound();
  if (b.dataset.v === "sig") sigTest(false);
});

// 사는 신호 검증: 자동매매 규칙(신호 + 절반 익절·따라 올라가는 손절·기간 정리)을 지난 일봉으로
const sg = { market: "KR", chart: null, busy: false };
$("#sg-mkt").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  sg.market = b.dataset.v;
  $$("#sg-mkt button").forEach((x) => x.classList.toggle("on", x === b));
  sigTest(false);
});
$("#sg-run").onclick = () => sigTest(true);
async function sigTest(run) {
  const out = $("#sg-out");
  if (sg.busy) return;
  sg.busy = true;
  if (run) out.innerHTML = `<div class="skeleton" style="height:220px"></div><p class="note">지난 시세를 받아 돌려 보는 중이에요...</p>`;
  try {
    const d = await api("/api/signal-test", { method: "POST", body: { market: sg.market, refresh: run } });
    sigRender(d);
  } catch (e) {
    if (run) out.innerHTML = `<p class="note">${esc(e.message)}</p>`;
  } finally { sg.busy = false; }
}
function sigRender(d) {
  const s = d.summary || {}, w = Math.round((d.weight || 0.2) * 100);
  const pf = s.profit_factor == null ? "-" : s.profit_factor.toFixed(2);
  const k = (label, v, sub, c = "") => `<div><span>${label}</span><b class="${c}">${v}</b><em>${sub}</em></div>`;
  if (!s.trades) {
    $("#sg-out").innerHTML = `<div class="empty"><i data-lucide="search-x"></i><p>신호가 한 번도 뜨지 않았어요</p><span class="note">종목 ${d.symbols}개 · 지난 3년 동안 규칙에 맞는 자리가 없었어요.</span></div>`;
    icons(); return;
  }
  const names = d.names || {};
  $("#sg-out").innerHTML = `<p class="note">${esc(d.date || "")} 기준 · 종목 ${d.symbols}개${d.skipped?.length ? ` (자료 부족 ${d.skipped.length}개 뺌)` : ""}${d.cached ? " · 오늘 돌린 결과 (다시 돌리려면 '돌려 보기')" : ""}</p>
    <div class="calc-out sg-kpi">
      ${k("거래 수", `${s.trades}번`, `평균 ${s.avg_days}거래일 들고 있음`)}
      ${k("이긴 비율", `${Math.round(s.win_rate * 100)}%`, `절반 익절까지 간 거래 ${Math.round(s.half_rate * 100)}%`)}
      ${k("거래 1번 평균", pct(s.avg), `이길 때 ${pct(s.avg_win)} · 질 때 ${pct(s.avg_loss)}`, cls(s.avg))}
      ${k("손익비 (번 돈 ÷ 잃은 돈)", pf, s.profit_factor > 1.05 ? "번 돈이 잃은 돈보다 많았어요" : s.profit_factor < 0.95 ? "잃은 돈이 번 돈보다 많았어요" : "번 돈과 잃은 돈이 비슷했어요",
        s.profit_factor > 1.05 ? "up" : s.profit_factor < 0.95 ? "down" : "")}
      ${k(`계좌 수익 (거래마다 ${w}%씩)`, pct(s.total), `가장 많이 빠진 구간 ${pct(s.max_drawdown)}`, cls(s.total))}
    </div>
    <div id="sg-chart" class="chart small"></div>
    <h4>신호별</h4>
    <table><thead><tr><th>신호</th><th class="r">거래</th><th class="r">이긴 비율</th><th class="r">평균</th><th class="r">손익비</th><th class="r">평균 R</th></tr></thead><tbody>
      ${Object.entries(d.by_kind || {}).map(([n, x]) => `<tr><td>${esc(n)}</td><td class="r">${x.trades}</td><td class="r">${Math.round(x.win_rate * 100)}%</td>
        <td class="r ${cls(x.avg)}">${pct(x.avg)}</td><td class="r">${x.profit_factor == null ? "-" : x.profit_factor.toFixed(2)}</td><td class="r">${x.avg_r}</td></tr>`).join("")}</tbody></table>
    <h4>최근 거래 <small class="note">줄을 누르면 종목 화면</small></h4>
    <table><thead><tr><th>종목</th><th>신호</th><th>산 날</th><th>판 날</th><th>어떻게</th><th class="r">수익률</th></tr></thead><tbody>
      ${(d.trades || []).map((t) => `<tr class="clickable" data-open="${esc(t.symbol)}"><td>${esc(names[t.symbol] || t.symbol)}</td><td>${esc(t.kind)}</td>
        <td>${esc(t.entry_date.slice(2))}</td><td>${esc(t.exit_date.slice(2))}</td><td class="note">${esc(t.how)}</td><td class="r ${cls(t.ret)}">${pct(t.ret)}</td></tr>`).join("")}</tbody></table>`;
  icons();
  if (!window.LightweightCharts || !(d.curve || []).length) return;
  const ch = LightweightCharts.createChart($("#sg-chart"), {
    layout: { background: { color: "transparent" }, textColor: "#8b98a5" }, autoSize: true,
    grid: { vertLines: { color: "#1e262f" }, horzLines: { color: "#1e262f" } },
    rightPriceScale: { borderColor: "#26303b" }, timeScale: { borderColor: "#26303b" },
    localization: { priceFormatter: (v) => (v * 100).toFixed(1) + "%" },
  });
  const up = (d.curve.at(-1)?.value || 0) >= 0;
  ch.addAreaSeries({ lineColor: up ? "#f04452" : "#3182f6", topColor: up ? "rgba(240,68,82,.3)" : "rgba(49,130,246,.3)", bottomColor: "rgba(0,0,0,0)" }).setData(d.curve);
  ch.timeScale().fitContent();
}
