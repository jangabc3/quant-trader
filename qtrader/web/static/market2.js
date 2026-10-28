"use strict";
// 시장 탭: 맨 위 '내 요약', 지금 뜨는 산업 (토스 섹터 랭킹), 외국인·기관·개인 매매 동향

const tr = { tab: "rank", market: "KR", kind: "TOP_GAINERS", dur: "1d", sel: null, side: "buy" };
// 한 번 받은 내용은 기억해 두고, 탭을 다시 열면 빈 상자 없이 바로 보여 준 뒤 뒤에서 새로 받는다 (토스 앱처럼)
const trCache = new Map();
async function trGet(url, maxAge, show) {
  const hit = trCache.get(url);
  if (hit) show(hit.data, true);
  if (hit && Date.now() - hit.at < maxAge) return;
  const data = await api(url);
  trCache.set(url, { at: Date.now(), data });
  if (!hit || JSON.stringify(hit.data) !== JSON.stringify(data)) show(data, false);
}
const bigWon = (v) => v == null ? "-" : bigKRW(v) + "원";
const DUR_KO = { "1d": "1일", "1w": "1주", "1mo": "1개월", "3mo": "3개월", "1y": "1년" };

// 내 요약: 총자산 · 오늘 손익 · 보유 · 자동매매 상태 (누르면 내 계좌)
// 내 자산 한눈에: 총자산(원화 환산) · 오늘 손익 · 평가 손익 · 국내/미국 비중 · 가진 종목 · 자동매매 상태
const ms = { first: true };
async function loadMySum() {
  const box = $("#my-sum");
  if (!box) return;
  try {
    const [ov, pos] = await Promise.all([api("/api/account/overview"), api("/api/positions").catch(() => [])]);
    const s = state.status || await api("/api/status");
    const fx = mkt.ov?.fx?.rate || 1400;
    const k = ov.markets?.KR && !ov.markets.KR.error ? ov.markets.KR : null, u = ov.markets?.US && !ov.markets.US.error ? ov.markets.US : null;
    const krw = (m, v) => (m === "US" ? v * fx : v) || 0;
    const total = krw("KR", k?.equity) + krw("US", u?.equity);
    const prevOf = (m, x) => x && x.daily_return != null ? x.equity / (1 + x.daily_return) : x?.equity;
    // 토스가 계산해 준 오늘 손익이 있으면 그 값을, 없으면 어제 자산과 비교
    const dayOf = (m, x) => !x ? 0 : x.day_pnl != null ? krw(m, x.day_pnl) : krw(m, x.equity - prevOf(m, x));
    const today = dayOf("KR", k) + dayOf("US", u);
    const todayRate = total - today ? today / (total - today) : 0;
    const unreal = krw("KR", k?.unrealized) + krw("US", u?.unrealized);
    const cost = krw("KR", k?.cost) + krw("US", u?.cost);
    const cash = krw("KR", k?.cash) + krw("US", u?.cash), stock = total - cash;
    const top = pos.slice().sort((a, b) => krw(b.market, b.value) - krw(a.market, a.value)).slice(0, 3);
    const seg = (v, c, n) => total && v > 0 ? `<i style="width:${(v / total * 100).toFixed(1)}%;background:${c}" title="${n} ${(v / total * 100).toFixed(0)}%"></i>` : "";
    box.innerHTML = `<div class="my-hero">
      <div class="mh-main"><span class="mh-k">내 자산 <small>원화 환산 · 환율 ${Math.round(fx).toLocaleString()}원</small></span>
        <b class="mh-total" id="mh-total">${money(total, "KR")}</b>
        <div class="mh-rows"><span>오늘 <b class="${cls(today)}">${today > 0 ? "+" : ""}${money(today, "KR")} (${pct(todayRate)})</b></span>
          <span>평가 손익 <b class="${cls(unreal)}">${unreal > 0 ? "+" : ""}${money(unreal, "KR")}${cost ? ` (${pct(unreal / cost)})` : ""}</b></span></div>
        <div class="mh-bar">${seg(stock, "#3182f6", "주식")}${seg(cash, "#3a4656", "현금")}</div>
        <div class="mh-legend"><span><i style="background:#3182f6"></i>주식 ${money(stock, "KR")}</span><span><i style="background:#3a4656"></i>현금 ${money(cash, "KR")}</span>
          <span>국내 ${k ? money(k.equity, "KR") : "-"} · 미국 ${u ? money(u.equity, "US") : "꺼짐"}</span></div></div>
      <div class="mh-hold"><span class="mh-k">가진 주식 ${pos.length ? `<small>${pos.length}종목</small>` : ""}</span>
        ${top.map((p) => `<button class="mh-stock" data-open="${esc(p.symbol)}">${logo(p.symbol, p.name || p.symbol, 28)}<span>${esc(p.name || p.symbol)}</span>
          <b class="${cls(p.pnl)}">${pct(p.pnl)}</b></button>`).join("") || `<p class="note">아직 없어요. 자동매매가 사면 여기 보여요.</p>`}
        ${pos.length > 3 ? `<button class="mh-more" data-goto="dash">${pos.length - 3}종목 더 보기 ›</button>` : ""}</div>
      <div class="mh-side">
        <button class="mh-auto ${s.auto_trading ? "on" : ""} ${s.kill_switch ? "kill" : ""}" data-goto="office"><span class="mh-k">자동매매</span>
          <b><i class="ms-dot"></i>${s.kill_switch ? "긴급 정지 중" : s.auto_trading ? "켜짐 · 일하는 중" : "꺼짐"}</b><em>${MODE_NAME[s.mode]} · AI 사무실 ›</em></button>
        <button class="primary mh-go" data-goto="dash"><i data-lucide="wallet"></i> 내 계좌 자세히</button></div></div>`;
    box.querySelectorAll("[data-goto]").forEach((b) => b.onclick = () => openTab(b.dataset.goto));
    if (ms.first) { ms.first = false; countTo($("#mh-total"), total, { format: (v) => money(v, "KR") }); animateIn("#my-sum .mh-main, #my-sum .mh-hold, #my-sum .mh-side", { step: 80 }); }
    icons();
  } catch { box.innerHTML = ""; }
}

