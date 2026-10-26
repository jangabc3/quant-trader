"use strict";
// 시장 한눈에 보기: 지수·수급·환율·금리·순위

const mkt = { market: "KR", kind: "amount", duration: "realtime", charts: {} };

function sparkChart(el, data, up) {
  if (!window.LightweightCharts || !data.length) { el.innerHTML = ""; return; }
  let c = mkt.charts[el.id];
  if (!c) {
    const chart = LightweightCharts.createChart(el, {
      layout: { background: { color: "transparent" }, textColor: "#8b98a5", fontSize: 10 },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { visible: false }, leftPriceScale: { visible: false },
      timeScale: { visible: false }, crosshair: { vertLine: { visible: false }, horzLine: { visible: false } },
      handleScroll: false, handleScale: false, autoSize: true,
    });
    c = { chart, series: chart.addAreaSeries({ lineWidth: 2, priceLineVisible: false, lastValueVisible: false }) };
    mkt.charts[el.id] = c;
  }
  const color = up ? "#f0534e" : "#4c8dff";
  c.series.applyOptions({ lineColor: color, topColor: up ? "rgba(240,83,78,.35)" : "rgba(76,141,255,.35)", bottomColor: "rgba(0,0,0,0)" });
  c.series.setData(data);
  c.chart.timeScale().fitContent();
}

function flowCell(label, v) {
  return `<div class="flow"><span>${label}</span><b class="${cls(v)}">${v > 0 ? "+" : ""}${Number(v || 0).toLocaleString()}억</b></div>`;
}

function renderOverview(ov) {
  $("#mkt-session").textContent = ov.session || "국내 시장";
  $("#mkt-asof").textContent = ov.asof ? ov.asof.slice(11, 19) + " 기준" : "";
  $("#mkt-demo").classList.toggle("hidden", !ov.demo);
  const box = $("#idx-cards");
  if (!box.dataset.ready) {
    box.innerHTML = ov.indices.map((i) => `
      <div class="idx-card" id="idx-${i.symbol}">
        <div class="idx-top"><span class="idx-name"></span><span class="idx-tag"></span></div>
        <div class="idx-val"></div><div class="idx-chg"></div>
        <div class="spark" id="spark-${i.symbol}"></div>
        <div class="flows"></div>
      </div>`).join("");
    box.dataset.ready = "1";
  }
  ov.indices.forEach((i) => {
    const el = $("#idx-" + i.symbol);
    if (!el) return;
    const up = i.change >= 0;
    el.querySelector(".idx-name").textContent = i.name;
    el.querySelector(".idx-tag").textContent = up ? "상승" : "하락";
    el.querySelector(".idx-tag").className = "idx-tag " + (up ? "up-bg" : "down-bg");
    el.querySelector(".idx-val").textContent = Number(i.price).toLocaleString("ko-KR", { maximumFractionDigits: 2 });
    el.querySelector(".idx-chg").innerHTML = `<span class="${cls(i.change)}">${i.change > 0 ? "+" : ""}${i.change.toFixed(2)} (${pct(i.change_rate)})</span>`;
    const f = i.flows || {};
    el.querySelector(".flows").innerHTML = f.unit ? flowCell("개인", f.individual) + flowCell("외국인", f.foreigner) + flowCell("기관", f.institution) : "";
    sparkChart($("#spark-" + i.symbol), i.spark || [], up);
  });
  const fx = ov.fx || {};
  $("#fx-card").innerHTML = `<div class="k">달러 환율</div><div class="v">${fx.rate ? fx.rate.toLocaleString("ko-KR", { maximumFractionDigits: 1 }) : "-"}<small>원</small></div>
    <div class="${fx.change > 0 ? "up" : fx.change < 0 ? "down" : ""} s">${fx.change ? (fx.change > 0 ? "+" : "") + fx.change : ""}</div>`;
  $("#bond-card").innerHTML = `<div class="k">국고채 금리</div>` + (ov.bonds || []).map((b) =>
    `<div class="row"><span>${esc(b.name)}</span><b>${b.value.toFixed(3)}%</b></div>`).join("");
  $("#us-card").innerHTML = `<div class="k">미국 지수 ETF</div>` + (ov.us || []).map((u) =>
    `<div class="row"><span>${esc(u.name)}</span><b class="${cls(u.change_rate)}">${pct(u.change_rate)}</b></div>`).join("");
}

