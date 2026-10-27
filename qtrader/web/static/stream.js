"use strict";
// 실시간 시세: 지금 화면에 보이는 종목만 모아 서버 스트림(2초)을 받는다. 바뀐 가격 칸은 잠깐 깜빡인다
const rt = { es: null, key: "", last: {} };

function wantedSymbols() {
  const s = new Set();
  $$(`#tab-${state.tab} [data-key^="p:"]`).forEach((el) => s.add(el.dataset.key.slice(2)));
  if (state.tab === "stock" && (typeof st !== "undefined" && st.symbol)) s.add(st.symbol);
  if (state.tab === "dash") (dash.data?.positions || []).forEach((p) => s.add(p.symbol));
  return [...s].slice(0, 100);
}

function connect() {
  const syms = wantedSymbols(), key = syms.join(",");
  if (key === rt.key && rt.es) return;
  rt.es?.close(); rt.es = null; rt.key = key;
  if (!syms.length || document.hidden || !state.status?.keys?.toss) { $("#rt-dot")?.classList.remove("on"); return; }
  rt.es = new EventSource("/api/stream?symbols=" + encodeURIComponent(key));
  rt.es.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    for (const [sym, px] of Object.entries(d.prices || {})) apply(sym, px);
    $("#rt-dot")?.classList.add("on");
  };
  rt.es.onerror = () => $("#rt-dot")?.classList.remove("on");
}

function apply(sym, px) {
  const old = rt.last[sym];
  rt.last[sym] = px;
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

// 화면이 바뀌거나 목록이 다시 그려지면 구독 종목을 다시 맞춘다
setInterval(connect, 3000);
document.addEventListener("tab", () => setTimeout(connect, 800));
document.addEventListener("visibilitychange", () => { if (document.hidden) { rt.es?.close(); rt.es = null; rt.key = ""; } else connect(); });
