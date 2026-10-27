"use strict";
// 내 계좌: 총자산·오늘 손익·누적 수익률 한눈에, 자산 추이, 자산 구성, 보유 종목 카드(손절~익절 위치), 주문·미체결·기록 탭

const dash = { market: "KR", data: null, chart: null, series: null, tab: "orders" };

function kpi(label, valueHtml, sub = "", cls_ = "", id = "") {
  return `<div class="kpi ${cls_}"><span>${label}</span><b ${id ? `id="${id}"` : ""}>${valueHtml}</b><em>${sub}</em></div>`;
}

window.renderDash = (d) => {
  dash.data = d;
  const m = dash.market;
  $("#run-market").value = m;
  const ov = d.overview?.markets?.[m];
  const pos = d.positions.filter((p) => p.market === m);
  // 핵심 숫자
  if (!ov) {
    $("#kpis").innerHTML = `<div class="kpi wide"><span>${MARKET_NAME[m]} 계좌</span><b>사용 안 함</b><em>설정에서 ${MARKET_NAME[m]} 주식 자동매매를 켜면 여기 표시됩니다</em></div>`;
  } else if (ov.error) {
    $("#kpis").innerHTML = `<div class="kpi wide"><span>${MARKET_NAME[m]} 계좌</span><b class="down">불러오기 실패</b><em>${esc(ov.error)}</em></div>`;
  } else {
    const fm = (v) => money(v, m);
    $("#kpis").innerHTML = [
      kpi("총 자산", "", `현금 ${fm(ov.cash)} · 주식 ${fm(ov.invested)}`, "hero", "k-eq"),
      kpi("오늘", ov.daily_return == null ? "-" : pct(ov.daily_return), "전 거래일 대비", cls(ov.daily_return)),
      kpi("누적 수익률", ov.total_return == null ? "-" : pct(ov.total_return), "처음 기록 대비", cls(ov.total_return)),
      kpi("평가 손익", fm(ov.unrealized), `${pct(ov.unrealized_rate)} · 보유 ${ov.positions}종목`, cls(ov.unrealized)),
    ].join("");
    countTo($("#k-eq"), ov.equity, { format: (v) => money(v, m) });
  }
  // 자산 추이
  const hist = ov?.history || [];
  if (window.LightweightCharts) {
    if (!dash.chart) {
      dash.chart = LightweightCharts.createChart($("#equity-chart"), {
        layout: { background: { color: "transparent" }, textColor: "#8a97a6" }, autoSize: true,
        grid: { vertLines: { visible: false }, horzLines: { color: "#1b222b" } },
        rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false },
      });
      dash.series = dash.chart.addAreaSeries({ lineColor: "#3182f6", topColor: "rgba(49,130,246,.3)", bottomColor: "rgba(0,0,0,0)", lineWidth: 2 });
    }
    dash.series.setData(hist);
    dash.chart.timeScale().fitContent();
  }
  $("#eq-note").textContent = hist.length < 2 ? "자동매매가 하루 이상 돌면 날마다의 자산이 쌓여 그래프가 그려집니다." : `${hist.length}거래일 기록`;
  // 자산 구성 (도넛)
  const parts = pos.map((p) => ({ name: p.symbol, v: p.value })).concat(ov ? [{ name: "현금", v: ov.cash }] : []);
  const tot = parts.reduce((s, x) => s + x.v, 0) || 1;
  const colors = ["#3182f6", "#f04452", "#2fbf71", "#e9a23b", "#8b5cf6", "#0ea5e9", "#e11d74", "#14b8a6", "#6b7785"];
  let acc = 0;
  const arcs = parts.map((x, i) => {
    const a0 = acc / tot * Math.PI * 2, a1 = (acc += x.v) / tot * Math.PI * 2;
    const r = 70, cx = 90, cy = 90, large = a1 - a0 > Math.PI ? 1 : 0;
    const p0 = [cx + r * Math.sin(a0), cy - r * Math.cos(a0)], p1 = [cx + r * Math.sin(a1 - 0.0001), cy - r * Math.cos(a1 - 0.0001)];
    const c = x.name === "현금" ? "#3a4656" : colors[i % colors.length];
    return { c, path: `M${p0[0]},${p0[1]} A${r},${r} 0 ${large} 1 ${p1[0]},${p1[1]}`, x };
  });
  $("#alloc").innerHTML = parts.length ? `<div class="alloc"><svg viewBox="0 0 180 180" class="donut">${arcs.map((a) =>
    `<path d="${a.path}" stroke="${a.c}" stroke-width="26" fill="none"/>`).join("")}
    <text x="90" y="86" text-anchor="middle" fill="#8a97a6" font-size="11">주식 비중</text>
    <text x="90" y="104" text-anchor="middle" fill="#e8edf3" font-size="18" font-weight="800">${Math.round((1 - (ov?.cash || 0) / tot) * 100)}%</text></svg>
    <div class="legend">${arcs.map((a) => `<div><i style="background:${a.c}"></i><span>${esc(a.x.name)}</span><b>${(a.x.v / tot * 100).toFixed(1)}%</b></div>`).join("")}</div></div>`
    : `<p class="note">자산 정보가 없습니다.</p>`;
  // 보유 종목 카드
  $("#holdings").innerHTML = pos.length ? pos.map((p) => {
    const pl = state.plans[p.symbol];
    let bar = "";
    if (pl) {
      const lo = pl.stop, hi = pl.take, at = Math.max(0, Math.min(100, (p.last_price - lo) / (hi - lo) * 100));
      const en = Math.max(0, Math.min(100, (pl.entry - lo) / (hi - lo) * 100));
      bar = `<div class="sl-bar"><i class="fill" style="width:${at}%"></i><i class="entry" style="left:${en}%"></i><i class="now" style="left:${at}%"></i></div>
        <div class="sl-lbl"><span class="down">손절 ${money(pl.stop, p.market)}</span><span class="up">익절 ${money(pl.take, p.market)}</span></div>`;
    }
    return `<div class="hold" data-open="${esc(p.symbol)}">
      <div class="hold-top">${logo(p.symbol, p.symbol, 36)}<div><b>${esc(p.symbol)}</b><span class="note">${p.qty.toLocaleString()}주 · 평균 ${money(p.avg_price, p.market)}</span></div>
        <div class="hold-pl"><b class="${cls(p.pnl)}">${pct(p.pnl)}</b><span class="note">${money(p.value, p.market)}</span></div></div>
      ${bar || `<div class="note">AI 계획 없음 (직접 산 종목이거나 규칙 전략)</div>`}</div>`;
  }).join("") : `<div class="empty"><i data-lucide="inbox"></i><p>보유 종목이 없습니다</p><span class="note">자동매매를 시작하거나 '미리보기'로 오늘 무엇을 살지 확인해 보세요.</span></div>`;
  if (dash.animKey !== m) { dash.animKey = m; animateIn("#holdings .hold, #kpis .kpi", { y: 12 }); }
  renderOrders(d.orders.filter((o) => o.market === m || !o.market));
  renderEvents(d.events);
  icons();
};

