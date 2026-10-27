"use strict";
// 시장 탭: 맨 위 '내 요약', 지금 뜨는 산업 (토스 섹터 랭킹), 외국인·기관·개인 매매 동향

const tr = { tab: "ind", market: "KR", kind: "TOP_GAINERS", dur: "1d", sel: null, side: "buy" };
const bigWon = (v) => v == null ? "-" : bigKRW(v) + "원";
const DUR_KO = { "1d": "1일", "1w": "1주", "1mo": "1개월", "3mo": "3개월", "1y": "1년" };

// 내 요약: 총자산 · 오늘 손익 · 보유 · 자동매매 상태 (누르면 내 계좌)
async function loadMySum() {
  const box = $("#my-sum");
  if (!box) return;
  try {
    const [ov, s] = [await api("/api/account/overview"), state.status || await api("/api/status")];
    const k = ov.markets?.KR, u = ov.markets?.US;
    const day = (m) => m && m.daily_return != null ? `<b class="${cls(m.daily_return)}">${pct(m.daily_return)}</b>` : `<b>-</b>`;
    box.innerHTML = `<button class="ms-card" data-goto="dash"><span>내 국내 계좌</span><b>${k && !k.error ? money(k.equity, "KR") : "-"}</b><em>오늘 ${day(k)} · 보유 ${k?.positions ?? 0}종목</em></button>
      <button class="ms-card" data-goto="dash"><span>내 미국 계좌</span><b>${u && !u.error ? money(u.equity, "US") : "사용 안 함"}</b><em>${u ? `오늘 ${day(u)} · 보유 ${u.positions ?? 0}종목` : "설정에서 켤 수 있어요"}</em></button>
      <button class="ms-card ms-auto ${s.auto_trading ? "on" : ""} ${s.kill_switch ? "kill" : ""}" data-goto="office"><span>자동매매</span>
        <b><i class="ms-dot"></i>${s.kill_switch ? "긴급 정지 중" : s.auto_trading ? "켜짐 · 일하는 중" : "꺼짐"}</b><em>${MODE_NAME[s.mode]} · AI 사무실 보기 ›</em></button>`;
    box.querySelectorAll("[data-goto]").forEach((b) => b.onclick = () => openTab(b.dataset.goto));
  } catch { box.innerHTML = ""; }
}

async function loadIndustries() {
  const body = $("#ind-body");
  body.innerHTML = `<tr><td colspan="6"><div class="skeleton" style="height:200px"></div></td></tr>`;
  try {
    const r = await api(`/api/market/industries?market=${tr.market}&kind=${tr.kind}&duration=${tr.dur}`);
    $("#trend-note").textContent = (r.demo ? "체험용 · " : "토스증권 산업 분류 · ") + "줄을 누르면 오른쪽에 자세히";
    body.innerHTML = r.items.map((x) => `<tr class="clickable ${tr.sel === x.sectorId ? "sel" : ""}" data-ind="${esc(x.sectorId)}">
        <td class="rk">${x.rank}</td><td><b>${esc(x.name)}</b></td>
        <td class="r"><span class="rate ${cls(x.changeRate)}">${pct(x.changeRate)}</span></td>
        <td class="r">${bigWon(x.tradingAmount)}</td><td class="r">${bigWon(x.marketCap)}</td>
        <td class="ind-lead">${x.leader ? `${logo(x.leader_symbol || x.leader, x.leader, 20)}<span>${esc(x.leader)}${x.count ? ` 외 ${x.count - 1}개` : ""}</span>` : "-"}</td></tr>`).join("")
      || `<tr><td colspan="6" class="note">산업 데이터가 없습니다.</td></tr>`;
    animateIn("#ind-body tr", { step: 18 });
    if (!tr.sel && r.items[0]) loadIndustry(r.items[0].sectorId);
  } catch (e) { body.innerHTML = `<tr><td colspan="6" class="note">${esc(e.message)}</td></tr>`; }
}

