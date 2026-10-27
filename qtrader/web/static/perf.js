"use strict";
// 수익 리포트: 얼마를 벌었는지 한눈에. 총 손익 · 승률 · 손익비 · 최대 낙폭 · 누적 손익 곡선 · 거래별 결과

const pf = { data: null, period: "all", chart: null, series: null, demo: false, more: 10, key: "" };

function pfDemo() {
  // 예시 데이터 (화면 미리 보기 전용, 실제 기록 아님)
  const names = [["005930", "삼성전자"], ["000660", "SK하이닉스"], ["035720", "카카오"], ["373220", "LG에너지솔루션"], ["005380", "현대차"], ["068270", "셀트리온"]];
  const trades = [];
  let d = new Date(); d.setDate(d.getDate() - 52);
  const day = (x) => x.toLocaleDateString("sv-SE");
  for (let i = 0; i < 14; i++) {
    const [s, n] = names[i % names.length];
    const win = [1, 1, 0, 1, 0, 1, 1, 0, 1, 1, 0, 1, 0, 1][i];
    const buy = 50000 + (i * 7919) % 150000, pct = win ? 0.03 + (i % 4) * 0.02 : -0.025 - (i % 3) * 0.01, qty = 3 + (i % 5);
    const b = new Date(d); d.setDate(d.getDate() + 2 + (i % 3));
    trades.push({ symbol: s, name: n, qty, buy_date: day(b), sell_date: day(d), buy, sell: Math.round(buy * (1 + pct)),
      pnl: Math.round(qty * buy * pct), pct, days: 2 + (i % 3), why: win ? "익절" : i % 2 ? "손절" : "기간 만료" });
  }
  return pfCompute(trades.reverse(), 120000, 10000000);
}

function pfCompute(trades, open = 0, base = null) {
  const sorted = trades.slice().sort((a, b) => a.sell_date.localeCompare(b.sell_date));
  const curve = []; let cum = 0, peak = 0, mdd = 0;
  for (const t of sorted) {
    cum += t.pnl; peak = Math.max(peak, cum); mdd = Math.min(mdd, cum - peak);
    if (curve.length && curve.at(-1).time === t.sell_date) curve.at(-1).value = cum; else curve.push({ time: t.sell_date, value: cum });
  }
  const wins = trades.filter((t) => t.pnl > 0), losses = trades.filter((t) => t.pnl <= 0);
  const gw = wins.reduce((s, t) => s + t.pnl, 0), gl = -losses.reduce((s, t) => s + t.pnl, 0);
  const realized = cum;
  return { trades, curve, summary: { realized, open, total: realized + open, count: trades.length,
    win_rate: trades.length ? wins.length / trades.length : null, avg_win: wins.length ? gw / wins.length : 0,
    avg_loss: losses.length ? -gl / losses.length : 0, profit_factor: gl ? gw / gl : null, no_loss: !gl && wins.length > 0, mdd,
    avg_days: trades.length ? trades.reduce((s, t) => s + t.days, 0) / trades.length : null, base,
    total_rate: base ? (realized + open) / base : null } };
}

function pfFiltered() {
  const d = pf.data;
  if (!d || pf.period === "all") return d;
  const since = new Date(); since.setDate(since.getDate() - Number(pf.period) + 1);
  const s = since.toLocaleDateString("sv-SE");
  return pfCompute(d.trades.filter((t) => t.sell_date >= s), d.summary.open, d.summary.base);
}

function ring(v) {
  const r = 26, c = 2 * Math.PI * r, p = v == null ? 0 : v;
  return `<svg viewBox="0 0 64 64" class="pf-ring"><circle cx="32" cy="32" r="${r}" class="bg"/>
    <circle cx="32" cy="32" r="${r}" class="fg" stroke-dasharray="${c}" stroke-dashoffset="${c}" data-off="${c * (1 - p)}"/>
    <text x="32" y="37" text-anchor="middle">${v == null ? "-" : Math.round(p * 100) + "%"}</text></svg>`;
}