window.acctTab = acctTab;
function acctTab(v) {
  dash.tab = v;
  $$("#acct-tab button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  ["orders", "open", "run", "log"].forEach((k) => $("#acct-" + k).classList.toggle("hidden", k !== v));
  if (v === "open") loadOpenOrders();
}
$("#acct-tab").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) acctTab(b.dataset.v); });
$("#acct-market").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  dash.market = b.dataset.v;
  $$("#acct-market button").forEach((x) => x.classList.toggle("on", x === b));
  if (dash.data) window.renderDash(dash.data);
});

async function loadOpenOrders() {
  const el = $("#acct-open");
  el.innerHTML = "<p class='note'>불러오는 중...</p>";
  try {
    const r = await api("/api/account/open-orders");
    if (r.note) { el.innerHTML = `<p class="note">${esc(r.note)}</p>`; return; }
    el.innerHTML = r.items.length ? `<table><thead><tr><th>시각</th><th>종목</th><th>구분</th><th class="r">수량</th><th class="r">체결</th><th class="r">가격</th><th>상태</th><th></th></tr></thead><tbody>
      ${r.items.map((o) => `<tr><td>${esc((o.orderedAt || "").slice(5, 16).replace("T", " "))}</td><td>${esc(o.symbol)}</td>
        <td class="${o.side === "BUY" ? "up" : "down"}">${o.side === "BUY" ? "매수" : "매도"}</td><td class="r">${esc(o.quantity)}</td>
        <td class="r">${esc(o.execution?.filledQuantity || 0)}</td><td class="r">${esc(o.price || "시장가")}</td><td>${esc(o.status)}</td>
        <td><button data-cancel="${esc(o.orderId)}"><i data-lucide="x-circle"></i> 취소</button></td></tr>`).join("")}</tbody></table>`
      : `<p class="note">미체결 주문이 없습니다.</p>`;
    icons();
  } catch (e) { el.innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-cancel]");
  if (!b || !confirm("이 주문을 취소할까요?")) return;
  try { await api(`/api/account/orders/${b.dataset.cancel}/cancel`, { method: "POST" }); loadOpenOrders(); } catch (err) { alert(err.message); }
});
