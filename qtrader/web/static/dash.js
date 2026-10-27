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
    loadBenchmark(m);
  }
  // 자산 구성 (도넛)
  const parts = pos.map((p) => ({ name: p.name || p.symbol, v: p.value })).concat(ov ? [{ name: "현금", v: ov.cash }] : []);
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
  window.renderHoldings(pos, m);
  if (dash.animKey !== m) { dash.animKey = m; animateIn("#holdings .hl-row, #kpis .kpi", { y: 12 }); }
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
        <td><button data-modify="${esc(o.orderId)}" data-sym="${esc(o.symbol)}" data-qty="${esc(o.quantity)}" data-px="${esc(o.price || "")}"><i data-lucide="pencil"></i> 정정</button>
          <button data-cancel="${esc(o.orderId)}"><i data-lucide="x-circle"></i> 취소</button></td></tr>`).join("")}</tbody></table>`
      : `<p class="note">미체결 주문이 없습니다.</p>`;
    icons();
  } catch (e) { el.innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}
// 주문 정정: 가격(미국) 또는 가격·수량(국내)
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-modify]");
  if (!b) return;
  const kr = /^\d/.test(b.dataset.sym);
  modal(`<div class="plan-edit"><h3>주문 정정</h3><p class="note">${esc(b.dataset.sym)} · 지금 ${esc(b.dataset.px || "시장가")} · ${esc(b.dataset.qty)}주</p>
    <label>새 가격 (비우면 시장가) <input id="md-px" inputmode="decimal" value="${esc(b.dataset.px)}"></label>
    ${kr ? `<label>수량 <input id="md-qty" inputmode="numeric" value="${esc(b.dataset.qty)}"></label>` : `<p class="note">미국 주식은 가격만 바꿀 수 있어요.</p>`}
    <div class="tk-c-btns"><button id="md-x">취소</button><button class="primary" id="md-ok">정정하기</button></div></div>`);
  $("#md-x").onclick = () => $("#modal").classList.add("hidden");
  $("#md-ok").onclick = async () => {
    try {
      await api(`/api/account/orders/${b.dataset.modify}/modify`, { method: "POST", body: { symbol: b.dataset.sym, price: Number($("#md-px").value.replace(/,/g, "")) || 0, qty: kr ? Number($("#md-qty").value) : 0 } });
      $("#modal").classList.add("hidden"); toast("주문을 정정했어요", "pencil"); loadOpenOrders();
    } catch (err) { toast(err.message, "alert-triangle"); }
  };
});
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-cancel]");
  if (!b || !confirm("이 주문을 취소할까요?")) return;
  try { await api(`/api/account/orders/${b.dataset.cancel}/cancel`, { method: "POST" }); loadOpenOrders(); } catch (err) { alert(err.message); }
});

// 보유 종목의 손절·익절 정하기
$("#holdings").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-plan], [data-unplan]");
  if (!b) return;
  e.stopPropagation();
  const sym = b.dataset.plan || b.dataset.unplan;
  const p = dash.data.positions.find((x) => x.symbol === sym), pl = state.plans[sym];
  if (b.dataset.unplan) {
    if (!confirm("이 종목의 손절·익절 감시를 끌까요? 그러면 자동으로 팔지 않아요.")) return;
    await api("/api/plans/" + encodeURIComponent(sym), { method: "DELETE" }).catch((err) => toast(err.message, "alert-triangle"));
    return refresh();
  }
  const H = { auto: [7, 15], long: [7, 15], swing: [5, 10], short: [2.5, 5] }[state.config?.ai?.holding || "auto"] || [7, 15];
  const r = (v) => p.market === "KR" ? Math.round(v) : Math.round(v * 100) / 100;
  modal(`<div class="plan-edit"><h3>${esc(p.name || sym)} 손절·익절</h3>
    <p class="note">지금 ${money(p.last_price, p.market)} · 평균 ${money(p.avg_price, p.market)}. 정한 가격에 닿으면 장중 2분 안에 자동으로 팝니다.</p>
    <label>손절가 (이 아래로 내리면 판다) <input id="pe-stop" inputmode="decimal" value="${pl?.stop ?? r(p.avg_price * (1 - H[0] / 100))}"></label>
    <label>익절가 (이 위로 오르면 판다) <input id="pe-take" inputmode="decimal" value="${pl?.take ?? r(p.avg_price * (1 + H[1] / 100))}"></label>
    <div class="tk-c-btns"><button id="pe-x">취소</button><button class="primary" id="pe-ok">저장</button></div></div>`);
  $("#pe-x").onclick = () => $("#modal").classList.add("hidden");
  $("#pe-ok").onclick = async () => {
    try {
      await api("/api/plans", { method: "POST", body: { symbol: sym, stop: Number($("#pe-stop").value.replace(/,/g, "")), take: Number($("#pe-take").value.replace(/,/g, "")) } });
      $("#modal").classList.add("hidden"); toast("손절·익절을 정했어요. 이제 자동으로 지켜 볼게요", "shield-check"); refresh();
    } catch (err) { toast(err.message, "alert-triangle"); }
  };
});

// 내 계좌 하위 탭: 보유 종목 · 수익 · 거래 기록 · 오늘
function acctSub(v) {
  $$("#acct-sub button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  $$("#tab-dash .acct-pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== v));
  try { localStorage.setItem("qtrader.acct.sub", v); } catch {}
  animateIn(`#tab-dash .acct-pane[data-pane="${v}"] > *`, { y: 10, step: 50 });
  if (v === "profit" && dash.chart) setTimeout(() => dash.chart.timeScale().fitContent(), 50);
}
$("#acct-sub").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) acctSub(b.dataset.v); });
try { const v = localStorage.getItem("qtrader.acct.sub"); if (v) acctSub(v); } catch {}

// 지수 대비 성과: 두 선 (내 수익률 = 파랑, 지수 = 회색)
async function loadBenchmark(m) {
  const b = await api("/api/benchmark?market=" + m).catch(() => null);
  if (!b || !dash.chart) return;
  if (!dash.bmSeries) dash.bmSeries = dash.chart.addLineSeries({ color: "#8a97a6", lineWidth: 2, lineStyle: 2, priceLineVisible: false, lastValueVisible: true });
  dash.series.applyOptions({ priceFormat: { type: "custom", formatter: (v) => (v * 100).toFixed(1) + "%" } });
  dash.series.setData(b.ours);
  dash.bmSeries.setData(b.index);
  dash.chart.timeScale().fitContent();
  $("#bm-sub").textContent = `내 계좌 수익률 vs ${b.name}`;
  $("#bm-head").innerHTML = b.ours.length ? `<div><span><i style="background:#3182f6"></i>내 계좌</span><b class="${cls(b.ours.at(-1).value)}">${pct(b.ours.at(-1).value)}</b></div>
    <div><span><i style="background:#8a97a6"></i>${esc(b.name)}</span><b class="${cls(b.index.at(-1)?.value)}">${pct(b.index.at(-1)?.value)}</b></div>
    ${b.diff != null ? `<div class="bm-diff ${b.diff >= 0 ? "win" : "lose"}">${b.diff >= 0 ? "시장보다" : "시장보다"} <b>${b.diff >= 0 ? "+" : ""}${(b.diff * 100).toFixed(1)}%p</b> ${b.diff >= 0 ? "앞서요" : "뒤처져요"}</div>` : ""}` : "";
  $("#eq-note").textContent = b.ours.length ? `같은 날(${b.ours[0].time})부터 잰 수익률이에요. 자동매매가 시장보다 나은지 판단하는 가장 중요한 기준이에요.` : b.note || "";
}