async function loadIndustries() {
  const body = $("#ind-body");
  const url = `/api/market/industries?market=${tr.market}&kind=${tr.kind}&duration=${tr.dur}`;
  if (!trCache.has(url)) body.innerHTML = `<tr><td colspan="6"><div class="skeleton" style="height:200px"></div></td></tr>`;
  try {
    await trGet(url, 60000, (r, fromCache) => {
      $("#trend-note").textContent = (r.demo ? "체험용 · " : "토스증권 산업 분류 · ") + "줄을 누르면 오른쪽에 자세히";
      body.innerHTML = r.items.map((x) => `<tr class="clickable ${tr.sel === x.sectorId ? "sel" : ""}" data-ind="${esc(x.sectorId)}">
          <td class="rk">${x.rank}</td><td><b>${esc(x.name)}</b></td>
          <td class="r"><span class="rate ${cls(x.changeRate)}">${pct(x.changeRate)}</span></td>
          <td class="r">${bigWon(x.tradingAmount)}</td><td class="r">${bigWon(x.marketCap)}</td>
          <td class="ind-lead">${x.leader ? `${logo(x.leader_symbol || x.leader, x.leader, 20)}<span>${esc(x.leader)}${x.count ? ` 외 ${x.count - 1}개` : ""}</span>` : "-"}</td></tr>`).join("")
        || `<tr><td colspan="6" class="note">산업 데이터가 없습니다.</td></tr>`;
      if (!fromCache) animateIn("#ind-body tr", { step: 18 });
      if (!tr.sel && r.items[0]) loadIndustry(r.items[0].sectorId);
    });
  } catch (e) { if (!trCache.has(url)) body.innerHTML = `<tr><td colspan="6" class="note">${esc(e.message)}</td></tr>`; }
}