async function loadIndustry(id) {
  tr.sel = id;
  $$("#ind-body tr").forEach((r) => r.classList.toggle("sel", r.dataset.ind === id));
  const el = $("#ind-detail");
  el.innerHTML = `<div class="skeleton" style="height:260px"></div>`;
  try {
    const d = await api(`/api/market/industry/${encodeURIComponent(id)}?market=${tr.market}`);
    const vals = d.perf.flatMap((p) => [p.sector, p.index]).filter((v) => v != null).map(Math.abs);
    const mx = Math.max(...vals, 0.01);
    el.innerHTML = `<h4>${esc(d.name || "")}</h4>${d.summary ? `<p class="note">${esc(d.summary)}</p>` : ""}
      <div class="ind-legend"><span><i style="background:#ff8a3d"></i>${esc(d.name || "산업")}</span><span><i style="background:#20b2aa"></i>${tr.market === "KR" ? "코스피" : "시장"}</span></div>
      <div class="ind-bars">${d.perf.map((p) => `<div class="ib-row"><span class="ib-k">${DUR_KO[p.duration]}</span>
        <div class="ib-pair"><div class="ib"><i style="width:${p.sector == null ? 0 : Math.abs(p.sector) / mx * 100}%;background:#ff8a3d"></i><b class="${cls(p.sector)}">${pct(p.sector)}</b></div>
        <div class="ib"><i style="width:${p.index == null ? 0 : Math.abs(p.index) / mx * 100}%;background:#20b2aa"></i><b class="${cls(p.index)}">${pct(p.index)}</b></div></div></div>`).join("")}</div>
      <h4 style="margin-top:14px">주식</h4>
      ${d.stocks.map((s) => `<div class="ind-stock" data-open="${esc(s.symbol)}">${logo(s.symbol, s.name, 26)}<b>${esc(s.name)}</b><span>${s.price ? money(s.price, tr.market) : ""}</span></div>`).join("")}`;
    if (!REDUCED && window.anime) anime({ targets: "#ind-detail .ib i", scaleX: [0, 1], duration: 600, delay: anime.stagger(40), easing: "easeOutCubic" });
  } catch (e) { el.innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}

async function loadInvestors() {
  const box = $("#inv-cols");
  box.innerHTML = `<div class="skeleton" style="height:300px"></div>`;
  try {
    const r = await api("/api/market/investors?side=" + tr.side);
    $("#inv-note").textContent = r.demo ? "체험용 데이터" : `국내 · 거래대금 상위 40종목 기준 · ${r.updated ? String(r.updated).slice(5, 16).replace("T", " ") + " 기준" : ""}`;
    const col = (k, title) => `<section><header><b>${title}</b><span class="note">거래액</span></header>
      ${(r[k] || []).map((x, i) => `<div class="inv-row" data-open="${esc(x.symbol)}"><span class="rk">${i + 1}</span>${logo(x.symbol, x.name, 28)}
        <div><b>${esc(x.name)}</b><span>${money(x.price, "KR")} <em class="${cls(x.change_rate)}">${pct(x.change_rate)}</em></span></div>
        <strong class="${tr.side === "buy" ? "up" : "down"}">${bigKRW(Math.abs(x[k]))}원</strong></div>`).join("") || `<p class="note">자료가 없습니다.</p>`}</section>`;
    box.innerHTML = col("foreigner", "외국인") + col("institution", "기관") + col("individual", "개인");
    animateIn("#inv-cols .inv-row", { step: 15 });
  } catch (e) { box.innerHTML = `<p class="note">${esc(e.message)}</p>`; }
}

function trSeg(id, fn) {
  $(id)?.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    $$(`${id} button`).forEach((x) => x.classList.toggle("on", x === b));
    fn(b.dataset.v);
  });
}
trSeg("#trend-tab", (v) => { tr.tab = v; $("#trend-ind").classList.toggle("hidden", v !== "ind"); $("#trend-inv").classList.toggle("hidden", v !== "inv"); v === "ind" ? loadIndustries() : loadInvestors(); });
trSeg("#ind-market", (v) => { tr.market = v; tr.sel = null; loadIndustries(); });
trSeg("#ind-kind", (v) => { tr.kind = v; loadIndustries(); });
trSeg("#ind-dur", (v) => { tr.dur = v; loadIndustries(); });
trSeg("#inv-side", (v) => { tr.side = v; loadInvestors(); });
$("#ind-body")?.addEventListener("click", (e) => { const r = e.target.closest("[data-ind]"); if (r) loadIndustry(r.dataset.ind); });
document.addEventListener("tab", (e) => { if (e.detail === "mkt") { loadMySum(); tr.tab === "ind" ? loadIndustries() : loadInvestors(); } });
setTimeout(() => { if (state.tab === "mkt") { loadMySum(); loadIndustries(); } }, 600);
setInterval(() => { if (state.tab === "mkt" && !document.hidden) loadMySum(); }, 30000);
