"use strict";
// 종목 상세: 실시간 시세, 캔들(1·5·15·60분, 일·주·월) + 이동평균 + 거래량, 호가, 체결, 재무, 투자자 매매, 뉴스, AI 회의 기록

const st = { symbol: null, sum: null, iv: "1d", type: "candle", chart: null, main: null, vol: null, mas: {}, timers: [], last: null };
const MA_COLORS = { 5: "#ffd34d", 20: "#f08a5e", 60: "#3fe07a", 120: "#c084fc" };
const WARN_KO = { OVERHEATED: "단기과열", VI_STATIC: "정적 VI", VI_DYNAMIC: "동적 VI", INVESTMENT_CAUTION: "투자주의",
  INVESTMENT_WARNING: "투자경고", INVESTMENT_RISK: "투자위험", TRADING_HALT: "거래정지", ADMINISTRATIVE: "관리종목" };
const isKR = (s) => /^\d/.test(s) || ["KOSPI", "KOSDAQ"].includes(s);
const pxFmt = (v) => v == null ? "-" : isKR(st.symbol) ? Math.round(v).toLocaleString() : Number(v).toFixed(2);

function clearTimers() { st.timers.forEach(clearInterval); st.timers = []; }

window.openStock = async (symbol) => {
  clearTimers();
  st.symbol = symbol.toUpperCase();
  st.last = null;
  $("#stock-empty").classList.add("hidden");
  $("#stock-view").classList.remove("hidden");
  $("#st-name").textContent = st.symbol;
  ["#st-ob", "#st-trades", "#st-fin", "#st-flows", "#st-news", "#st-ai-hist"].forEach((id) => ($(id).innerHTML = "<p class='note'>불러오는 중...</p>"));
  await loadSummary();
  buildChart(); loadCandles();
  window.ticketLoad && ticketLoad(st.symbol);
  loadOrderbook(); loadTrades(); loadFin(); loadFlows(); loadNews(); loadAiHist(); loadExtra();
  st.timers.push(setInterval(() => { if (state.tab === "stock" && !document.hidden) { loadSummary(); loadOrderbook(); } }, 3000));
  st.timers.push(setInterval(() => { if (state.tab === "stock" && !document.hidden) loadTrades(); }, 5000));
  st.timers.push(setInterval(() => { if (state.tab === "stock" && !document.hidden) loadCandles(true); }, 10000));
  window.scrollTo(0, 0);
};

async function loadSummary() {
  try {
    const s = await api(`/api/stock/${encodeURIComponent(st.symbol)}/summary`);
    st.sum = s;
    $("#st-logo").innerHTML = logo(s.symbol, s.name, 44);
    $("#st-name").textContent = s.name;
    $("#st-sub").textContent = `${s.symbol} · ${s.market || ""}${s.demo ? " · 체험용 데이터" : ""}`;
    const prevPx = st.last;
    st.last = s.price;
    $("#st-price").innerHTML = bigPriceHtml(s.price, isKR(s.symbol) ? "KR" : "US");
    $("#st-price").className = prevPx && s.price !== prevPx ? (s.price > prevPx ? "flash-up" : "flash-down") : "";
    $("#st-chg").innerHTML = `<span class="${cls(s.change)}">${s.change > 0 ? "▲" : s.change < 0 ? "▼" : ""} ${isKR(s.symbol) ? pxFmt(Math.abs(s.change)) : money(Math.abs(s.change), "US")} (${pct(s.change_rate)})</span>`;
    $("#st-tags").innerHTML = (s.warnings || []).map((w) => `<span class="badge red">${esc(WARN_KO[w] || w)}</span>`).join("");
    $("#st-info").innerHTML = [["전일 종가", pxFmt(s.prev_close)], ["52주 최고", pxFmt(s.high52)], ["52주 최저", pxFmt(s.low52)],
      ["시가총액", s.market_cap ? (isKR(s.symbol) ? bigKRW(s.market_cap) + "원" : bigUSD(s.market_cap)) : "-"],
      ["상한가", pxFmt(s.upper_limit)], ["하한가", pxFmt(s.lower_limit)], ["오늘 거래량", s.volume_today ? Number(s.volume_today).toLocaleString() : "-"]]
      .map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join("");
    const w = new Set(state.config?.watchlist || []);
    $("#st-watch").classList.toggle("on", w.has(s.symbol));
    $("#st-live").textContent = (s.demo ? "체험용 · " : "") + "실시간 " + new Date().toLocaleTimeString("ko-KR");
  } catch (e) { $("#st-sub").textContent = e.message; }
}

