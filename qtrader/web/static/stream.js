"use strict";
// 실시간 시세: 지금 화면에 보이는 종목만 모아 서버 스트림(2초)을 받는다. 바뀐 가격 칸은 잠깐 깜빡인다
const rt = { es: null, key: "", last: {}, rates: {}, idx: {} };

function wantedSymbols() {
  const s = new Set();
  $$(`#tab-${state.tab} [data-key^="p:"], #tab-${state.tab} [data-key^="r:"]`).forEach((el) => s.add(el.dataset.key.slice(2)));
  $$(`#tab-${state.tab} [data-key^="i:"]`).forEach((el) => s.add("IDX:" + el.dataset.key.slice(2)));
  $$(`#tab-${state.tab} [data-key^="q:"]`).forEach((el) => s.add(el.dataset.key.slice(2)));  // 미국 지수 ETF 카드
  s.add("FX");  // 달러 환율은 어느 화면에서나 (원/달러 보기, 미국 종목 원화 환산)
  if (state.tab === "stock" && (typeof st !== "undefined" && st.symbol)) s.add(st.symbol);
  if (state.tab === "dash") (dash.data?.positions || []).forEach((p) => s.add(p.symbol));
  return [...s].slice(0, 100);
}

function connect() {
  const syms = wantedSymbols(), key = syms.join(",");
  if (key === rt.key && rt.es) return;
  rt.es?.close(); rt.es = null; rt.key = key;
  if (syms.length <= 1 && state.tab !== "mkt" || document.hidden || !state.status?.keys?.toss) { $("#rt-dot")?.classList.remove("on"); return; }
  rt.es = new EventSource("/api/stream?symbols=" + encodeURIComponent(key));
  rt.es.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    for (const [sym, px] of Object.entries(d.prices || {})) apply(sym, px);
    for (const [sym, r] of Object.entries(d.rates || {})) applyRate(sym, r);
    for (const [sym, v] of Object.entries(d.idx || {})) applyIdx(sym, v);
    if (d.fx?.rate) applyFx(d.fx);
    $("#rt-dot")?.classList.add("on");
    const b = $("#rt-dot")?.parentElement;
    if (b) b.title = `토스 실시간 시세 · 마지막 ${new Date().toLocaleTimeString("ko-KR", { hour12: false })} (2초마다)`;
  };
  rt.es.onerror = () => $("#rt-dot")?.classList.remove("on");
}

function apply(sym, px) {
  const old = rt.last[sym];
  rt.last[sym] = px;
  applyCard("q", sym, px);
  const m = /^\d/.test(sym) ? "KR" : "US";
  $$(`[data-key="p:${CSS.escape(sym)}"]`).forEach((el) => {
    el.dataset.val = px;
    el.textContent = money(px, m);
  });
  if (window.flashChanges) $$(`[data-key="p:${CSS.escape(sym)}"]`).forEach((el) => flashChanges(el.parentElement || el));
  if (state.tab === "stock" && (typeof st !== "undefined" && st.symbol) === sym && old != null && px !== old) {
    const el = $("#st-price");
    el.innerHTML = bigPriceHtml(px, m);
    el.classList.remove("flash-up", "flash-down"); void el.offsetWidth; el.classList.add(px > old ? "flash-up" : "flash-down");
    st.last = px;
  }
}

// 등락률 칸: 토스가 계산한 값 그대로 (순위표·관심·외국인 동향·종목 화면 모두 같은 숫자)
function applyRate(sym, r) {
  rt.rates[sym] = r;
  $$(`[data-key="r:${CSS.escape(sym)}"]`).forEach((el) => {
    if (el.dataset.val === String(r)) return;
    el.dataset.val = r;
    el.textContent = pct(r);
    el.classList.remove("up", "down");
    const c = cls(r); if (c) el.classList.add(c);
  });
}
// 지수 카드: 전일 종가(data-prev)로 등락을 다시 계산 (i: 국내 지수, q: 미국 지수 ETF)
function applyIdx(sym, v) { rt.idx[sym] = v; applyCard("i", sym, v); }
function applyCard(kind, sym, v) {
  $$(`[data-key="${kind}:${CSS.escape(sym)}"]`).forEach((card) => {
    const prev = Number(card.dataset.prev), old = Number(card.dataset.val);
    if (!prev || v === old) return;
    card.dataset.val = v;
    const ch = v - prev, r = v / prev - 1;
    const val = card.querySelector(".idx-val"), chg = card.querySelector(".idx-chg span"), tag = card.querySelector(".idx-tag");
    if (val) { val.textContent = Number(v).toLocaleString("ko-KR", { maximumFractionDigits: 2 }); val.classList.remove("flash-up", "flash-down"); void val.offsetWidth; val.classList.add(v > old ? "flash-up" : "flash-down"); }
    if (chg) { chg.className = cls(ch); chg.textContent = `${ch > 0 ? "+" : ""}${ch.toFixed(2)} (${pct(r)})`; }
    if (tag) { tag.className = "idx-tag " + (ch >= 0 ? "up-bg" : "down-bg"); tag.textContent = ch >= 0 ? "▲ 상승" : "▼ 하락"; }
  });
}
function applyFx(fx) {
  if (fx.rate === fxView.rate && fx.change === fxView.change) return;
  fxView.rate = fx.rate; fxView.change = fx.change;
  window.drawFxCard && drawFxCard();
  drawFxToggle();
}

// 화면 일부를 다시 그리면 (시장 요약 15초·순위 4초마다) 서버에 담아 둔 조금 전 값으로 바뀐다.
// 스트림은 바뀐 값만 보내므로, 다시 그린 칸에 마지막으로 받은 실시간 값을 곧바로 다시 입힌다 (예전엔 지수가 옛 값으로 남았다)
function rtReapply(root) {
  const q = (sel) => (root || document).querySelectorAll(sel);
  q('[data-key^="p:"]').forEach((el) => { const s = el.dataset.key.slice(2), v = rt.last[s];
    if (v != null && Number(el.dataset.val) !== v) { el.dataset.val = v; el.textContent = money(v, /^\d/.test(s) ? "KR" : "US"); } });
  q('[data-key^="r:"]').forEach((el) => { const s = el.dataset.key.slice(2), r = rt.rates[s];
    if (r != null && el.dataset.val !== String(r)) { el.dataset.val = r; el.textContent = pct(r); el.classList.remove("up", "down"); const c = cls(r); if (c) el.classList.add(c); } });
  q('[data-key^="i:"]').forEach((el) => { const s = el.dataset.key.slice(2); if (rt.idx[s] != null) applyCard("i", s, rt.idx[s]); });
  q('[data-key^="q:"]').forEach((el) => { const s = el.dataset.key.slice(2); if (rt.last[s] != null) applyCard("q", s, rt.last[s]); });
}
window.rtReapply = rtReapply;
let rtPend = false;
new MutationObserver((muts) => {
  if (rtPend || !muts.some((m) => m.addedNodes.length)) return;
  rtPend = true;
  requestAnimationFrame(() => { rtPend = false; rtReapply(); });
}).observe(document.body, { childList: true, subtree: true });

// 화면이 바뀌거나 목록이 다시 그려지면 구독 종목을 다시 맞춘다
setInterval(connect, 3000);
document.addEventListener("tab", () => setTimeout(connect, 800));
document.addEventListener("visibilitychange", () => { if (document.hidden) { rt.es?.close(); rt.es = null; rt.key = ""; } else connect(); });