async function loadIndustry(id) {
  tr.sel = id;
  $$("#ind-body tr").forEach((r) => r.classList.toggle("sel", r.dataset.ind === id));
  const el = $("#ind-detail");
  const url = `/api/market/industry/${encodeURIComponent(id)}?market=${tr.market}`;
  const hit = trCache.get(url);
  if (!hit) el.innerHTML = `<div class="skeleton" style="height:260px"></div>`;
  try {
    const d = hit && Date.now() - hit.at < 60000 ? hit.data : await api(url);
    trCache.set(url, hit && hit.data === d ? hit : { at: Date.now(), data: d });
    if (tr.sel !== id) return;  // 기다리는 사이 다른 산업을 눌렀으면 그쪽을 보여 준다
    const vals = d.perf.flatMap((p) => [p.sector, p.index]).filter((v) => v != null).map(Math.abs);
    const mx = Math.max(...vals, 0.01);
    el.innerHTML = `<h4>${esc(d.name || "")}</h4>${d.summary ? `<p class="note">${esc(d.summary)}</p>` : ""}
      <div class="ind-legend"><span><i style="background:#ff8a3d"></i>${esc(d.name || "산업")}</span><span><i style="background:#20b2aa"></i>${tr.market === "KR" ? "코스피" : "시장"}</span></div>
      <div id="ind-chart" class="ind-chart"></div><p class="note ind-chart-note" id="ind-chart-note"></p>
      <div class="ind-bars">${d.perf.map((p) => `<div class="ib-row"><span class="ib-k">${DUR_KO[p.duration]}</span>
        <div class="ib-pair"><div class="ib"><i style="width:${p.sector == null ? 0 : Math.abs(p.sector) / mx * 100}%;background:#ff8a3d"></i><b class="${cls(p.sector)}">${pct(p.sector)}</b></div>
        <div class="ib"><i style="width:${p.index == null ? 0 : Math.abs(p.index) / mx * 100}%;background:#20b2aa"></i><b class="${cls(p.index)}">${pct(p.index)}</b></div></div></div>`).join("")}</div>
      <h4 style="margin-top:14px">주식</h4>
      ${d.stocks.map((s) => `<div class="ind-stock" data-open="${esc(s.symbol)}">${logo(s.symbol, s.name, 26)}<b>${esc(s.name)}</b><span>${s.price ? money(s.price, tr.market) : ""}</span></div>`).join("")}
      ${(d.etfs || []).length ? `<h4 style="margin-top:14px">이 산업에 투자하는 ETF <small class="note">한 번에 여러 종목</small></h4>
        ${d.etfs.map((s) => `<div class="ind-stock" data-open="${esc(s.symbol)}">${logo(s.symbol, s.name, 26)}<b>${esc(s.name)}</b><span>${s.price ? money(s.price, tr.market) : ""}</span></div>`).join("")}` : ""}`;
    drawIndustryChart(id, d.name);
    if (!REDUCED && window.anime) anime({ targets: "#ind-detail .ib i", scaleX: [0, 1], duration: 600, delay: anime.stagger(40), easing: "easeOutCubic" });
  } catch (e) { el.innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}

async function loadInvestors() {
  const box = $("#inv-cols");
  const url = "/api/market/investors?side=" + tr.side;
  if (!trCache.has(url)) box.innerHTML = `<div class="skeleton" style="height:300px"></div>`;
  try {
    await trGet(url, 60000, (r, fromCache) => {
      $("#inv-note").textContent = r.demo ? "체험용 데이터" : `국내 · 거래대금 상위 40종목 기준 · ${r.updated ? String(r.updated).slice(5, 16).replace("T", " ") + " 기준" : ""}`;
      const col = (k, title) => `<section><header><b>${title}</b><span class="note">거래액</span></header>
        ${(r[k] || []).map((x, i) => `<div class="inv-row" data-open="${esc(x.symbol)}"><span class="rk">${i + 1}</span>${logo(x.symbol, x.name, 28)}
          <div><b>${esc(x.name)}</b><span>${money(x.price, "KR")} <em class="${cls(x.change_rate)}">${pct(x.change_rate)}</em></span></div>
          <strong class="${tr.side === "buy" ? "up" : "down"}">${bigKRW(Math.abs(x[k]))}원</strong></div>`).join("") || `<p class="note">자료가 없습니다.</p>`}</section>`;
      box.innerHTML = col("foreigner", "외국인") + col("institution", "기관") + col("individual", "개인");
      if (!fromCache) animateIn("#inv-cols .inv-row", { step: 15 });
    });
  } catch (e) { if (!trCache.has(url)) box.innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}

function trSeg(id, fn) {
  $(id)?.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    $$(`${id} button`).forEach((x) => x.classList.toggle("on", x === b));
    fn(b.dataset.v);
  });
}
// 실시간 차트 · 지금 뜨는 산업 · 외국인·기관 매매 동향: 한 패널 안에서 탭으로 (밑줄이 미끄러지듯)
function trendInk() {
  const on = $(`#trend-tab button[data-v="${tr.tab}"]`), ink = $("#trend-tab .tt-ink");
  if (on && ink) { ink.style.width = on.offsetWidth + "px"; ink.style.transform = `translateX(${on.offsetLeft}px)`; }
}
function trendTab(v) {
  tr.tab = v;
  ["rank", "ind", "inv"].forEach((k) => $("#trend-" + k).classList.toggle("hidden", k !== v));
  const on = $(`#trend-tab button[data-v="${v}"]`), ink = $("#trend-tab .tt-ink");
  $$("#trend-tab button").forEach((b) => b.classList.toggle("on", b === on));
  if (on && ink) { ink.style.width = on.offsetWidth + "px"; ink.style.transform = `translateX(${on.offsetLeft}px)`; }
  if (v === "ind") loadIndustries(); else if (v === "inv") loadInvestors();
  animateIn(`#trend-${v} tbody tr, #trend-${v} .inv-row`, { step: 15 });
  try { localStorage.setItem("qtrader.trend", v); } catch {}
}
$("#trend-tab").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) trendTab(b.dataset.v); });
setTimeout(() => { let v = "rank"; try { v = localStorage.getItem("qtrader.trend") || "rank"; } catch {} trendTab(v); }, 400);
window.addEventListener("resize", trendInk);  // 창 크기가 바뀌면 밑줄만 옮긴다 (다시 받지 않음)
trSeg("#ind-market", (v) => { tr.market = v; tr.sel = null; loadIndustries(); });
trSeg("#ind-kind", (v) => { tr.kind = v; loadIndustries(); });
trSeg("#ind-dur", (v) => { tr.dur = v; loadIndustries(); });
trSeg("#inv-side", (v) => { tr.side = v; loadInvestors(); });
$("#ind-body")?.addEventListener("click", (e) => { const r = e.target.closest("[data-ind]"); if (r) loadIndustry(r.dataset.ind); });
document.addEventListener("tab", (e) => { if (e.detail === "mkt") { loadMySum(); setTimeout(() => trendTab(tr.tab), 50); } });
setTimeout(() => { if (state.tab === "mkt") loadMySum(); }, 600);
setInterval(() => { if (state.tab === "mkt" && !document.hidden) loadMySum(); }, 30000);

