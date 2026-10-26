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
    <div><span>필요한 월 투자액</span><b>${won(monthly)}</b></div>
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
    cpSeries = cpChart.addAreaSeries({ lineColor: "#4c8dff", topColor: "rgba(76,141,255,.35)", bottomColor: "rgba(0,0,0,0)" });
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
    <div><span>살 수량</span><b>${qty.toLocaleString()}주</b></div>
    <div><span>투입 금액</span><b>${won(cost)} (계좌의 ${(cost / acct * 100).toFixed(1)}%)</b></div>
    <div><span>손절 시 손실</span><b class="down">${won(qty * per)}</b></div>
    <div><span>손절 폭</span><b>${(per / entry * 100).toFixed(1)}%</b></div>`;
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

document.addEventListener("tab", (e) => { if (e.detail === "calc") { compound(); position(); txRender(); } });
