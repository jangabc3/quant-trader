"use strict";
// 뉴스 탭: 국내증시 | 해외증시 두 줄로 나란히, 최신순 목록. 언론사·검색으로 거르기

const nw = { items: [], src: "전체", cat: "all", loaded: 0, more: { kr: 30, global: 30 } };
const SRC_COLOR = { "인베스팅닷컴": "#e9a23b", "한국경제 증권": "#1b4ea8", "매일경제 증권": "#e1251b", "연합뉴스 마켓": "#0b5fa5",
  "머니투데이 증권": "#d71f26", "조선비즈 증권": "#0e3a7a", "파이낸셜뉴스 증권": "#163b8c" };
const CAT_NAME = { kr: "국내증시", global: "해외증시" };

function ago(t) {
  if (!t) return "";
  const m = Math.round((Date.now() - new Date(t)) / 60000);
  if (m < 1) return "방금"; if (m < 60) return m + "분 전"; if (m < 1440) return Math.round(m / 60) + "시간 전";
  return new Date(t).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
}

function srcColor(s) {
  if (SRC_COLOR[s]) return SRC_COLOR[s];
  let h = 0;
  for (const ch of String(s)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return ["#3182f6", "#00a37a", "#e9a23b", "#7c5cf6", "#0ea5e9", "#d6336c"][h % 6];
}

function row(n) {
  return `<a class="nrow" href="${esc(n.link)}" target="_blank" rel="noopener noreferrer" style="--c:${srcColor(n.source)}">
    <div class="nrow-meta"><span class="nsrc">${esc(n.source)}</span><span>${ago(n.time)}</span></div>
    <b class="ntitle">${esc(n.title)}</b>${n.summary ? `<p class="nsum">${esc(n.summary)}</p>` : ""}</a>`;
}

function column(cat, items, wide) {
  const shown = items.slice(0, nw.more[cat]);
  return `<section class="ncol ${wide ? "wide" : ""}">
    <header><b>${CAT_NAME[cat]}</b><span class="note">${items.length}건 · 최신순</span></header>
    <div class="nlist">${shown.map(row).join("") || `<p class="note pad">해당하는 뉴스가 없습니다.</p>`}</div>
    ${items.length > shown.length ? `<button class="nmore" data-more="${cat}">${items.length - shown.length}건 더 보기</button>` : ""}
  </section>`;
}

function renderNews() {
  const q = $("#news-q").value.trim();
  const base = nw.items.filter((n) => (nw.src === "전체" || n.feed === nw.src) && (!q || n.title.includes(q)));
  const by = (c) => base.filter((n) => n.cat === c).sort((a, b) => new Date(b.time || 0) - new Date(a.time || 0));
  const cats = nw.cat === "all" ? ["kr", "global"] : [nw.cat];
  $("#news-list").className = "news-cols" + (cats.length === 1 ? " one" : "");
  $("#news-list").innerHTML = cats.map((c) => column(c, by(c), cats.length === 1)).join("");
  // 언론사 칩은 지금 보는 분류의 언론사만
  const srcs = ["전체", ...new Set(nw.items.filter((n) => nw.cat === "all" || n.cat === nw.cat).map((n) => n.feed))];
  if (!srcs.includes(nw.src)) nw.src = "전체";
  $("#news-src").innerHTML = srcs.map((s) => `<button data-v="${esc(s)}" class="${s === nw.src ? "on" : ""}" style="--c:${s === "전체" ? "#4e5968" : srcColor(s)}">${esc(s)}</button>`).join("");
  window.animateIn && animateIn("#news-list .nrow");
}

async function loadNewsTab(force = false) {
  if (!force && Date.now() - nw.loaded < 120000) return renderNews();
  $("#news-time").textContent = "불러오는 중...";
  try {
    const d = await api("/api/news");
    nw.items = d.items;
    nw.loaded = Date.now();
    $("#news-time").textContent = `${new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })} 기준` +
      (d.errors.length ? ` · 연결 안 된 곳 ${d.errors.length}` : "");
    renderNews();
  } catch (e) { $("#news-time").textContent = e.message; }
}

$("#news-src").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  nw.src = b.dataset.v;
  renderNews();
});
$("#news-cat").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  nw.cat = b.dataset.v;
  $$("#news-cat button").forEach((x) => x.classList.toggle("on", x === b));
  renderNews();
});
$("#news-list").addEventListener("click", (e) => {
  const b = e.target.closest("[data-more]");
  if (!b) return;
  nw.more[b.dataset.more] += 30;
  renderNews();
});
$("#news-q").addEventListener("input", renderNews);
$("#news-refresh").onclick = () => loadNewsTab(true);
document.addEventListener("tab", (e) => { if (e.detail === "news") loadNewsTab(); });
setInterval(() => { if (state.tab === "news" && !document.hidden) loadNewsTab(true); }, 300000);