function buildChart() {
  const el = $("#st-chart");
  el.innerHTML = "";
  if (!window.LightweightCharts) { el.textContent = "차트 라이브러리를 불러오지 못했습니다 (인터넷 연결 확인)."; return; }
  const intraday = st.iv.endsWith("m");
  const chart = LightweightCharts.createChart(el, {
    layout: { background: { color: "transparent" }, textColor: "#8b98a5" },
    grid: { vertLines: { color: "#1b222b" }, horzLines: { color: "#1b222b" } },
    rightPriceScale: { borderColor: "#26303b", scaleMargins: { top: 0.06, bottom: 0.26 } },
    timeScale: { borderColor: "#26303b", timeVisible: intraday, secondsVisible: false },
    crosshair: { mode: 0 }, autoSize: true,
    localization: { locale: "ko-KR", priceFormatter: (p) => pxFmt(p) },
  });
  st.main = st.type === "candle"
    ? chart.addCandlestickSeries({ upColor: "#f04452", downColor: "#3182f6", borderVisible: false, wickUpColor: "#f04452", wickDownColor: "#3182f6" })
    : chart.addAreaSeries({ lineColor: "#3182f6", topColor: "rgba(49,130,246,.3)", bottomColor: "rgba(0,0,0,0)", lineWidth: 2 });
  st.vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  st.mas = {};
  [5, 20, 60, 120].forEach((n) => {
    st.mas[n] = chart.addLineSeries({ color: MA_COLORS[n], lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      visible: !!$(`#st-ma input[value="${n}"]`)?.checked });
  });
  st.chart = chart;
}

function sma(rows, n) {
  const out = [];
  let sum = 0;
  rows.forEach((r, i) => {
    sum += r.close;
    if (i >= n) sum -= rows[i - n].close;
    if (i >= n - 1) out.push({ time: r.time, value: sum / n });
  });
  return out;
}

async function loadCandles(update = false) {
  if (!st.symbol || !st.main) return;
  try {
    const d = await api(`/api/stock/${encodeURIComponent(st.symbol)}/candles?interval=${st.iv}`);
    const rows = d.candles;
    if (!rows.length) return;
    // 마지막 봉을 실시간 가격으로 맞춘다
    if (st.last && st.iv === "1d") {
      const l = rows[rows.length - 1];
      l.close = st.last; l.high = Math.max(l.high, st.last); l.low = Math.min(l.low, st.last);
    }
    st.main.setData(st.type === "candle" ? rows : rows.map((r) => ({ time: r.time, value: r.close })));
    st.vol.setData(rows.map((r) => ({ time: r.time, value: r.volume, color: r.close >= r.open ? "rgba(240,68,82,.45)" : "rgba(49,130,246,.45)" })));
    [5, 20, 60, 120].forEach((n) => st.mas[n].setData(sma(rows, n)));
    if (!update) st.chart.timeScale().fitContent();
  } catch (e) { $("#st-live").textContent = e.message; }
}

function segBind(id, fn) {
  $$(`#${id} button`).forEach((b) => b.addEventListener("click", () => {
    $$(`#${id} button`).forEach((x) => x.classList.toggle("on", x === b));
    fn(b.dataset.v);
  }));
}
segBind("st-iv", (v) => { st.iv = v; buildChart(); loadCandles(); });
segBind("st-type", (v) => { st.type = v; buildChart(); loadCandles(); });
$("#st-ma").addEventListener("change", (e) => { const n = e.target.value; st.mas[n]?.applyOptions({ visible: e.target.checked }); });