async function loadWatch() {
  try {
    const rows = await api("/api/market/watch");
    $("#watch").innerHTML = rows.map((w) => `<button class="chip" data-sym="${esc(w.symbol)}">
      <span>${esc(w.name)}</span><b class="${cls(w.change_rate)}">${pct(w.change_rate)}</b></button>`).join("");
  } catch { $("#watch").innerHTML = ""; }
}

function rateCell(r) {
  const a = Math.min(Math.abs(r) / 0.15, 1);
  const bg = r > 0 ? `rgba(240,83,78,${0.15 + a * 0.6})` : r < 0 ? `rgba(76,141,255,${0.15 + a * 0.6})` : "transparent";
  return `<span class="rate" style="background:${bg}">${pct(r)}</span>`;
}

async function loadRankings() {
  try {
    const r = await api(`/api/market/rankings?kind=${mkt.kind}&market=${mkt.market}&duration=${mkt.duration}&count=30`);
    $("#rank-time").textContent = (r.demo ? "체험용 · " : "") + (r.rankedAt ? r.rankedAt.slice(11, 16) + " 기준" : "");
    const watch = new Set((state.config?.watchlist) || []);
    $("#rank-body").innerHTML = r.items.map((x) => `<tr>
      <td class="rk">${x.rank}</td>
      <td><button class="heart ${watch.has(x.symbol) ? "on" : ""}" data-heart="${esc(x.symbol)}" title="관심 종목">♥</button></td>
      <td><div class="nm">${esc(x.name)}</div><div class="sym">${esc(x.symbol)}</div></td>
      <td class="r">${money(x.price, x.currency)}</td>
      <td class="r">${rateCell(x.change_rate)}</td>
      <td class="r">${x.currency === "USD" ? "$" + bigKRW(x.amount) : bigKRW(x.amount) + "원"}</td>
      <td class="r"><button class="ai-btn" data-ai="${esc(x.symbol)}">AI 분석</button></td></tr>`).join("")
      || `<tr><td colspan="7" class="note">순위 데이터가 없습니다.</td></tr>`;
  } catch (e) { $("#rank-body").innerHTML = `<tr><td colspan="7" class="note">${esc(e.message)}</td></tr>`; }
}

async function loadMarket() {
  try { renderOverview(await api("/api/market/overview")); } catch (e) { $("#mkt-session").textContent = e.message; }
  loadRankings(); loadWatch();
}

function seg(id, key) {
  $$(`#${id} button`).forEach((b) => b.addEventListener("click", () => {
    $$(`#${id} button`).forEach((x) => x.classList.toggle("on", x === b));
    mkt[key] = b.dataset.v;
    loadRankings();
  }));
}
seg("rank-market", "market");
seg("rank-kind", "kind");
$("#rank-duration").onchange = () => { mkt.duration = $("#rank-duration").value; loadRankings(); };

document.addEventListener("click", async (e) => {
  const ai = e.target.closest("[data-ai]");
  if (ai) { openTab("office"); window.startMeeting && window.startMeeting(ai.dataset.ai); return; }
  const chip = e.target.closest(".chip[data-sym]");
  if (chip) { openTab("office"); $("#meet-symbol").value = chip.dataset.sym; return; }
  const heart = e.target.closest("[data-heart]");
  if (heart) {
    const c = state.config || await api("/api/config");
    const s = heart.dataset.heart;
    const list = new Set(c.watchlist || []);
    list.has(s) ? list.delete(s) : list.add(s);
    c.watchlist = [...list];
    try { state.config = await api("/api/config", { method: "PUT", body: c }); heart.classList.toggle("on"); loadWatch(); }
    catch (err) { alert(err.message); }
  }
});

document.addEventListener("tab", (e) => { if (e.detail === "mkt") loadMarket(); });
(async () => { try { state.config = await api("/api/config"); } catch {} loadMarket(); })();
setInterval(() => { if (state.tab === "mkt" && !document.hidden) loadMarket(); }, 20000);
