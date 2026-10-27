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
  const color = up ? "#f04452" : "#3182f6";
  c.series.applyOptions({ lineColor: color, topColor: up ? "rgba(240,68,82,.35)" : "rgba(49,130,246,.35)", bottomColor: "rgba(0,0,0,0)" });
  c.series.setData(data);
  c.chart.timeScale().fitContent();
}

function flowCell(label, v) {
  return `<div class="flow"><span>${label}</span><b class="${cls(v)}">${v > 0 ? "+" : ""}${Number(v || 0).toLocaleString()}억</b></div>`;
}

function idxCard(id, name, tag, price, change, rate, flows, sym) {
  const up = change >= 0;
  return `<div class="idx-card" ${sym ? `data-open="${esc(sym)}"` : ""}>
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
  box.style.gridTemplateColumns = `repeat(${mkt.group === "KR" ? 2 : 3}, minmax(0, 1fr))`;
  if (mkt.group === "KR") {
    box.innerHTML = ov.indices.map((i) => idxCard(i.symbol, i.name, "", i.price, i.change, i.change_rate,
      i.flows?.unit ? flowCell("개인", i.flows.individual) + flowCell("외국인", i.flows.foreigner) + flowCell("기관", i.flows.institution) : "", i.symbol)).join("");
    ov.indices.forEach((i) => sparkChart($("#spark-" + i.symbol), i.spark || [], i.change >= 0));
  } else {
    if ((ov.us_index || []).length) { // 실제 지수 (야후 파이낸스 공개 차트)
      const stat = (k, v) => `<div class="flow"><span>${k}</span><b>${v}</b></div>`;
      const f2 = (v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 });
      box.innerHTML = ov.us_index.map((u) => idxCard(u.symbol.replace("^", ""), u.name, "지수", u.price, u.change, u.change_rate,
        stat("전일 종가", f2(u.prev)) + stat("고가", `<span class="up">${f2(u.high)}</span>`) + stat("저가", `<span class="down">${f2(u.low)}</span>`), "")).join("");
      ov.us_index.forEach((u) => sparkChart($("#spark-" + u.symbol.replace("^", "")), u.spark || [], u.change >= 0));
      $$("#idx-group button").forEach((b) => b.classList.toggle("on", b.dataset.v === mkt.group));
      return;
    }
    const us = Object.fromEntries((ov.us || []).map((u) => [u.symbol, u]));
    const stat = (k, v) => `<div class="flow"><span>${k}</span><b>${v}</b></div>`;
    const usd = (v) => v ? "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 }) : "-";
    box.innerHTML = US_IDX.map(([s, n, t]) => {
      const u = us[s] || { price: 0, change: 0, change_rate: 0 };
      return idxCard(s, n, t, u.price, u.change, u.change_rate,
        stat("전일 종가", usd(u.prev)) + stat("고가", `<span class="up">${usd(u.high)}</span>`) + stat("저가", `<span class="down">${usd(u.low)}</span>`), s);
    }).join("");
    for (const [s] of US_IDX) {
      let pts = us[s]?.spark || [];
      if (!pts.length) {
        try { pts = (await api(`/api/stock/${s}/candles?interval=5m&count=78`)).candles.map((x) => ({ time: x.time, value: x.close })); } catch {}
      }
      sparkChart($("#spark-" + s), pts, (us[s]?.change ?? 0) >= 0);
    }
  }
  $$("#idx-group button").forEach((b) => b.classList.toggle("on", b.dataset.v === mkt.group));
}

function renderOverview(ov) {
  mkt.ov = ov;
  if (!mkt.group) mkt.group = liveGroup();
  $("#mkt-session").textContent = mkt.group === "KR" ? (ov.session || "국내 시장") : "미국 정규장 · 한국 시각 22:30~05:00 (겨울 23:30~06:00)" + ((ov.us_index || []).length ? " · 실제 지수 (야후 파이낸스, 지연될 수 있음)" : " · 대표 ETF 기준");
  $("#mkt-asof").textContent = ov.asof ? ov.asof.slice(11, 19) + " 기준" : "";
  $("#mkt-demo").classList.toggle("hidden", !ov.demo || (mkt.group === "US" && (ov.us_index || []).length));
  renderIndices();
  const fx = ov.fx || {};
  if (fx.rate) { fxView.rate = fx.rate; drawFxToggle(); }
  $("#fx-card").innerHTML = `<div class="k">달러 환율</div><div class="v">${fx.rate ? fx.rate.toLocaleString("ko-KR", { maximumFractionDigits: 1 }) : "-"}<small>원</small></div>
    <div class="${fx.change > 0 ? "up" : fx.change < 0 ? "down" : ""} s">${fx.change ? (fx.change > 0 ? "▲ " : "▼ ") + Math.abs(fx.change) : ""}</div>`;
  $("#bond-card").innerHTML = `<div class="k">국고채 금리 <small class="note">만기별</small></div>` + bondCurve(ov.bonds || []);
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
    $("#watch").innerHTML = rows.length ? `<span class="watch-lbl"><i data-lucide="heart"></i> 관심 종목</span>` + rows.map((w) => `<button class="chip" data-open="${esc(w.symbol)}">
      ${logo(w.symbol, w.name, 20)}<span>${esc(w.name)}</span><b>${money(w.price, w.symbol.match(/^\d/) ? "KR" : "US")}</b><b class="${cls(w.change_rate)}">${pct(w.change_rate)}</b></button>`).join("") : "";
    if (!rows.length) $("#watch").innerHTML = `<span class="watch-lbl note"><i data-lucide="heart"></i> 아래 실시간 차트에서 ♥ 를 누르면 여기와 '♥ 관심' 탭에 모입니다</span>`;
    icons();
  } catch { $("#watch").innerHTML = ""; }
}

// 토스증권처럼 등락률은 배경 없이 빨강(상승)·파랑(하락) 글자로
// ♥ 를 누르면 하트가 '관심' 탭으로 날아간다
function flyHeart(from) {
  const to = document.querySelector("#rank-kind .watch-tab");
  if (!to || !window.anime || REDUCED) return;
  const a = from.getBoundingClientRect(), b = to.getBoundingClientRect();
  const h = document.createElement("div");
  h.className = "fly-heart"; h.textContent = "♥";
  h.style.left = a.left + "px"; h.style.top = a.top + "px";
  document.body.appendChild(h);
  anime({ targets: h, translateX: b.left - a.left + 10, translateY: [{ value: -40, duration: 260, easing: "easeOutQuad" }, { value: b.top - a.top, duration: 420, easing: "easeInQuad" }],
    scale: [1.6, 0.8], duration: 680, complete: () => { h.remove(); to.classList.add("pop"); setTimeout(() => to.classList.remove("pop"), 400); } });
}

function rateCell(r) {
  return `<span class="rate ${cls(r)}">${pct(r)}</span>`;
}

async function loadWatchTable() {
  const rows = await api("/api/market/watch").catch(() => []);
  $("#rank-time").textContent = rows.some((r) => r.demo) ? "체험용 시세" : "";
  $("#rank-body").innerHTML = rows.map((x, i) => {
    const cur = /^\d/.test(x.symbol) ? "KR" : "US";
    return `<tr><td class="rk">${i + 1}</td>
      <td><button class="heart on" data-heart="${esc(x.symbol)}" title="관심 종목에서 빼기">♥</button></td>
      <td class="clickable" data-open="${esc(x.symbol)}"><div class="namecell">${logo(x.symbol, x.name)}<div><div class="nm">${esc(x.name)}</div><div class="sym">${esc(x.symbol)}</div></div></div></td>
      <td class="r px"><span data-key="p:${esc(x.symbol)}" data-val="${x.price}">${money(x.price, cur)}</span></td>
      <td class="r">${rateCell(x.change_rate)}</td><td class="r note">-</td>
      <td class="r"><button class="ai-btn" data-ai="${esc(x.symbol)}">AI 분석</button></td></tr>`;
  }).join("") || `<tr><td colspan="7"><div class="empty"><i data-lucide="heart"></i><p>아직 관심 종목이 없어요</p>
    <span class="note">다른 순위 탭에서 종목 옆 ♥ 를 누르면 여기에 모입니다.</span></div></td></tr>`;
  animateIn("#rank-body tr", { step: 18 });
  flashChanges($("#rank-body"));
  icons();
}

async function loadRankings() {
  if (mkt.kind === "watch") return loadWatchTable();
  try {
    const r = await api(`/api/market/rankings?kind=${mkt.kind}&market=${mkt.market}&duration=${mkt.duration}&count=30&safe=${mkt.safe ? 1 : 0}`);
    $("#rank-time").textContent = (r.demo ? "체험용 · " : "") + (r.rankedAt ? r.rankedAt.slice(11, 16) + " 기준" : "");
    const watch = new Set((state.config?.watchlist) || []);
    $("#rank-body").innerHTML = r.items.map((x) => `<tr>
      <td class="rk">${x.rank}</td>
      <td><button class="heart ${watch.has(x.symbol) ? "on" : ""}" data-heart="${esc(x.symbol)}" title="관심 종목">♥</button></td>
      <td class="clickable" data-open="${esc(x.symbol)}"><div class="namecell">${logo(x.symbol, x.name)}<div><div class="nm">${esc(x.name)}</div><div class="sym">${esc(x.symbol)}</div></div></div></td>
      <td class="r px"><span data-key="p:${esc(x.symbol)}" data-val="${x.price}">${money(x.price, x.currency)}</span></td>
      <td class="r">${rateCell(x.change_rate)}</td>
      <td class="r">${x.amount ? bigKRW(x.amount) + "원" : "-"}</td>
      <td class="r"><button class="ai-btn" data-ai="${esc(x.symbol)}">AI 분석</button></td></tr>`).join("")
      || `<tr><td colspan="7" class="note">순위 데이터가 없습니다.</td></tr>`;
    const key = mkt.kind + mkt.market + mkt.duration + (mkt.safe ? "s" : "");
    if (key !== mkt.lastKey) { mkt.lastKey = key; animateIn("#rank-body tr", { step: 18 }); }
    flashChanges($("#rank-body"));
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
$("#rank-safe").onchange = () => { mkt.safe = $("#rank-safe").checked; loadRankings(); };

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
    try {
      state.config = await api("/api/config", { method: "PUT", body: c }); heart.classList.toggle("on"); loadWatch();
      if (list.has(s)) flyHeart(heart);
      toast(list.has(s) ? "관심 종목에 담았어요. 실시간 차트의 '♥ 관심' 탭에서 모아 볼 수 있어요" : "관심 종목에서 뺐어요", list.has(s) ? "heart" : "heart-off");
      if (mkt.kind === "watch") loadWatchTable();
    }
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


// 창 안 종목을 누르면 창을 닫는다
document.addEventListener("click", (e) => { if (e.target.closest("#modal [data-open]")) $("#modal").classList.add("hidden"); }, true);

// 국고채 금리 곡선: 2·3·5·10·20·30년을 선으로 (짧은 만기가 더 높으면 '역전' — 경기 둔화 신호로 자주 봄)
function bondCurve(bonds) {
  if (!bonds.length) return "";
  const yrs = bonds.map((b) => Number((b.symbol.match(/(\d+)Y/) || [])[1] || 0));
  const vs = bonds.map((b) => b.value), lo = Math.min(...vs), hi = Math.max(...vs), W = 200, H = 54;
  const x = (k) => 8 + k / Math.max(bonds.length - 1, 1) * (W - 16), y = (v) => H - 10 - (hi - lo ? (v - lo) / (hi - lo) : 0.5) * (H - 22);
  const pts = vs.map((v, k) => `${x(k).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const inv = vs.length > 2 && vs[0] > vs[vs.indexOf(Math.max(...vs.slice(2)))] && vs[0] > vs[vs.length - 1];
  return `<svg class="bond-curve" viewBox="0 0 ${W} ${H}"><polyline points="${pts}" fill="none" stroke="var(--blue)" stroke-width="2"/>
    ${vs.map((v, k) => `<circle cx="${x(k)}" cy="${y(v)}" r="2.6" fill="var(--blue)"><title>${yrs[k]}년 ${v.toFixed(3)}%</title></circle>
      <text x="${x(k)}" y="${H - 1}" text-anchor="middle">${yrs[k]}년</text>`).join("")}</svg>
    <div class="row"><span>${esc(bonds[0].name)}</span><b>${bonds[0].value.toFixed(3)}%</b></div>
    <div class="row"><span>${esc(bonds[bonds.length - 1].name)}</span><b>${bonds[bonds.length - 1].value.toFixed(3)}%</b></div>
    ${inv ? `<div class="note" style="color:var(--amber)">단기 금리가 장기보다 높아요 (금리 역전)</div>` : ""}`;
}