async function loadOrderbook() {
  const el = $("#st-ob");
  try {
    const ob = await api(`/api/stock/${encodeURIComponent(st.symbol)}/orderbook`);
    const all = [...ob.asks, ...ob.bids];
    const max = Math.max(...all.map((x) => x.volume), 1);
    const askSum = ob.asks.reduce((s, x) => s + x.volume, 0), bidSum = ob.bids.reduce((s, x) => s + x.volume, 0);
    const prev = st.sum?.prev_close;
    const row = (x, side) => {
      const r = prev ? x.price / prev - 1 : 0;
      return `<div data-px="${x.price}" title="이 가격으로 주문" class="ob-row ${side}${Math.abs(x.price - st.last) < 1e-9 ? " cur" : ""}">
        <span class="ob-vol ${side === "ask" ? "" : "hide"}"><i style="width:${x.volume / max * 100}%"></i>${side === "ask" ? Number(x.volume).toLocaleString() : ""}</span>
        <span class="ob-px ${cls(r)}">${pxFmt(x.price)}<small>${pct(r)}</small></span>
        <span class="ob-vol right ${side === "bid" ? "" : "hide"}"><i style="width:${x.volume / max * 100}%"></i>${side === "bid" ? Number(x.volume).toLocaleString() : ""}</span></div>`;
    };
    el.innerHTML = ob.asks.map((x) => row(x, "ask")).join("") + ob.bids.map((x) => row(x, "bid")).join("") +
      `<div class="ob-sum"><span>매도 잔량 ${askSum.toLocaleString()}</span><span>매수 잔량 ${bidSum.toLocaleString()}</span></div>
       <div class="tug"><span class="bullside" style="width:${bidSum / (askSum + bidSum || 1) * 100}%">매수 ${Math.round(bidSum / (askSum + bidSum || 1) * 100)}%</span><span class="bearside">매도</span></div>`;
    $("#ob-time").textContent = ob.demo ? "체험용" : (ob.timestamp || "").slice(11, 19);
  } catch (e) { el.innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}

async function loadTrades() {
  try {
    const t = await api(`/api/stock/${encodeURIComponent(st.symbol)}/trades`);
    let prev = null;
    $("#st-trades").innerHTML = `<div class="tr-row head"><span>시간</span><span>체결가</span><span>수량</span></div>` + t.items.slice(0, 25).map((x) => {
      const dir = prev == null ? "" : x.price > prev ? "up" : x.price < prev ? "down" : "";
      prev = x.price;
      return `<div class="tr-row"><span class="note">${esc(String(x.time || "").slice(11, 19))}</span><span class="${dir}">${pxFmt(x.price)}</span><span>${Number(x.volume).toLocaleString()}</span></div>`;
    }).join("");
  } catch (e) { $("#st-trades").innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}

function finChart(fin) {
  const ys = fin.years, s = fin.series;
  const keys = [["revenue", "매출", "#3182f6"], ["operating_income", "영업이익", "#3fe07a"], ["net_income", "순이익", "#ffd34d"]];
  const vals = keys.flatMap(([k]) => (s[k] || []).filter((v) => v != null));
  if (!vals.length) return "";
  const max = Math.max(...vals.map(Math.abs), 1);
  const W = 520, H = 180, gw = W / ys.length, bw = gw / 4;
  const zero = H - 24;
  const bars = ys.map((y, i) => keys.map(([k, , c], j) => {
    const v = (s[k] || [])[i];
    if (v == null) return "";
    const h = Math.abs(v) / max * (H - 40);
    return `<rect x="${i * gw + 12 + j * bw}" y="${v >= 0 ? zero - h : zero}" width="${bw - 4}" height="${h}" fill="${c}" rx="2"><title>${y} ${keys[j][1]} ${bigKRW(v)}</title></rect>`;
  }).join("") + `<text x="${i * gw + gw / 2}" y="${H - 6}" text-anchor="middle" fill="#8b98a5" font-size="12">${y}</text>`).join("");
  const legend = keys.map(([, n, c], j) => `<rect x="${j * 90}" y="0" width="10" height="10" fill="${c}"/><text x="${j * 90 + 14}" y="9" fill="#c7d0d9" font-size="11">${n}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H + 16}" class="finsvg"><g transform="translate(0,0)">${legend}</g><g transform="translate(0,16)">${bars}<line x1="0" x2="${W}" y1="${zero}" y2="${zero}" stroke="#2c3743"/></g></svg>`;
}

async function loadFin() {
  const el = $("#st-fin");
  try {
    const f = await api(`/api/stock/${encodeURIComponent(st.symbol)}/financials`);
    if (f.error) { el.innerHTML = `<p class="note">${esc(f.error)}</p>`; $("#fin-src").textContent = ""; return; }
    const r = f.ratios || {};
    const unit = f.currency === "USD" ? "$" : "";
    const p = (v) => v == null ? "-" : (v * 100).toFixed(1) + "%";
    const x = (v) => v == null ? "-" : v.toFixed(1) + "배";
    $("#fin-src").innerHTML = `<a href="${esc(f.source_url)}" target="_blank" rel="noopener noreferrer">${esc(f.source)}</a>`;
    el.innerHTML = `<div class="ratio-grid">${[["영업이익률", p(r.operating_margin)], ["순이익률", p(r.net_margin)], ["ROE", p(r.roe)],
      ["부채비율", p(r.debt_ratio)], ["매출 성장", p(r.revenue_growth)], ["PER", x(r.per)], ["PBR", x(r.pbr)]]
      .map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join("")}</div>${finChart(f)}
      <table class="fin"><thead><tr><th></th>${f.years.map((y) => `<th class="r">${y}</th>`).join("")}</tr></thead><tbody>
      ${[["revenue", "매출"], ["operating_income", "영업이익"], ["net_income", "순이익"], ["operating_cf", "영업현금흐름"], ["equity", "자본"], ["liabilities", "부채"]]
        .map(([k, n]) => `<tr><td>${n}</td>${(f.series[k] || []).map((v) => `<td class="r ${v < 0 ? "down" : ""}">${v == null ? "-" : unit + bigKRW(v)}</td>`).join("")}</tr>`).join("")}
      </tbody></table>`;
  } catch (e) { el.innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}

async function loadFlows() {
  const el = $("#st-flows");
  if (!/^\d/.test(st.symbol)) { el.innerHTML = "<p class='note'>국내 종목만 제공됩니다.</p>"; return; }
  try {
    const d = await api(`/api/stock/${encodeURIComponent(st.symbol)}/flows`);
    const recs = (d.records || []).slice(0, 20);
    if (!recs.length) { el.innerHTML = "<p class='note'>토스증권 키를 등록하면 투자자별 매매 동향이 보입니다.</p>"; return; }
    const g = (r, k) => Number((r[k] || {}).netBuyVolume || 0);
    const max = Math.max(...recs.flatMap((r) => ["individual", "foreigner", "institution"].map((k) => Math.abs(g(r, k)))), 1);
    const bar = (v) => `<div class="fbar"><i class="${v >= 0 ? "pos" : "neg"}" style="width:${Math.abs(v) / max * 50}%"></i></div>`;
    el.innerHTML = `<table class="flows-t"><thead><tr><th>날짜</th><th class="r">개인</th><th class="r">외국인</th><th class="r">기관</th><th>외국인 흐름</th></tr></thead><tbody>
      ${recs.map((r) => `<tr><td>${esc(r.date.slice(5))}</td>${["individual", "foreigner", "institution"].map((k) =>
        `<td class="r ${cls(g(r, k))}">${r[k] ? g(r, k).toLocaleString() : "-"}</td>`).join("")}<td>${bar(g(r, "foreigner"))}</td></tr>`).join("")}</tbody></table>`;
  } catch (e) { el.innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}

function newsItems(items) {
  return items.map((n) => `<a class="news-item" href="${esc(n.link)}" target="_blank" rel="noopener noreferrer">
    <b>${esc(n.title)}</b><span class="note">${esc(n.source || "")} · ${n.time ? new Date(n.time).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}</span></a>`).join("");
}
window.newsItems = newsItems;

async function loadNews() {
  try {
    const d = await api(`/api/stock/${encodeURIComponent(st.symbol)}/news?name=${encodeURIComponent(st.sum?.name || st.symbol)}`);
    $("#st-news").innerHTML = newsItems(d.items) || `<p class="note">${esc(d.error || "뉴스가 없습니다.")}</p>`;
  } catch (e) { $("#st-news").innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}

async function loadAiHist() {
  try {
    const runs = (await api("/api/ai/runs?limit=100")).filter((r) => r.symbol === st.symbol && r.status === "done").slice(0, 5);
    $("#st-ai-hist").innerHTML = runs.length ? runs.map((r) => {
      const f = r.final || {}, p = r.plan;
      return `<div class="aih" data-run="${r.id}"><b style="color:${({ Buy: "#f04452", Overweight: "#f08a5e", Hold: "#8b98a5", Underweight: "#5a9bff", Sell: "#3182f6" })[f.rating]}">${esc(r.rating_ko || f.rating)}</b>
        <span>${esc(r.trade_date)} · 확신도 ${Math.round((f.confidence || 0) * 100)}%</span>
        ${p ? `<span class="note">진입 ${pxFmt(p.entry)} · 손절 ${pxFmt(p.stop)} · 익절 ${pxFmt(p.take)}</span>` : ""}
        <div class="note">${esc(f.headline || "")}</div></div>`;
    }).join("") : `<p class="note">아직 이 종목의 AI 회의가 없습니다. 위의 'AI 투자 회의' 버튼을 누르세요.</p>`;
  } catch (e) { $("#st-ai-hist").innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}
$("#st-ai-hist").addEventListener("click", (e) => {
  const r = e.target.closest("[data-run]");
  if (r) { openTab("office"); window.showRunById && window.showRunById(r.dataset.run); }
});

$("#st-ai").onclick = () => { openTab("office"); window.startMeeting && window.startMeeting(st.symbol); };
$("#st-watch").onclick = async () => {
  const c = state.config || await api("/api/config");
  const list = new Set(c.watchlist || []);
  list.has(st.symbol) ? list.delete(st.symbol) : list.add(st.symbol);
  c.watchlist = [...list];
  try { state.config = await api("/api/config", { method: "PUT", body: c }); $("#st-watch").classList.toggle("on"); } catch (e) { alert(e.message); }
};
document.addEventListener("tab", (e) => { if (e.detail === "stock" && st.symbol) { loadSummary(); loadCandles(true); } });


// 수급 상세: 프로그램매매·공매도·신용거래 (국내)
let extraTab = "program", extraData = null;
async function loadExtra() {
  const el = $("#st-extra");
  if (!/^\d/.test(st.symbol)) { $("#st-extra-panel").classList.add("hidden"); return; }
  $("#st-extra-panel").classList.remove("hidden");
  try { extraData = await api(`/api/stock/${encodeURIComponent(st.symbol)}/extra`); } catch (e) { el.innerHTML = `<p class="note">${esc(e.message)}</p>`; return; }
  drawExtra();
}
function drawExtra() {
  const el = $("#st-extra");
  const recs = (extraData?.[extraTab] || []).slice(0, 20);
  if (!recs.length) { el.innerHTML = "<p class='note'>토스증권 키를 등록하면 국내 종목의 프로그램매매·공매도·신용거래·대차거래 동향이 보입니다.</p>"; return; }
  const n = (v) => Number(v || 0);
  let rows, head, series;
  if (extraTab === "program") {
    head = ["날짜", "차익 순매수", "비차익 순매수", "합계"];
    rows = recs.map((r) => { const a = n(r.arbitrage?.netBuyVolume), b = n(r.nonArbitrage?.netBuyVolume); return [r.date.slice(5), a, b, a + b]; });
    series = rows.map((r) => r[3]);
  } else if (extraTab === "short") {
    head = ["날짜", "공매도 수량", "공매도 금액", "거래량 비중"];
    rows = recs.map((r) => [r.date.slice(5), n(r.shortSellingVolume), n(r.shortSellingAmount), r.shortSellingVolumeRate == null ? null : n(r.shortSellingVolumeRate)]);
    series = rows.map((r) => r[1]);
  } else if (extraTab === "lending") {
    head = ["날짜", "빌린 주식", "갚은 주식", "대차 잔고", "잔고 금액"];
    rows = recs.map((r) => [String(r.date).slice(5), n(r.exec), n(r.repay), n(r.balance), n(r.amount)]);
    series = rows.map((r) => r[3]);
  } else {
    head = ["날짜", "신용 신규", "신용 상환", "신용 잔고", "잔고율"];
    rows = recs.map((r) => [r.date.slice(5), n(r.marginLoan?.newQuantity), n(r.marginLoan?.returnQuantity), n(r.marginLoan?.balanceQuantity), n(r.marginLoan?.balanceRate)]);
    series = rows.map((r) => r[3]);
  }
  const max = Math.max(...series.map(Math.abs), 1);
  const bars = series.slice().reverse().map((v) => `<i class="${v >= 0 ? "pos" : "neg"}" style="height:${Math.abs(v) / max * 100}%"></i>`).join("");
  const fmt = (v, i) => v == null ? "-" : (extraTab === "short" && i === 3) || (extraTab === "credit" && i === 4) ? (v * 100).toFixed(2) + "%" :
    (extraTab === "short" && i === 2) || (extraTab === "lending" && i === 4) ? bigKRW(v) + "원" : `<span class="${extraTab === "program" ? cls(v) : ""}">${Number(v).toLocaleString()}</span>`;
  const tip = extraTab === "lending" ? `<p class="note">대차거래: 기관이 주식을 빌리고 갚은 양. 잔고가 빠르게 늘면 공매도가 늘어날 수 있다는 신호로 봐요.</p>` : "";
  el.innerHTML = `${tip}<div class="mini-bars">${bars}</div><table><thead><tr>${head.map((h, i) => `<th class="${i ? "r" : ""}">${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((v, i) => `<td class="${i ? "r" : ""}">${i ? fmt(v, i) : v}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
$("#st-extra-tab").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  extraTab = b.dataset.v;
  $$("#st-extra-tab button").forEach((x) => x.classList.toggle("on", x === b));
  drawExtra();
});

$("#st-ob").addEventListener("click", (e) => { const r = e.target.closest("[data-px]"); if (r && window.ticketSetPrice) ticketSetPrice(Number(r.dataset.px)); });