function renderPerf() {
  const box = $("#perf");
  if (!box) return;
  const m = dash.market, d = pfFiltered();
  const S = d?.summary;
  const fm = (v) => money(v, m);
  if (!d || (!S.count && !pf.demo)) {
    box.innerHTML = `<div class="pf-empty"><div class="pf-empty-ico"><i data-lucide="trophy"></i></div>
      <b>아직 사고판 거래가 없어요</b><span class="note">자동매매가 사고팔기 시작하면 여기서 얼마를 벌었는지, 몇 번 이겼는지, 가장 크게 잃었을 때가 언제인지 한눈에 보여 드려요.
      ${S?.open ? `<br>지금 가진 주식의 평가 손익: <b class="${cls(S.open)}">${fm(S.open)}</b>` : ""}</span>
      <button id="pf-demo-btn"><i data-lucide="eye"></i> 예시로 미리 보기</button></div>`;
    $("#pf-demo-btn").onclick = () => { pf.demo = true; pf.data = pfDemo(); renderPerf(); };
    icons();
    return;
  }
  const pfv = S.no_loss ? "∞" : S.profit_factor == null ? "-" : S.profit_factor.toFixed(2);
  const up = S.total >= 0;
  box.innerHTML = `
    ${pf.demo ? `<div class="pf-demo-note"><i data-lucide="info"></i> 예시 화면이에요. 실제 기록이 아닙니다. <button id="pf-demo-off">닫기</button></div>` : ""}
    <div class="pf-hero ${up ? "up" : "down"}">
      <div><span class="note">총 손익 ${pf.period === "all" ? "" : pf.period === "1" ? "· 오늘" : `· 최근 ${pf.period}일`}</span>
        <b id="pf-total" class="${cls(S.total)}">${fm(S.total)}</b>
        <div class="pf-sub">${S.total_rate != null ? `<span class="pf-chip ${cls(S.total)}">${pct(S.total_rate)}</span>` : ""}
          <span>실현 ${fm(S.realized)}</span><span>· 평가 ${fm(S.open)}</span></div></div>
      <div class="pf-ring-wrap">${ring(S.win_rate)}<div><b>승률</b><span class="note">${S.count}번 중 ${Math.round((S.win_rate || 0) * S.count)}번 이익</span></div></div>
    </div>
    <div class="pf-kpis">
      <div><span>손익비</span><b>${pfv}</b><em>번 돈 ÷ 잃은 돈</em></div>
      <div><span>평균 이익</span><b class="up">${fm(S.avg_win)}</b><em>이긴 거래 1번</em></div>
      <div><span>평균 손실</span><b class="down">${fm(S.avg_loss)}</b><em>진 거래 1번</em></div>
      <div><span>최대 낙폭</span><b class="down">${fm(S.mdd)}</b><em>가장 크게 줄었을 때</em></div>
      <div><span>평균 보유</span><b>${S.avg_days == null ? "-" : S.avg_days.toFixed(1) + "일"}</b><em>${S.count}번 거래</em></div>
    </div>
    <div class="pf-chart-wrap"><div class="pf-chart-head"><b>누적 손익</b><span class="note">판 날마다 쌓인 확정 손익</span></div><div id="pf-chart" class="pf-chart"></div></div>
    <div class="pf-list">${d.trades.slice(0, pf.more).map((t) => `<div class="pf-tr" data-open="${esc(t.symbol)}">
        ${logo(t.symbol, t.name || t.symbol, 30)}
        <div class="pf-tr-n"><b>${esc(t.name || t.symbol)}</b><span class="note">${esc(t.buy_date.slice(5))} → ${esc(t.sell_date.slice(5))} · ${t.days}일 보유 · ${t.qty}주</span></div>
        <span class="pf-why ${t.why === "익절" ? "up" : t.why === "손절" ? "down" : ""}">${esc(t.why)}</span>
        <div class="pf-tr-p"><span class="note">${fm(t.buy)} → ${fm(t.sell)}</span></div>
        <div class="pf-tr-r"><b class="${cls(t.pnl)}">${t.pnl > 0 ? "+" : ""}${fm(t.pnl)}</b><span class="${cls(t.pct)}">${pct(t.pct)}</span></div></div>`).join("")}
      ${d.trades.length > pf.more ? `<button class="nmore" id="pf-more">${d.trades.length - pf.more}건 더 보기</button>` : ""}</div>`;
  // 차트: 0 위는 빨강, 아래는 파랑
  if (window.LightweightCharts) {
    pf.chart = LightweightCharts.createChart($("#pf-chart"), {
      layout: { background: { color: "transparent" }, textColor: "#8a97a6" }, autoSize: true,
      grid: { vertLines: { visible: false }, horzLines: { color: "#1b222b" } },
      rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false },
      localization: { priceFormatter: (v) => m === "US" ? "$" + v.toFixed(0) : Math.round(v).toLocaleString() },
    });
    const s = pf.chart.addBaselineSeries({ baseValue: { type: "price", price: 0 }, lineWidth: 2,
      topLineColor: "#f04452", topFillColor1: "rgba(240,68,82,.28)", topFillColor2: "rgba(240,68,82,0)",
      bottomLineColor: "#3182f6", bottomFillColor1: "rgba(49,130,246,0)", bottomFillColor2: "rgba(49,130,246,.28)" });
    s.setData(d.curve.length === 1 ? [{ time: d.curve[0].time, value: 0 }, ...d.curve].filter((x, i, a) => i === 0 || x.time !== a[i - 1].time) : d.curve);
    pf.chart.timeScale().fitContent();
  }
  $("#pf-more")?.addEventListener("click", () => { pf.more += 10; renderPerf(); });
  $("#pf-demo-off")?.addEventListener("click", () => { pf.demo = false; loadPerf(true); });
  // 모션: 숫자 올라가기, 승률 링 채우기, 거래 줄 차례로
  const key = JSON.stringify([m, pf.period, pf.demo, S.count, S.total]);
  const fresh = key !== pf.key; pf.key = key;
  if (fresh) {
    countTo($("#pf-total"), S.total, { format: fm });
    animateIn("#perf .pf-kpis > div, #perf .pf-tr", { step: 30 });
  } else $("#pf-total").textContent = fm(S.total);
  requestAnimationFrame(() => $$("#perf .pf-ring .fg").forEach((c) => { c.style.transition = fresh ? "stroke-dashoffset 1s cubic-bezier(.2,.8,.2,1)" : "none"; c.style.strokeDashoffset = c.dataset.off; }));
  icons();
}

async function loadPerf(force = false) {
  if (pf.demo && !force) return renderPerf();
  let d = null;
  try { d = await api("/api/performance?market=" + dash.market); } catch {}
  const sig = JSON.stringify([dash.market, d]);
  if (!force && sig === pf.sig && $("#perf").childElementCount) return; // 바뀐 게 없으면 다시 그리지 않는다 (깜빡임 방지)
  pf.sig = sig; pf.data = d;
  renderPerf();
}

$("#perf-period")?.addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  pf.period = b.dataset.v;
  $$("#perf-period button").forEach((x) => x.classList.toggle("on", x === b));
  renderPerf();
});
const _loadDashExtras3 = window.loadDashExtras;
window.loadDashExtras = async () => { await Promise.all([_loadDashExtras3(), loadPerf()]); };
