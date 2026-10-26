"use strict";
// 시장 한눈에 보기: 시간대에 맞춰 국내(코스피·코스닥) / 미국(S&P500·나스닥100·다우) 지수, 수급, 환율·금리, 실시간 순위

const mkt = { market: "KR", kind: "amount", duration: "realtime", charts: {}, group: null, ov: null };
const US_IDX = [["SPY", "S&P500", "SPY 기준"], ["QQQ", "나스닥100", "QQQ 기준"], ["DIA", "다우존스", "DIA 기준"]];

// 지금 열려 있는 장: 한국 09:00~15:30 이면 국내, 한국 시각 22:30~06:00 이면 미국 (서머타임 차이는 HUD 안내)
function liveGroup() {
  const kst = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  const hm = kst.getHours() * 60 + kst.getMinutes();
  return hm >= 22 * 60 || hm < 6 * 60 ? "US" : "KR";
}

function sparkChart(el, data, up, intraday = true) {
  if (!window.LightweightCharts || !el) return;
  let c = mkt.charts[el.id];
  if (!c) {
    const chart = LightweightCharts.createChart(el, {
      layout: { background: { color: "transparent" }, textColor: "#8b98a5", fontSize: 10 },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { visible: false }, leftPriceScale: { visible: false },
      timeScale: { visible: false, timeVisible: intraday }, crosshair: { vertLine: { visible: false }, horzLine: { visible: false } },
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

function idxCard(id, name, tag, price, change, rate, flows, sym) {
  const up = change >= 0;
  return `<div class="idx-card" data-open="${esc(sym)}">
    <div class="idx-top"><span class="idx-name">${esc(name)} <small class="note">${esc(tag)}</small></span>
      <span class="idx-tag ${up ? "up-bg" : "down-bg"}">${up ? "▲ 상승" : "▼ 하락"}</span></div>
    <div class="idx-val">${Number(price).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}</div>
    <div class="idx-chg"><span class="${cls(change)}">${change > 0 ? "+" : ""}${Number(change).toFixed(2)} (${pct(rate)})</span></div>
    <div class="spark" id="spark-${id}"></div>
    <div class="flows">${flows || ""}</div></div>`;
}

async function renderIndices() {
  const ov = mkt.ov;
  if (!ov) return;
  const box = $("#idx-cards");
  mkt.charts = {};
  if (mkt.group === "KR") {
    box.innerHTML = ov.indices.map((i) => idxCard(i.symbol, i.name, "", i.price, i.change, i.change_rate,
      i.flows?.unit ? flowCell("개인", i.flows.individual) + flowCell("외국인", i.flows.foreigner) + flowCell("기관", i.flows.institution) : "", i.symbol)).join("");
    ov.indices.forEach((i) => sparkChart($("#spark-" + i.symbol), i.spark || [], i.change >= 0));
  } else {
    const us = Object.fromEntries((ov.us || []).map((u) => [u.symbol, u]));
    box.innerHTML = US_IDX.map(([s, n, t]) => {
      const u = us[s] || { price: 0, change: 0, change_rate: 0 };
      return idxCard(s, n, t, u.price, u.change, u.change_rate, `<div class="flow"><span>지수 대신 대표 ETF 시세</span><b class="note">토스증권 기준</b></div>`, s);
    }).join("");
    for (const [s] of US_IDX) {
      try {
        const c = await api(`/api/stock/${s}/candles?interval=5m&count=78`);
        const pts = c.candles.map((x) => ({ time: x.time, value: x.close }));
        sparkChart($("#spark-" + s), pts, (us[s]?.change ?? 0) >= 0);
      } catch {}
    }
  }
  $$("#idx-group button").forEach((b) => b.classList.toggle("on", b.dataset.v === mkt.group));
}

function renderOverview(ov) {
  mkt.ov = ov;
  if (!mkt.group) mkt.group = liveGroup();
  $("#mkt-session").textContent = mkt.group === "KR" ? (ov.session || "국내 시장") : "미국 정규장 한국 시각 22:30~05:00 (서머타임 해제 시 23:30~06:00)";
  $("#mkt-asof").textContent = ov.asof ? ov.asof.slice(11, 19) + " 기준" : "";
  $("#mkt-demo").classList.toggle("hidden", !ov.demo);
  renderIndices();
  const fx = ov.fx || {};
  $("#fx-card").innerHTML = `<div class="k">달러 환율</div><div class="v">${fx.rate ? fx.rate.toLocaleString("ko-KR", { maximumFractionDigits: 1 }) : "-"}<small>원</small></div>
    <div class="${fx.change > 0 ? "up" : fx.change < 0 ? "down" : ""} s">${fx.change ? (fx.change > 0 ? "▲ " : "▼ ") + Math.abs(fx.change) : ""}</div>`;
  $("#bond-card").innerHTML = `<div class="k">국고채 금리</div>` + (ov.bonds || []).map((b) =>
    `<div class="row"><span>${esc(b.name)}</span><b>${b.value.toFixed(3)}%</b></div>`).join("");
  $("#us-card").innerHTML = `<div class="k">${mkt.group === "KR" ? "미국 지수 ETF" : "국내 지수"}</div>` + (mkt.group === "KR"
    ? (ov.us || []).map((u) => `<div class="row clickable" data-open="${esc(u.symbol)}"><span>${esc(u.name)}</span><b class="${cls(u.change_rate)}">${pct(u.change_rate)}</b></div>`).join("")
    : (ov.indices || []).map((i) => `<div class="row"><span>${esc(i.name)}</span><b class="${cls(i.change_rate)}">${pct(i.change_rate)}</b></div>`).join(""));
}

$$("#idx-group button").forEach((b) => b.addEventListener("click", () => {
  mkt.group = b.dataset.v;
  renderOverview(mkt.ov);
  if ((b.dataset.v === "US") !== (mkt.market === "US")) $(`#rank-market button[data-v="${b.dataset.v}"]`).click();
}));

async function loadWatch() {
  try {
    const rows = await api("/api/market/watch");
    $("#watch").innerHTML = rows.length ? `<span class="note">관심</span>` + rows.map((w) => `<button class="chip" data-open="${esc(w.symbol)}">
      ${logo(w.symbol, w.name, 20)}<span>${esc(w.name)}</span><b>${money(w.price, w.symbol.match(/^\d/) ? "KR" : "US")}</b><b class="${cls(w.change_rate)}">${pct(w.change_rate)}</b></button>`).join("") : "";
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
      <td class="clickable" data-open="${esc(x.symbol)}"><div class="namecell">${logo(x.symbol, x.name)}<div><div class="nm">${esc(x.name)}</div><div class="sym">${esc(x.symbol)}</div></div></div></td>
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
(async () => {
  try { state.config = await api("/api/config"); } catch {}
  mkt.market = liveGroup();
  $$("#rank-market button").forEach((b) => b.classList.toggle("on", b.dataset.v === mkt.market));
  loadMarket();
})();
setInterval(() => { if (state.tab === "mkt" && !document.hidden) loadMarket(); }, 15000);