// 산업 vs 코스피 1년 선 그래프 (토스처럼 주황 = 산업, 청록 = 코스피)
async function drawIndustryChart(id, name) {
  const el = $("#ind-chart");
  if (!el || !window.LightweightCharts) return;
  const c = await api(`/api/market/industry/${encodeURIComponent(id)}/chart?market=${tr.market}`).catch(() => null);
  if (!c || !c.sector.length || tr.sel !== id) { el.innerHTML = `<p class="note">차트 자료가 없어요.</p>`; return; }
  el.innerHTML = "";
  const ch = LightweightCharts.createChart(el, { layout: { background: { color: "transparent" }, textColor: "#8a97a6", fontSize: 11 }, autoSize: true,
    grid: { vertLines: { visible: false }, horzLines: { color: "#1b222b" } }, rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false }, crosshair: { horzLine: { visible: false } },
    localization: { priceFormatter: (v) => (v * 100).toFixed(0) + "%" } });
  ch.addLineSeries({ color: "#ff8a3d", lineWidth: 2, priceLineVisible: false }).setData(c.sector);
  if (c.index.length) ch.addLineSeries({ color: "#20b2aa", lineWidth: 2, priceLineVisible: false }).setData(c.index);
  ch.timeScale().fitContent();
  const s1 = c.sector.at(-1).value, i1 = c.index.at(-1)?.value;
  $("#ind-chart-note").innerHTML = `1년 <b style="color:#ff8a3d">${esc(name || "산업")} ${pct(s1)}</b>${i1 != null ? ` · <b style="color:#20b2aa">코스피 ${pct(i1)}</b>` : ""}`
    + (c.basket?.length ? ` · ${esc(c.basket.slice(0, 3).join(", "))} 등 시총 상위 ${c.basket.length}종목으로 계산` : " · 체험용");
}

// 산업 분류 전체 (대·중·소분류 나무)
async function openSectorTree() {
  let el = $("#sector-tree");
  if (!el) { el = document.createElement("div"); el.id = "sector-tree"; el.className = "modal-back"; document.body.appendChild(el); }
  el.innerHTML = `<div class="st-box"><header><b>산업 분류 전체</b><input id="st-q" placeholder="산업 찾기 (예: 반도체)"><button data-x>닫기</button></header><div class="st-body"><div class="skeleton" style="height:240px"></div></div></div>`;
  el.classList.remove("hidden");
  el.onclick = (e) => {
    if (e.target === el || e.target.closest("[data-x]")) { el.classList.add("hidden"); return; }
    const n = e.target.closest("[data-sid]");
    if (n) { el.classList.add("hidden"); loadIndustry(n.dataset.sid); }
  };
  const r = await api("/api/market/sector-tree").catch((e) => ({ tree: [], err: e.message }));
  const node = (s) => `<li><span data-sid="${esc(s.id)}" class="st-n d${s.depth}">${esc(s.name)}</span>${s.children.length ? `<ul>${s.children.map(node).join("")}</ul>` : ""}</li>`;
  const body = el.querySelector(".st-body");
  body.innerHTML = r.tree.length ? `${r.demo ? `<p class="note">체험용 예시예요. 토스 키를 넣으면 전체 분류가 나와요.</p>` : ""}<ul class="st-tree">${r.tree.map(node).join("")}</ul>` : `<p class="note">${esc(r.err || "분류를 받지 못했어요")}</p>`;
  $("#st-q").oninput = () => {
    const q = $("#st-q").value.trim();
    body.querySelectorAll("li").forEach((li) => { li.style.display = !q || li.textContent.includes(q) ? "" : "none"; });
  };
}
$("#ind-tree-btn")?.addEventListener("click", openSectorTree);
