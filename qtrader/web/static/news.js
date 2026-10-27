"use strict";
// 뉴스 탭: 여러 언론사 RSS 를 카드로. 위쪽 큰 카드 3개는 가장 최근 소식

const nw = { items: [], src: "전체", cat: "all", loaded: 0 };
const GLOBAL = new Set(["CNBC", "MarketWatch"]);
const SRC_COLOR = { "인베스팅닷컴": "#e9a23b", "한국경제 증권": "#1b4ea8", "매일경제 증권": "#e1251b", "연합뉴스 마켓": "#0b5fa5",
  "머니투데이 증권": "#d71f26", "조선비즈 증권": "#0e3a7a", "파이낸셜뉴스 증권": "#163b8c", "CNBC": "#005594", "MarketWatch": "#2c8c3c" };

function ago(t) {
  if (!t) return "";
  const m = Math.round((Date.now() - new Date(t)) / 60000);
  if (m < 1) return "방금"; if (m < 60) return m + "분 전"; if (m < 1440) return Math.round(m / 60) + "시간 전";
  return new Date(t).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
}

function card(n, big = false) {
  const c = SRC_COLOR[n.source] || "#6b7785";
  return `<a class="ncard ${big ? "big" : ""}" href="${esc(n.link)}" target="_blank" rel="noopener noreferrer" style="--c:${c}">
    <div class="nmeta"><span class="nsrc">${esc(n.source)}</span><span class="note">${ago(n.time)}</span></div>
    <b class="ntitle">${esc(n.title)}</b>${n.summary ? `<p class="nsum">${esc(n.summary)}</p>` : ""}</a>`;
}

function renderNews() {
  const q = $("#news-q").value.trim();
  const items = nw.items.filter((n) => (nw.src === "전체" || n.source === nw.src) &&
    (nw.cat === "all" || (nw.cat === "global") === GLOBAL.has(n.source)) && (!q || n.title.includes(q)));
  $("#news-hero").innerHTML = items.slice(0, 3).map((n) => card(n, true)).join("");
  $("#news-list").innerHTML = items.slice(3, 120).map((n) => card(n)).join("") || (items.length ? "" : `<p class="note">뉴스가 없습니다.</p>`);
}

async function loadNewsTab(force = false) {
  if (!force && Date.now() - nw.loaded < 120000) return renderNews();
  $("#news-time").textContent = "불러오는 중...";
  try {
    const d = await api("/api/news");
    nw.items = d.items;
    nw.loaded = Date.now();
    const srcs = ["전체", ...new Set(d.items.map((n) => n.source))];
    $("#news-src").innerHTML = srcs.map((s) => `<button data-v="${esc(s)}" class="${s === nw.src ? "on" : ""}" style="--c:${SRC_COLOR[s] || "#6b7785"}">${esc(s)}</button>`).join("");
    $("#news-time").textContent = `${d.items.length}건 · ${new Date().toLocaleTimeString("ko-KR")} 기준` + (d.errors.length ? ` · 실패 ${d.errors.length}곳` : "");
    renderNews();
  } catch (e) { $("#news-time").textContent = e.message; }
}

$("#news-src").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  nw.src = b.dataset.v;
  $$("#news-src button").forEach((x) => x.classList.toggle("on", x === b));
  renderNews();
});
$("#news-cat").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  nw.cat = b.dataset.v;
  $$("#news-cat button").forEach((x) => x.classList.toggle("on", x === b));
  renderNews();
});
$("#news-q").addEventListener("input", renderNews);
$("#news-refresh").onclick = () => loadNewsTab(true);
document.addEventListener("tab", (e) => { if (e.detail === "news") loadNewsTab(); });
setInterval(() => { if (state.tab === "news" && !document.hidden) loadNewsTab(true); }, 300000);
